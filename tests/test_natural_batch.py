"""Artificial presets and dates only: sampling, exclusion and batch failure ordering."""
import copy
import random
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from sleep_sync_lab import batch
from sleep_sync_lab.automation import load
from sleep_sync_lab.automation_model import build_plan, validate_automation_plan, legacy_generated_stages
from sleep_sync_lab.model import bounds
from sleep_sync_lab.presets import preset_request, sample_preset
from sleep_sync_lab.sleep_generation import generated_stages, generated_stages_v1, stage_blocks, DEEP, REM, weighted_integer
from test_automation import CONFIG, BASE, FakeClient, fixture


SPEC = {'version': 1, 'calorie': {'range': [10, 50], 'preferred': [15, 25]},
        'exercise': {'range': [4, 20], 'preferred': [7, 12]},
        'active': {'range': [3, 7], 'preferred': [3, 5]},
        'bedtime': {'range': [0, 45], 'preferred': [20, 40]},
        'wake': {'range': [420, 570], 'preferred': [450, 510]},
        'duration_range': [380, 525], 'restore_original_score': True}


def shifted_fixture(day):
    f = fixture()
    base = bounds(dict(CONFIG, date=day))[0]
    offset = base - BASE
    for domain in ['sleep', 'sport', 'intensity', 'active']:
        for r in f[domain]['detailInfos']:
            for obj in [r] + r.get('samplePoints', []):
                for key in ['startTime', 'endTime']:
                    obj[key] = str(int(obj[key]) + offset)
    for domain, key in [('sport_stats', 'sportStat'), ('sleep_stats', 'professionalSleepTotal')]:
        f[domain][key][0]['recordDay'] = int(day.replace('-', ''))
    return f


class MultiClient(FakeClient):
    states = {}
    def __init__(self, config):
        self.config = dict(config)
        start, end = bounds(config)
        self.config.update(day_start_ms=start, day_end_ms=end)
        self.config.setdefault('sleep_query_start_ms', start - 43200000)
        self.config.setdefault('sleep_query_end_ms', start + 43200000 - 1)
        self.state = self.states[config['date']]


