"""SteamAutoCrack transport through the Steam Downloader's owned VM profile."""
import importlib
import json
import os
from pathlib import Path
import time
import uuid

GUEST = '/usr/local/lib/playlite-vm/autocrack_worker.py'
ASSETS = Path(__file__).resolve().parent


def steam_backend():
    try:
        return importlib.import_module('playlite_plugins.steamdepotdownloader.vm_backend')
    except ModuleNotFoundError:
        from playlite.providers import discover_plugins
        if 'SteamDepotDownloader' not in discover_plugins():
            raise ValueError('Install and enable Steam Downloader, then set up its Steam VM.') from None
        return importlib.import_module('playlite_plugins.steamdepotdownloader.vm_backend')


def default_root():
    data = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share')))
    return Path(os.environ.get('PLAYLITE_STEAM_VM_ROOT', data / 'playlite/plugin-data/SteamDepotDownloader/steam-vm'))


def shared_game_path(directory, shared):
    root, shared = Path(directory).expanduser(), Path(shared).expanduser().resolve()
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise ValueError('Select an installed game folder before running SteamAutoCrack.')
    root = root.resolve()
    relative = None
    # Support the two host mount aliases used by release and repository profiles.
    for parent in root.parents:
        try:
            if os.path.samefile(parent, shared):
                relative = root.relative_to(parent)
                break
        except OSError:
            continue
    if relative is None or not relative.parts or relative.parts[0].startswith('.'):
        raise ValueError('The game must be inside the Steam VM shared library folder.')
    if any(c in str(relative) for c in '\r\n\0\\:'):
        raise ValueError('The game folder name cannot be represented as a Windows path.')
    return '/mnt/standalone/' + relative.as_posix()


