"""Offline generation and later read-only binding, with artificial data only."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from sleep_sync_lab.automation import bind, execute, prepare as prepare_new, load, save
from sleep_sync_lab.automation_model import describe, validate_automation_plan
from sleep_sync_lab.batch import prepare_batch as prepare_batch_new, bind_batch, validate_batch, execute_batch
from sleep_sync_lab.generation import generate, bind_plan
from sleep_sync_lab.panel import Panel
from sleep_sync_lab.presets import preset_request
from test_automation import CONFIG, BASE, FakeClient, fixture
from test_natural_batch import SPEC, MultiClient, shifted_fixture


RANDOM_SLEEP = {'mode': 'random', 'stage_model': 'natural_v2',
                'bedtime_range': ['23:00', '01:00'], 'wake_range': ['07:00', '09:00'],
                'duration_range': [420, 540]}


# These regression tests exercise saved v3/v4 behavior explicitly.
def prepare(config, request, *args, **kwargs):
    return prepare_new(config, dict(request, planning_mode='legacy'), *args, **kwargs)


def prepare_batch(config, request, *args, **kwargs):
    return prepare_batch_new(config, dict(request, planning_mode='legacy'), *args, **kwargs)


class OfflineTests(unittest.TestCase):
    def setUp(self):
        FakeClient.state = copy.deepcopy(fixture())
        FakeClient.calls, FakeClient.fail_upload, FakeClient.ignore_lower = 0, False, False

    def test_prepare_without_native_or_backup_keeps_fixed_targets_and_stages(self):
        request = {'calorie': [10, 20], 'steps': [500, 800], 'sleep': RANDOM_SLEEP}
        with tempfile.TemporaryDirectory() as folder, patch('sleep_sync_lab.automation.AutomationClient') as native:
            path = prepare(CONFIG, request, Path(folder) / 'job')
            native.assert_not_called()
            plan = load(path)
            self.assertEqual(plan['format_version'], 3)
            self.assertNotIn('baseline', plan)
            self.assertFalse((path.parent / 'backup_private.json').exists())
            self.assertFalse(list(path.parent.glob('*started*')))
            validate_automation_plan(plan)
            self.assertTrue(420 <= len(plan['sleep']['sequence']) <= 540)
            self.assertTrue(describe(plan)['offline_generated'])

    def test_bound_random_matches_preview_and_preserves_all_generation_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = prepare(CONFIG, {'steps': [500, 800], 'sleep': RANDOM_SLEEP}, Path(folder) / 'job')
            old = {p: p.read_bytes() for p in path.parent.glob('*') if p.is_file()}
            draft = load(path)
            with patch('sleep_sync_lab.automation.AutomationClient', FakeClient):
                bound = bind(path)
            self.assertEqual(FakeClient.calls, 0)
            plan = load(bound)
            self.assertEqual(plan['targets'], draft['targets'])
            self.assertEqual(plan['sleep']['target_start'], draft['sleep']['target_start'])
            self.assertEqual(plan['sleep']['target_end'], draft['sleep']['target_end'])
            self.assertEqual([r['samplePoints'][0]['key'] for r in plan['sleep']['target']], draft['sleep']['sequence'])
            validate_automation_plan(plan)
            for p, raw in old.items():
                self.assertEqual(p.read_bytes(), raw)
            self.assertTrue((bound.parent / 'backup_private.json').exists())

    def test_generation_tamper_and_bound_tamper_rejected(self):
        draft = generate(CONFIG, {'sleep': RANDOM_SLEEP}, 11)
        draft['sleep']['sequence'][0] = 'PROFESSIONAL_SLEEP_DEEP'
        with self.assertRaises(ValueError): validate_automation_plan(draft)
        plan = bind_plan(generate(CONFIG, {'steps': 500}, 11), fixture())
        plan['sport'][0]['sportBasicInfos'][0]['steps'] += 1
        with self.assertRaises(ValueError): validate_automation_plan(plan)

    def test_shift_can_generate_offline_but_needs_original_when_binding(self):
        draft = generate(CONFIG, {'sleep': {'mode': 'shift', 'advance_minutes': [30, 90]}}, 7)
        review = describe(draft)
        self.assertTrue(review['sleep']['original_required'])
        plan = bind_plan(draft, fixture())
        self.assertEqual(plan['sleep']['target_start'], BASE + 3600000 - draft['sleep']['advance_minutes'] * 60000)
        empty = fixture()
        empty['sleep']['detailInfos'] = []
        with self.assertRaisesRegex(ValueError, 'No records'): bind_plan(draft, empty)

    def test_direct_apply_offline_refused_before_connection_or_started_marker(self):
        with tempfile.TemporaryDirectory() as folder:
            path = prepare(CONFIG, {'steps': 500}, Path(folder) / 'job')
            with patch('sleep_sync_lab.automation.AutomationClient') as native:
                with self.assertRaisesRegex(ValueError, 'execution backup'): execute(path)
                native.assert_not_called()
            self.assertFalse(list(path.parent.glob('*started*')))

    def test_random_binding_after_phone_empty_does_not_require_old_sleep(self):
        empty = fixture()
        empty['sleep']['detailInfos'] = []
        empty['sleep_stats']['professionalSleepTotal'] = []
        draft = generate(CONFIG, {'sleep': RANDOM_SLEEP}, 5)
        plan = bind_plan(draft, empty)
        validate_automation_plan(plan)
        self.assertEqual(plan['sleep']['summary']['professionalSleep']['sleepScore'], 0)
        self.assertEqual(plan['baseline'], empty)
        self.assertTrue(all(r['deviceCode'] == '654321' for r in plan['sleep']['target']))
        self.assertEqual(plan['sleep']['local_delete_start'], BASE - 43200000)
        self.assertEqual(plan['sleep']['local_delete_end'], BASE + 43200000)

    def test_legacy_empty_sleep_without_wearable_category_stops_before_write(self):
        FakeClient.state['sleep']['detailInfos'] = []
        FakeClient.state['sleep_stats']['professionalSleepTotal'] = []
        with tempfile.TemporaryDirectory() as folder, patch('sleep_sync_lab.automation.AutomationClient', FakeClient), patch('sleep_sync_lab.automation.time.sleep'):
            bound = bind(prepare(CONFIG, {'sleep': RANDOM_SLEEP}, Path(folder) / 'job'))
            with self.assertRaisesRegex(ValueError, 'category missing'):
                execute(bound, True, phone_deleted=True)
            self.assertFalse((bound.parent / 'apply_started_private.json').exists())

    def test_missing_original_score_or_ambiguous_source_stops_binding(self):
        empty = fixture()
        empty['sleep']['detailInfos'] = []
        empty['sleep_stats']['professionalSleepTotal'] = []
        spec = dict(RANDOM_SLEEP, restore_original_score=True)
        with self.assertRaisesRegex(ValueError, 'Original score missing'):
            bind_plan(generate(CONFIG, {'sleep': spec}, 5), empty)
        empty['active']['detailInfos'][0]['deviceCode'] = 'OTHER_FABRICATED'
        with self.assertRaisesRegex(ValueError, 'No unique sleep source'):
            bind_plan(generate(CONFIG, {'sleep': RANDOM_SLEEP}, 5), empty)

    def test_whole_offline_bind_apply_sleep_lifecycle_and_replay_protection(self):
        with tempfile.TemporaryDirectory() as folder, patch('sleep_sync_lab.automation.AutomationClient', FakeClient), patch('sleep_sync_lab.automation.time.sleep'):
            path = prepare(CONFIG, {'sleep': RANDOM_SLEEP}, Path(folder) / 'job')
            bound = bind(path)
            FakeClient.state['sleep']['detailInfos'] = []
            FakeClient.state['sleep_stats']['professionalSleepTotal'] = []
            result = execute(bound, True, phone_deleted=True)
            self.assertTrue(result['sleep_cloud_segments_match'])
            self.assertTrue(result['sleep_cloud_summary_match'])
            with self.assertRaisesRegex(ValueError, 'execution marker'): execute(bound, True, phone_deleted=True)

    def test_bound_sport_baseline_changes_stop_before_writes(self):
        with tempfile.TemporaryDirectory() as folder, patch('sleep_sync_lab.automation.AutomationClient', FakeClient):
            bound = bind(prepare(CONFIG, {'steps': 500}, Path(folder) / 'job'))
            FakeClient.state['sport_stats']['sportStat'][0]['sportBasicInfo']['steps'] += 1
            with self.assertRaisesRegex(ValueError, 'Selected cloud data changed'): execute(bound)
            self.assertEqual(FakeClient.calls, 0)
            self.assertFalse((bound.parent / 'apply_started_private.json').exists())

    def test_multiple_bindings_cannot_execute_same_offline_generation_twice(self):
        with tempfile.TemporaryDirectory() as folder, patch('sleep_sync_lab.automation.AutomationClient', FakeClient):
            draft = prepare(CONFIG, {'steps': 500}, Path(folder) / 'job')
            first, second = bind(draft), bind(draft)
            execute(first)
            calls = FakeClient.calls
            with self.assertRaisesRegex(ValueError, 'execution marker'): bind(draft)
            with self.assertRaisesRegex(ValueError, 'execution marker'): execute(second)
            self.assertEqual(FakeClient.calls, calls)

    def test_preset_skips_lower_totals_only_at_binding_and_sleep_stays_fixed(self):
        draft = generate(CONFIG, preset_request(SPEC), 5)
        state = fixture()
        stat = state['sport_stats']['sportStat'][0]
        stat['sportBasicInfo']['calorie'] = 100000
        stat['exerciseTimeBasic']['totalMidHighIntensity'] = 100
        stat['activeHourBasic']['countActiveHour'] = 20
        bound = bind_plan(draft, state)
        self.assertTrue(all(v is None for v in bound['targets'].values()))
        self.assertEqual(bound['sampled_targets'], draft['targets'])
        self.assertEqual(bound['sleep']['target_start'], draft['sleep']['target_start'])
        self.assertEqual([r['samplePoints'][0]['key'] for r in bound['sleep']['target']], draft['sleep']['sequence'])

    def test_batch_generation_never_connects_and_cannot_apply_unbound(self):
        with tempfile.TemporaryDirectory() as folder, patch('sleep_sync_lab.batch.AutomationClient') as native, patch('sleep_sync_lab.automation.AutomationClient') as native2:
            path = prepare_batch(CONFIG, preset_request(SPEC), '2001-01-01', '2001-01-03', Path(folder) / 'batch')
            native.assert_not_called()
            native2.assert_not_called()
            manifest, plans = validate_batch(path)
            self.assertEqual(len(plans), 3)
            self.assertEqual(len({p['seed'] for _, p in plans}), 3)
            with self.assertRaisesRegex(ValueError, 'execution backup'): execute_batch(path, True, phone_deleted=True)
            native.assert_not_called()
            self.assertFalse(list(path.parent.rglob('*started*')))

    def test_panel_execute_offline_only_starts_backup_and_never_calls_write(self):
        with tempfile.TemporaryDirectory() as folder:
            path = prepare(CONFIG, {'sleep': RANDOM_SLEEP}, Path(folder) / 'job')
            panel = object.__new__(Panel)
            panel.plan_path, panel.busy = path, False
            panel.plan_inputs = {'artificial': True}
            panel.form_values = lambda: panel.plan_inputs
            panel.backup_plan = Mock()
            panel.task = Mock()
            panel.apply()
            panel.backup_plan.assert_called_once()
            panel.task.assert_not_called()


if __name__ == '__main__':
    unittest.main()
