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
        self.job = PRIVATE / 'jobs' / uuid.uuid4().hex
        self.job.mkdir(parents=True, mode=0o700)
        os.chmod(self.job, 0o700)
        write_json(self.job / 'request.json', request)
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
        self.status.setText(json.loads(result.read_text()).get('Message', 'Finished') if result.exists()
                            else 'SteamAutoCrack stopped. The game remains saved in Playlite and Lutris.')
        self.button.setText('Close')

    def cancel_or_close(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            (self.job / 'cancel').touch()
            self.status.setText('Cancelling… Restoring original game files.')
        else:
            self.accept()

    def reject(self):
        self.cancel_or_close()
