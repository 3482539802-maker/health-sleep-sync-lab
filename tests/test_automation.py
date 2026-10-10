"""Fabricated fixtures: random plans, midnight shifts, scope and failure recovery."""
import copy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from sleep_sync_lab.automation_model import build_plan, describe, generated_stages, validate_automation_plan
from sleep_sync_lab.automation import execute, save
from sleep_sync_lab.model import bounds

CONFIG = {'date': '2001-01-01', 'timezone': '+08:00'}
BASE = bounds(CONFIG)[0]


def health(begin, end, kind, key, value):
    return {'type': kind, 'deviceCode': 'FABRICATED', 'startTime': str(begin), 'endTime': str(end),
            'timeZone': '+0800', 'mergedFlag': 0,
            'samplePoints': [{'key': key, 'value': value, 'startTime': str(begin), 'endTime': str(end)}]}


def fixture():
    sleep = [health(BASE + (60 + i) * 60000, BASE + (61 + i) * 60000, 9,
                    ['PROFESSIONAL_SLEEP_SHALLOW', 'PROFESSIONAL_SLEEP_DEEP', 'PROFESSIONAL_SLEEP_DREAM'][i % 3], '') for i in range(300)]
    sport = [{'deviceCode': 'FABRICATED', 'startTime': str(BASE + m * 60000), 'endTime': str(BASE + (m + 1) * 60000),
              'sportType': 5, 'appType': 1, 'timeZone': '+0800', 'mergedFields': ['steps', 'calorie'],
              'sportBasicInfos': [{'steps': 50, 'calorie': 1000}]} for m in [720, 721, 1080, 1081]]
    total = {'recordDay': 20010101, 'sportType': 0, 'dataSource': 999, 'deviceCode': '0', 'timeZone': '+0800',
             'sportBasicInfo': {'steps': 200, 'calorie': 7000}, 'exerciseTimeBasic': {'totalMidHighIntensity': 2, 'intensityMap': {'1': 2}},
             'activeHourBasic': {'countActiveHour': 2}}
    return {'local_sleep': {'resultCode': 0, 'intervals': []}, 'sport': {'resultCode': 0, 'detailInfos': sport}, 'sport_stats': {'resultCode': 0, 'sportStat': [total]},
            'active': {'resultCode': 0, 'detailInfos': [health(BASE + h * 3600000, BASE + (h + 1) * 3600000 - 1, 200005, 'ACTIVE_HOUR', '{"isActive":1}') for h in [12, 18]]},
            'intensity': {'resultCode': 0, 'detailInfos': [health(BASE + m * 60000, BASE + (m + 1) * 60000, 12, 'EXERCISE_INTENSITY', '{"exerciseType":1}') for m in [720, 1080]]},
            'sleep': {'resultCode': 0, 'detailInfos': sleep}, 'sleep_stats': {'resultCode': 0, 'professionalSleepTotal': [{'recordDay': 20010101,
            'dataSource': 2, 'deviceCode': '0', 'timeZone': '+0800', 'professionalSleep': {'sleepScore': 72, 'allSleepTime': 300}}]}}


class FakeClient:
    state = None
    fail_upload = False
    calls = 0
    ignore_lower = False
    def __init__(self, config):
        self.config = dict(config)
        self.config.update(day_start_ms=BASE, day_end_ms=BASE + 86400000)
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def configure(self, **values): self.config.update(values)
    def snapshot(self): return copy.deepcopy(self.state)
    def local_sleep(self): return copy.deepcopy(self.state.get('local_sleep', {'resultCode': 0, 'intervals': []}))
    def upload_health(self, records):
        type(self).calls += 1
        if self.fail_upload:
            return {'resultCode': 1001}
        for r in records:
            key = {9: 'sleep', 12: 'intensity', 200005: 'active'}[r['type']]
            self.state[key]['detailInfos'].append(copy.deepcopy(r))
        return {'resultCode': 0}
    def upload_sport(self, records):
        type(self).calls += 1
        if self.fail_upload:
            return {'resultCode': 1001}
        lookup = {r['startTime']: r for r in records}
        self.state['sport']['detailInfos'] = [copy.deepcopy(lookup.get(r['startTime'], r)) for r in self.state['sport']['detailInfos']]
        return {'resultCode': 0}
    def sport_summary(self, value):
        target = copy.deepcopy(value)
        if self.ignore_lower:
            old = self.state['sport_stats']['sportStat'][0]
            target['exerciseTimeBasic']['totalMidHighIntensity'] = max(old['exerciseTimeBasic']['totalMidHighIntensity'], target['exerciseTimeBasic']['totalMidHighIntensity'])
        self.state['sport_stats']['sportStat'] = [target]
        return {'resultCode': 0}
    def sleep_summary(self, value):
        self.state['sleep_stats']['professionalSleepTotal'] = [copy.deepcopy(value)]
        return {'resultCode': 0}
    def clear_local_sleep(self, start, end): return {'localCleared': True, 'phoneLocalCleared': False}
    def delete_sleep(self, start, end):
        self.state['sleep']['detailInfos'] = []
        return {'resultCode': 0}
    def health_read(self, kind, start, end):
        key = {9: 'sleep', 12: 'intensity', 200005: 'active'}[kind]
        return {'resultCode': 0, 'detailInfos': [copy.deepcopy(r) for r in self.state[key]['detailInfos'] if int(r['startTime']) >= start and int(r['endTime']) <= end]}


