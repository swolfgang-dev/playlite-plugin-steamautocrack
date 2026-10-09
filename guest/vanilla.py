"""Read installed-build file hashes from Steam's local depot manifests.

Wire fields: https://github.com/SteamTracking/Protobufs/blob/master/steam/content_manifest.proto
Only target files are hashed by the backup layer; game assets are not read.
"""
import re
import struct
from pathlib import Path, PurePosixPath


def fields(data):
    position = 0
    def varint():
        nonlocal position
        value = 0
        for shift in range(0, 70, 7):
            if position >= len(data):
                raise ValueError('Truncated Steam depot manifest.')
            byte = data[position]; position += 1
            value |= (byte & 127) << shift
            if not byte & 128:
                return value
        raise ValueError('Invalid manifest integer.')
    while position < len(data):
        tag = varint(); number, wire = tag >> 3, tag & 7
        if not number:
            raise ValueError('Invalid manifest field.')
        if wire == 0:
            value = varint()
        elif wire in (1, 2, 5):
            length = varint() if wire == 2 else (8 if wire == 1 else 4)
            if length > len(data) - position:
                raise ValueError('Truncated Steam depot manifest.')
            value = data[position:position + length]; position += length
        else:
            raise ValueError('Unsupported manifest field.')
        yield number, value


def depot_files(data, depot, manifest):
    sections = {}; position = 0; ended = False
    while position + 4 <= len(data):
        magic, = struct.unpack_from('<I', data, position); position += 4
        if magic == 0x32C415AB:
            ended = True; break
        if magic not in (0x71F617D0, 0x1F4812BE, 0x1B81B817) or magic in sections or position + 4 > len(data):
            raise ValueError('Invalid Steam depot manifest framing.')
        length, = struct.unpack_from('<I', data, position); position += 4
        if length > len(data) - position:
            raise ValueError('Truncated Steam depot manifest.')
        sections[magic] = data[position:position + length]; position += length
    if not ended or position != len(data) or not {0x71F617D0, 0x1F4812BE}.issubset(sections):
        raise ValueError('Incomplete Steam depot manifest.')
    metadata = dict(fields(sections[0x1F4812BE]))
    if metadata.get(1) != int(depot) or metadata.get(2) != int(manifest):
        raise ValueError('Steam depot manifest identity mismatch.')
    if metadata.get(4, 0):
        raise ValueError('Cached Steam manifest filenames are encrypted. Verify the game in Steam first.')
    result = {}
    for number, mapping in fields(sections[0x71F617D0]):
        if number != 1:
            continue
        row = dict(fields(mapping))
        name = row.get(1, b'').decode('utf-8').rstrip('\0').replace('\\', '/')
        path = PurePosixPath(name)
        if not name or path.is_absolute() or '..' in path.parts or ':' in name or '\0' in name:
            raise ValueError('Unsafe path in Steam depot manifest.')
        if row.get(3, 0) & 64:  # Directory
            continue
        if row.get(7):
            raise ValueError('Steam symlink entries require review.')
        content_hash = row.get(5, b'')
        if not isinstance(content_hash, bytes) or len(content_hash) != 20 or not isinstance(row.get(2, 0), int):
            raise ValueError('Missing official file hash in Steam depot manifest.')
        key = path.as_posix().casefold()
        value = {'path': path.as_posix(), 'sha1': content_hash.hex(), 'size': row.get(2, 0)}
        if key in result and result[key] != value:
            raise ValueError('Conflicting official file entries.')
        result[key] = value
    return result


def parse_vdf(source):
    if len(source) > 1024 * 1024:
        raise ValueError('Steam installation record exceeds limit.')
    tokens = re.findall(r'"((?:\\.|[^"\\])*)"|([{}])', source); position = 0
    def block(depth=0):
        nonlocal position
        if depth > 16:
            raise ValueError('Invalid Steam installation record.')
        output = {}
        while position < len(tokens):
            key, brace = tokens[position]; position += 1
            if brace == '}' and depth:
                return output
            if brace or position >= len(tokens):
                raise ValueError('Invalid Steam installation record.')
            value, brace = tokens[position]; position += 1
            if brace == '{':
                value = block(depth + 1)
            elif brace:
                raise ValueError('Invalid Steam installation record.')
            output[key] = value
        if depth:
            raise ValueError('Incomplete Steam installation record.')
        return output
    return block()


def installed_baseline(root, appid, library=None):
    library = Path(library or Path.home() / '.steam/debian-installation')
    try:
        state = parse_vdf((library / 'steamapps' / ('appmanifest_' + str(appid) + '.acf')).read_text())['AppState']
        if state.get('appid') != str(appid) or state.get('StateFlags') != '4':
            raise ValueError('Finish installing or verifying this game in Steam first.')
        installed = library / 'steamapps/common' / state.get('installdir', '')
        if installed.resolve() != root.resolve():
            raise ValueError('The selected folder does not match this Steam installation.')
        depots = state.get('InstalledDepots', {})
        if not depots:
            raise ValueError('No installed Steam depot manifests are available.')
        result = {}; identities = {}; overlapping = {}
        for depot, info in depots.items():
            manifest = info.get('manifest', '')
            if not depot.isdecimal() or not manifest.isdecimal():
                raise ValueError('Invalid installed Steam depot identity.')
            path = library / 'depotcache' / (depot + '_' + manifest + '.manifest')
            if path.stat().st_size > 128 * 1024 * 1024:
                raise ValueError('Steam depot manifest exceeds limit.')
            for key, value in depot_files(path.read_bytes(), depot, manifest).items():
                if key in result and result[key] != value:
                    choices = overlapping.setdefault(key, [result[key]])
                    if value not in choices:
                        choices.append(value)
                else:
                    result[key] = value
            identities[depot] = manifest
        # DLC depots can legitimately overlay a file from the base depot.
        # Resolve only those paths against official alternatives, rather than
        # rejecting the whole installation or hashing unrelated game assets.
        import hashlib
        import json
        saved = {}
        previous = root / '.playlite-steamautocrack/manifest.json'
        if overlapping and previous.is_file():
            saved = json.loads(previous.read_text()).get('Vanilla', {}).get('Files', {})
        for key, choices in overlapping.items():
            path = root / choices[0]['path']
            selected = None
            if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root.resolve()):
                with path.open('rb') as stream:
                    sha1 = hashlib.file_digest(stream, 'sha1').hexdigest()
                selected = next((row for row in choices if row['size'] == path.stat().st_size and
                                 row['sha1'] == sha1), None)
            # A prior managed installation may currently contain an emulator.
            # Its saved baseline must still be an official depot alternative;
            # snapshot verifies the restored bytes before any new processing.
            if selected is None and saved.get(key) in choices:
                selected = saved[key]
            if selected is None:
                raise ValueError('Overlapping depot file does not match a vanilla original: ' + choices[0]['path'] +
                                 '. Restore or verify the game in Steam first.')
            result[key] = selected
        return {'AppId': str(appid), 'BuildId': state.get('buildid'), 'Depots': identities, 'Files': result}
    except (OSError, KeyError) as error:
        raise ValueError('Installed-build Steam manifests are unavailable. Install or verify the game in this Steam VM first. No new backup was created.') from error
