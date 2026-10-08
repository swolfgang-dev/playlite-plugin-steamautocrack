import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from source_support import ROOT

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
        self.jobs = self.root / 'jobs'
        self.jobs.mkdir()
        for name, value in [('SHARED', self.shared), ('CLI', self.cli), ('JOBS', self.jobs)]:
            patched = patch.object(worker, name, value)
            patched.start()
            self.addCleanup(patched.stop)
        mounted = patch.object(worker.os.path, 'ismount', return_value=True)
        mounted.start()
        self.addCleanup(mounted.stop)
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
            (root / 'steam_api64.dll').write_bytes(b'emulator')
        with patch.object(worker, 'run_cli', side_effect=succeed):
            worker.work(job)
        self.assertTrue(json.loads((folder / 'result.json').read_text())['Success'])
        manifest = self.game / '.playlite-steamautocrack/manifest.json'
        self.assertEqual(json.loads(manifest.read_text())['Backend'], 'vm-cli')
        restore_job = '3' * 32
        restore_folder = self.jobs / restore_job
        restore_folder.mkdir()
        (restore_folder / 'request.json').write_text(json.dumps({**self.request, 'Restore': True}))
        worker.work(restore_job)
        self.assertTrue(json.loads((restore_folder / 'result.json').read_text())['Success'])
        self.assertEqual((self.game / 'steam_api64.dll').read_bytes(), b'original')
