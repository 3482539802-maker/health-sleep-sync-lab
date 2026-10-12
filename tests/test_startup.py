"""Launch failures stop before writes and provide safe diagnostics."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from sleep_sync_lab.automation_client import AutomationClient
from sleep_sync_lab.errors import PanelOperationError, user_message
from sleep_sync_lab.automation import execute, save
from test_automation import CONFIG, fixture
from sleep_sync_lab.automation_model import build_plan


class StartupTests(unittest.TestCase):
    def setUp(self):
        # These check-only cases must not read the developer's local startup profile.
        patcher = patch('sleep_sync_lab.automation_client.load_profile', return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def client(self):
        config = dict(CONFIG, serial='emulator-5554', package='com.huawei.health', adb='synthetic-adb')
        return AutomationClient(config)

    def test_missing_emulator_reports_connection_before_cpu_or_app_start(self):
        client = self.client()
        client.config['emulator_verified'] = True
        client.verify_adb = Mock()
        client.adb = Mock(side_effect=AssertionError('Hardware/app action must not start'))
        result = subprocess.CompletedProcess([], 0, b'List of devices attached\n\n', b'')
        with patch('sleep_sync_lab.automation_client.run_adb', return_value=result):
            with self.assertRaises(PanelOperationError) as caught: client.__enter__()
        message = user_message(caught.exception)
        self.assertIn('未连接', message)
        self.assertIn('生成计划可以离线', message)
        self.assertNotIn('CPU', message)
        client.adb.assert_not_called()
        self.assertFalse(client.lock_owned)

    def test_connection_never_falls_back_to_phone_or_other_emulator(self):
        client = self.client()
        result = subprocess.CompletedProcess([], 0, b'List of devices attached\nPRIVATE_PHONE\tdevice\nemulator-5556\tdevice\n', b'')
        with patch('sleep_sync_lab.automation_client.run_adb', return_value=result):
            with self.assertRaises(PanelOperationError) as caught: client.verify_connection()
        self.assertNotIn('PRIVATE_PHONE', user_message(caught.exception))
        self.assertEqual(client.config['serial'], 'emulator-5554')

    def test_connection_reports_offline_and_unauthorized_separately(self):
        for status, expected in [('offline', 'offline'), ('unauthorized', '尚未授权')]:
            result = subprocess.CompletedProcess([], 0, f'List of devices attached\nemulator-5554\t{status}\n'.encode(), b'')
            with patch('sleep_sync_lab.automation_client.run_adb', return_value=result):
                with self.assertRaises(PanelOperationError) as caught: self.client().verify_connection()
            self.assertIn(expected, user_message(caught.exception))

    def test_connection_accepts_only_configured_online_emulator(self):
        result = subprocess.CompletedProcess([], 0, b'List of devices attached\nemulator-5554\tdevice\n', b'')
        with patch('sleep_sync_lab.automation_client.run_adb', return_value=result) as adb:
            self.client().verify_connection()
        self.assertIsNone(adb.call_args.kwargs.get('serial'))

    def test_direct_resolved_activity_no_monkey_events(self):
        client = self.client()
        client.adb = Mock(side_effect=['priority=0\ncom.huawei.health/.MainActivity', 'Status: ok'])
        client.process_exists = Mock(return_value=True)
        client.launch_original()
        self.assertEqual(client.adb.call_args_list[-1].args, ('shell', 'am', 'start', '-W', '-n', 'com.huawei.health/.MainActivity'))
        self.assertFalse(any('monkey' in call.args for call in client.adb.call_args_list))

    def test_missing_launcher_never_starts_other_application(self):
        client = self.client()
        client.adb = Mock(return_value='other.application/.Activity')
        with self.assertRaises(PanelOperationError): client.launch_original()
        self.assertEqual(client.adb.call_count, 1)

    def test_raw_native_error_not_displayed(self):
        client = self.client()
        client.adb = Mock(side_effect=ValueError('FAKE_PRIVATE_PAYLOAD'))
        with self.assertRaises(PanelOperationError) as failure: client.launch_original()
        self.assertNotIn('FAKE_PRIVATE_PAYLOAD', user_message(failure.exception))
        self.assertEqual(user_message(ValueError('FAKE_PRIVATE_PAYLOAD')), '操作停止。请查看私有任务目录中的失败记录；已有写入标记的计划不要重复执行。')

    def test_daemon_not_ready_stops_before_attach(self):
        client = self.client()
        client.adb = Mock(side_effect=['com.huawei.health/.MainActivity', 'Status: ok'])
        client.process_exists = Mock(return_value=False)
        with patch('sleep_sync_lab.automation_client.time.sleep'):
            with self.assertRaises(PanelOperationError): client.launch_original()
        self.assertEqual(client.process_exists.call_count, 40)

    def test_root_readiness_retry_does_not_launch_or_write(self):
        client = self.client()
        client.adb = Mock(side_effect=[ValueError('ADB operation failed; inspect privately'), 'uid=0(root)'])
        with patch('sleep_sync_lab.automation_client.time.sleep'):
            client.verify_root()
        self.assertEqual(client.adb.call_count, 2)
        self.assertTrue(all(call.args == ('shell', 'su', '-c', 'id') for call in client.adb.call_args_list))

    def test_root_failure_keeps_all_attempts_and_stops(self):
        client = self.client()
        client.adb = Mock(side_effect=ValueError('PC emulator root required'))
        with patch('sleep_sync_lab.automation_client.time.sleep'):
            with self.assertRaises(PanelOperationError) as caught: client.verify_root()
        self.assertEqual(client.adb.call_count, 3)
        self.assertEqual(len(caught.exception.attempts), 3)

    def test_multiple_prewrite_failures_keep_each_diagnostic(self):
        plan = build_plan(fixture(), CONFIG, {'steps': 500}, 5)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'job' / 'plan_private.json'
            save(path, plan)
            with patch('sleep_sync_lab.automation.AutomationClient') as native:
                native.return_value.__enter__.side_effect = PanelOperationError('模拟启动失败，尚未写入。')
                for _ in range(2):
                    with self.assertRaises(PanelOperationError): execute(path)
            self.assertEqual(len(list(path.parent.glob('apply_failure_private*.json'))), 2)
            self.assertFalse((path.parent / 'apply_started_private.json').exists())


if __name__ == '__main__': unittest.main()
