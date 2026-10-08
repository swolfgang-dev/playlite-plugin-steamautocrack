"""Verified original-file recovery, shared by legacy and VM CLI jobs."""
import hashlib
import json
import os
from pathlib import Path
import shutil

BACKUP = '.playlite-steamautocrack'
LOCK = '.playlite-steamautocrack.lock'


def write_json(path, value):
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream)
    temporary.chmod(0o600)
    temporary.replace(path)


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def checked_path(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise ValueError('Invalid backup path.')
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Backup paths must remain inside the game folder.')
    for component in (path, *path.parents):
        if component == root:
            break
        if component.is_symlink():
            raise ValueError('Backup paths cannot contain symlinks.')
    return path


def files(root):
    output = {}
    for folder, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name != BACKUP]
        for name in dirs + names:
            if (Path(folder) / name).is_symlink():
                raise ValueError('Game folders containing symlinks must be reviewed before CLI processing.')
        for name in names:
            if name == LOCK:
                continue
            path = Path(folder) / name
            if not path.is_file():
                raise ValueError('Game folders must contain only regular files and directories.')
            output[str(path.relative_to(root))] = path
    return output


def mutable_file(relative, path):
    return (path.suffix.lower() in ('.dll', '.exe', '.so', '.bak', '.unpacked') or
            'steam_settings' in Path(relative).parts or
            path.name.lower() in ('steam_interfaces.txt', 'local_save.txt', 'steamapicheckbypass.json'))


def snapshot(root, appid, check=lambda: None, status=lambda text: None):
    backup = root / BACKUP
    if backup.exists() or backup.is_symlink():
        raise ValueError('Restore the previous original-file backup before processing again.')
    existing = files(root)
    if not any(path.name.lower() in ('steam_api.dll', 'steam_api64.dll') for path in existing.values()):
        raise ValueError('No Windows Steam API DLL found in the shared game folder.')
    backup.mkdir(mode=0o700)
    entries = []
    try:
        for relative, path in existing.items():
            check()
            if not mutable_file(relative, path):
                continue
            status('Backing up ' + relative + '…')
            saved = 'original-' + str(len(entries))
            original = digest(path)
            shutil.copy2(path, backup / saved)
            if digest(backup / saved) != original or digest(path) != original:
                raise ValueError('Game files changed while preparing their backup.')
            entries.append(dict(path=relative, backup=saved, original=original, applied=original))
        write_json(backup / 'manifest.json', dict(AppId=appid, files=entries,
                   FileSet=list(existing), Backend='vm-cli', Status='Applying'))
    except Exception:
        shutil.rmtree(backup)
        raise


def finalize(root):
    backup = root / BACKUP
    manifest = json.loads((backup / 'manifest.json').read_text())
    current = files(root)
    for entry in manifest['files']:
        path = checked_path(root, entry['path'])
        entry['applied'] = digest(path) if path.is_file() else None
    for relative in current.keys() - set(manifest['FileSet']):
        if mutable_file(relative, current[relative]):
            manifest['files'].append(dict(path=relative, applied=digest(current[relative])))
    manifest['Status'] = 'Complete'
    write_json(backup / 'manifest.json', manifest)


def restore(root, check=lambda: None, status=lambda text: None):
    backup = root / BACKUP
    if backup.is_symlink():
        raise ValueError('Backup directory cannot be a symlink.')
    manifest = json.loads((backup / 'manifest.json').read_text())
    entries = manifest['files']
    # Validate every entry before changing any game file. This also supports
    # the manifests produced by the former native engine.
    for entry in entries:
        check()
        path = checked_path(root, entry['path'])
        if path.exists() and digest(path) not in (entry.get('original'), entry.get('applied')):
            raise ValueError(entry['path'] + ' changed after processing. Move it aside before restoring.')
        if entry.get('backup'):
            original = checked_path(backup, entry['backup'])
            if not original.is_file() or digest(original) != entry['original']:
                raise ValueError('Original backup verification failed. Nothing was restored.')
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
