"""Regression cases for late deletes, stale tails, source clearing and PC isolation."""
import copy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch
from sleep_sync_lab.automation import execute, save, settings
from sleep_sync_lab.automation_model import build_plan
from sleep_sync_lab.automation_client import AutomationClient
from test_automation import fixture, CONFIG, BASE, FakeClient


class RecurrenceTests(unittest.TestCase):
    def run_sleep(self, client=FakeClient, phone_deleted=True, empty=True):
        f = fixture()
        p = build_plan(f, settings(CONFIG), {'sleep': {'mode': 'shift', 'advance_minutes': 120}}, 7)
        FakeClient.state = copy.deepcopy(f)
        FakeClient.calls, FakeClient.fail_upload = 0, False
        if empty:
            FakeClient.state['sleep']['detailInfos'] = []
            FakeClient.state['sleep_stats']['professionalSleepTotal'] = []
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'job' / 'plan_private.json'
            save(path, p)
            with patch('sleep_sync_lab.automation.AutomationClient', client), patch('sleep_sync_lab.automation.time.sleep'):
                return execute(path, allow_sleep_delete=True, phone_deleted=phone_deleted)

    def test_phone_confirmation_required_even_with_delete_permission(self):
        with self.assertRaisesRegex(ValueError, 'Phone night deletion'):
            self.run_sleep(phone_deleted=False)
        self.assertEqual(FakeClient.calls, 0)

    def test_cloud_not_empty_blocks_local_clear_and_upload(self):
        with patch.object(FakeClient, 'clear_local_sleep') as clear:
            with self.assertRaisesRegex(ValueError, 'empty cloud'):
                self.run_sleep(empty=False)
            clear.assert_not_called()
        self.assertEqual(FakeClient.calls, 0)

    def test_cloud_summary_alone_blocks_rebuild(self):
        class OldSummary(FakeClient):
            def snapshot(self):
                result = super().snapshot()
                result['sleep_stats'] = fixture()['sleep_stats']
                return result
        with self.assertRaisesRegex(ValueError, 'empty cloud'):
            self.run_sleep(OldSummary)
        self.assertEqual(FakeClient.calls, 0)

    def test_local_true_response_does_not_hide_remaining_old_tail(self):
        class FalseClear(FakeClient):
            def local_sleep(self):
                result = super().local_sleep()
                result['intervals'] = [{'start': BASE + 3600000, 'end': BASE + 6 * 3600000}]
                return result
            def clear_local_sleep(self, start, end):
                self.state['local_sleep']['intervals'] = [{'start': start, 'end': end}]
                return {'localCleared': True}
        with self.assertRaisesRegex(ValueError, 'PC old sleep remains'):
            self.run_sleep(FalseClear)
        self.assertEqual(FakeClient.calls, 0)

    def test_pending_queue_in_any_sleep_table_blocks_rebuild(self):
        for field, value in [('pendingDeletes', [{'start': BASE}]), ('pendingSummaryDeletes', 1),
                             ('pendingDictionaryDeletes', [{'start': BASE}])]:
            class NotSettled(FakeClient):
                def retire_acknowledged_sleep_deletes(self, start, end):
                    self.state['local_sleep'][field] = value
                    return {'resultCode': 0}
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'Pending sleep deletions'):
                self.run_sleep(NotSettled)
            self.assertEqual(FakeClient.calls, 0)

    def test_old_tail_outside_target_is_detected(self):
        class TailReturns(FakeClient):
            def sleep_summary(self, value):
                result = super().sleep_summary(value)
                self.state['sleep']['detailInfos'].append(copy.deepcopy(fixture()['sleep']['detailInfos'][-1]))
                return result
        with self.assertRaisesRegex(ValueError, 'old tail returned'):
            self.run_sleep(TailReturns)

    def test_rebuild_does_not_send_another_cloud_delete(self):
        with patch.object(FakeClient, 'delete_sleep') as delete:
            result = self.run_sleep()
            delete.assert_not_called()
            self.assertTrue(result['sleep_cloud_segments_match'])

    def test_main_process_respawn_with_same_name_is_rejected_by_pid(self):
        c = AutomationClient(dict(CONFIG, package='com.huawei.health', serial='emulator-5554'))
        c.spawn_device = Mock()
        c.guarded_pids = {1}
        c.spawn_device.enumerate_processes.return_value = [SimpleNamespace(name='com.huawei.health', pid=2)]
        with self.assertRaisesRegex(ValueError, 'Unguarded app process'):
            c.assert_guarded()

    def test_new_health_process_kept_paused_other_app_resumed(self):
        c = AutomationClient(dict(CONFIG, package='com.huawei.health', serial='emulator-5554'))
        c.spawn_device = Mock()
        c.on_spawn(SimpleNamespace(identifier='com.huawei.health:NewService', pid=10))
        c.spawn_device.resume.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'new app process was paused'):
            c.assert_guarded()
        c.on_spawn(SimpleNamespace(identifier='example.unrelated', pid=11))
        c.spawn_device.resume.assert_called_once_with(11)

    def test_exit_retains_network_block_when_quarantine_install_fails(self):
        c = AutomationClient(dict(CONFIG, package='com.huawei.health', process='com.huawei.health:DaemonService', serial='emulator-5554'))
        c.validated = True
        c.adb = Mock()
        c.set_quarantine = Mock(side_effect=ValueError('isolation failed'))
        c.set_offline = Mock()
        with self.assertRaisesRegex(ValueError, 'isolation failed'):
            c.__exit__()
        c.set_offline.assert_called_once_with(True)

    def test_exit_stops_app_then_installs_verified_quarantine(self):
        c = AutomationClient(dict(CONFIG, package='com.huawei.health', serial='emulator-5554'))
        c.validated = True
        order = []
        c.adb = Mock(side_effect=lambda *a: order.append('stop'))
        def isolate(enabled):
            order.append('isolate')
            c.quarantine_confirmed = enabled
        c.set_quarantine = isolate
        c.set_offline = Mock(side_effect=lambda enabled: order.append('remove-temporary'))
        c.__exit__()
        self.assertEqual(order, ['stop', 'isolate', 'remove-temporary'])
