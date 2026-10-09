"""Bounded, asynchronous Windows CLI jobs, run as the VM desktop user."""
import fcntl
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vanilla import installed_baseline
from backup import BACKUP, LOCK, write_json, snapshot, finalize, restore, collect_cli_backups, collect_existing_settings, files, digest, protect_cli_backups

SHARED = Path('/mnt/standalone')
PREFIX = Path.home() / '.local/share/playlite/wine-steam-auto-crack'
CLI = PREFIX / 'drive_c/SteamAutoCrackCLI'
JOBS = Path.home() / '.local/state/playlite-autocrack/jobs'
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


def identity(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{32}', value):
        raise ValueError('Invalid VM job identity.')
    return value


def game_path(value):
    if not isinstance(value, str) or len(value) > 4096 or any(c in value for c in '\r\n\0\\'):
        raise ValueError('Invalid shared game path.')
    path = Path(value)
    root = path.resolve()
    shared = SHARED.resolve()
    if not os.path.ismount(SHARED):
        raise ValueError('The shared Steam library is not mounted.')
    if not path.is_absolute() or path.is_symlink() or not root.is_dir() or root == shared or not root.is_relative_to(shared):
        raise ValueError('Choose a game folder inside the VM shared Steam library.')
    if root.relative_to(shared).parts[0].startswith('.'):
        raise ValueError('VM staging and private folders cannot be processed.')
    return root


def configuration(request):
    restoring = bool(request.get('Restore'))
    info = bool(request.get('GenerateInfo', True)) and not restoring
    key = request.get('ApiKey', '')
    if info and (not isinstance(key, str) or not re.fullmatch('[A-Fa-f0-9]{32}', key)):
        raise ValueError('Set a Steam Web API key or disable game-info generation.')
    username = request.get('GoldbergUsername', 'Player')
    if not isinstance(username, str) or not username.strip() or len(username) > 128 or any(c in username for c in '\r\n\0'):
        raise ValueError('Invalid emulator username.')
    # Upstream requires its basic steam_appid.txt even when network game-info
    # generation is disabled. Use its own offline generator for that case.
    return dict(ProcessConfigs=dict(GenerateEMUGameInfo=not restoring, GenerateEMUConfig=not restoring,
                Unpack=bool(request.get('Unpack', True)) and not restoring, ApplyEMU=not restoring,
                GenerateCrackOnly=False, Restore=restoring),
                EMUConfigs=dict(AccountName=username),
                EMUGameInfoConfigs=dict(GameInfoAPI=1 if info else 2, SteamWebAPIKeyType=0, SteamWebAPIKey=key if info else '',
                                       GenerateImages=bool(request.get('GenerateAchievements', info)), UseSteamWebAppList=False),
                SteamStubUnpackerConfigs=dict(SteamAPICheckBypassMode=0), LogToFile=False)


def filter_game_info(root, request):
    # Upstream exposes one generator for all three categories. Filter only
    # its installed settings, leaving recovery and original backups intact.
    for relative, path in files(root).items():
        if 'steam_settings' not in path.relative_to(root).parts:
            continue
        achievements = bool(request.get('GenerateAchievements', request.get('GenerateInfo', True)))
        stats = bool(request.get('GenerateStats', request.get('GenerateInfo', True)))
        dlc = bool(request.get('GenerateDlc', request.get('GenerateInfo', True)))
        if ((not achievements and (path.name == 'achievements.json' or 'achievement_images' in path.parts)) or
                (not stats and path.name in ('stats.json', 'stats.txt'))):
            path.unlink()
        elif not dlc and path.name == 'configs.app.ini':
            text = path.read_text(encoding='utf-8-sig')
            text = re.sub(r'(?ms)^\[app::dlcs\][^\n]*\n.*?(?=^\[|\Z)', '', text)
            path.write_text(text.rstrip() + '\n\n[app::dlcs]\nunlock_all=0\n', encoding='utf-8')


def redact(text, key=''):
    text = ANSI.sub('', text)
    if key:
        text = text.replace(key, '[redacted]')
    return re.sub(r'(?i)(key|access_token)=([^&\s]+)', r'\1=[redacted]', text)


def run_cli(root, request, folder, check, status):
    executable = CLI / 'SteamAutoCrack.CLI.exe'
    if not executable.is_file():
        raise ValueError('Set up the VM CLI in SteamAutoCrack settings first.')
    config = folder / 'cli-config.json'
    write_json(config, configuration(request))
    relative = root.relative_to(SHARED.resolve())
    windows_path = 'S:\\' + str(relative).replace('/', '\\')
    config_path = 'Z:' + str(config).replace('/', '\\')
    env = os.environ.copy()
    env.update(WINEPREFIX=str(PREFIX), WINEARCH='win64', WINEDLLOVERRIDES='mshtml=',
               WINEDEBUG='-all', DISPLAY=':0', XAUTHORITY=str(Path.home() / '.Xauthority'))
    env.pop('DOTNET_ROOT', None)
    process = None
    complete, failed = False, False
    output = ''
    pending = b''
    try:
        process = subprocess.Popen(['wine', str(executable), '--debug', 'crack', windows_path,
            '--config', config_path, '--appid', str(request['AppId'])], cwd=CLI, env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True)
        os.set_blocking(process.stdout.fileno(), False)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                check()
                for key, _ in selector.select(.2):
                    chunk = os.read(key.fd, 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        if pending:
                            chunk, pending = pending + b'\n', b''
                        else:
                            continue
                    pending += chunk
                    if len(pending) > 65536:
                        raise ValueError('CLI output exceeded its line limit.')
                    while b'\n' in pending:
                        line, pending = pending.split(b'\n', 1)
                        line = redact(line.decode(errors='replace').strip(), request.get('ApiKey', ''))
                        failed |= '[ERR]' in line or '[FTL]' in line
                        if 'Unhandled exception' in line or 'starting debugger' in line:
                            raise ValueError('The Windows CLI crashed. ' + line)
                        complete |= 'All process completed.' in line
                        if line:
                            output = (output + line + '\n')[-16384:]
                            (folder / 'log.txt').write_text(output)
                            status(line[-500:])
        code = process.wait(timeout=5)
        if code or failed or not complete:
            raise ValueError('Windows CLI processing failed. ' + output[-1500:])
    finally:
        config.unlink(missing_ok=True)
        if process is not None:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            process.stdout.close()


def verify_applied(root, request, status):
    for relative, path in files(root).items():
        if path.name.lower() in ('steam_api.dll', 'steam_api64.dll'):
            architecture = 'x64' if path.name.lower() == 'steam_api64.dll' else 'x86'
            expected = CLI / 'Goldberg/regular' / architecture / path.name.lower()
            if not expected.is_file() or digest(path) != digest(expected):
                raise ValueError('Emulator DLL was not applied: ' + relative)
            settings = path.parent / 'steam_settings'
            if not (settings / 'steam_appid.txt').is_file() or not (settings / 'configs.user.ini').is_file():
                raise ValueError('Emulator configuration was not applied: ' + relative)
    if request.get('Unpack', True):
        for relative, path in files(root).items():
            if path.name.lower().endswith('.exe.bak'):
                executable = path.with_suffix('')
                if not executable.is_file() or digest(executable) == digest(path):
                    raise ValueError('Unpacked executable was not applied: ' + relative)
            if path.name.lower().endswith('.exe.unpacked.exe'):
                raise ValueError('Unpacked executable was not applied: ' + relative)
    status('Verified emulator DLLs, configuration, and executable outputs.')


def work(job):
    folder = JOBS / identity(job)
    request = json.loads((folder / 'request.json').read_text())
    (folder / 'request.json').unlink()
    key = request.get('ApiKey', '')
    deadline = time.monotonic() + int(request.get('TimeoutMinutes', 5)) * 60
    def status(text):
        text = redact(text, key)
        log = folder / 'activity.log'
        previous = log.read_text(errors='replace') if log.exists() else ''
        log.write_text((previous + text + '\n')[-65536:])
        write_json(folder / 'status.json', {'message': text[-1000:]})
    def check():
        if (folder / 'cancel').exists():
            raise ValueError('Cancelled.')
        if time.monotonic() >= deadline:
            raise ValueError('VM processing timed out.')
    result = {'Success': False, 'Message': 'VM processing stopped. Backups remain available.'}
    try:
        status('Preparing the Steam VM CLI…')
        global_lock = JOBS.parent / 'cli.lock'
        with global_lock.open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('Another SteamAutoCrack CLI job is running in this VM.')
            if request.get('Setup'):
                from install_cli import install
                install(check, status)
                result = {'Success': True, 'Message': 'Windows CLI is ready in the Steam VM.'}
            else:
                root = game_path(request.get('GuestPath'))
                if (root / LOCK).is_symlink():
                    raise ValueError('The game lock cannot be a symlink.')
                with (root / LOCK).open('a') as game_lock:
                    try:
                        fcntl.flock(game_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        raise ValueError('Another operation is already modifying this game.')
                    check()
                    if request.get('Restore'):
                        restore(root, check, status)
                        result = {'Success': True, 'Message': 'Original game files restored.'}
                    else:
                        appid = str(request.get('AppId', ''))
                        if not appid.isascii() or not appid.isdigit() or not 0 < int(appid) <= 4294967295:
                            raise ValueError('Invalid Steam App ID.')
                        configuration(request)
                        if not (CLI / 'SteamAutoCrack.CLI.exe').is_file():
                            raise ValueError('Set up the VM CLI in SteamAutoCrack settings first.')
                        status('Reading the installed-build vanilla manifest…')
                        baseline = installed_baseline(root, appid)
                        previous = root / BACKUP / 'manifest.json'
                        if previous.is_file():
                            saved_app = json.loads(previous.read_text()).get('AppId')
                            if saved_app is not None and str(saved_app) != appid:
                                raise ValueError('The previous backup belongs to a different Steam game. Restore it separately first.')
                            status('Restoring the previous installation before running SteamAutoCrack…')
                            restore(root, check, status)
                        snapshot(root, appid, check, status, vanilla=baseline, unpack=bool(request.get('Unpack', True)))
                        try:
                            collect_cli_backups(root, 'existing', check, status)
                            collect_existing_settings(root, check, status)
                            status('Running Windows SteamAutoCrack CLI…')
                            with protect_cli_backups(root):
                                run_cli(root, request, folder, check, status)
                            filter_game_info(root, request)
                            verify_applied(root, request, status)
                            collect_cli_backups(root, 'generated', check, status)
                            finalize(root)
                        except Exception:
                            status('Restoring original files after an interrupted CLI job…')
                            finalize(root)
                            restore(root, status=status)
                            raise
                        result = {'Success': True, 'Message': 'VM CLI processing complete. Originals are backed up for restoration.'}
    except Exception as error:
        message = str(error) if isinstance(error, (ValueError, OSError)) else 'VM CLI processing failed. Check the VM and retained backups.'
        result = {'Success': False, 'Message': redact(message, key)[-2500:]}
    finally:
        (folder / 'cli-config.json').unlink(missing_ok=True)
        write_json(folder / 'result.json', result)


def dispatch(command, job=None):
    JOBS.mkdir(parents=True, exist_ok=True, mode=0o700)
    if command == 'start':
        source = sys.stdin.buffer.read(16385)
        if len(source) > 16384:
            raise ValueError('VM job request exceeds limit.')
        request = json.loads(source)
        if not isinstance(request, dict):
            raise ValueError('Invalid VM job request.')
        job = identity(request.pop('JobId', None))
        timeout = request.get('TimeoutMinutes', 5)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 120:
            raise ValueError('Timeout must be between 1 and 120 minutes.')
        folder = JOBS / job
        folder.mkdir(mode=0o700)
        write_json(folder / 'request.json', request)
        with (folder / 'worker.log').open('w') as log:
            try:
                child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'work', job],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
                write_json(folder / 'pid.json', {'pid': child.pid})
            except Exception:
                shutil.rmtree(folder)
                raise
        return {'JobId': job}
    folder = JOBS / identity(job)
    if not folder.is_dir() or folder.is_symlink():
        raise ValueError('VM job is unavailable.')
    if command == 'cancel':
        (folder / 'cancel').touch()
        return {'cancelled': True}
    if command == 'cleanup':
        if not (folder / 'result.json').is_file():
            raise ValueError('The VM job is still active.')
        shutil.rmtree(folder)
        return {'removed': True}
    if command == 'status':
        log = folder / 'activity.log'
        detail = {'log': log.read_text(errors='replace')[-65536:] if log.exists() else ''}
        result = folder / 'result.json'
        if result.is_file():
            return {'done': True, 'result': json.loads(result.read_text()), **detail}
        state = folder / 'status.json'
        pid = json.loads((folder / 'pid.json').read_text())['pid']
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return {'done': True, 'result': {'Success': False, 'Message': 'VM worker stopped. Backups were retained for recovery.'}}
        return {'done': False, 'message': json.loads(state.read_text())['message'] if state.exists() else 'Starting VM CLI…', **detail}
    raise ValueError('Invalid VM job command.')


def main():
    if os.getuid() == 0:
        os.execv('/usr/sbin/runuser', ['runuser', '-u', 'ubuntu', '--', 'env', 'HOME=/home/ubuntu',
                 '/usr/bin/python3', str(Path(__file__).resolve()), *sys.argv[1:]])
    os.umask(0o077)
    if len(sys.argv) == 3 and sys.argv[1] == 'work':
        work(sys.argv[2])
        return
    try:
        if len(sys.argv) not in (2, 3):
            raise ValueError('Invalid VM CLI arguments.')
        print(json.dumps({'ok': True, 'result': dispatch(*sys.argv[1:])}))
    except Exception as error:
        print(json.dumps({'ok': False, 'error': str(error) if isinstance(error, ValueError) else 'VM CLI control failed.'}))
        sys.exit(1)


if __name__ == '__main__':
    main()
