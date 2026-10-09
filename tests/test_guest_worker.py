import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from source_support import ROOT

backup_spec = importlib.util.spec_from_file_location('backup', ROOT.parent / 'playlite-plugin-crack-tools/backup.py')
backup_module = importlib.util.module_from_spec(backup_spec)
sys.modules['backup'] = backup_module
backup_spec.loader.exec_module(backup_module)

spec = importlib.util.spec_from_file_location('autocrack_guest_test', ROOT / 'guest/worker.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class GuestWorkerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.shared = self.root / 'shared'
        self.game = self.shared / 'game'
        self.game.mkdir(parents=True)
        (self.game / 'steam_api64.dll').write_bytes(b'original')
        self.cli = self.root / 'cli'
        self.cli.mkdir()
        (self.cli / 'SteamAutoCrack.CLI.exe').touch()
        emulator = self.cli / 'Goldberg/regular/x64/steam_api64.dll'
        emulator.parent.mkdir(parents=True)
        emulator.write_bytes(b'emulator')
        self.jobs = self.root / 'jobs'
        self.jobs.mkdir()
        for name, value in [('SHARED', self.shared), ('CLI', self.cli), ('JOBS', self.jobs)]:
            patched = patch.object(worker, name, value)
            patched.start()
            self.addCleanup(patched.stop)
        mounted = patch.object(worker.os.path, 'ismount', return_value=True)
        mounted.start()
        self.addCleanup(mounted.stop)
        baseline = {'AppId': '123', 'BuildId': '1', 'Depots': {'1': '2'}, 'Files': {'steam_api64.dll': {'path': 'steam_api64.dll', 'sha1': hashlib.sha1(b'original').hexdigest(), 'size': 8}}}
        baseline_patch = patch.object(worker, 'installed_baseline', return_value=baseline)
        baseline_patch.start(); self.addCleanup(baseline_patch.stop)
        self.request = dict(GuestPath=str(self.game), AppId='123', GenerateInfo=False,
                            Unpack=False, TimeoutMinutes=2, GoldbergUsername='Test Player')

    def test_configuration_maps_preferences_and_disables_bypass_and_file_logs(self):
        config = worker.configuration(self.request)
        self.assertEqual(config['EMUGameInfoConfigs']['GameInfoAPI'], 2)
        self.assertTrue(config['ProcessConfigs']['GenerateEMUGameInfo'])
        self.assertFalse(config['ProcessConfigs']['Unpack'])
        self.assertEqual(config['EMUConfigs']['AccountName'], 'Test Player')
        self.assertEqual(config['SteamStubUnpackerConfigs']['SteamAPICheckBypassMode'], 0)
        self.assertFalse(config['LogToFile'])

    def test_separate_game_info_controls_filter_only_selected_outputs(self):
        settings = self.game / 'steam_settings'
        settings.mkdir()
        for name in ('achievements.json', 'stats.json', 'steam_appid.txt'):
            (settings / name).write_text('example')
        (settings / 'configs.app.ini').write_text('[app::general]\nappid=123\n\n[app::dlcs]\nunlock_all=1\n456=DLC\n\n[app::other]\nvalue=1\n')
        images = settings / 'achievement_images'
        images.mkdir()
        (images / 'icon.jpg').write_bytes(b'image')
        request = dict(self.request, GenerateInfo=True, ApiKey='a' * 32,
                       GenerateAchievements=False, GenerateStats=True, GenerateDlc=False)
        self.assertFalse(worker.configuration(request)['EMUGameInfoConfigs']['GenerateImages'])
        worker.filter_game_info(self.game, request)
        self.assertFalse((settings / 'achievements.json').exists())
        self.assertFalse((images / 'icon.jpg').exists())
        self.assertTrue((settings / 'stats.json').exists())
        self.assertTrue((settings / 'steam_appid.txt').exists())
        ini = (settings / 'configs.app.ini').read_text()
        self.assertNotIn('456=DLC', ini)
        self.assertIn('unlock_all=0', ini)
        self.assertIn('[app::other]', ini)
        worker.filter_game_info(self.game, dict(request, GenerateStats=False))
        self.assertFalse((settings / 'stats.json').exists())

    def test_path_validation_rejects_root_outside_private_and_unmounted_folders(self):
        private = self.shared / '.staging'
        private.mkdir()
        for path in (self.root, self.shared, private):
            with self.assertRaises(ValueError):
                worker.game_path(str(path))
        with patch.object(worker.os.path, 'ismount', return_value=False):
            with self.assertRaisesRegex(ValueError, 'not mounted'):
                worker.game_path(str(self.game))

    def test_cli_error_with_zero_exit_is_failure_and_secrets_are_redacted(self):
        folder = self.jobs / 'test'
        folder.mkdir()
        request = {**self.request, 'ApiKey': 'a' * 32}
        actual_popen = subprocess.Popen
        def launch(args, **kwargs):
            self.assertNotIn('a' * 32, ' '.join(args))
            return actual_popen([sys.executable, '-c',
                'print("[ERR] key=' + 'a' * 32 + '");print("All process completed.")'], **kwargs)
        with patch.object(worker.subprocess, 'Popen', side_effect=launch):
            with self.assertRaisesRegex(ValueError, 'CLI processing failed'):
                worker.run_cli(self.game, request, folder, lambda: None, lambda text: None)
        self.assertNotIn('a' * 32, (folder / 'log.txt').read_text())
        self.assertFalse((folder / 'cli-config.json').exists())

    def test_modified_original_blocks_cli_before_processing(self):
        folder = self.jobs / ('3' * 32); folder.mkdir()
        (folder / 'request.json').write_text(json.dumps(self.request))
        (self.game / 'steam_api64.dll').write_bytes(b'already modified')
        with patch.object(worker, 'run_cli') as cli:
            worker.work('3' * 32)
        result = json.loads((folder / 'result.json').read_text())
        self.assertFalse(result['Success'])
        self.assertIn('Not a verified vanilla original', result['Message'])
        cli.assert_not_called()
        self.assertFalse((self.game / backup_module.BACKUP).exists())

    def test_guest_failure_restores_originals_and_removes_new_files(self):
        job = '1' * 32
        folder = self.jobs / job
        folder.mkdir()
        (folder / 'request.json').write_text(json.dumps(self.request))
        def fail(root, *args):
            (root / 'steam_api64.dll').write_bytes(b'partly applied')
            (root / 'new.dll').write_bytes(b'generated')
            raise ValueError('Cancelled.')
        with patch.object(worker, 'run_cli', side_effect=fail):
            worker.work(job)
        result = json.loads((folder / 'result.json').read_text())
        self.assertFalse(result['Success'])
        self.assertEqual((self.game / 'steam_api64.dll').read_bytes(), b'original')
        self.assertFalse((self.game / 'new.dll').exists())
        self.assertFalse((self.game / '.playlite-steamautocrack').exists())
        self.assertFalse((folder / 'request.json').exists())

    def test_guest_success_retains_backups_for_explicit_restoration(self):
        job = '2' * 32
        folder = self.jobs / job
        folder.mkdir()
        (folder / 'request.json').write_text(json.dumps(self.request))
        def succeed(root, *args):
            self.assertEqual((root / 'steam_api64.dll').read_bytes(), b'original')
            (root / 'steam_api64.dll').write_bytes(b'emulator')
            settings = root / 'steam_settings'
            settings.mkdir()
            (settings / 'steam_appid.txt').write_text('123')
            (settings / 'configs.user.ini').write_text('settings')
        with patch.object(worker, 'run_cli', side_effect=succeed):
            worker.work(job)
        self.assertTrue(json.loads((folder / 'result.json').read_text())['Success'])
        manifest = self.game / '.playlite-steamautocrack/manifest.json'
        self.assertEqual(json.loads(manifest.read_text())['Backend'], 'vm-cli')
        repeat = self.jobs / ('4' * 32)
        repeat.mkdir()
        (repeat / 'request.json').write_text(json.dumps(self.request))
        with patch.object(worker, 'run_cli', side_effect=succeed):
            worker.work('4' * 32)
        self.assertTrue(json.loads((repeat / 'result.json').read_text())['Success'])
        restore_job = '3' * 32
        restore_folder = self.jobs / restore_job
        restore_folder.mkdir()
        (restore_folder / 'request.json').write_text(json.dumps({**self.request, 'Restore': True}))
        worker.work(restore_job)
        self.assertTrue(json.loads((restore_folder / 'result.json').read_text())['Success'])
        self.assertEqual((self.game / 'steam_api64.dll').read_bytes(), b'original')

    def test_verification_rejects_skipped_emulator(self):
        with self.assertRaisesRegex(ValueError, 'not applied'):
            worker.verify_applied(self.game, self.request, lambda text: None)
