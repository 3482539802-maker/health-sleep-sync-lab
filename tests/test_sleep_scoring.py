"""Scoring provenance, offline calculation and fail-before-write regressions."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from sleep_sync_lab import automation
from sleep_sync_lab.automation_model import describe, build_plan, validate_automation_plan
from sleep_sync_lab.generation import generate, bind_plan
from sleep_sync_lab.sleep_scoring import (estimate_score, score_mode, original_score_value,
                                         SHALLOW, DEEP, REM)
from sleep_sync_lab.standalone import empty_snapshot, bind_source
from sleep_sync_lab.sleep_preflight import validate_sleep_upload
from test_automation import CONFIG, fixture, FakeClient
from test_offline_generation import RANDOM_SLEEP
from test_standalone import EmptyClient


class ScoringTests(unittest.TestCase):
    def test_estimation_depends_on_stages_and_duration_and_is_explicit(self):
        good = [SHALLOW]*240 + [DEEP]*120 + [REM]*120
        estimate = estimate_score(good)
        self.assertEqual(estimate['score'], 95)
        self.assertEqual(estimate['raw_score'], 100)
        self.assertEqual(estimate['score_ceiling'], 95)
        self.assertFalse(estimate['vendor_recalculated'])
        self.assertFalse(estimate['medical_score'])
        self.assertLess(estimate_score([SHALLOW]*480)['score'], estimate['score'])
        self.assertLess(estimate_score(good[:240])['score'], estimate['score'])
        self.assertLess(estimate_score([SHALLOW, DEEP, REM]*160)['score'], estimate['score'])
        self.assertEqual(estimate_score(good), estimate)

    def test_v1_saved_plan_stays_valid_and_new_prepare_uses_v2(self):
        spec = dict(RANDOM_SLEEP, score_mode='estimate')
        old = generate(CONFIG, {'planning_mode':'standalone', 'sleep':spec}, 123)
        self.assertEqual(old['sleep']['score_provenance']['algorithm'], 'stage_estimate_v1')
        validate_automation_plan(old)
        with tempfile.TemporaryDirectory() as folder:
            path = automation.prepare(CONFIG, {'sleep':spec}, Path(folder)/'job')
            new = automation.load(path)
            self.assertEqual(new['sleep']['score_provenance']['algorithm'], 'stage_estimate_v2')
            self.assertLessEqual(new['sleep']['summary']['professionalSleep']['sleepScore'],95)
        self.assertEqual(estimate_score([SHALLOW]*240+[DEEP]*120+[REM]*120,'stage_estimate_v1')['score'],100)

    def test_unsupported_stages_are_not_silently_scored(self):
        with self.assertRaisesRegex(ValueError, 'continuous'):
            estimate_score([SHALLOW]*479 + ['AWAKE'])
        for value in [None, 0, -1, 101, True, 70.5]:
            with self.assertRaisesRegex(ValueError, 'Original score missing'):
                original_score_value(value)

    def test_legacy_mode_and_conflicting_requests(self):
        self.assertEqual(score_mode({}), 'none')
        self.assertEqual(score_mode({'restore_original_score': True}), 'original')
        for spec in [{'score_mode': 'official'}, {'score_mode': 'estimate', 'restore_original_score': True}]:
            with self.assertRaises(ValueError): score_mode(spec)

    def test_estimate_offline_needs_no_original_and_does_not_change_timeline(self):
        spec = dict(RANDOM_SLEEP, score_mode='estimate')
        plain = generate(CONFIG, {'planning_mode': 'standalone', 'sleep': RANDOM_SLEEP}, 123)
        scored = generate(CONFIG, {'planning_mode': 'standalone', 'sleep': spec}, 123)
        self.assertEqual(scored['sleep']['target'], plain['sleep']['target'])
        self.assertFalse(scored.get('execution_backup_required'))
        review = describe(scored)
        self.assertEqual(review['sleep']['score_mode'], 'estimate')
        self.assertGreater(review['sleep']['score'], 0)
        self.assertIn('components', review['sleep']['score_provenance'])
        validate_automation_plan(scored)

    def test_zero_original_score_cannot_be_presented_as_restored(self):
        baseline = fixture()
        baseline['sleep_stats']['professionalSleepTotal'][0]['professionalSleep']['sleepScore'] = 0
        draft = generate(CONFIG, {'planning_mode': 'standalone', 'sleep': dict(RANDOM_SLEEP, restore_original_score=True)}, 1)
        with self.assertRaisesRegex(ValueError, 'Original score missing'):
            bind_plan(draft, baseline)

    def test_estimate_real_executor_provenance_and_positive_cloud_score(self):
        EmptyClient.state = empty_snapshot()
        EmptyClient.calls = 0
        EmptyClient.sources_available = True
        with tempfile.TemporaryDirectory() as folder, patch('sleep_sync_lab.automation.AutomationClient', EmptyClient), patch('sleep_sync_lab.automation.time.sleep'):
            path = automation.prepare(CONFIG, {'sleep': dict(RANDOM_SLEEP, score_mode='estimate')}, Path(folder)/'job')
            result = automation.execute(path)
            self.assertEqual(result['sleep_score']['mode'], 'estimate')
            self.assertFalse(result['sleep_score']['vendor_recalculated'])
            self.assertEqual(result['sleep_score']['score'], EmptyClient.state['sleep_stats']['professionalSleepTotal'][0]['professionalSleep']['sleepScore'])


class SleepUploadPreflightTests(unittest.TestCase):
    def setUp(self):
        EmptyClient.state = empty_snapshot()
        EmptyClient.calls = 0
        EmptyClient.sources_available = True
        self.source = EmptyClient(CONFIG).upload_source()
        draft = generate(CONFIG, {'planning_mode': 'standalone', 'sleep': RANDOM_SLEEP}, 7)
        self.resolved = bind_source(draft, self.source)

    def test_request_source_vs_query_source_and_other_envelope_errors(self):
        validate_sleep_upload(self.resolved, self.source)
        for field, value in [('dataSource', 1), ('recordDay', 20010102), ('deviceCode', '654321'), ('timeZone', '+0000')]:
            bad = copy.deepcopy(self.resolved)
            bad['sleep']['summary'][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'request scope'):
                validate_sleep_upload(bad, self.source)

    def test_category_and_timeline_must_match(self):
        for field, value in [('deviceCategory', 0), ('allSleepTime', 1), ('lightSleepTime', 1), ('wakeupTime', '0')]:
            bad = copy.deepcopy(self.resolved)
            bad['sleep']['summary']['professionalSleep'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_sleep_upload(bad, self.source)

    def test_bad_resolved_summary_stops_before_any_sport_or_sleep_write(self):
        def wrong_source(plan, source):
            plan = bind_source(plan, source)
            plan['sleep']['summary']['dataSource'] = 1
            return plan
        with tempfile.TemporaryDirectory() as folder, patch('sleep_sync_lab.automation.AutomationClient', EmptyClient), patch('sleep_sync_lab.automation.bind_source', side_effect=wrong_source):
            path = automation.prepare(CONFIG, {'steps': 8000, 'sleep': RANDOM_SLEEP}, Path(folder)/'job')
            with self.assertRaisesRegex(ValueError, 'request scope'):
                automation.execute(path)
            self.assertEqual(EmptyClient.calls, 0)
            self.assertFalse(list(path.parent.glob('*started*')))

    def test_legacy_concrete_phone_source_is_rejected_without_rewriting_plan(self):
        baseline = fixture()
        for row in baseline['sleep']['detailInfos']: row['deviceCode'] = '123456'
        plan = build_plan(baseline, CONFIG, {'steps': 500, 'sleep': {'mode': 'random', **RANDOM_SLEEP}}, 9)
        FakeClient.state = copy.deepcopy(baseline)
        FakeClient.state['sleep']['detailInfos'] = []
        FakeClient.state['sleep_stats']['professionalSleepTotal'] = []
        FakeClient.calls = 0
        with tempfile.TemporaryDirectory() as folder, patch('sleep_sync_lab.automation.AutomationClient', FakeClient):
            path = Path(folder)/'plan_private.json'
            automation.save(path, plan)
            original = path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'registered wearable'):
                automation.execute(path, True, phone_deleted=True)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(FakeClient.calls, 0)
            self.assertFalse(list(path.parent.glob('*started*')))


if __name__ == '__main__':
    unittest.main()
