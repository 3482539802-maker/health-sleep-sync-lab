"""Regression cases for Windows launch failures and invalid user forms; artificial only."""
import copy
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from sleep_sync_lab.automation import prepare, load
from sleep_sync_lab.automation_client import AutomationClient
from sleep_sync_lab.client import Client
from sleep_sync_lab.diagnostics import record_incident
from sleep_sync_lab.errors import AdbOperationError, AdbLaunchError, PanelOperationError, user_message
from sleep_sync_lab.host_process import run_adb
from sleep_sync_lab.input_validation import (InputValidationError, number, hours, activity_windows, date_input,
                                            validate_request, validate_preset)
from sleep_sync_lab.panel import Panel, read_config
from test_automation import CONFIG
from test_natural_batch import SPEC


class LaunchRegressionTests(unittest.TestCase):
    def result(self, code=0, stdout=b'', stderr=b''):
        return subprocess.CompletedProcess([], code, stdout, stderr)

    def test_gui_child_has_no_console_and_independent_input(self):
        with patch('sleep_sync_lab.host_process.subprocess.run', return_value=self.result(stdout=b'ok')) as run:
            Client(dict(CONFIG, adb='fake-adb', serial='emulator-1234')).adb('shell', 'getprop', 'ro.product.model')
        options = run.call_args.kwargs
        self.assertEqual(options['stdin'], subprocess.DEVNULL)
        self.assertTrue(options['capture_output'])
        if os.name == 'nt':
            self.assertEqual(options['creationflags'], subprocess.CREATE_NO_WINDOW)

    def test_unsigned_and_signed_windows_codes_survive_safe_wrapping(self):
        for code in (0xc0000142, -1073741502):
            error = AdbOperationError(['shell', 'getprop', 'ro.product.model'], self.result(code, stderr=b'PRIVATE_NATIVE_CONTENT'))
            wrapped = PanelOperationError('generic wrapper')
            wrapped.__cause__ = error
            message = user_message(wrapped)
            self.assertIn('0xc0000142', message)
            self.assertIn('模拟器型号', message)
            self.assertNotIn('PRIVATE_NATIVE_CONTENT', message)

    def test_absent_pid_only_accepts_empty_exit_one(self):
        client = AutomationClient(dict(CONFIG, adb='fake-adb', serial='emulator-1234', process='com.huawei.health:DaemonService'))
        with patch('sleep_sync_lab.host_process.subprocess.run', return_value=self.result(1)):
            self.assertFalse(client.process_exists())
        for result in [self.result(0xc0000142), self.result(1, stderr=b'device offline')]:
            with patch('sleep_sync_lab.host_process.subprocess.run', return_value=result):
                with self.assertRaises(AdbOperationError):
                    client.process_exists()

    def test_adb_preflight_failure_never_stops_or_starts_health(self):
        client = AutomationClient(dict(CONFIG, adb='fake-adb', serial='emulator-1234'))
        client.adb = Mock()
        with patch('sleep_sync_lab.host_process.subprocess.run', return_value=self.result(0xc0000142)) as run:
            with self.assertRaises(AdbOperationError):
                client.__enter__()
        self.assertEqual(run.call_count, 1)
        client.adb.assert_not_called()
        self.assertFalse(client.validated)
        self.assertFalse(client.lock_owned)

    def test_critical_root_launch_failure_is_not_retried(self):
        client = AutomationClient(dict(CONFIG, serial='emulator-1234'))
        client.adb = Mock(side_effect=AdbOperationError(['shell', 'su', '-c', 'id'], self.result(0xc0000142)))
        with self.assertRaises(PanelOperationError) as caught:
            client.verify_root()
        self.assertEqual(client.adb.call_count, 1)
        self.assertIn('0xc0000142', user_message(caught.exception))

    def test_timeout_and_missing_executable_are_actionable(self):
        for error, word in [(subprocess.TimeoutExpired(['fake-adb'], 10), '超时'), (FileNotFoundError(2, 'not here'), '找不到ADB')]:
            with patch('sleep_sync_lab.host_process.subprocess.run', side_effect=error):
                with self.assertRaises(AdbLaunchError) as caught:
                    run_adb('fake-adb', ['version'], timeout=10)
            self.assertIn(word, user_message(caught.exception))

    def test_device_offline_message_does_not_print_private_output(self):
        message = user_message(AdbOperationError(['shell', 'getprop'], self.result(1, stderr=b'error: device offline PRIVATE_NATIVE_CONTENT')))
        self.assertIn('offline', message)
        self.assertNotIn('PRIVATE_NATIVE_CONTENT', message)

    def test_activity_stdout_error_stops_before_daemon_wait(self):
        client = AutomationClient(dict(CONFIG, serial='emulator-1234', package='com.huawei.health'))
        client.adb = Mock(side_effect=['com.huawei.health/.MainActivity', 'Error: activity not found'])
        client.process_exists = Mock()
        with self.assertRaises(PanelOperationError):
            client.launch_original()
        client.process_exists.assert_not_called()


