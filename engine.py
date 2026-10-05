"""Native SteamAutoCrack processing; game binaries are inspected, never executed."""
import configparser
import fcntl
import hashlib
import io
import json
import os
import re
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from playlite.storage import atomic_json

BACKUP = '.playlite-steamautocrack'
API_NAMES = {'steam_api.dll', 'steam_api64.dll', 'libsteam_api.so'}


def digest(path):
    with path.open('rb') as stream:
        value = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
        return value.hexdigest()


def architecture(path):
    with path.open('rb') as stream:
        header = stream.read(64)
        if header.startswith(b'\x7fELF') and len(header) > 4:
            machine = struct.unpack_from('<H' if header[5] == 1 else '>H', header, 18)[0] if len(header) >= 20 else 0
            bits = 32 if header[4] == 1 and machine == 3 else 64 if header[4] == 2 and machine == 62 else 0
            return ('linux', bits, False)
        if not header.startswith(b'MZ') or len(header) < 64:
            raise ValueError(f'Not a PE or ELF binary: {path.name}')
        offset = struct.unpack_from('<I', header, 60)[0]
        stream.seek(offset)
        pe = stream.read(24)
        if len(pe) != 24 or pe[:4] != b'PE\0\0':
            raise ValueError(f'Invalid PE header: {path.name}')
        machine, sections = struct.unpack_from('<HH', pe, 4)
        bits = {0x14c: 32, 0x8664: 64}.get(machine, 0)
        optional = struct.unpack_from('<H', pe, 20)[0]
        stream.seek(offset + 24 + optional)
        bind = any(stream.read(40)[:8].rstrip(b'\0') == b'.bind' for _ in range(sections))
        return ('windows', bits, bind)


def candidates(root):
    apis, packed = [], []
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name != BACKUP and not (Path(folder) / name).is_symlink()]
        for name in files:
            path = Path(folder) / name
            if name.lower() in API_NAMES:
                if path.is_symlink():
                    raise ValueError(f'Steam API symlinks are not supported: {path}')
                apis.append(path)
            elif name.lower().endswith('.exe') and not path.is_symlink():
                try:
                    if architecture(path)[2]:
                        packed.append(path)
                except (ValueError, OSError):
                    continue
    return apis, packed


def emulator_for(path, root):
    platform, bits, _ = architecture(path)
    if not bits:
        raise ValueError(f'Unsupported binary architecture: {path.name}')
    matches = []
    for candidate in Path(root).rglob('*'):
        if candidate.name.lower() != path.name.lower() or candidate.is_symlink() or not candidate.is_file() or 'experimental' in candidate.parts:
            continue
        try:
            if architecture(candidate)[:2] == (platform, bits):
                matches.append(candidate)
        except ValueError:
            continue
    if not matches:
        raise ValueError(f'No {platform} {bits}-bit emulator for {path.name}. Install tools or choose an emulator folder in settings.')
    regular = [candidate for candidate in matches if 'regular' in candidate.parts]
    return sorted(regular or matches)[0]


