import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from PyQt6.QtCore import QProcess
from PyQt6.QtWidgets import QApplication
from source_support import ROOT
from autocrack_source.progress import SteamBatchProgress, SteamProgress

APP = QApplication.instance() or QApplication([])


class BatchProgressTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.jobs = [(dict(Id=str(i), Name='Game ' + str(i)), dict(InstallDirectory='/games/' + str(i))) for i in range(3)]
        self.private = patch('playlite_plugins.cracktools.jobs.PRIVATE', Path(self.directory.name))
        self.private.start()
        self.addCleanup(self.private.stop)
        self.start = patch.object(QProcess, 'start')
        self.process = self.start.start()
        self.addCleanup(self.start.stop)

    def finish(self, dialog, success=True):
        (dialog.job / 'result.json').write_text(json.dumps(dict(Success=success, Message='result')))
        dialog.process_finished()
        APP.processEvents()

    def test_single_game_displays_saved_log_and_final_restore_error(self):
        dialog = SteamProgress(dict(InstallDirectory='/games/test', Restore=True))
        (dialog.job / 'log.txt').write_text('Checking original backups\nVerifying steam_api64.dll\n')
        dialog.poll()
        self.assertIn('Verifying steam_api64.dll', dialog.activity_log.toPlainText())
        (dialog.job / 'result.json').write_text(json.dumps(dict(Success=False, Message='DLL changed after processing')))
        dialog.process_finished()
        self.assertIn('DLL changed after processing', dialog.activity_log.toPlainText())
        self.assertIn(str(dialog.job / 'log.txt'), dialog.log_path.text())
        dialog.accept()

    def test_completion_message_already_in_log_is_not_appended_again(self):
        dialog = SteamProgress(dict(InstallDirectory='/games/test'))
        message = 'Original game files restored.'
        (dialog.job / 'log.txt').write_text('Preparing…\n' + message + '\n')
        (dialog.job / 'result.json').write_text(json.dumps(dict(Success=True, Message=message)))
        dialog.process_finished()
        self.assertEqual(dialog.activity_log.toPlainText().count(message), 1)
        dialog.accept()

    def test_jobs_are_sequential_and_failures_do_not_stop_the_rest(self):
        dialog = SteamBatchProgress(self.jobs)
        self.assertEqual(self.process.call_count, 1)
        self.finish(dialog, False)
        self.assertEqual(self.process.call_count, 2)
        self.finish(dialog)
        self.assertEqual(self.process.call_count, 3)
        self.finish(dialog)
        self.assertTrue(dialog.batch_finished)
        self.assertIn('2 succeeded, 1 failed', dialog.status.text())
        dialog.accept()

    def test_cancel_between_jobs_does_not_start_another_process(self):
        dialog = SteamBatchProgress(self.jobs)
        (dialog.job / 'result.json').write_text(json.dumps(dict(Success=True, Message='done')))
        dialog.process_finished()
        dialog.cancel_or_close()
        APP.processEvents()
        self.assertEqual(self.process.call_count, 1)
        self.assertTrue(dialog.batch_finished)
        self.assertIn('2 skipped', dialog.status.text())
        dialog.accept()

    def test_preflight_failure_skips_only_that_game(self):
        def check(game):
            if game['Id'] == '1':
                raise ValueError('Game is running')
        dialog = SteamBatchProgress(self.jobs, preflight=check)
        self.finish(dialog)
        self.assertEqual(self.process.call_count, 2)
        self.assertEqual(dialog.results[1][1]['Message'], 'Game is running')
        self.finish(dialog)
        self.assertTrue(dialog.batch_finished)
        dialog.accept()

    def test_process_start_failure_advances_the_queue(self):
        dialog = SteamBatchProgress(self.jobs)
        dialog.failed(QProcess.ProcessError.FailedToStart)
        APP.processEvents()
        self.assertFalse(dialog.results[0][1]['Success'])
        self.assertEqual(self.process.call_count, 2)
        dialog.cancel_or_close()
        dialog.accept()