class AutomationTests(unittest.TestCase):
    def test_seed_fixed_and_tampering_rejected(self):
        request = {'steps': [500, 900], 'calorie': [10, 20]}
        a = build_plan(fixture(), CONFIG, request, 42)
        self.assertEqual(a, build_plan(fixture(), CONFIG, request, 42))
        validate_automation_plan(a)
        a['sport'][0]['sportBasicInfos'][0]['steps'] += 1
        with self.assertRaises(ValueError): validate_automation_plan(a)

    def test_calorie_residual_retained_not_added_twice(self):
        p = build_plan(fixture(), CONFIG, {'calorie': 11}, 1)
        self.assertEqual(sum(r['sportBasicInfos'][0]['calorie'] for r in p['sport']), 8000)
        self.assertEqual(p['sport_summary']['sportBasicInfo']['calorie'], 11000)

    def test_steps_stay_in_existing_active_hours(self):
        p = build_plan(fixture(), CONFIG, {'steps': 700}, 1)
        self.assertEqual(sum(r['sportBasicInfos'][0]['steps'] for r in p['sport']), 700)
        self.assertTrue(all((int(r['startTime']) - BASE) // 3600000 in [12, 18] for r in p['sport']))

    def test_activity_only_adds_selected_hour(self):
        p = build_plan(fixture(), CONFIG, {'active': 3, 'active_hours': [19]}, 1)
        self.assertEqual(len(p['active']), 1)
        self.assertEqual(int(p['active'][0]['startTime']), BASE + 19 * 3600000)
        self.assertEqual(p['sport'], [])
        self.assertEqual(p['intensity'], [])

    def test_exercise_adds_unused_minutes_in_allowed_active_hours(self):
        p = build_plan(fixture(), CONFIG, {'exercise': 7}, 1)
        times = [int(r['startTime']) for r in p['intensity']]
        self.assertEqual(len(set(times)), 5)
        self.assertFalse(set(times) & {BASE + 720 * 60000, BASE + 1080 * 60000})
        self.assertEqual(sum(p['sport_summary']['exerciseTimeBasic']['intensityMap'].values()), 7)

    def test_lower_exercise_is_labelled_not_false_success(self):
        p = build_plan(fixture(), CONFIG, {'exercise': 1}, 1)
        self.assertEqual(p['intensity'], [])
        self.assertTrue(p['notices'])

    def test_shift_cross_midnight_preserves_stage_order(self):
        p = build_plan(fixture(), CONFIG, {'sleep': {'mode': 'shift', 'advance_minutes': 120}}, 1)
        self.assertEqual(p['sleep']['target_start'], BASE - 3600000)
        self.assertEqual([r['samplePoints'][0]['key'] for r in p['sleep']['target']], [r['samplePoints'][0]['key'] for r in fixture()['sleep']['detailInfos']])
        self.assertEqual(p['sleep']['summary']['professionalSleep']['sleepScore'], 0)

    def test_random_sleep_duration_stages_and_reproducible_windows(self):
        request = {'sleep': {'mode': 'random', 'bedtime_range': ['23:00', '01:00'], 'wake_range': ['07:00', '09:00'], 'duration_range': [420, 540]}}
        for seed in range(20):
            p = build_plan(fixture(), CONFIG, request, seed)
            s = p['sleep']
            self.assertTrue(420 <= len(s['target']) <= 540)
            self.assertTrue(BASE - 3600000 <= s['target_start'] <= BASE + 3600000)
            self.assertTrue(BASE + 7 * 3600000 <= s['target_end'] <= BASE + 9 * 3600000)
            summary = s['summary']['professionalSleep']
            self.assertEqual(sum(summary[k] for k in ['lightSleepTime', 'deepSleepTime', 'dreamTime']), len(s['target']))
            self.assertTrue(s['synthetic_not_measured'])

    def test_impossible_windows_stop_before_writes(self):
        with self.assertRaises(ValueError):
            build_plan(fixture(), CONFIG, {'sleep': {'mode': 'random', 'bedtime_range': ['01:00', '01:30'], 'wake_range': ['02:00', '02:30'], 'duration_range': [420, 540]}}, 1)

    def test_local_tail_is_in_reviewed_delete_scope(self):
        f = fixture()
        f['local_sleep'] = {'resultCode': 0, 'intervals': [{'start': BASE + 3600000, 'end': BASE + 7 * 3600000}]}
        p = build_plan(f, CONFIG, {'sleep': {'mode': 'shift', 'advance_minutes': 30}}, 1)
        self.assertEqual(p['sleep']['delete_end'], BASE + 6 * 3600000)
        self.assertEqual(p['sleep']['local_delete_end'], BASE + 7 * 3600000)

    def test_another_local_session_rejected(self):
        f = fixture()
        f['local_sleep'] = {'resultCode': 0, 'intervals': [{'start': BASE + 3600000, 'end': BASE + 12 * 3600000}]}
        with self.assertRaises(ValueError): build_plan(f, CONFIG, {'sleep': {'mode': 'shift', 'advance_minutes': 30}}, 1)

    def execute_fixture(self, request, failed=False, sleep_authorized=False):
        f = fixture()
        FakeClient.state, FakeClient.fail_upload, FakeClient.calls = copy.deepcopy(f), failed, 0
        p = build_plan(f, CONFIG, request, 7)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'job' / 'plan_private.json'
            save(path, p)
            with patch('sleep_sync_lab.automation.AutomationClient', FakeClient):
                if failed:
                    with self.assertRaises(ValueError): execute(path)
                    self.assertTrue((path.parent / 'apply_started_private.json').exists())
                    self.assertEqual(FakeClient.calls, 1)
                    with self.assertRaises(ValueError): execute(path)
                    self.assertEqual(FakeClient.calls, 1)
                    return
                return execute(path, allow_sleep_delete=sleep_authorized)

    def test_failure_never_retries_writes_and_keeps_marker(self):
        self.execute_fixture({'active': 3, 'active_hours': [19]}, failed=True)

    def test_execution_reports_actual_totals(self):
        r = self.execute_fixture({'active': 3, 'active_hours': [19]})
        self.assertTrue(r['target_adopted']['active'])
        self.assertTrue(r['phone_verification_pending'])

    def test_success_response_but_ignored_lower_total_reported(self):
        FakeClient.ignore_lower = True
        try:
            result = self.execute_fixture({'exercise': 1})
            self.assertFalse(result['target_adopted']['exercise'])
            self.assertEqual(result['cloud_totals']['exercise'], 2)
        finally:
            FakeClient.ignore_lower = False

    def test_sleep_delete_rebuild_verified_in_batches(self):
        r = self.execute_fixture({'sleep': {'mode': 'shift', 'advance_minutes': 120}}, sleep_authorized=True)
        self.assertTrue(r['sleep_cloud_segments_match'])
        self.assertTrue(r['sleep_cloud_summary_match'])
        self.assertTrue(r['sleep_phone_local_deletion_not_guaranteed'])

    def test_missing_sleep_confirmation_does_not_write(self):
        f = fixture()
        p = build_plan(f, CONFIG, {'sleep': {'mode': 'shift', 'advance_minutes': 120}}, 7)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'plan_private.json'
            save(path, p)
            with patch('sleep_sync_lab.automation.AutomationClient') as native:
                with self.assertRaises(ValueError): execute(path)
                native.assert_not_called()


if __name__ == '__main__':
    unittest.main()
