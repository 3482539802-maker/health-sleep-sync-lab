"""Guard snapshots tolerate retired companions while rejecting lost live guards."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from sleep_sync_lab.automation_client import AutomationClient
from test_automation import CONFIG


class GuardLifecycleTests(unittest.TestCase):
    def setUp(self):
        with patch('sleep_sync_lab.automation_client.load_profile', return_value=None):
            self.client = AutomationClient(dict(CONFIG, package='com.huawei.health', serial='emulator-5554'))
        self.daemon = SimpleNamespace(pid=1, name='com.huawei.health:DaemonService')
        self.main = SimpleNamespace(pid=2, name='com.huawei.health')
        self.client.spawn_device = Mock()
        self.client.spawn_device.enumerate_processes.return_value = [self.daemon, self.main]
        self.client.guarded_pids = {1, 2}
        self.client.script = Mock()
        self.client.script.exports_sync.auto_guard_state.return_value = {'blocked': {}}
        self.companion = Mock()
        self.companion.exports_sync.auto_guard_state.return_value = {'blocked': {}}
        self.client.guard_scripts = [self.companion]
        self.client.guard_script_pids = [2]

    def test_live_companion_guard_is_read(self):
        result = self.client.guard_state()
        self.assertEqual(result['other_processes'], [{'blocked': {}}])
        self.assertEqual(result['retired_process_pids'], [])

    def test_retired_companion_is_reported_without_calling_destroyed_script(self):
        self.client.spawn_device.enumerate_processes.return_value = [self.daemon]
        self.companion.exports_sync.auto_guard_state.side_effect = RuntimeError('script destroyed')
        result = self.client.guard_state()
        self.assertEqual(result['retired_process_pids'], [2])
        self.companion.exports_sync.auto_guard_state.assert_not_called()

    def test_companion_exiting_during_rpc_requires_pid_disappearance(self):
        state = [self.daemon, self.main]
        self.client.spawn_device.enumerate_processes.side_effect = lambda: state[:]
        def exit_during_rpc():
            state.remove(self.main)
            raise RuntimeError('script destroyed')
        self.companion.exports_sync.auto_guard_state.side_effect = exit_during_rpc
        result = self.client.guard_state()
        self.assertEqual(result['retired_process_pids'], [2])

    def test_destroyed_script_with_live_pid_remains_fatal(self):
        self.companion.exports_sync.auto_guard_state.side_effect = RuntimeError('script destroyed')
        with self.assertRaisesRegex(RuntimeError, 'script destroyed'):
            self.client.guard_state()

    def test_replacement_process_still_stops_snapshot(self):
        self.client.spawn_device.enumerate_processes.return_value = [self.daemon, SimpleNamespace(pid=3, name=self.main.name)]
        with self.assertRaisesRegex(ValueError, 'Unguarded app process'):
            self.client.guard_state()

    def test_incomplete_pid_mapping_is_rejected(self):
        self.client.guard_script_pids = []
        with self.assertRaisesRegex(ValueError, 'Guard process mapping incomplete'):
            self.client.guard_state()


if __name__ == '__main__':
    unittest.main()
