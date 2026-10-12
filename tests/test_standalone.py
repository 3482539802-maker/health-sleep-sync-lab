"""Artificial empty-account uploads and conditional original-data workflows."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from sleep_sync_lab import automation, batch
from sleep_sync_lab.automation_model import describe, validate_automation_plan
from sleep_sync_lab.generation import generate, bind_plan
from sleep_sync_lab.panel import Panel
from sleep_sync_lab.presets import preset_request
from sleep_sync_lab.standalone import SOURCE, bind_source, empty_snapshot
from sleep_sync_lab.model import bounds
from test_automation import CONFIG, BASE, FakeClient, fixture
from test_natural_batch import SPEC
from test_offline_generation import RANDOM_SLEEP


class EmptyClient(FakeClient):
    sources_available = True
    deletes = 0
    def upload_source(self):
        return {'resultCode': 0, 'deviceCode': '123456', 'appType': 1,
                'sleepSource': {'deviceCode': '654321', 'appType': 1, 'deviceCategory': 42,
                                'sourceKind': 'registered_wearable'}} if self.sources_available else {'resultCode': 1}

    def upload_sport(self, records):
        type(self).calls += 1
        if self.fail_upload:
            return {'resultCode': 1}
        self.assert_resolved(records)
        from sleep_sync_lab.automation import sport_key
        updates = {sport_key(r): copy.deepcopy(r) for r in records}
        for row in self.state['sport']['detailInfos']:
            updates.setdefault(sport_key(row), row)
        self.state['sport']['detailInfos'] = list(updates.values())
        return {'resultCode': 0}

    @staticmethod
    def assert_resolved(records):
        assert all(str(r['deviceCode']) != SOURCE for r in records)

    def upload_health(self, records):
        self.assert_resolved(records)
        return super().upload_health(records)

    def clear_local_sleep(self, start, end):
        type(self).deletes += 1
        self.state['local_sleep']['intervals'] = []
        return {'localCleared': True}

    def retire_acknowledged_sleep_deletes(self, start, end):
        type(self).deletes += 1
        self.state['local_sleep'].update(dictionaryIntervals=[], pendingDictionaryDeletes=[],
                                        pendingDeletes=[], pendingSummaryDeletes=0)
        return {'resultCode': 0}


def fresh(request, seed=25):
    return generate(CONFIG, dict(request, planning_mode='standalone'), seed)


class EmptyMultiClient(EmptyClient):
    states = {}
    def __init__(self, config):
        self.config = dict(config)
        base, end = bounds(config)
        self.config.update(day_start_ms=base, day_end_ms=end)
        self.config.setdefault('sleep_query_start_ms', base - 43200000)
        self.config.setdefault('sleep_query_end_ms', base + 43200000 - 1)
        self.state = self.states[config['date']]


class StandaloneTests(unittest.TestCase):
    def setUp(self):
        EmptyClient.state = empty_snapshot()
        EmptyClient.calls = EmptyClient.deletes = 0
        EmptyClient.fail_upload = EmptyClient.ignore_lower = False
        EmptyClient.sources_available = True

    def run_job(self, request):
        with tempfile.TemporaryDirectory() as folder, patch('sleep_sync_lab.automation.AutomationClient', EmptyClient), patch('sleep_sync_lab.automation.time.sleep'):
            path = automation.prepare(CONFIG, request, Path(folder) / 'job')
            raw = path.read_bytes()
            result = automation.execute(path)
            self.assertEqual(path.read_bytes(), raw)
            self.assertTrue((path.parent / 'resolved_plan_private.json').exists())
            self.assertEqual(EmptyClient.deletes, 0)
            self.assertTrue(all(result['target_adopted'].values()))
            if request.get('sleep'):
                self.assertTrue(result['sleep_cloud_segments_match'])
                self.assertTrue(result['sleep_cloud_summary_match'])
            with self.assertRaisesRegex(ValueError, 'execution marker'):
                automation.execute(path)
            return result

    def test_all_empty_day_uploads_five_targets_without_bind_or_phone_deletion(self):
        result = self.run_job({'calorie': 350, 'exercise': 35, 'active': 12, 'steps': 14000, 'sleep': RANDOM_SLEEP})
        self.assertEqual(result['cloud_totals'], {'calorie': 350, 'exercise': 35, 'active': 12, 'steps': 14000})
        self.assertTrue(all(not c['compared'] for c in result['comparisons'].values()))
        self.assertEqual(sum(r['sportBasicInfos'][0].get('steps', 0) for r in EmptyClient.state['sport']['detailInfos']), 14000)
        self.assertEqual(sum(r['sportBasicInfos'][0].get('calorie', 0) for r in EmptyClient.state['sport']['detailInfos']), 350000)

    def test_sleep_uses_registered_wearable_without_original_sleep_template(self):
        plan = fresh({'steps': 10000, 'sleep': RANDOM_SLEEP})
        resolved = bind_source(plan, EmptyClient(CONFIG).upload_source())
        self.assertTrue(all(r['deviceCode'] == '123456' for r in resolved['sport']))
        self.assertTrue(all(r['deviceCode'] == '654321' for r in resolved['sleep']['target']))
        self.assertEqual(resolved['sleep']['summary']['professionalSleep']['deviceCategory'], 42)
        self.assertEqual(resolved['sleep']['target_start'], plan['sleep']['target_start'])
        self.assertEqual([r['samplePoints'] for r in resolved['sleep']['target']],
                         [r['samplePoints'] for r in plan['sleep']['target']])

    def test_phone_source_cannot_silently_fill_fresh_sleep(self):
        with self.assertRaisesRegex(ValueError, 'Registered sleep device'):
            bind_source(fresh({'sleep': RANDOM_SLEEP}), {'resultCode': 0, 'deviceCode': '123456', 'appType': 1})
        class PhoneOnly(EmptyClient):
            def upload_source(self):
                return {'resultCode': 0, 'deviceCode': '123456', 'appType': 1}
        with tempfile.TemporaryDirectory() as folder, patch('sleep_sync_lab.automation.AutomationClient', PhoneOnly):
            path = automation.prepare(CONFIG, {'steps': 10000, 'sleep': RANDOM_SLEEP}, Path(folder) / 'job')
            with self.assertRaisesRegex(ValueError, 'Registered sleep device'):
                automation.execute(path)
            self.assertEqual(EmptyClient.calls, 0)
            self.assertFalse((path.parent / 'apply_started_private.json').exists())

    def test_sleep_upload_checks_wearable_category_readback(self):
        result = self.run_job({'sleep': RANDOM_SLEEP})
        self.assertTrue(result['sleep_registered_source_match'])
        self.assertTrue(result['sleep_device_category_match'])

    def test_server_ignoring_wearable_category_is_not_reported_as_success(self):
        class IgnoresCategory(EmptyClient):
            def sleep_summary(self, value):
                changed = copy.deepcopy(value)
                changed['professionalSleep']['deviceCategory'] = 0
                return super().sleep_summary(changed)
        with tempfile.TemporaryDirectory() as folder, patch('sleep_sync_lab.automation.AutomationClient', IgnoresCategory), patch('sleep_sync_lab.automation.time.sleep'):
            path = automation.prepare(CONFIG, {'sleep': RANDOM_SLEEP}, Path(folder) / 'job')
            with self.assertRaisesRegex(ValueError, 'source/category not adopted'):
                automation.execute(path)
            self.assertTrue((path.parent / 'apply_started_private.json').exists())
            self.assertFalse((path.parent / 'result_private.json').exists())

    def test_default_preset_on_empty_day_generates_companion_steps_and_no_score_dependency(self):
        result = self.run_job(preset_request(SPEC))
        self.assertGreater(result['targets']['steps'], 0)
        self.assertEqual(EmptyClient.state['sleep_stats']['professionalSleepTotal'][0]['professionalSleep']['sleepScore'], 0)

    def test_sleep_only_empty_day_does_not_require_sport_summary(self):
        result = self.run_job({'sleep': RANDOM_SLEEP})
        self.assertEqual(result['cloud_totals'], dict.fromkeys(['calorie', 'exercise', 'active', 'steps']))
        self.assertEqual(EmptyClient.state['sport_stats']['sportStat'], [])

    def test_steps_only_empty_day_has_consistent_active_hours_and_bounded_minutes(self):
        self.run_job({'steps': 18000})
        rows = EmptyClient.state['sport']['detailInfos']
        self.assertTrue(all(0 < r['sportBasicInfos'][0]['steps'] <= 240 for r in rows))
        active = EmptyClient.state['active']['detailInfos']
        self.assertEqual(len(active), EmptyClient.state['sport_stats']['sportStat'][0]['activeHourBasic']['countActiveHour'])
        hours = {(int(r['startTime']) - BASE) // 3600000 for r in active}
        self.assertTrue(all((int(r['startTime']) - BASE) // 3600000 in hours for r in rows))

    def test_optional_comparison_uses_only_present_fields(self):
        state = empty_snapshot()
        state['sport_stats']['sportStat'] = [{'recordDay': 20010101, 'sportType': 0, 'sportBasicInfo': {'calorie': 100000}}]
        from sleep_sync_lab.standalone import resolve
        p = resolve(fresh({'calorie': 80, 'exercise': 30, 'active': 12, 'increase_only': True}), state)
        self.assertIsNone(p['targets']['calorie'])
        self.assertEqual(p['targets']['exercise'], 30)
        self.assertEqual(p['targets']['active'], 12)
        self.assertTrue(p['comparisons']['calorie']['compared'])
        self.assertFalse(p['comparisons']['exercise']['compared'])
        self.assertEqual(p['sport_summary']['sportBasicInfo']['calorie'], 100000)

    def test_missing_summary_and_templates_does_not_block_each_ring(self):
        for field, target in [('calorie', 150), ('exercise', 30), ('active', 10)]:
            self.setUp()
            self.run_job({field: target})

    def test_shift_and_score_options_require_originals_but_random_does_not(self):
        for spec in [{'mode': 'shift', 'advance_minutes': 30}, dict(RANDOM_SLEEP, restore_original_score=True)]:
            draft = fresh({'sleep': spec})
            self.assertEqual(draft['format_version'], 3)
            with self.assertRaises(ValueError):
                bind_plan(draft, empty_snapshot())
        self.assertEqual(fresh({'sleep': RANDOM_SLEEP})['format_version'], 5)

    def test_random_score_can_use_summary_without_any_old_sleep_template(self):
        original = empty_snapshot()
        original['sleep_stats']['professionalSleepTotal'] = [{'recordDay': 20010101, 'professionalSleep': {'sleepScore': 70}}]
        bound = bind_plan(fresh({'sleep': dict(RANDOM_SLEEP, restore_original_score=True)}), original)
        self.assertEqual(bound['sleep']['summary']['professionalSleep']['sleepScore'], 70)
        validate_automation_plan(bound)

    def test_conflicting_sleep_stops_before_any_sport_write_or_marker(self):
        EmptyClient.state['sleep'] = fixture()['sleep']
        with tempfile.TemporaryDirectory() as folder, patch('sleep_sync_lab.automation.AutomationClient', EmptyClient):
            path = automation.prepare(CONFIG, {'steps': 14000, 'sleep': RANDOM_SLEEP}, Path(folder) / 'job')
            with self.assertRaisesRegex(ValueError, 'Existing sleep requires backup'):
                automation.execute(path)
            self.assertEqual(EmptyClient.calls, 0)
            self.assertFalse((path.parent / 'apply_started_private.json').exists())

    def test_unregistered_source_and_failed_queries_stop_without_started_marker(self):
        for fault in ['source', 'query']:
            self.setUp()
            if fault == 'source': EmptyClient.sources_available = False
            else: EmptyClient.state['sport']['resultCode'] = 1
            with tempfile.TemporaryDirectory() as folder, patch('sleep_sync_lab.automation.AutomationClient', EmptyClient), patch('sleep_sync_lab.automation.time.sleep'):
                path = automation.prepare(CONFIG, {'steps': 10000}, Path(folder) / 'job')
                with self.assertRaises(ValueError): automation.execute(path)
                self.assertEqual(EmptyClient.calls, 0)
                self.assertFalse((path.parent / 'apply_started_private.json').exists())

    def test_preview_tampering_rejected_and_source_resolution_keeps_large_integer_exact(self):
        p = fresh({'steps': 10000})
        validate_automation_plan(p)
        code = '9007199254740993'
        resolved = bind_source(p, {'resultCode': 0, 'deviceCode': code, 'appType': 1})
        self.assertTrue(all(r['deviceCode'] == code for r in resolved['sport']))
        p['sport'][0]['sportBasicInfos'][0]['steps'] += 1
        with self.assertRaises(ValueError): validate_automation_plan(p)

    def test_generated_activity_stays_awake_and_steps_bounded_across_seeds(self):
        request = {'calorie': 450, 'exercise': 40, 'active': 12, 'steps': 24000, 'sleep': RANDOM_SLEEP}
        for seed in range(30):
            p = fresh(request, seed)
            sleep = p['sleep']
            for domain in ['sport', 'intensity', 'active']:
                self.assertTrue(all(int(r['endTime']) <= sleep['target_start'] or int(r['startTime']) >= sleep['target_end'] for r in p[domain]))
            self.assertEqual(len(p['intensity']), 40)
            self.assertEqual(len(p['active']), 12)
            self.assertTrue(all(r['sportBasicInfos'][0].get('steps', 0) <= 240 for r in p['sport']))

    def test_panel_direct_execution_does_not_route_to_original_data_step(self):
        with tempfile.TemporaryDirectory() as folder:
            path = automation.prepare(CONFIG, {'sleep': RANDOM_SLEEP}, Path(folder) / 'job')
            panel = object.__new__(Panel)
            panel.plan_path, panel.busy = path, False
            panel.plan_inputs = {'artificial': True}
            panel.form_values = lambda: panel.plan_inputs
            panel.delete_confirm = Mock(get=lambda: False)
            panel.backup_plan, panel.task = Mock(), Mock()
            panel.apply()
            panel.backup_plan.assert_not_called()
            panel.task.assert_called_once()
            self.assertFalse(describe(automation.load(path))['execution_backup_required'])

    def test_optional_binding_empty_date_still_executes_without_phone_confirmation(self):
        with tempfile.TemporaryDirectory() as folder, patch('sleep_sync_lab.automation.AutomationClient', EmptyClient), patch('sleep_sync_lab.automation.time.sleep'):
            original = automation.prepare(CONFIG, {'sleep': RANDOM_SLEEP}, Path(folder) / 'job')
            bound = automation.bind(original)
            self.assertEqual(automation.load(bound)['format_version'], 6)
            automation.execute(bound)
            with self.assertRaisesRegex(ValueError, 'execution marker'): automation.execute(original)
            with self.assertRaisesRegex(ValueError, 'execution marker'): automation.bind(original)

    def test_shift_keeps_original_stage_and_score_backup_after_phone_deletion(self):
        state = fixture()
        for key in ['sleep', 'sport', 'intensity', 'active']:
            for row in state[key]['detailInfos']: row['deviceCode'] = '654321'
        EmptyClient.state = state
        with tempfile.TemporaryDirectory() as folder, patch('sleep_sync_lab.automation.AutomationClient', EmptyClient), patch('sleep_sync_lab.automation.time.sleep'):
            draft = automation.prepare(CONFIG, {'sleep': {'mode': 'shift', 'advance_minutes': 60, 'restore_original_score': True}}, Path(folder) / 'job')
            bound = automation.bind(draft)
            EmptyClient.state['sleep']['detailInfos'] = []
            EmptyClient.state['sleep_stats']['professionalSleepTotal'] = []
            result = automation.execute(bound, True, phone_deleted=True)
            self.assertTrue(result['sleep_cloud_summary_match'])
            self.assertEqual(EmptyClient.state['sleep_stats']['professionalSleepTotal'][0]['professionalSleep']['sleepScore'], 72)

    def test_sparse_original_sport_can_be_extended_without_overwriting_other_fields(self):
        EmptyClient.state = fixture()
        EmptyClient.state['sleep']['detailInfos'] = []
        EmptyClient.state['sleep_stats']['professionalSleepTotal'] = []
        old_calorie = {r['startTime']: r['sportBasicInfos'][0]['calorie'] for r in EmptyClient.state['sport']['detailInfos']}
        self.run_job({'steps': 14000})
        for row in EmptyClient.state['sport']['detailInfos']:
            if row['startTime'] in old_calorie:
                self.assertEqual(row['sportBasicInfos'][0]['calorie'], old_calorie[row['startTime']])

    def test_batch_empty_days_preflights_and_uploads_without_bind_or_deletion(self):
        days = ['2001-01-01', '2001-01-02']
        EmptyMultiClient.states = {day: empty_snapshot() for day in days}
        with tempfile.TemporaryDirectory() as folder, patch('sleep_sync_lab.batch.AutomationClient', EmptyMultiClient), patch('sleep_sync_lab.automation.AutomationClient', EmptyMultiClient), patch('sleep_sync_lab.automation.time.sleep'):
            path = batch.prepare_batch(CONFIG, preset_request(SPEC), *days, Path(folder) / 'batch')
            result = batch.execute_batch(path)
            self.assertEqual(result['completed_dates'], days)
            self.assertTrue(all(all(d['target_adopted'].values()) for d in result['days']))

    def test_batch_later_conflict_blocks_first_day_before_any_write(self):
        days = ['2001-01-01', '2001-01-02']
        EmptyMultiClient.states = {day: empty_snapshot() for day in days}
        EmptyMultiClient.states[days[1]]['sleep_stats']['professionalSleepTotal'] = [{'recordDay': 20010102}]
        with tempfile.TemporaryDirectory() as folder, patch('sleep_sync_lab.batch.AutomationClient', EmptyMultiClient), patch('sleep_sync_lab.batch.execute') as apply:
            path = batch.prepare_batch(CONFIG, preset_request(SPEC), *days, Path(folder) / 'batch')
            with self.assertRaises(ValueError): batch.execute_batch(path)
            apply.assert_not_called()
            self.assertFalse(list(path.parent.rglob('*started*')))

    def test_bound_sleep_requires_confirmation_if_original_summary_existed(self):
        EmptyClient.state['sleep_stats']['professionalSleepTotal'] = [{'recordDay': 20010101, 'professionalSleep': {'sleepScore': 50}}]
        with tempfile.TemporaryDirectory() as folder, patch('sleep_sync_lab.automation.AutomationClient', EmptyClient):
            path = automation.bind(automation.prepare(CONFIG, {'sleep': RANDOM_SLEEP}, Path(folder) / 'job'))
            EmptyClient.state['sleep_stats']['professionalSleepTotal'] = []
            with self.assertRaisesRegex(ValueError, 'Phone night deletion'):
                automation.execute(path)
            self.assertEqual(EmptyClient.calls, 0)
            self.assertFalse((path.parent / 'apply_started_private.json').exists())


if __name__ == '__main__':
    unittest.main()
