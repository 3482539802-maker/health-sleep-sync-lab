"""Real prepare-to-apply lifecycle with artificial native storage; preserve old evidence."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from sleep_sync_lab.automation import prepare, execute, load, save
from sleep_sync_lab.errors import user_message
from test_automation import CONFIG, FakeClient, fixture


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        FakeClient.state = copy.deepcopy(fixture())
        FakeClient.fail_upload, FakeClient.ignore_lower, FakeClient.calls = False, False, 0

    def test_prepare_then_execute_preserves_both_guards(self):
        with tempfile.TemporaryDirectory() as folder, patch('sleep_sync_lab.automation.AutomationClient', FakeClient):
            plan = prepare(CONFIG, {'steps': 500}, Path(folder) / 'job')
            guard = plan.parent / 'prepare_session_guard_private.json'
            original = guard.read_bytes()
            result = execute(plan)
            self.assertTrue(result['target_adopted']['steps'])
            self.assertEqual(guard.read_bytes(), original)
            self.assertTrue((plan.parent / 'apply_session_guard_private.json').exists())

    def test_old_task_guard_and_before_apply_do_not_block_retry(self):
        with tempfile.TemporaryDirectory() as folder, patch('sleep_sync_lab.automation.AutomationClient', FakeClient):
            plan = prepare(CONFIG, {'steps': 500}, Path(folder) / 'job')
            # Historical preparation used this filename; never delete or overwrite it.
            guard = plan.parent / 'session_guard_private.json'
            save(guard, {'historical': True})
            original = {p: p.read_bytes() for p in [plan, guard]}
            FakeClient.state['sport_stats']['sportStat'][0]['sportBasicInfo']['steps'] = 201
            for attempt in range(2):
                with self.assertRaisesRegex(ValueError, 'Selected cloud data changed'):
                    execute(plan)
                self.assertFalse((plan.parent / 'apply_started_private.json').exists())
                self.assertEqual(FakeClient.calls, 0)
            snapshots = {p: p.read_bytes() for p in plan.parent.glob('before_apply_private*.json')}
            self.assertEqual(len(snapshots), 2)
            self.assertEqual(len(list(plan.parent.glob('apply_session_guard_private*.json'))), 2)
            FakeClient.state = copy.deepcopy(fixture())
            result = execute(plan)
            self.assertTrue(result['target_adopted']['steps'])
            for p, raw in {**original, **snapshots}.items():
                self.assertEqual(p.read_bytes(), raw)
            self.assertEqual(len(list(plan.parent.glob('before_apply_private*.json'))), 3)
            # A started plan remains nonrepeatable after fixing the logging collision.
            calls = FakeClient.calls
            with self.assertRaisesRegex(ValueError, 'execution marker'):
                execute(plan)
            self.assertEqual(FakeClient.calls, calls)

    def test_prepared_sleep_rebuild_after_phone_empty_keeps_prepare_evidence(self):
        with tempfile.TemporaryDirectory() as folder, patch('sleep_sync_lab.automation.AutomationClient', FakeClient), patch('sleep_sync_lab.automation.time.sleep'):
            plan = prepare(CONFIG, {'sleep': {'mode': 'shift', 'advance_minutes': 60}}, Path(folder) / 'job')
            backup = (plan.parent / 'backup_private.json').read_bytes()
            FakeClient.state['sleep']['detailInfos'] = []
            FakeClient.state['sleep_stats']['professionalSleepTotal'] = []
            result = execute(plan, allow_sleep_delete=True, phone_deleted=True)
            self.assertTrue(result['sleep_cloud_segments_match'])
            self.assertTrue(result['sleep_cloud_summary_match'])
            self.assertEqual((plan.parent / 'backup_private.json').read_bytes(), backup)

    def test_file_collision_is_actionable_without_private_path(self):
        error = FileExistsError(17, 'already exists', '/PRIVATE_HEALTH_PATH')
        message = user_message(error)
        self.assertIn('同名文件', message)
        self.assertNotIn('PRIVATE_HEALTH_PATH', message)


if __name__ == '__main__':
    unittest.main()
