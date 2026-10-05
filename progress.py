import json
import os
import sys
import uuid
from PyQt6.QtCore import QProcess, QTimer
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton

class SteamProgress(QDialog):
    def __init__(self, request, parent=None):
        super().__init__(parent)
        from .runner import PRIVATE, write_json
        self.setProperty('playliteBackgroundJob', True)
        self.setWindowTitle('SteamAutoCrack')
        self.install_directory = request.get('InstallDirectory')
        self.resize(600, 180)
        layout = QVBoxLayout(self)
        self.status = QLabel('Starting SteamAutoCrack…')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.button = QPushButton('Cancel')
        self.button.clicked.connect(self.cancel_or_close)
        layout.addWidget(self.button)
        self.process = QProcess(self)
        self.process.finished.connect(self.process_finished)
        self.process.errorOccurred.connect(self.failed)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.start_job(request)

    def start_job(self, request):
        from .runner import PRIVATE, write_json
        self.install_directory = request.get('InstallDirectory')
        self.job = PRIVATE / 'jobs' / uuid.uuid4().hex
        self.job.mkdir(parents=True, mode=0o700)
        os.chmod(self.job, 0o700)
        write_json(self.job / 'request.json', request)
        self.button.setText('Cancel')
        self.timer.start(500)
        self.process.start(sys.executable, ['-m', 'playlite.plugin_runner', 'SteamAutoCrack', self.job.name])

    def poll(self):
        status = self.job / 'status.txt'
        if status.exists():
            self.status.setText(status.read_text(errors='replace')[-500:])

    def failed(self, error):
        self.timer.stop()
        (self.job / 'request.json').unlink(missing_ok=True)
        self.status.setText('Could not start SteamAutoCrack. The game remains saved in Playlite and Lutris.')
        self.button.setText('Close')

    def process_finished(self, *args):
        self.timer.stop()
        result = self.job / 'result.json'
        try:
            self.result_data = json.loads(result.read_text())
        except (OSError, ValueError):
            self.result_data = dict(Success=False, Message='SteamAutoCrack stopped. Backups remain available for recovery.')
        self.status.setText(self.result_data.get('Message', 'Finished'))
        self.button.setText('Close')

    def cancel_or_close(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            (self.job / 'cancel').touch()
            self.status.setText('Cancelling… Restoring original game files.')
        else:
            self.accept()

    def reject(self):
        self.cancel_or_close()


class SteamBatchProgress(SteamProgress):
    """One progress dialog processes a selection sequentially."""
    def __init__(self, jobs, parent=None, preflight=lambda game: None):
        self.jobs = jobs
        self.index = 0
        self.results = []
        self.cancelled = False
        self.batch_finished = False
        self.preflight = preflight
        super().__init__(jobs[0][1], parent)
        self.resize(760, 240)
        from PyQt6.QtWidgets import QPlainTextEdit
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.layout().insertWidget(1, self.log)
        self.status.setText(f'1/{len(jobs)}: {jobs[0][0]["Name"]}')

    def process_finished(self, *args):
        super().process_finished(*args)
        game = self.jobs[self.index][0]
        self.results.append((game['Name'], self.result_data))
        self.log.appendPlainText(game['Name'] + ': ' + self.result_data.get('Message', 'Finished'))
        self.index += 1
        QTimer.singleShot(0, self.advance)

    def failed(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            super().failed(error)
            self.process_finished()

    def advance(self):
        while self.index < len(self.jobs) and not self.cancelled:
            game, request = self.jobs[self.index]
            try:
                self.preflight(game)
            except (ValueError, OSError) as error:
                result = dict(Success=False, Message=str(error))
                self.results.append((game['Name'], result))
                self.log.appendPlainText(game['Name'] + ': ' + str(error))
                self.index += 1
                continue
            self.start_job(request)
            self.status.setText(f'{self.index + 1}/{len(self.jobs)}: {game["Name"]}')
            return
        self.batch_finished = True
        succeeded = sum(bool(result.get('Success')) for _, result in self.results)
        self.status.setText(f'Finished: {succeeded} succeeded, {len(self.results) - succeeded} failed, {len(self.jobs) - self.index} skipped.')
        self.button.setText('Close')

    def cancel_or_close(self):
        if self.batch_finished:
            self.accept()
            return
        self.cancelled = True
        if self.process.state() != QProcess.ProcessState.NotRunning:
            super().cancel_or_close()
        else:
            self.advance()
