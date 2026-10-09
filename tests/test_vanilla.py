import importlib.util
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from source_support import ROOT

spec = importlib.util.spec_from_file_location('vanilla_test', ROOT / 'guest/vanilla.py')
vanilla = importlib.util.module_from_spec(spec); spec.loader.exec_module(vanilla)


def integer(value):
    output = bytearray()
    while value > 127:
        output.append((value & 127) | 128); value >>= 7
    output.append(value)
    return bytes(output)


def field(number, value):
    if isinstance(value, int):
        return integer(number << 3) + integer(value)
    return integer((number << 3) | 2) + integer(len(value)) + value


def manifest(name=b'steam_api64.dll', encrypted=0, depot=1, gid=2, content=b'original'):
    mapping = field(1, name) + field(2, len(content)) + field(3, 0) + field(5, hashlib.sha1(content).digest())
    payload = field(1, mapping)
    metadata = field(1, depot) + field(2, gid) + field(4, encrypted)
    return (struct.pack('<II', 0x71F617D0, len(payload)) + payload +
            struct.pack('<II', 0x1F4812BE, len(metadata)) + metadata + struct.pack('<I', 0x32C415AB))


class VanillaTests(unittest.TestCase):
    def test_installed_build_uses_matching_cached_depot_without_reading_game_assets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); apps = root / 'steamapps'; apps.mkdir()
            game = apps / 'common/Game'; game.mkdir(parents=True)
            (root / 'depotcache').mkdir()
            (root / 'depotcache/1_2.manifest').write_bytes(manifest())
            (apps / 'appmanifest_123.acf').write_text('"AppState" { "appid" "123" "StateFlags" "4" "installdir" "Game" "buildid" "99" "InstalledDepots" { "1" { "manifest" "2" } } }')
            baseline = vanilla.installed_baseline(game, '123', root)
            self.assertEqual(baseline['BuildId'], '99')
            self.assertEqual(baseline['Depots'], {'1': '2'})
            self.assertEqual(baseline['Files']['steam_api64.dll']['sha1'], hashlib.sha1(b'original').hexdigest())
            with self.assertRaisesRegex(ValueError, 'selected folder'):
                vanilla.installed_baseline(root, '123', root)
            (root / 'depotcache/1_2.manifest').unlink()
            with self.assertRaisesRegex(ValueError, 'manifests are unavailable'):
                vanilla.installed_baseline(game, '123', root)

    def test_overlapping_depots_select_verified_original_and_reject_unknown_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); apps = root / 'steamapps'; apps.mkdir()
            game = apps / 'common/Game'; game.mkdir(parents=True)
            (root / 'depotcache').mkdir()
            (root / 'depotcache/1_2.manifest').write_bytes(manifest())
            (root / 'depotcache/3_4.manifest').write_bytes(manifest(depot=3, gid=4, content=b'overlay'))
            (apps / 'appmanifest_123.acf').write_text('"AppState" { "appid" "123" "StateFlags" "4" "installdir" "Game" "InstalledDepots" { "1" { "manifest" "2" } "3" { "manifest" "4" } } }')
            dll = game / 'steam_api64.dll'; dll.write_bytes(b'overlay')
            baseline = vanilla.installed_baseline(game, '123', root)
            self.assertEqual(baseline['Files']['steam_api64.dll']['sha1'], hashlib.sha1(b'overlay').hexdigest())
            dll.write_bytes(b'emulator')
            with self.assertRaisesRegex(ValueError, 'does not match a vanilla original'):
                vanilla.installed_baseline(game, '123', root)
            import json
            backup = game / '.playlite-steamautocrack'; backup.mkdir()
            (backup / 'manifest.json').write_text(json.dumps({'Vanilla': baseline}))
            self.assertEqual(vanilla.installed_baseline(game, '123', root)['Files'], baseline['Files'])
            baseline['Files']['steam_api64.dll']['sha1'] = '0' * 40
            (backup / 'manifest.json').write_text(json.dumps({'Vanilla': baseline}))
            with self.assertRaisesRegex(ValueError, 'does not match a vanilla original'):
                vanilla.installed_baseline(game, '123', root)

    def test_encrypted_wrong_build_and_unsafe_paths_fail_closed(self):
        for data in [manifest(encrypted=1), manifest(gid=3), manifest(name=b'../escape'), manifest(name=b'C:\\escape'), manifest()[:-4]]:
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    vanilla.depot_files(data, '1', '2')

    def test_truncated_and_invalid_protobuf_fail_closed(self):
        for data in [b'\x0a\x05x', b'\x0f', b'\0', b'\x80' * 12]:
            with self.assertRaises(ValueError):
                list(vanilla.fields(data))
