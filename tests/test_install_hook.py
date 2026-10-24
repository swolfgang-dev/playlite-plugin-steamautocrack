import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
from source_support import ROOT
from autocrack_source.plugin import Plugin

APP = QApplication.instance() or QApplication([])


class InstallHookTests(unittest.TestCase):
    def setUp(self):
        backend=patch.dict(os.environ,{'PLAYLITE_STEAM_BACKEND':'vm'})
        backend.start();self.addCleanup(backend.stop)
    def test_setup_cancels_on_shutdown_and_releases_pending_state(self):
        import autocrack_source.plugin as module
        tasks = []
        plugin = Plugin()
        widget = plugin.create_settings()
        def setup(root, cancelled, progress):
            self.assertTrue(cancelled())
            raise ValueError('Cancelled')
        def run(dialog):
            self.assertTrue(dialog.shutdown_pending())
            dialog.request_shutdown()
            tasks[0].run()
            self.assertFalse(dialog.shutdown_pending())
            return dialog.result()
        try:
            with patch('autocrack_source.vm_backend.setup', side_effect=setup), \
                    patch.object(module.QThreadPool, 'globalInstance', return_value=Mock(start=tasks.append)), \
                    patch.object(module, 'run_dialog', side_effect=run), patch.object(module, 'show_warning') as warning:
                self.assertIsNone(plugin.setup_vm(widget))
            warning.assert_not_called()
        finally:
            widget.deleteLater()

    def test_install_hook_uses_vm_setup_and_preserves_preferences(self):
        manifest = json.loads((ROOT / 'manifest.json').read_text())
        self.assertTrue(manifest['installation_hooks'])
        self.assertEqual(manifest['plugin_dependencies'][0]['id'], 'SteamDepotDownloader')
        with tempfile.TemporaryDirectory() as folder:
            settings = QSettings(str(Path(folder) / 'settings.ini'), QSettings.Format.IniFormat)
            settings.setValue('username', 'Player')
            plugin = Plugin()
            with patch.object(plugin, 'settings', return_value=settings), patch.object(plugin, 'setup_vm') as setup:
                plugin.post_install()
            setup.assert_called_once()
            self.assertEqual(settings.value('username'), 'Player')

    def test_settings_replace_native_paths_and_migrate_only_obsolete_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = QSettings(str(Path(folder) / 'settings.ini'), QSettings.Format.IniFormat)
            settings.setValue('emulatorDirectory', '/old/tools')
            settings.setValue('unpackerPath', '/old/unpacker')
            settings.setValue('runOnAdd/Manual', True)
            plugin = Plugin()
            with patch.object(plugin, 'settings', return_value=settings):
                widget = plugin.create_settings()
                self.assertFalse(hasattr(widget, 'emulator'))
                self.assertFalse(hasattr(widget, 'unpacker'))
                widget.vm_root.setText('/private/steam-vm')
                plugin.save_settings(widget)
                widget.deleteLater()
            self.assertEqual(settings.value('vmRoot'), '/private/steam-vm')
            self.assertFalse(settings.contains('emulatorDirectory'))
            self.assertFalse(settings.contains('unpackerPath'))
            self.assertTrue(settings.value('runOnAdd/Manual', type=bool))
