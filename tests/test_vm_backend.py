import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from source_support import ROOT
from autocrack_source import vm_backend


class VMTransportTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.shared = Path(folder.name) / 'library'
        self.shared.mkdir()
        self.game = self.shared / 'Game with spaces'
        self.game.mkdir()
        self.backend = Mock()
        self.backend.configuration.return_value = {'domain': 'test-vm', 'shared': str(self.shared)}
        self.agent = self.backend.Agent.return_value
        self.agent.rpc.return_value = dict(connected=True, protected=True)
        self.patch = patch.object(vm_backend, 'steam_backend', return_value=self.backend)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_shared_paths_include_spaces_but_reject_library_root_and_outside_games(self):
        self.assertEqual(vm_backend.shared_game_path(self.game, self.shared), '/mnt/standalone/Game with spaces')
        for directory in (self.shared, self.shared.parent):
            with self.assertRaises(ValueError):
                vm_backend.shared_game_path(directory, self.shared)

    def test_symlink_root_and_private_staging_are_rejected(self):
        alias = self.shared / 'alias'
        alias.symlink_to(self.game)
        private = self.shared / '.steam-vm-test'
        private.mkdir()
        for directory in (alias, private):
            with self.assertRaises(ValueError):
                vm_backend.shared_game_path(directory, self.shared)

    def test_submits_key_via_stdin_and_polls_result_without_native_tools(self):
        responses = [dict(JobId='dummy'), dict(done=False, message='Processing'),
                     dict(done=True, result=dict(Success=True, Message='Complete')), dict(removed=True)]
        self.agent.execute.side_effect = [(0, json.dumps(dict(ok=True, result=value)).encode(), b'') for value in responses]
        client = vm_backend.Client('/vm')
        request = dict(InstallDirectory=str(self.game), VmRoot='/vm', GenerateInfo=True,
                       AppId='123', ApiKey='a' * 32, TimeoutMinutes=5)
        with patch.object(vm_backend.time, 'sleep'):
            result = client.execute(request)
        self.assertTrue(result['Success'])
        args, payload = self.agent.execute.call_args_list[0].args
        self.assertNotIn('a' * 32, ' '.join(args))
        job = json.loads(payload)
        self.assertEqual(job['ApiKey'], 'a' * 32)
        self.assertEqual(job['GuestPath'], '/mnt/standalone/Game with spaces')
        self.assertNotIn('InstallDirectory', job)
        self.assertNotIn('VmRoot', job)
        self.agent.rpc.assert_called_once_with({'command': 'vpn_check'})

    def test_cancel_is_forwarded_and_waits_for_guest_rollback(self):
        request = dict(InstallDirectory=str(self.game), AppId='123', GenerateInfo=False, TimeoutMinutes=5)
        client = vm_backend.Client('/vm')
        with patch.object(client, 'boot'), patch.object(client, 'check_cancelled'), \
             patch.object(client, 'cancel', return_value=True), patch.object(client, 'command') as command:
            command.side_effect = [{}, {}, dict(done=True, result=dict(Success=False, Message='Cancelled.')), {}]
            with self.assertRaisesRegex(ValueError, 'Cancelled'):
                client.execute(request)
        self.assertEqual([call.args[0] for call in command.call_args_list][:3], ['start', 'cancel', 'status'])

    def test_disconnected_vpn_reports_actionable_error_before_starting_job(self):
        self.agent.rpc.side_effect = RuntimeError('NordVPN disconnected in the VM.')
        client = vm_backend.Client('/vm')
        with patch.object(client, 'boot'), patch.object(client, 'command') as command, \
             patch.object(vm_backend.time, 'monotonic', side_effect=[0, 1, 1, 181]):
            with self.assertRaisesRegex(ValueError, 'No game files were changed'):
                client.execute(dict(InstallDirectory=str(self.game), GenerateInfo=True))
            command.assert_not_called()
        self.assertEqual([call.args[0]['command'] for call in self.agent.rpc.call_args_list], ['vpn_check', 'vpn_connect', 'vpn_check'])

    def test_disconnected_vpn_connects_and_verifies_before_starting_job(self):
        self.agent.rpc.side_effect = [RuntimeError('Disconnected'), {}, dict(connected=True, protected=True)]
        client = vm_backend.Client('/vm')
        with patch.object(client, 'boot'), patch.object(client, 'command') as command:
            command.side_effect = [{}, dict(done=True, result=dict(Success=True, Message='Complete')), {}]
            result = client.execute(dict(InstallDirectory=str(self.game), GenerateInfo=True))
        self.assertTrue(result['Success'])
        self.assertEqual([call.args[0]['command'] for call in self.agent.rpc.call_args_list],
                         ['vpn_check', 'vpn_connect', 'vpn_check'])

    def test_waits_when_connect_returns_before_vpn_is_ready(self):
        client = vm_backend.Client('/vm')
        self.agent.rpc.side_effect = [RuntimeError('Starting'), RuntimeError('Still starting'),
                                     dict(connected=False), dict(connected=True, protected=False),
                                     dict(connected=True, protected=True)]
        with patch.object(vm_backend.time, 'sleep') as sleep:
            client.wait_for_vpn()
        self.assertTrue(sleep.called)
        self.assertEqual(self.agent.rpc.call_args_list[-1].args[0], {'command': 'vpn_check'})

    def test_legacy_restore_does_not_require_a_vm_or_native_tools(self):
        from autocrack_source.backup import BACKUP, digest, write_json
        backup = self.game / BACKUP
        backup.mkdir()
        (backup / 'original').write_bytes(b'original')
        (self.game / 'steam_api64.dll').write_bytes(b'applied')
        write_json(backup / 'manifest.json', dict(files=[dict(path='steam_api64.dll', backup='original',
            original=digest(backup / 'original'), applied=digest(self.game / 'steam_api64.dll'))]))
        with patch.object(vm_backend, 'Client', side_effect=AssertionError('VM should not be needed')):
            result = vm_backend.process(dict(InstallDirectory=str(self.game), Restore=True))
        self.assertTrue(result['Success'])
        self.assertEqual((self.game / 'steam_api64.dll').read_bytes(), b'original')
