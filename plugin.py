import getpass
import re
from pathlib import Path
from PyQt6.QtWidgets import QCheckBox, QLineEdit, QDialog, QVBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import QSettings, QThreadPool, Qt
from playlite.providers import GenericPlugin
from playlite.lifecycle import run_dialog, show_warning, choose_directory, choose_file
from .tools import TOOLS


class Plugin(GenericPlugin):
    settings_group = 'installation'

    def settings(self):
        return QSettings('Playlite', 'SteamAutoCrack')

    def before_launch(self, window, game):
        import fcntl
        from .progress import SteamProgress
        from PyQt6.QtCore import QProcess
        for dialog in window.findChildren(SteamProgress):
            if dialog.install_directory == game.get('InstallDirectory') and dialog.process.state() != QProcess.ProcessState.NotRunning:
                show_warning(window, 'SteamAutoCrack', 'Wait for game-file processing to finish before launching.')
                return False
        lock = Path(game.get('InstallDirectory') or '') / '.playlite-steamautocrack.lock'
        if lock.is_file():
            with lock.open('a') as stream:
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    show_warning(window, 'SteamAutoCrack', 'Wait for game-file processing to finish before launching.')
                    return False
        return True

    def create_settings(self, parent=None):
        from PyQt6.QtWidgets import QWidget, QFormLayout, QSpinBox, QHBoxLayout
        widget = QWidget(parent)
        form = QFormLayout(widget)
        settings = self.settings()
        widget.username = QLineEdit(settings.value('username', getpass.getuser()))
        widget.api_key = QLineEdit(settings.value('apiKey', ''))
        widget.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        widget.api_key.setPlaceholderText('Required only when generating Steam game info')
        widget.timeout = QSpinBox()
        widget.timeout.setRange(1, 120)
        widget.timeout.setValue(settings.value('timeout', 5, type=int))
        widget.emulator = QLineEdit(settings.value('emulatorDirectory', str(TOOLS / 'emulator')))
        widget.unpacker = QLineEdit(settings.value('unpackerPath', str(TOOLS / 'unpacker/steamless')))
        form.addRow('Emulator username', widget.username)
        form.addRow('Steam Web API key', widget.api_key)
        form.addRow('Timeout (minutes)', widget.timeout)
        for title, field, folder in [('Emulator libraries', widget.emulator, True), ('Native unpacker', widget.unpacker, False)]:
            row = QHBoxLayout()
            row.addWidget(field)
            browse = QPushButton('Browse…')
            def pick(checked=False, field=field, folder=folder):
                value = choose_directory(widget, 'Emulator libraries', field.text()) if folder else choose_file(widget, 'Native unpacker', field.text(), 'All files (*)')[0]
                if value:
                    field.setText(value)
            browse.clicked.connect(pick)
            row.addWidget(browse)
            form.addRow(title, row)
        widget.generate_info = QCheckBox('Generate Steam achievements, stats, and DLC configuration')
        widget.generate_info.setChecked(settings.value('generateInfo', True, type=bool))
        widget.unpack = QCheckBox('Unpack SteamStub executables')
        widget.unpack.setChecked(settings.value('unpack', True, type=bool))
        form.addRow(widget.generate_info)
        form.addRow(widget.unpack)
        install = QPushButton('Install / update native tools…')
        install.clicked.connect(lambda: self.install_tools(widget))
        install_row = QHBoxLayout()
        install_row.addStretch()
        install_row.addWidget(install)
        install_row.addStretch()
        form.addRow(install_row)
        description = QLabel('Runs natively on Linux. Tool setup downloads emulator libraries and builds the original 32-bit and 64-bit Steamless unpackers with a private Linux .NET SDK. Original game files are backed up for restoration.')
        description.setWordWrap(True)
        form.addRow(description)
        return widget

    def install_tools(self, widget):
        import threading
        from .tools import install
        from playlite.metadata_dialog import Task
        from PyQt6.QtCore import QObject, pyqtSignal
        class Updates(QObject):
            progress = pyqtSignal(str)
        cancel = threading.Event()
        dialog = QDialog(widget)
        dialog.setWindowTitle('Set up native SteamAutoCrack tools')
        dialog.setMinimumSize(640, 160)
        dialog.resize(760, 200)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        label = QLabel('Setting up native tools…')
        label.setWordWrap(True)
        layout.addWidget(label, 1)
        button = QPushButton('Cancel')
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
        button.clicked.connect(lambda: (cancel.set(), button.setEnabled(False), label.setText('Cancelling…')))
        def check():
            if cancel.is_set():
                raise ValueError('Tool setup cancelled.')
        updates = Updates(dialog)
        updates.progress.connect(label.setText)
        task = Task(lambda: install(check=check, status=updates.progress.emit))
        def complete(result):
            widget.emulator.setText(result['EmulatorDirectory'])
            widget.unpacker.setText(result['UnpackerPath'])
            dialog.accept()
        def failed(error):
            dialog.reject()
            if not cancel.is_set():
                show_warning(widget, 'Native tool setup', error)
        task.signals.succeeded.connect(complete)
        task.signals.failed.connect(failed)
        dialog.finished.connect(lambda: cancel.set())
        dialog.task = task
        QThreadPool.globalInstance().start(task)
        run_dialog(dialog)

    def save_settings(self, widget):
        if not widget.username.text().strip() or any(c in widget.username.text() for c in '\r\n\0'):
            raise ValueError('Enter a valid emulator username.')
        key = widget.api_key.text().strip()
        if key and not re.fullmatch(r'[a-fA-F0-9]{32}', key):
            raise ValueError('Enter a valid Steam Web API key (32 hexadecimal characters), or leave it blank.')
        for field in (widget.emulator, widget.unpacker):
            if field.text().strip() and not Path(field.text().strip()).is_absolute():
                raise ValueError('Native tool paths must be absolute Linux paths.')
        settings = self.settings()
        for key, value in [('username', widget.username.text().strip()), ('timeout', widget.timeout.value()),
                           ('apiKey', widget.api_key.text().strip()), ('emulatorDirectory', widget.emulator.text().strip()),
                           ('unpackerPath', widget.unpacker.text().strip()), ('generateInfo', widget.generate_info.isChecked()),
                           ('unpack', widget.unpack.isChecked())]:
            settings.setValue(key, value)
        settings.sync()
        filename = Path(settings.fileName())
        if filename.is_file():
            filename.chmod(0o600)

    def default_for(self, method):
        settings = self.settings()
        return settings.value('runOnAdd/' + method, settings.value('runOnAdd', False, type=bool), type=bool)

    def create_settings_contribution(self, target, parent=None):
        if target.type != 'installation':
            return None
        checkbox = QCheckBox('Run SteamAutoCrack after adding by default', parent)
        checkbox.setChecked(self.default_for(target.id))
        return checkbox

    def save_settings_contribution(self, target, widget):
        settings = self.settings()
        settings.setValue('runOnAdd/' + target.id, widget.isChecked())
        settings.sync()

    def augment_add_editor(self, editor):
        editor.autocrack = QCheckBox('Run SteamAutoCrack after adding')
        method = getattr(editor, 'installation_plugin', None)
        editor.autocrack.setChecked(self.default_for(method.id if method else 'Manual'))
        editor.installation_method.currentIndexChanged.connect(
            lambda: editor.autocrack.setChecked(self.default_for(editor.installation_plugin.id)))
        editor.installation_form.addRow(editor.autocrack)
        editor.steam_request = None

    def request(self, game, key=''):
        settings = self.settings()
        appid = (game.get('MetadataIds') or {}).get('Steam') or next((match.group(1) for link in game.get('Links', [])
            if (match := re.search(r'https?://store\.steampowered\.com/app/(\d+)', link.get('Url', '')))), None)
        if not appid or not str(appid).isascii() or not str(appid).isdigit() or not 0 < int(appid) <= 4294967295:
            raise ValueError('Set the Steam metadata ID before running SteamAutoCrack.')
        root = Path(game.get('InstallDirectory') or '').expanduser()
        if not root.is_absolute() or not root.is_dir() or root.is_symlink() or game.get('ArchivePath'):
            raise ValueError('Select an installed game folder before running SteamAutoCrack.')
        key = key or settings.value('apiKey', '')
        info = settings.value('generateInfo', True, type=bool)
        if info and not re.fullmatch(r'[a-fA-F0-9]{32}', key):
            raise ValueError('Set a Steam Web API key, or disable game-info generation in plugin settings.')
        emulator = settings.value('emulatorDirectory', str(TOOLS / 'emulator'))
        if not Path(emulator).is_dir():
            raise ValueError('Install native tools or choose an emulator folder in plugin settings.')
        return dict(InstallDirectory=str(root), AppId=str(appid), ApiKey=key, GenerateInfo=info,
                    GenerateConfig=True, Unpack=settings.value('unpack', True, type=bool), ApplyEmulator=True,
                    EmulatorDirectory=emulator, UnpackerPath=settings.value('unpackerPath', str(TOOLS / 'unpacker/steamless')),
                    TimeoutMinutes=settings.value('timeout', 5, type=int), GoldbergUsername=settings.value('username', getpass.getuser()))

    def prepare_add(self, editor, game, registration):
        editor.steam_request = self.request(game) if editor.autocrack.isChecked() else None

    def after_game_added(self, window, game, editor):
        if editor.steam_request:
            from .progress import SteamProgress
            try:
                self.ensure_stopped(window, game)
            except ValueError as error:
                show_warning(window, 'Cannot run SteamAutoCrack', str(error))
                return
            run_dialog(SteamProgress(editor.steam_request, window))

    def ensure_stopped(self, window, game):
        if window.game_detection.status(game['Id']) in ('Launching', 'Running'):
            raise ValueError('Stop the game before modifying or restoring its files.')
        from playlite.providers import IntegrationPlugin
        for provider in window.game_providers:
            if isinstance(provider, IntegrationPlugin) and provider.owns(game) and game['Id'] in provider.detect_running([game]):
                raise ValueError('Stop the game before modifying or restoring its files.')

    def game_actions(self, window, game):
        if game.get('InstallDirectory') and not game.get('ArchivePath'):
            actions = [('Run…', lambda: self.run_game(window, game))]
            if (Path(game['InstallDirectory']) / '.playlite-steamautocrack/manifest.json').is_file():
                actions.append(('Restore originals…', lambda: self.run_game(window, game, restore=True)))
            return actions
        return []

    def run_game(self, window, game, restore=False):
        from .progress import SteamProgress
        from PyQt6.QtWidgets import QMessageBox
        try:
            self.ensure_stopped(window, game)
            request = dict(InstallDirectory=game['InstallDirectory'], Restore=True) if restore else self.request(game)
            title = 'Restore originals' if restore else 'SteamAutoCrack'
            if QMessageBox.question(window, title, f'{title} for “{game["Name"]}”?') != QMessageBox.StandardButton.Yes:
                return
            run_dialog(SteamProgress(request, window))
        except (ValueError, OSError) as error:
            show_warning(window, 'Cannot run SteamAutoCrack', str(error))
