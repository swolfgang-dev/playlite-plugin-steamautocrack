import getpass
import re
from pathlib import Path
from PyQt6.QtWidgets import QCheckBox, QLineEdit, QDialog, QVBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import QSettings, QThreadPool, Qt
from playlite.providers import GenericPlugin
from playlite.lifecycle import run_dialog, show_warning, choose_directory
from .vm_backend import default_root


class Plugin(GenericPlugin):
    settings_group = 'installation'

    def post_install(self, parent=None):
        widget = self.create_settings(parent)
        try:
            self.setup_vm(widget)
        finally:
            widget.deleteLater()

    def settings(self):
        return QSettings('Playlite', 'SteamAutoCrack')

    def before_launch(self, window, game):
        from playlite_plugins.cracktools.plugin import Plugin as CrackTools
        return CrackTools().before_launch(window, game)

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
        widget.vm_root = QLineEdit(settings.value('vmRoot', str(default_root())))
        form.addRow('Emulator username', widget.username)
        form.addRow('Steam Web API key', widget.api_key)
        form.addRow('Timeout (minutes)', widget.timeout)
        row = QHBoxLayout()
        row.addWidget(widget.vm_root)
        browse = QPushButton('Browse…')
        def pick():
            value = choose_directory(widget, 'Steam VM userdata folder', widget.vm_root.text())
            if value:
                widget.vm_root.setText(value)
        browse.clicked.connect(pick)
        row.addWidget(browse)
        form.addRow('Steam VM userdata', row)
        widget.generate_info = QCheckBox('Generate Steam achievements, stats, and DLC configuration')
        widget.generate_info.setChecked(settings.value('generateInfo', True, type=bool))
        widget.unpack = QCheckBox('Unpack SteamStub executables')
        widget.unpack.setChecked(settings.value('unpack', True, type=bool))
        form.addRow(widget.generate_info)
        form.addRow(widget.unpack)
        setup = QPushButton('Set up / check VM CLI…')
        setup.clicked.connect(lambda: self.setup_vm(widget))
        form.addRow(setup)
        description = QLabel('Runs the Windows SteamAutoCrack CLI through Wine in the Steam Downloader VM. '
                             'Choose Windows games inside its shared Steam library. Original game files '
                             'are backed up for restoration. Game-info requests use the VM VPN.')
        description.setWordWrap(True)
        form.addRow(description)
        return widget

    def setup_vm(self, widget):
        import threading
        from .vm_backend import setup
        from playlite.metadata_dialog import Task
        from PyQt6.QtCore import QObject, pyqtSignal
        class Updates(QObject):
            progress = pyqtSignal(str)
        cancel = threading.Event()
        installed = []
        dialog = QDialog(widget)
        dialog.setWindowTitle('Set up SteamAutoCrack VM CLI')
        dialog.setMinimumSize(640, 160)
        layout = QVBoxLayout(dialog)
        label = QLabel('Preparing the Steam VM CLI…')
        label.setWordWrap(True)
        layout.addWidget(label)
        button = QPushButton('Cancel')
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
        button.clicked.connect(lambda: (cancel.set(), button.setEnabled(False), label.setText('Cancelling…')))
        updates = Updates(dialog)
        updates.progress.connect(label.setText)
        vm_root = widget.vm_root.text().strip()
        task = Task(lambda: setup(vm_root, cancel.is_set, updates.progress.emit))
        def complete(result):
            installed.append(result)
            dialog.accept()
        def failed(error):
            dialog.reject()
            if not cancel.is_set():
                show_warning(widget, 'Steam VM CLI setup', error)
        task.signals.succeeded.connect(complete)
        task.signals.failed.connect(failed)
        dialog.finished.connect(lambda: cancel.set())
        dialog.task = task
        QThreadPool.globalInstance().start(task)
        run_dialog(dialog)
        return installed[0] if installed else None

    def save_settings(self, widget):
        if not widget.username.text().strip() or len(widget.username.text().strip()) > 128 or any(c in widget.username.text() for c in '\r\n\0'):
            raise ValueError('Enter a valid emulator username.')
        key = widget.api_key.text().strip()
        if key and not re.fullmatch(r'[a-fA-F0-9]{32}', key):
            raise ValueError('Enter a valid Steam Web API key (32 hexadecimal characters), or leave it blank.')
        vm_root = widget.vm_root.text().strip()
        if not Path(vm_root).is_absolute() or any(c in vm_root for c in '\r\n\0'):
            raise ValueError('Select an absolute Steam VM userdata folder.')
        settings = self.settings()
        for key, value in [('username', widget.username.text().strip()), ('timeout', widget.timeout.value()),
                           ('apiKey', widget.api_key.text().strip()), ('vmRoot', vm_root),
                           ('generateInfo', widget.generate_info.isChecked()), ('unpack', widget.unpack.isChecked())]:
            settings.setValue(key, value)
        for obsolete in ('emulatorDirectory', 'unpackerPath'):
            settings.remove(obsolete)
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
        if getattr(editor, 'editor_purpose', None) == 'play_action':
            return
        editor.autocrack = QCheckBox('Run SteamAutoCrack after adding')
        method = getattr(editor, 'installation_plugin', None)
        editor.autocrack.setChecked(self.default_for(method.id if method else 'Manual'))
        editor.installation_method.currentIndexChanged.connect(
            lambda: editor.autocrack.setChecked(self.default_for(editor.installation_plugin.id)))
        editor.installation_form.addRow(editor.autocrack)
        editor.steam_request = None

    def request(self, game, key=''):
        settings = self.settings()
        appid = (game.get('MetadataIds') or {}).get('SteamMetadata') or next((match.group(1) for link in game.get('Links', [])
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
        from .vm_backend import Client, shared_game_path
        vm_root = settings.value('vmRoot', str(default_root()))
        client = Client(vm_root)
        shared_game_path(root, client.cfg['shared'])
        return dict(InstallDirectory=str(root), AppId=str(appid), ApiKey=key if info else '', GenerateInfo=info,
                    GenerateConfig=True, Unpack=settings.value('unpack', True, type=bool), ApplyEmulator=True,
                    VmRoot=vm_root, TimeoutMinutes=settings.value('timeout', 5, type=int),
                    GoldbergUsername=settings.value('username', getpass.getuser()))

    def restore_request(self, game):
        settings = self.settings()
        return dict(InstallDirectory=game['InstallDirectory'], Restore=True,
                    VmRoot=settings.value('vmRoot', str(default_root())),
                    TimeoutMinutes=settings.value('timeout', 5, type=int))

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
        from playlite_plugins.cracktools.guards import ensure_stopped
        ensure_stopped(window, game)

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
            request = self.restore_request(game) if restore else self.request(game)
            title = 'Restore originals' if restore else 'SteamAutoCrack'
            if QMessageBox.question(window, title, f'{title} for “{game["Name"]}”?') != QMessageBox.StandardButton.Yes:
                return
            run_dialog(SteamProgress(request, window))
        except (ValueError, OSError) as error:
            show_warning(window, 'Cannot run SteamAutoCrack', str(error))


    def batch_game_actions(self, window, games):
        eligible = [dict(game) for game in games if game.get('InstallDirectory') and not game.get('ArchivePath')]
        actions = []
        if eligible:
            actions.append((f'Run for {len(eligible)} games…', lambda: self.run_games(window, eligible)))
        recoverable = [game for game in eligible if (Path(game['InstallDirectory']) / '.playlite-steamautocrack/manifest.json').is_file()]
        if recoverable:
            actions.append((f'Restore originals for {len(recoverable)} games…', lambda: self.run_games(window, recoverable, True)))
        return actions

    def run_games(self, window, games, restore=False):
        from .progress import SteamBatchProgress
        from PyQt6.QtWidgets import QMessageBox
        title = 'Restore originals' if restore else 'SteamAutoCrack'
        if QMessageBox.question(window, title, f'{title} for {len(games)} selected games?') != QMessageBox.StandardButton.Yes:
            return
        jobs, failures = [], []
        for game in games:
            try:
                self.ensure_stopped(window, game)
                request = self.restore_request(game) if restore else self.request(game)
                jobs.append((game, request))
            except (ValueError, OSError) as error:
                failures.append(game['Name'] + ': ' + str(error))
        if failures:
            show_warning(window, 'Skipped games', '\n'.join(failures))
        if jobs:
            run_dialog(SteamBatchProgress(jobs, window, preflight=lambda game: self.ensure_stopped(window, game)))