class FieldPreflightTests(unittest.TestCase):
    def test_signed_shift_and_fullwidth_delimiters(self):
        self.assertEqual(number('-60--30', '提前分钟', -720, 720), [-60, -30])
        self.assertEqual(number('30～60', '锻炼分钟', 0, 1440), [30, 60])
        self.assertEqual(activity_windows('09：00-12：00，18：00-24：00'), '09:00-12:00,18:00-24:00')

    def test_bad_numbers_name_field_value_and_reason(self):
        for value, reason in [('34分钟', '不要填写单位'), ('4.5', '小数'), ('60-30', '最小值'), ('25', '0到24'), ([1], '两个整数')]:
            with self.assertRaises(InputValidationError) as caught:
                number(value, '活动小时', 0, 24)
            message = user_message(caught.exception)
            self.assertIn('活动小时', message)
            self.assertIn('当前填写', message)
            # 60-30 is outside the active bounds; test inversion separately below.
            if value != '60-30':
                self.assertIn(reason, message)
        with self.assertRaisesRegex(InputValidationError, '最小值'):
            number('60-30', '锻炼分钟', 0, 1440)

    def test_invalid_dates_hours_and_overlapping_windows(self):
        for value in ['2001-02-30', '01/01/2001']:
            with self.assertRaisesRegex(InputValidationError, '有效日期'):
                date_input(value)
        for value in ['24', '9,9', '9,17,', '9.5']:
            with self.assertRaises(InputValidationError):
                hours(value)
        with self.assertRaisesRegex(InputValidationError, '重叠'):
            activity_windows('09:00-12:00,11:00-13:00')
        with self.assertRaisesRegex(InputValidationError, '跨午夜'):
            activity_windows('22:00-02:00')

    def test_sleep_combinations_and_wrong_clock_rejected_offline(self):
        request = {'sleep': {'mode': 'random', 'bedtime_range': ['23:00','01:00'], 'wake_range': ['07:00','09:00'], 'duration_range': [420,540]}}
        validate_request(request)
        request['sleep']['bedtime_range'] = ['01:00','01:00']
        request['sleep']['wake_range'] = ['07:00','07:00']
        with self.assertRaisesRegex(InputValidationError, '没有可同时满足'):
            validate_request(request)
        request['sleep']['wake_range'] = ['07:60','08:00']
        with self.assertRaisesRegex(InputValidationError, '醒来区间'):
            validate_request(request)

    def test_invalid_preset_preferred_band_detected_before_sampling(self):
        spec = copy.deepcopy(SPEC)
        spec['exercise']['preferred'] = [0, 999]
        with self.assertRaisesRegex(InputValidationError, '锻炼分钟高概率区段'):
            validate_preset(spec)

    def test_invalid_form_never_opens_native_session_and_keeps_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / 'job'
            with patch('sleep_sync_lab.automation.AutomationClient') as native:
                with self.assertRaises(InputValidationError):
                    prepare(CONFIG, {'active': 25}, directory)
            native.assert_not_called()
            self.assertIn('活动小时', load(directory / 'prepare_failure_private.json')['message'])
            self.assertFalse(list(directory.glob('*started*')))
            self.assertFalse((directory / 'backup_private.json').exists())

    def test_config_json_error_names_line_and_column(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            path.write_text('{\n "fake": }', encoding='utf-8')
            with self.assertRaisesRegex(InputValidationError, '第2行第10列'):
                read_config(path)

    def test_private_incident_captures_early_failure_without_displaying_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            packet = record_incident(ValueError('PRIVATE_NATIVE_CONTENT'), folder, '输入检查', {'active': '25'})
            self.assertNotIn('PRIVATE_NATIVE_CONTENT', packet['message'])
            record = load(Path(packet['evidence']) / 'panel_failure_private.json')
            self.assertIn('PRIVATE_NATIVE_CONTENT', record['traceback'])
            self.assertEqual(record['inputs']['active'], '25')

    def test_edit_after_plan_generation_blocks_execution(self):
        panel = object.__new__(Panel)
        panel.plan_path, panel.busy = Path('artificial.json'), False
        panel.plan_inputs = {'active': '12'}
        panel.form_values = lambda: {'active': '15'}
        panel.root = None
        panel.task = Mock()
        with patch('sleep_sync_lab.panel.messagebox.showinfo') as info, patch('sleep_sync_lab.panel.load') as read:
            panel.apply()
        info.assert_called_once()
        read.assert_not_called()
        panel.task.assert_not_called()


if __name__ == '__main__':
    unittest.main()
