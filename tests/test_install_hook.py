import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('autocrack_install_test', ROOT / 'plugin.py', submodule_search_locations=[str(ROOT)])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
APP = QApplication.instance() or QApplication([])

class InstallHookTests(unittest.TestCase):
    def test_setup_saves_only_tool_paths(self):
        self.assertTrue(json.loads((ROOT / 'manifest.json').read_text())['installation_hooks'])
        with tempfile.TemporaryDirectory() as folder:
            settings = QSettings(str(Path(folder) / 'settings.ini'), QSettings.Format.IniFormat)
            settings.setValue('username', 'Player')
            plugin = module.Plugin()
            result = dict(EmulatorDirectory='/private/emulator', UnpackerPath='/private/steamless')
            with patch.object(plugin, 'settings', return_value=settings), patch.object(plugin, 'install_tools', return_value=result) as install:
                plugin.post_install()
            install.assert_called_once()
            self.assertEqual(settings.value('emulatorDirectory'), result['EmulatorDirectory'])
            self.assertEqual(settings.value('unpackerPath'), result['UnpackerPath'])
            self.assertEqual(settings.value('username'), 'Player')

    def test_cancel_preserves_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = QSettings(str(Path(folder) / 'settings.ini'), QSettings.Format.IniFormat)
            settings.setValue('emulatorDirectory', '/existing/emulator')
            plugin = module.Plugin()
            with patch.object(plugin, 'settings', return_value=settings), patch.object(plugin, 'install_tools', return_value=None):
                plugin.post_install()
            self.assertEqual(settings.value('emulatorDirectory'), '/existing/emulator')
