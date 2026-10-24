import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
from zipfile import ZipFile
from PyQt6.QtWidgets import QApplication
from source_support import ROOT
from autocrack_source import container_backend as backend
from autocrack_source.plugin import Plugin
from autocrack_source.runner import process as dispatch
from test_vanilla import vanilla,manifest

APP=QApplication.instance() or QApplication([])


class ContainerTests(unittest.TestCase):
    def test_offline_worker_has_no_network_and_only_game_work_mounts(self):
        args=backend.container_args(Path('/games/Example'),Path('/tmp/private-work'),'playlite-autocrack-'+'a'*32)
        self.assertEqual(args[args.index('--network')+1],'none')
        self.assertIn('--read-only',args);self.assertIn('no-new-privileges',args)
        mounts=[args[i+1] for i,value in enumerate(args) if value=='--mount']
        self.assertEqual(len(mounts),2)
        self.assertNotIn('/var/run/docker.sock',str(args))
        self.assertNotIn('/home/wolfgang',str(args))

    def test_online_worker_uses_guarded_desktop_uid_arguments(self):
        network=Mock()
        network.user_probe_args.return_value=['run','--rm','--network','container:verified','--entrypoint','/bin/sh','--memory','64m','--pids-limit','32','gateway-image','-c','']
        with patch.object(backend,'downloader_module',return_value=Mock(IMAGE='gateway-image')):
            args=backend.container_args(Path('/games/Example'),Path('/tmp/work'),'playlite-autocrack-'+'b'*32,network)
        network.user_probe_args.assert_called_once()
        self.assertEqual(args[args.index('--network')+1],'container:verified')
        self.assertNotIn('host',args);self.assertNotIn('ApiKey',str(args))

    def test_restore_needs_neither_vm_nor_cli_image(self):
        with tempfile.TemporaryDirectory() as directory,patch('playlite_plugins.cracktools.guards.restore_game',return_value={'Success':True}) as restore,patch.object(backend,'require_cli') as ready:
            request={'InstallDirectory':directory,'Restore':True}
            self.assertTrue(backend.process(request)['Success'])
            restore.assert_called_once();ready.assert_not_called()

    def test_default_dispatch_uses_container_and_explicit_vm_is_retained(self):
        with patch('autocrack_source.container_backend.process',return_value='container') as native,patch('autocrack_source.vm_backend.process',return_value='vm') as vm:
            self.assertEqual(dispatch({}),'container');self.assertEqual(dispatch({'Backend':'vm'}),'vm')
        native.assert_called_once();vm.assert_called_once()

    def test_default_request_accepts_game_outside_the_vm_shared_library(self):
        from PyQt6.QtCore import QSettings
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'PLAYLITE_STEAM_BACKEND':'depot'}):
            settings=QSettings(str(Path(directory)/'settings.ini'),QSettings.Format.IniFormat)
            settings.setValue('generateInfo',False)
            plugin=Plugin()
            with patch.object(plugin,'settings',return_value=settings),patch('autocrack_source.vm_backend.Client') as vm:
                request=plugin.request({'InstallDirectory':directory,'MetadataIds':{'SteamMetadata':'123'}})
                self.assertEqual(request['Backend'],'container');vm.assert_not_called()

    def test_depot_download_baseline_matches_app_and_retained_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);retained=root/'.playlite-depot-manifests';retained.mkdir()
            (retained/'installed.json').write_text(json.dumps({'AppId':'123','Depots':{'1':'2'}}))
            (retained/'1_2.manifest').write_bytes(manifest())
            baseline=vanilla.installed_baseline(root,'123')
            self.assertEqual(baseline['Depots'],{'1':'2'})
            self.assertIn('steam_api64.dll',baseline['Files'])
            with self.assertRaises(ValueError):vanilla.installed_baseline(root,'999')

    def test_release_contains_container_entrypoint_and_build_recipe(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run([sys.executable,str(ROOT/'tools/build_release.py'),'--output',directory],check=True,capture_output=True)
            with ZipFile(Path(directory)/'plugin.zip') as archive:
                for name in ('container_backend.py','guest/container_entry.py','guest/Dockerfile','guest/vanilla.py'):
                    self.assertIn(name,archive.namelist())

    def test_container_failure_reports_redacted_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            child=Mock();child.poll.return_value=1;child.returncode=1
            def popen(args,**kwargs):
                kwargs['stdout'].write(b'Docker failed with key=secret-key');return child
            request={'InstallDirectory':directory,'AppId':'123','GenerateInfo':False,'ApiKey':'secret-key'}
            with patch.object(backend,'require_cli'),patch('autocrack_source.guest.vanilla.installed_baseline',return_value={'AppId':'123'}),patch.object(backend.subprocess,'Popen',side_effect=popen):
                with self.assertRaises(ValueError) as error:backend.process(request)
            self.assertNotIn('secret-key',str(error.exception));self.assertIn('[redacted]',str(error.exception))