class NaturalTests(unittest.TestCase):
    def test_blocks_allow_brief_runs_and_preserve_architecture_trend(self):
        for minutes in [240, 360, 445, 480, 531, 720]:
            for seed in range(30):
                sequence = generated_stages(minutes, random.Random(seed))
                self.assertEqual(len(sequence), minutes)
                blocks = stage_blocks(sequence)
                self.assertGreaterEqual(min(b['end_minute'] - b['start_minute'] for b in blocks), 3)
                self.assertGreater(sequence[:minutes // 2].count(DEEP), sequence[minutes // 2:].count(DEEP))
                self.assertLess(sequence[:minutes // 2].count(REM), sequence[minutes // 2:].count(REM))
                self.assertEqual(sequence, generated_stages(minutes, random.Random(seed)))

    def test_short_runs_occur_and_long_runs_become_rare_without_changing_totals(self):
        sizes = []
        nights_with_short = 0
        for seed in range(500):
            old = generated_stages_v1(480, random.Random(seed))
            new = generated_stages(480, random.Random(seed))
            self.assertEqual({s: old.count(s) for s in set(old)}, {s: new.count(s) for s in set(new)})
            durations = [b['end_minute'] - b['start_minute'] for b in stage_blocks(new)]
            sizes.extend(durations)
            nights_with_short += any(n < 10 for n in durations)
        self.assertGreater(nights_with_short / 500, .9)
        self.assertTrue(.05 < sum(n < 10 for n in sizes) / len(sizes) < .35)
        self.assertLess(sum(n > 50 for n in sizes) / len(sizes), .02)
        self.assertGreater(sum(n > 50 for n in sizes), 0)

    def test_saved_v1_request_uses_original_generator_new_defaults_use_v2(self):
        request = {'sleep': {'mode': 'random', 'stage_model': 'natural_v1',
                            'bedtime_range': ['00:00', '00:00'], 'wake_range': ['08:00', '08:00'],
                            'duration_range': [480, 480]}}
        plan = build_plan(fixture(), CONFIG, request, 11)
        rng = random.Random(11)
        rng.choice([(0, 480, 480)]); rng.randint(480, 480)
        self.assertEqual([r['samplePoints'][0]['key'] for r in plan['sleep']['target']], generated_stages_v1(480, rng))
        validate_automation_plan(plan)
        self.assertEqual(preset_request(SPEC)['sleep']['stage_model'], 'natural_v2')

    def test_distribution_favors_preferred_band_but_retains_tail(self):
        rng = random.Random(3)
        spec = {'range': [0, 100], 'preferred': [35, 55]}
        samples = [weighted_integer(spec, rng) for _ in range(5000)]
        self.assertTrue(all(0 <= x <= 100 for x in samples))
        self.assertGreater(sum(35 <= x <= 55 for x in samples) / len(samples), .6)
        self.assertGreater(max(samples), 85)
        self.assertLess(min(samples), 15)

    def test_sleep_constraints_hold_and_samples_vary(self):
        samples = [sample_preset(SPEC, random.Random(i)) for i in range(300)]
        for targets, bed, wake in samples:
            self.assertIsNone(targets['steps'])
            self.assertTrue(0 <= bed <= 45 and 420 <= wake <= 570)
            self.assertTrue(380 <= wake - bed <= 525)
        self.assertGreater(len({(bed, wake) for _, bed, wake in samples}), 250)

    def test_lower_or_equal_rings_skipped_without_changing_details(self):
        f = fixture()
        total = f['sport_stats']['sportStat'][0]
        total['sportBasicInfo']['calorie'] = 100000
        total['exerciseTimeBasic']['totalMidHighIntensity'] = 100
        total['activeHourBasic']['countActiveHour'] = 20
        p = build_plan(f, CONFIG, preset_request(SPEC), 5)
        self.assertEqual(p['targets'], {'calorie': None, 'exercise': None, 'active': None, 'steps': None})
        self.assertEqual(p['sport'], [])
        self.assertEqual(p['intensity'], [])
        self.assertEqual(p['active'], [])
        self.assertEqual(p['sport_summary']['sportBasicInfo'], total['sportBasicInfo'])

    def test_activity_never_overlaps_old_or_new_sleep_steps_preserved(self):
        f = fixture()
        # Put an adopted sport minute near a candidate wake time.
        row = copy.deepcopy(f['sport']['detailInfos'][0])
        row.update(startTime=str(BASE + 430 * 60000), endTime=str(BASE + 431 * 60000))
        f['sport']['detailInfos'].append(row)
        for seed in range(30):
            p = build_plan(f, CONFIG, preset_request(SPEC), seed)
            s = p['sleep']
            for domain in ['sport', 'intensity', 'active']:
                for r in p[domain]:
                    self.assertFalse(int(r['startTime']) < s['target_end'] and int(r['endTime']) > s['target_start'])
            old_steps = {r['startTime']: r['sportBasicInfos'][0]['steps'] for r in f['sport']['detailInfos']}
            self.assertTrue(all(r['sportBasicInfos'][0]['steps'] == old_steps[r['startTime']] for r in p['sport']))
            self.assertIsNone(p['targets']['steps'])

    def test_random_replacement_accepts_backup_awake_without_fabricating_awake(self):
        f = fixture()
        f['sleep']['detailInfos'][20]['samplePoints'][0]['key'] = 'PROFESSIONAL_SLEEP_WAKE'
        p = build_plan(f, CONFIG, preset_request(SPEC), 5)
        self.assertNotIn('PROFESSIONAL_SLEEP_WAKE', [r['samplePoints'][0]['key'] for r in p['sleep']['target']])
        validate_automation_plan(p)

    def test_no_awake_hour_capacity_keeps_original_active_total(self):
        spec = copy.deepcopy(SPEC)
        spec['active'] = {'range': [24, 24]}
        p = build_plan(fixture(), CONFIG, preset_request(spec), 5)
        self.assertIsNone(p['targets']['active'])
        self.assertEqual(p['skipped']['active']['reason'], 'insufficient_awake_hours')

    def test_out_of_bounds_preset_or_disabled_guards_rejected(self):
        spec = copy.deepcopy(SPEC)
        spec['active'] = {'range': [25, 25]}
        with self.assertRaises(ValueError): build_plan(fixture(), CONFIG, preset_request(spec), 5)
        request = preset_request(SPEC)
        request['exclude_sleep'] = False
        with self.assertRaises(ValueError): build_plan(fixture(), CONFIG, request, 5)

    def test_legacy_random_plan_replays_original_generator(self):
        request = {'sleep': {'mode': 'random', 'bedtime_range': ['00:00', '00:00'],
                            'wake_range': ['08:00', '08:00'], 'duration_range': [480, 480]}}
        p = build_plan(fixture(), CONFIG, request, 11)
        rng = random.Random(11)
        rng.choice([(0, 480, 480)]); rng.randint(480, 480)
        self.assertEqual([r['samplePoints'][0]['key'] for r in p['sleep']['target']], legacy_generated_stages(480, rng))
        validate_automation_plan(p)


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.days = ['2001-01-01', '2001-01-02']
        MultiClient.states = {d: shifted_fixture(d) for d in self.days}
        MultiClient.fail_upload = False

    def prepare(self, directory):
        with patch('sleep_sync_lab.batch.AutomationClient', MultiClient):
            path = batch.prepare_batch(CONFIG, preset_request(SPEC), *self.days, directory)
        # Simulate the user's original-phone deletion AFTER the immutable backup.
        for state in MultiClient.states.values():
            state['sleep']['detailInfos'] = []
            state['sleep_stats']['professionalSleepTotal'] = []
        return path

    def test_child_dates_seeds_hashes_and_tamper_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.prepare(Path(tmp) / 'batch')
            manifest, plans = batch.validate_batch(path)
            self.assertEqual([p['config']['date'] for _, p in plans], self.days)
            self.assertNotEqual(plans[0][1]['seed'], plans[1][1]['seed'])
            child = plans[1][0]
            child.write_text(child.read_text(encoding='utf-8') + ' ', encoding='utf-8')
            with self.assertRaises(ValueError): batch.validate_batch(path)

    def test_all_day_preflight_failure_occurs_before_any_write_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.prepare(Path(tmp) / 'batch')
            MultiClient.states[self.days[1]]['sport_stats']['sportStat'][0]['sportBasicInfo']['steps'] += 1
            with patch('sleep_sync_lab.batch.AutomationClient', MultiClient), patch('sleep_sync_lab.batch.execute') as execute:
                with self.assertRaises(ValueError): batch.execute_batch(path, True, phone_deleted=True)
                execute.assert_not_called()
            self.assertFalse((path.parent / 'batch_apply_started_private.json').exists())
            self.assertTrue((path.parent / 'apply_failure_private.json').exists())

    def test_success_then_replay_refused_with_all_markers_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.prepare(Path(tmp) / 'batch')
            with patch('sleep_sync_lab.batch.AutomationClient', MultiClient), patch('sleep_sync_lab.automation.AutomationClient', MultiClient):
                result = batch.execute_batch(path, True, phone_deleted=True)
                self.assertEqual(result['completed_dates'], self.days)
                with self.assertRaises(ValueError): batch.execute_batch(path, True, phone_deleted=True)
            self.assertTrue((path.parent / 'batch_apply_started_private.json').exists())
            for day in self.days:
                self.assertTrue((path.parent / 'days' / day / 'apply_started_private.json').exists())

    def test_first_day_failure_stops_second_day_and_retains_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.prepare(Path(tmp) / 'batch')
            MultiClient.fail_upload = True
            with patch('sleep_sync_lab.batch.AutomationClient', MultiClient), patch('sleep_sync_lab.automation.AutomationClient', MultiClient):
                with self.assertRaises(ValueError): batch.execute_batch(path, True, phone_deleted=True)
            self.assertTrue((path.parent / 'batch_apply_started_private.json').exists())
            self.assertTrue((path.parent / 'days' / self.days[0] / 'apply_started_private.json').exists())
            self.assertFalse((path.parent / 'days' / self.days[1] / 'apply_started_private.json').exists())

    def test_missing_batch_confirmation_never_opens_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.prepare(Path(tmp) / 'batch')
            with patch('sleep_sync_lab.batch.AutomationClient') as native:
                with self.assertRaises(ValueError): batch.execute_batch(path)
                native.assert_not_called()

    def test_unadopted_day_stops_later_day_and_preserves_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.prepare(Path(tmp) / 'batch')
            result = {'target_adopted': {'exercise': False}, 'sleep_cloud_segments_match': True,
                      'sleep_cloud_summary_match': True}
            with patch('sleep_sync_lab.batch.AutomationClient', MultiClient), patch('sleep_sync_lab.batch.execute', return_value=result) as execute:
                with self.assertRaises(ValueError): batch.execute_batch(path, True, phone_deleted=True)
                self.assertEqual(execute.call_count, 1)
            self.assertTrue((path.parent / ('day_' + self.days[0] + '_result_private.json')).exists())
            self.assertTrue((path.parent / 'batch_apply_started_private.json').exists())


if __name__ == '__main__':
    unittest.main()
