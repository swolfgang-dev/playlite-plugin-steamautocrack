import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
from source_support import ROOT
from autocrack_source.plugin import Plugin

APP = QApplication.instance() or QApplication([])


class InstallHookTests(unittest.TestCase):
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
