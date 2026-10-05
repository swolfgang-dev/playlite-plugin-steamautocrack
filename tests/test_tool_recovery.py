"""Source-level tool setup tests; no installed plugins or network needed."""
import importlib.util
import sys
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import subprocess
import time
import fcntl

SPEC = importlib.util.spec_from_file_location('autocrack_tools_under_test',
    Path(__file__).resolve().parents[1] / '__init__.py',
    submodule_search_locations=[str(Path(__file__).resolve().parents[1])])
PACKAGE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PACKAGE
SPEC.loader.exec_module(PACKAGE)
from autocrack_tools_under_test import tools


class ToolRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'tools'
        self.root.mkdir()
        self.prepared = {}
        for name in ('unpacker', 'emulator'):
            target = self.root / name
            target.mkdir()
            (target / 'version').write_text('old')
            stage = Path(self.temporary.name) / name
            stage.mkdir()
            (stage / 'version').write_text('new')
            self.prepared[name] = stage

    def assert_old_tools(self):
        for name in self.prepared:
            self.assertEqual((self.root / name / 'version').read_text(), 'old')
        self.assertFalse(list(self.root.glob('*.previous-*')))

    def test_partial_promotion_failure_rolls_back_both_tools(self):
        rename = Path.rename
        def fail_second(path, target):
            if path == self.prepared['emulator']:
                raise OSError('disk error')
            return rename(path, target)
        with patch.object(Path, 'rename', fail_second):
            with self.assertRaises(OSError):
                tools.promote(self.root, self.prepared)
        self.assert_old_tools()

    def test_metadata_failure_rolls_back_tools(self):
        def commit():
            raise OSError('metadata disk error')
        with self.assertRaises(OSError):
            tools.promote(self.root, self.prepared, commit)
        self.assert_old_tools()

    def test_success_commits_both_then_removes_previous_versions(self):
        observed = []
        tools.promote(self.root, self.prepared, lambda: observed.append([
            (self.root / name / 'version').read_text() for name in self.prepared]))
        self.assertEqual(observed, [['new', 'new']])
        self.assertFalse(list(self.root.glob('*.previous-*')))

    def test_cancelled_setup_does_not_touch_existing_tools(self):
        with patch.object(tools, '_install', side_effect=ValueError('cancelled')):
            with self.assertRaisesRegex(ValueError, 'cancelled'):
                tools.install(root=self.root)
        self.assert_old_tools()
        # The lock is released after a failed or cancelled setup.
        with patch.object(tools, '_install', return_value='retry'):
            self.assertEqual(tools.install(root=self.root), 'retry')

    def test_concurrent_setup_is_rejected_before_installation(self):
        with (self.root / '.setup.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(tools, '_install') as install:
                with self.assertRaisesRegex(ValueError, 'already running'):
                    tools.install(root=self.root)
                install.assert_not_called()

    def test_download_failure_keeps_installed_tools_and_metadata(self):
        sdk = self.root / 'dotnet'
        sdk.mkdir()
        (sdk / 'dotnet').touch()
        (self.root / 'versions.json').write_text('old metadata')
        with patch.object(tools.platform, 'machine', return_value='x86_64'), \
                patch.object(tools.shutil, 'which', return_value='/bin/7z'), \
                patch.object(tools, 'download', side_effect=OSError('network failure')):
            with self.assertRaises(OSError):
                tools.install(root=self.root)
        self.assert_old_tools()
        self.assertEqual((self.root / 'versions.json').read_text(), 'old metadata')
        self.assertFalse(list(self.root.glob('setup-*')))

    def test_cancelled_build_terminates_subprocess(self):
        process = subprocess.Popen
        launched = []
        def start(*args, **kwargs):
            child = process(*args, **kwargs)
            launched.append(child)
            return child
        def cancel():
            raise ValueError('cancelled')
        with patch.object(tools.subprocess, 'Popen', side_effect=start):
            with self.assertRaisesRegex(ValueError, 'cancelled'):
                tools.run_command([sys.executable, '-c', 'import time; time.sleep(30)'], cancel)
        self.assertIsNotNone(launched[0].poll())

    def test_failed_build_reports_failure(self):
        with self.assertRaisesRegex(ValueError, 'build-failed'):
            tools.run_command([sys.executable, '-c', 'import sys; print("build-failed"); sys.exit(1)'], lambda: None)