class Client:
    def __init__(self, root=None, cancel=lambda: False, status=lambda text: None):
        self.backend = steam_backend()
        self.root = Path(root or default_root()).expanduser().resolve()
        try:
            self.cfg = self.backend.configuration(self.root)
        except RuntimeError as error:
            raise ValueError(str(error)) from None
        self.agent = self.backend.Agent(self.cfg)
        self.cancel, self.status = cancel, status

    def check_cancelled(self):
        if self.cancel():
            raise ValueError('Cancelled before starting VM processing.')

    def boot(self):
        self.check_cancelled()
        self.status('Starting the Steam VM…')
        self.backend.installer_module().open_vm(self.cfg, desktop=False)
        deadline = time.monotonic() + 180
        while True:
            self.check_cancelled()
            try:
                self.agent.call({'execute': 'guest-ping'})
                break
            except RuntimeError:
                if time.monotonic() > deadline:
                    raise ValueError('Steam VM guest control did not become ready.') from None
                time.sleep(1)
        for source, target in [('guest/worker.py', 'autocrack_worker.py'),
                               ('guest/install_cli.py', 'install_cli.py'),
                               ('guest/vanilla.py', 'vanilla.py')]:
            self.agent.write_file('/usr/local/lib/playlite-vm/' + target, (ASSETS / source).read_bytes())
        from playlite_plugins.cracktools import backup
        self.agent.write_file('/usr/local/lib/playlite-vm/backup.py', Path(backup.__file__).read_bytes())

    def command(self, command, job=None, request=None):
        args = ['/usr/bin/python3', GUEST, command, *([job] if job else [])]
        code, output, _ = self.agent.execute(args, json.dumps(request).encode() if request is not None else None,
                                            timeout=30)
        try:
            envelope = json.loads(output)
        except ValueError:
            raise ValueError('Steam VM returned an invalid CLI response.') from None
        if code or not envelope.get('ok'):
            raise ValueError(envelope.get('error', 'Steam VM CLI request failed.'))
        return envelope['result']

    def wait_for_vpn(self):
        deadline = time.monotonic() + 180
        next_connect = 0
        last_error = 'VPN has not become ready.'
        while True:
            self.check_cancelled()
            try:
                state = self.agent.rpc({'command': 'vpn_check'})
                if state.get('connected') is True and state.get('protected') is True:
                    self.status('VM VPN connected and verified.')
                    return
            except RuntimeError as error:
                last_error = str(error)
            now = time.monotonic()
            if now >= deadline:
                raise ValueError('Timed out waiting for the VM VPN: ' + last_error +
                                 ' Check NordVPN in the Steam VM and retry. '
                                 'No game files were changed by this attempt.')
            if now >= next_connect:
                self.status('Waiting for the VM VPN service; connecting NordVPN…')
                try:
                    self.agent.rpc({'command': 'vpn_connect'})
                except RuntimeError as error:
                    last_error = str(error)
                next_connect = time.monotonic() + 30
                continue
            self.status('Waiting for NordVPN to connect and verify protection…')
            # Short waits keep cancellation responsive while the guest finishes booting.
            for _ in range(10):
                self.check_cancelled()
                time.sleep(.2)

    def execute(self, request):
        self.boot()
        payload = dict(request)
        payload.pop('InstallDirectory', None)
        payload.pop('VmRoot', None)
        if not request.get('Setup'):
            payload['GuestPath'] = shared_game_path(request['InstallDirectory'], self.cfg['shared'])
            if request.get('GenerateInfo') and not request.get('Restore'):
                self.status('Checking the VM VPN before Steam game-info requests…')
                self.wait_for_vpn()
        job = uuid.uuid4().hex
        payload['JobId'] = job
        self.check_cancelled()
        self.command('start', request=payload)
        deadline = time.monotonic() + int(payload.get('TimeoutMinutes', 5)) * 60 + 180
        cancelling = False
        try:
            while True:
                if self.cancel() and not cancelling:
                    self.command('cancel', job)
                    cancelling = True
                    self.status('Cancelling the VM CLI and restoring original files…')
                    deadline = min(deadline, time.monotonic() + 180)
                state = self.command('status', job)
                if state.get('log'):
                    self.status(state['log'])
                if state.get('done'):
                    result = state['result']
                    message = result.get('Message', 'Finished')
                    lines = state.get('log', '').rstrip().splitlines()
                    if not lines or lines[-1] != message:
                        self.status(message)
                    try:
                        self.command('cleanup', job)
                    except ValueError:
                        self.status('Could not remove the finished VM job; its log remains in the VM.')
                    if not result.get('Success'):
                        raise ValueError(result.get('Message', 'VM CLI processing failed.'))
                    return result
                if not state.get('log'):
                    self.status(state.get('message', 'Processing in the Steam VM…'))
                if time.monotonic() > deadline:
                    raise ValueError('VM CLI did not finish. Check the VM before retrying; backups were retained.')
                time.sleep(.5)
        except Exception:
            # Ask a still-running job to stop. A lost guest-agent connection does
            # not erase its backups, and its own timeout remains in effect.
            try:
                self.command('cancel', job)
            except Exception:
                pass
            raise


def setup(root=None, cancel=lambda: False, status=lambda text: None):
    return Client(root, cancel, status).execute({'Setup': True, 'TimeoutMinutes': 30})


def process(request, cancel=lambda: False, status=lambda text: None):
    # Preserve recovery of backups made by the former native plugin, including
    # games outside the shared VM library. No native processing tools are used.
    root = Path(request['InstallDirectory']).expanduser()
    from .backup import BACKUP, LOCK, restore
    manifest = root / BACKUP / 'manifest.json'
    if request.get('Restore') and manifest.is_file():
        if json.loads(manifest.read_text()).get('Backend') != 'vm-cli':
            import fcntl
            if not root.is_absolute() or root.is_symlink() or not root.is_dir():
                raise ValueError('Select an installed game folder before restoring originals.')
            with (root / LOCK).open('a') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ValueError('Another operation is already modifying this game.') from None
                def check():
                    if cancel():
                        raise ValueError('Restoration cancelled before changing game files.')
                restore(root.resolve(), check, status)
            return {'Success': True, 'Message': 'Original game files restored.'}
    return Client(request.get('VmRoot'), cancel, status).execute(request)
