from plugin_test_support import require_plugin
require_plugin('GameArchiver')
require_plugin('SteamAutoCrack')
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QCheckBox, QLineEdit
from playlite_plugins.steamautocrack.engine import process, architecture, BACKUP, config_files
from playlite_plugins.steamautocrack.runner import run
from playlite.providers import discover_plugins

APP = QApplication.instance() or QApplication([])


def pe(bits=64, bind=False, payload=b'original'):
    data = bytearray(128)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 60, 64)
    data[64:68] = b'PE\0\0'
    struct.pack_into('<HH', data, 68, 0x8664 if bits == 64 else 0x14c, 1 if bind else 0)
    if bind:
        data[88:96] = b'.bind\0\0\0'
    return bytes(data) + payload


class NativeProcessingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.game = self.root / 'game'
        self.game.mkdir()
        self.api = self.game / 'bin/steam_api64.dll'
        self.api.parent.mkdir()
        self.api.write_bytes(pe())
        self.emulator = self.root / 'emulator/regular/x64'
        self.emulator.mkdir(parents=True)
        (self.emulator / self.api.name).write_bytes(pe(payload=b'emulator'))
        self.request = dict(InstallDirectory=str(self.game), AppId='123', GenerateInfo=False,
                            EmulatorDirectory=str(self.root / 'emulator'), Unpack=False,
                            GoldbergUsername='Player')

    def tearDown(self):
        self.directory.cleanup()

    def test_apply_and_restore_preserves_originals_and_existing_settings(self):
        settings = self.api.parent / 'steam_settings'
        settings.mkdir()
        (settings / 'configs.user.ini').write_text('original user settings')
        (settings / 'save.dat').write_bytes(b'user save')
        original = self.api.read_bytes()
        process(self.request)
        self.assertEqual(self.api.read_bytes(), (self.emulator / self.api.name).read_bytes())
        self.assertIn('account_name = Player', (settings / 'configs.user.ini').read_text())
        manifest = json.loads((self.game / BACKUP / 'manifest.json').read_text())
        self.assertEqual(manifest['Status'], 'Complete')
        self.assertEqual((settings / 'save.dat').read_bytes(), b'user save')
        with self.assertRaisesRegex(ValueError, 'previous backup'):
            process(self.request)
        process(dict(InstallDirectory=str(self.game), Restore=True))
        self.assertEqual(self.api.read_bytes(), original)
        self.assertEqual((settings / 'configs.user.ini').read_text(), 'original user settings')
        self.assertFalse((settings / 'steam_appid.txt').exists())
        self.assertFalse((self.game / BACKUP).exists())
        self.assertEqual((settings / 'save.dat').read_bytes(), b'user save')

    def test_cancellation_after_first_write_rolls_back(self):
        original = self.api.read_bytes()
        def cancel():
            return self.api.read_bytes() != original
        with self.assertRaisesRegex(ValueError, 'Cancelled'):
            process(self.request, cancel=cancel)
        self.assertEqual(self.api.read_bytes(), original)
        self.assertFalse((self.game / BACKUP).exists())

    def test_restore_rejects_user_changes_and_corrupt_backups(self):
        process(self.request)
        self.api.write_bytes(b'user modified')
        with self.assertRaisesRegex(ValueError, 'changed after processing'):
            process(dict(InstallDirectory=str(self.game), Restore=True))
        self.assertEqual(self.api.read_bytes(), b'user modified')
        self.api.write_bytes((self.emulator / self.api.name).read_bytes())
        (self.game / BACKUP / 'original-0').write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'verification failed'):
            process(dict(InstallDirectory=str(self.game), Restore=True))

    def test_failed_restore_copy_preserves_applied_file_and_can_retry(self):
        import shutil
        original = self.api.read_bytes()
        process(self.request)
        applied = self.api.read_bytes()
        copy = shutil.copy2
        def interrupted(source, destination, *args, **kwargs):
            if Path(source).name == 'original-0':
                Path(destination).write_bytes(b'partial copy')
                raise OSError('simulated disk error')
            return copy(source, destination, *args, **kwargs)
        with patch('playlite_plugins.steamautocrack.engine.shutil.copy2', side_effect=interrupted):
            with self.assertRaises(OSError):
                process(dict(InstallDirectory=str(self.game), Restore=True))
        self.assertEqual(self.api.read_bytes(), applied)
        self.assertTrue((self.game / BACKUP / 'manifest.json').is_file())
        self.assertFalse(list(self.game.rglob('*.playlite-restore-tmp')))
        process(dict(InstallDirectory=str(self.game), Restore=True))
        self.assertEqual(self.api.read_bytes(), original)

    def test_mismatched_architecture_symlinks_and_missing_unpacker_keep_files(self):
        (self.emulator / self.api.name).write_bytes(pe(bits=32))
        with self.assertRaisesRegex(ValueError, '64-bit emulator'):
            process(self.request)
        self.assertFalse((self.game / BACKUP).exists())
        (self.emulator / self.api.name).write_bytes(pe())
        (self.game / 'game.exe').write_bytes(pe(bind=True))
        with self.assertRaisesRegex(ValueError, 'native unpacker'):
            process(dict(self.request, Unpack=True))
        settings = self.api.parent / 'steam_settings'
        settings.symlink_to(self.root / 'outside', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            process(self.request)
        self.assertFalse((self.root / 'outside').exists())

    def test_native_linux_api_library(self):
        self.api.unlink()
        native = self.api.parent / 'libsteam_api.so'
        header = bytearray(64)
        header[:6] = b'\x7fELF\x02\x01'
        struct.pack_into('<H', header, 18, 62)
        native.write_bytes(header)
        replacement = self.emulator / native.name
        replacement.write_bytes(bytes(header) + b'emulator')
        process(self.request)
        self.assertEqual(native.read_bytes(), replacement.read_bytes())
        process(dict(InstallDirectory=str(self.game), Restore=True))
        self.assertEqual(native.read_bytes(), bytes(header))

    def test_archiver_cannot_move_a_game_being_processed(self):
        import fcntl
        from playlite_plugins.gamearchiver.transfer import transfer_game
        data = self.root / 'library'
        data.mkdir()
        game = dict(Id='a', Name='Example', InstallDirectory=str(self.game))
        (data / 'library.json').write_text(json.dumps([game]))
        with (self.game / '.playlite-steamautocrack.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, 'modifying this game'):
                transfer_game(data, game, self.root / 'archive')
        self.assertTrue(self.api.exists())
        self.assertFalse((self.root / 'archive').exists())

    def test_native_unpacking_runs_only_staged_copy(self):
        packed = self.game / 'game.exe'
        packed.write_bytes(pe(bind=True))
        unpacker = self.root / 'unpacker'
        unpacker.write_text('#!/usr/bin/python3\nimport sys,pathlib\np=pathlib.Path(sys.argv[1])\nb=bytearray(p.read_bytes()); b[88:96]=bytes(8); pathlib.Path(str(p)+".unpacked.exe").write_bytes(b)\n')
        unpacker.chmod(0o755)
        process(dict(self.request, Unpack=True, UnpackerPath=str(unpacker)))
        self.assertFalse(architecture(packed)[2])
        process(dict(InstallDirectory=str(self.game), Restore=True))
        self.assertTrue(architecture(packed)[2])

    def test_game_info_errors_do_not_expose_api_key(self):
        key = 'a' * 32
        with patch('playlite_plugins.steamautocrack.engine.fetch_json', side_effect=RuntimeError('https://api/?key=' + key)):
            with self.assertRaises(ValueError) as error:
                process(dict(self.request, GenerateInfo=True, ApiKey=key))
        self.assertNotIn(key, str(error.exception))
        self.assertFalse((self.game / BACKUP).exists())

    def test_game_schema_generates_compatible_configuration(self):
        schema = dict(game=dict(availableGameStats=dict(achievements=[dict(name='WIN', displayName='Win')],
                                                       stats=[dict(name='wins', defaultvalue=3)])))
        store = {'123': dict(data=dict(name='Example', dlc=[456]))}
        with patch('playlite_plugins.steamautocrack.engine.fetch_json', side_effect=[schema, store]):
            files = config_files(dict(self.request, GenerateInfo=True, ApiKey='a' * 32), lambda: None)
        self.assertEqual(json.loads(files['achievements.json'])[0]['name'], 'WIN')
        self.assertEqual(json.loads(files['stats.json'])[0]['default'], '3')
        self.assertIn(b'456 = DLC 456', files['configs.app.ini'])

    def test_native_runner_removes_credentials_before_processing(self):
        job = self.root / 'job'
        job.mkdir()
        request = dict(self.request, ApiKey='a' * 32)
        (job / 'request.json').write_text(json.dumps(request))
        run(job)
        self.assertNotIn('ApiKey', json.loads((job / 'request.json').read_text()))
        self.assertIn('complete', (job / 'status.txt').read_text())

    def test_plugin_requests_need_no_windows_cli_and_no_hardcoded_game_root(self):
        plugin = discover_plugins()['SteamAutoCrack']
        settings = QSettings(str(self.root / 'settings.ini'), QSettings.Format.IniFormat)
        settings.setValue('generateInfo', False)
        settings.setValue('emulatorDirectory', str(self.root / 'emulator'))
        plugin.settings = lambda: settings
        self.assertFalse(plugin.default_for('Manual'))
        request = plugin.request(dict(InstallDirectory=str(self.game), MetadataIds={'SteamMetadata': '123'}))
        self.assertEqual(request['AppId'], '123')
        self.assertEqual(request['EmulatorDirectory'], str(self.root / 'emulator'))
