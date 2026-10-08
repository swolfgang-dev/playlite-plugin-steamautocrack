from pathlib import Path
import tempfile
import unittest
from source_support import ROOT
from autocrack_source.backup import BACKUP, digest, snapshot, finalize, restore, write_json


class BackupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / 'steam_api64.dll').write_bytes(b'original')
        (self.root / 'assets.dat').write_bytes(b'immutable asset')

    def test_cli_changes_and_new_files_restore_with_existing_settings_intact(self):
        settings = self.root / 'steam_settings'
        settings.mkdir()
        (settings / 'save.dat').write_bytes(b'original save')
        snapshot(self.root, '123')
        (self.root / 'steam_api64.dll').write_bytes(b'emulator')
        (self.root / 'steam_api64.dll.bak').write_bytes(b'original')
        (settings / 'new.ini').write_text('generated')
        (settings / 'save.dat').write_bytes(b'changed')
        (self.root / 'new-assets.dat').write_bytes(b'unrelated new asset')
        finalize(self.root)
        restore(self.root)
        self.assertEqual((self.root / 'steam_api64.dll').read_bytes(), b'original')
        self.assertEqual((settings / 'save.dat').read_bytes(), b'original save')
        self.assertFalse((settings / 'new.ini').exists())
        self.assertFalse((self.root / 'steam_api64.dll.bak').exists())
        self.assertEqual((self.root / 'assets.dat').read_bytes(), b'immutable asset')
        self.assertEqual((self.root / 'new-assets.dat').read_bytes(), b'unrelated new asset')
        self.assertFalse((self.root / BACKUP).exists())

    def test_changed_game_file_blocks_restore_before_any_changes(self):
        snapshot(self.root, '123')
        (self.root / 'steam_api64.dll').write_bytes(b'emulator')
        finalize(self.root)
        (self.root / 'steam_api64.dll').write_bytes(b'updated game')
        with self.assertRaisesRegex(ValueError, 'changed after'):
            restore(self.root)
        self.assertEqual((self.root / 'steam_api64.dll').read_bytes(), b'updated game')
        self.assertTrue((self.root / BACKUP).exists())

    def test_legacy_manifest_still_restores(self):
        saved = self.root / BACKUP
        saved.mkdir()
        (saved / 'original-0').write_bytes(b'original')
        (self.root / 'steam_api64.dll').write_bytes(b'emulator')
        write_json(saved / 'manifest.json', {'files': [dict(path='steam_api64.dll', backup='original-0',
            original=digest(saved / 'original-0'), applied=digest(self.root / 'steam_api64.dll'))]})
        restore(self.root)
        self.assertEqual((self.root / 'steam_api64.dll').read_bytes(), b'original')

    def test_symlink_game_content_is_rejected_before_backup_or_cli(self):
        (self.root / 'linked.dll').symlink_to(self.root / 'steam_api64.dll')
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            snapshot(self.root, '123')
        self.assertFalse((self.root / BACKUP).exists())

    def test_backup_traversal_is_rejected(self):
        saved = self.root / BACKUP
        saved.mkdir()
        write_json(saved / 'manifest.json', {'files': [dict(path='../outside', applied='bad')]})
        with self.assertRaisesRegex(ValueError, 'Invalid backup path'):
            restore(self.root)
