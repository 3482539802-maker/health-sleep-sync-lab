"""Verify diagnostic evidence and operation ordering using artificial data only."""
import copy
from datetime import datetime
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from sleep_sync_lab.audit import failure_details
from sleep_sync_lab.automation import execute, load, save
from sleep_sync_lab.automation_model import build_plan
from sleep_sync_lab.client import Client
from sleep_sync_lab.errors import AdbOperationError, PanelOperationError, user_message
from sleep_sync_lab.model import digest
from sleep_sync_lab.panel import Panel
from test_automation import CONFIG, FakeClient, fixture


class Variable:
    def __init__(self): self.value = None
    def set(self, value): self.value = value


class AuditTests(unittest.TestCase):
    def test_success_records_runtime_artifact_hashes_and_verified_order(self):
        FakeClient.state, FakeClient.fail_upload = copy.deepcopy(fixture()), False
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'job' / 'plan_private.json'
            save(path, build_plan(fixture(), CONFIG, {'active': 3, 'active_hours': [19]}, 7))
            with patch('sleep_sync_lab.automation.AutomationClient', FakeClient): execute(path)
            events = [json.loads(line) for line in (path.parent / 'events_private.jsonl').read_text(encoding='utf-8').splitlines()]
            names = [r['event'] for r in events]
            self.assertLess(names.index('baseline_verified'), names.index('operation_begin'))
            self.assertLess(names.index('operation_begin'), names.index('operation_response_ok'))
            self.assertLess(names.index('operation_response_ok'), names.index('readback_verified'))
            self.assertEqual(names[-1], 'job_lock_released')
            self.assertTrue(all(datetime.fromisoformat(r['time']).tzinfo for r in events))
            plan_event = next(r for r in events if r.get('file') == 'plan_private.json')
            self.assertEqual(plan_event['sha256'], digest(path))
            manifest = load(path.parent / 'apply_runtime_private.json')
            self.assertIn('automation.py', manifest['source_sha256'])

    def test_adb_details_survive_safe_wrapping_and_private_failure(self):
        response = subprocess.CompletedProcess([], 1, b'FAKE_STDOUT', b'FAKE_STDERR')
        with patch('sleep_sync_lab.client.subprocess.run', return_value=response):
            with self.assertRaises(AdbOperationError) as caught:
                Client(dict(CONFIG, adb='fake', serial='emulator-1234')).adb('shell', 'am', 'start')
        native = caught.exception
        wrapped = PanelOperationError('无法启动，尚未写入。')
        wrapped.__cause__ = native
        self.assertNotIn('FAKE_STDERR', user_message(wrapped))
        self.assertEqual(failure_details(wrapped)[1]['stderr'], 'FAKE_STDERR')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'job' / 'plan_private.json'
            save(path, build_plan(fixture(), CONFIG, {'steps': 500}, 7))
            with patch('sleep_sync_lab.automation.AutomationClient') as client:
                client.return_value.__enter__.side_effect = wrapped
                with self.assertRaises(PanelOperationError): execute(path)
            failure = load(path.parent / 'apply_failure_private.json')
            self.assertFalse(failure['writes_may_have_started'])
            self.assertEqual(failure['diagnostics'][1]['stdout'], 'FAKE_STDOUT')
            self.assertFalse((path.parent / 'apply_started_private.json').exists())

    def test_open_saved_sleep_restores_settings_and_resets_delete_confirmation(self):
        request = {'sleep': {'mode': 'random', 'bedtime_range': ['23:30', '00:30'],
                            'wake_range': ['07:00', '08:00'], 'duration_range': [420, 480],
                            'restore_original_score': True}, 'activity_windows': '18:00-24:00',
                   'preserve_active_hours': False, 'active_hours': [19]}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'plan_private.json'
            save(path, build_plan(fixture(), CONFIG, request, 7))
            panel = object.__new__(Panel)
            for key in ['date', 'config_path', 'preserve', 'sleep_enabled', 'sleep_mode', 'restore_score', 'delete_confirm', 'status']:
                setattr(panel, key, Variable())
            panel.vars = {key: Variable() for key in ['calorie', 'exercise', 'steps', 'active', 'activity_windows', 'active_hours', 'advance_minutes', 'bedtime_range', 'wake_range', 'duration_range']}
            panel.show_review = lambda review: None
            panel.restore_plan(path)
            self.assertTrue(panel.sleep_enabled.value)
            self.assertEqual(panel.sleep_mode.value, 'random')
            self.assertEqual(panel.vars['bedtime_range'].value, '23:30-00:30')
            self.assertEqual(panel.vars['duration_range'].value, '420-480')
            self.assertFalse(panel.delete_confirm.value)


if __name__ == '__main__': unittest.main()