def fetch_json(url, timeout=30):
    request = urllib.request.Request(url, headers={'User-Agent': 'Playlite-SteamAutoCrack/2.0'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def config_files(request, check):
    appid = str(request['AppId'])
    user = configparser.ConfigParser(interpolation=None)
    user['user::general'] = dict(account_name=request.get('GoldbergUsername', 'Player'),
                                 language=request.get('Language', 'english'))
    output = io.StringIO()
    user.write(output)
    files = {'steam_appid.txt': appid + '\n', 'configs.user.ini': output.getvalue()}
    if request.get('GenerateInfo'):
        key = request.get('ApiKey', '')
        check()
        try:
            schema = fetch_json('https://api.steampowered.com/ISteamUserStats/GetSchemaForGame/v2/?' +
                                urllib.parse.urlencode(dict(key=key, appid=appid, l=request.get('Language', 'english'))))
            store = fetch_json('https://store.steampowered.com/api/appdetails?' + urllib.parse.urlencode(dict(appids=appid)))
        except Exception:
            # Do not expose a request URL containing the API key.
            raise ValueError('Steam game-info request failed. Check the API key and network connection.') from None
        stats = schema.get('game', {}).get('availableGameStats', {})
        files['achievements.json'] = json.dumps(stats.get('achievements', []), indent=2, ensure_ascii=False)
        files['stats.json'] = json.dumps([dict(name=item['name'], type='int', default=str(item.get('defaultvalue', 0)),
                                               **{'global': '0'}) for item in stats.get('stats', [])], indent=2)
        info = store.get(appid, {}).get('data', {})
        main = configparser.ConfigParser(interpolation=None)
        main['app::general'] = dict(is_beta_branch='0')
        # Record known DLC only; unknown DLC are not implicitly unlocked.
        if info.get('dlc'):
            main['app::dlcs'] = {'unlock_all': '0', **{str(identity): f'DLC {identity}' for identity in info['dlc']}}
        output = io.StringIO()
        main.write(output)
        files['configs.app.ini'] = output.getvalue()
        files['game_info.json'] = json.dumps(dict(appid=appid, name=info.get('name', '')), indent=2)
    return {name: value.encode('utf-8') for name, value in files.items()}


def checked_path(root, relative):
    path = root / relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('Invalid backup path.')
    if root not in path.resolve().parents or any(parent.is_symlink() for parent in [path, *path.parents] if parent != root):
        raise ValueError('Backup paths must remain inside the installation folder without symlinks.')
    return path


def restore(root, check, status):
    backup = root / BACKUP
    if backup.is_symlink():
        raise ValueError('Backup directory cannot be a symlink.')
    manifest = json.loads((backup / 'manifest.json').read_text())
    entries = manifest['files']
    for entry in entries:
        check()
        path = checked_path(root, entry['path'])
        if path.exists() and digest(path) not in (entry.get('original'), entry['applied']):
            raise ValueError(f'{entry["path"]} changed after processing. Keep it or move it aside before restoring.')
        if entry.get('backup'):
            original = checked_path(backup, entry['backup'])
            if not original.is_file() or digest(original) != entry['original']:
                raise ValueError('Original backup verification failed. Nothing was restored.')
    # Once restoring starts, finish restoring the verified originals.
    for entry in entries:
        path = checked_path(root, entry['path'])
        if entry.get('backup'):
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + '.playlite-restore-tmp')
            try:
                shutil.copy2(backup / entry['backup'], temporary)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        else:
            path.unlink(missing_ok=True)
    shutil.rmtree(backup)
    status('Original game files restored.')


def process(request, cancel=lambda: False, status=lambda text: None):
    root = Path(request['InstallDirectory']).expanduser()
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError('Select an existing absolute installation folder.')
    root = root.resolve()
    timeout = int(request.get('TimeoutMinutes', 5)) * 60
    if not 60 <= timeout <= 7200:
        raise ValueError('Timeout must be between 1 and 120 minutes.')
    deadline = time.monotonic() + timeout
    def check():
        if cancel():
            raise ValueError('Cancelled. Original game files were kept or restored.')
        if time.monotonic() > deadline:
            raise ValueError('Processing timed out. Original game files were kept or restored.')
    with (root / '.playlite-steamautocrack.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another operation is already modifying this game.')
        if request.get('Restore'):
            restore(root, check, status)
            return
        appid = str(request.get('AppId') or '')
        if not appid.isascii() or not appid.isdigit() or not 0 < int(appid) <= 4294967295:
            raise ValueError('Enter a valid Steam App ID.')
        username = request.get('GoldbergUsername', 'Player')
        if not username.strip() or any(value in username for value in ('\n', '\r', '\0')):
            raise ValueError('Enter a valid emulator username.')
        if request.get('GenerateInfo') and not re.fullmatch(r'[a-fA-F0-9]{32}', request.get('ApiKey', '')):
            raise ValueError('Game-info generation requires a valid Steam Web API key.')
        backup = root / BACKUP
        if backup.exists() or backup.is_symlink():
            raise ValueError('A previous backup exists. Restore original files before processing again.')
        apis, packed = candidates(root)
        if not apis:
            raise ValueError('No Steam API library found in the installation folder.')
        replacements = {path: emulator_for(path, request['EmulatorDirectory']) for path in apis} if request.get('ApplyEmulator', True) else {}
        if packed and request.get('Unpack') and not Path(request.get('UnpackerPath') or '').is_file():
            raise ValueError('Install the native unpacker before processing SteamStub executables.')
        status('Generating emulator configuration…')
        files = config_files(request, check)
        check()
        with tempfile.TemporaryDirectory(prefix='playlite-autocrack-') as directory:
            stage = Path(directory)
            planned = {}
            for path, emulator in replacements.items():
                check()
                planned[str(path.relative_to(root))] = emulator
            if request.get('GenerateConfig', True):
                for folder in {path.parent for path in apis}:
                    for name, content in files.items():
                        generated = stage / str(len(planned))
                        generated.write_bytes(content)
                        planned[str((folder / 'steam_settings' / name).relative_to(root))] = generated
            if request.get('Unpack'):
                for index, executable in enumerate(packed):
                    check()
                    status(f'Unpacking {executable.name}…')
                    staged = stage / f'unpack-{index}.exe'
                    shutil.copy2(executable, staged)
                    process = subprocess.Popen([request['UnpackerPath'], str(staged)], stdout=subprocess.DEVNULL,
                                               stderr=subprocess.DEVNULL, start_new_session=True)
                    try:
                        while process.poll() is None:
                            check()
                            time.sleep(0.05)
                        unpacked = Path(str(staged) + '.unpacked.exe')
                        if process.returncode or not unpacked.is_file() or architecture(unpacked)[:2] != architecture(executable)[:2]:
                            raise ValueError(f'Native unpacking failed for {executable.name}. Game files were kept.')
                        planned[str(executable.relative_to(root))] = unpacked
                    finally:
                        if process.poll() is None:
                            import signal
                            os.killpg(process.pid, signal.SIGTERM)
                            try:
                                process.wait(timeout=3)
                            except subprocess.TimeoutExpired:
                                os.killpg(process.pid, signal.SIGKILL)
                                process.wait()
            if not planned:
                raise ValueError('Select at least one processing step.')
            entries = []
            backup.mkdir(mode=0o700)
            try:
                for index, (relative, replacement) in enumerate(planned.items()):
                    check()
                    path = checked_path(root, relative)
                    entry = dict(path=relative, applied=digest(replacement))
                    if path.exists():
                        entry.update(backup=f'original-{index}', original=digest(path))
                        shutil.copy2(path, backup / entry['backup'])
                        if digest(backup / entry['backup']) != entry['original']:
                            raise ValueError('Backup verification failed.')
                    entries.append(entry)
                atomic_json(backup / 'manifest.json', dict(AppId=appid, files=entries, Status='Applying'))
                status('Applying emulator and configuration…')
                for entry in entries:
                    check()
                    path = checked_path(root, entry['path'])
                    if path.exists() and digest(path) != entry.get('original'):
                        raise ValueError('Game files changed during processing.')
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = path.with_name(path.name + '.playlite-tmp')
                    try:
                        shutil.copy2(planned[entry['path']], temporary)
                        temporary.replace(path)
                    finally:
                        temporary.unlink(missing_ok=True)
                atomic_json(backup / 'manifest.json', dict(AppId=appid, files=entries, Status='Complete'))
            except Exception:
                if (backup / 'manifest.json').exists():
                    restore(root, lambda: None, status)
                elif backup.exists():
                    shutil.rmtree(backup)
                raise
        status('Processing complete. Original files are backed up and can be restored.')
