"""Fabricated recovery regressions: preserve stages, refuse stale sources, never retry."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from sleep_sync_lab import sleep_repair as repair
from sleep_sync_lab.automation import load
from test_automation import CONFIG, fixture, FakeClient


def target_snapshot():
    value = fixture()
    minutes = value['sleep']['detailInfos']
    total = value['sleep_stats']['professionalSleepTotal'][0]
    total['dataSource'] = 1
    fields = total['professionalSleep']
    fields.update(lightSleepTime=100, deepSleepTime=100, dreamTime=100,
                  fallAsleepTime=minutes[0]['startTime'], wakeupTime=minutes[-1]['endTime'],
                  untouchedSyntheticField='retained')
    return value


NEIGHBORS = {'2000-12-31': {'segments': {'resultCode': 0, 'detailInfos': []},
                           'summary': {'resultCode': 0, 'professionalSleepTotal': []}}}


class RepairClient(FakeClient):
    summary_calls = 0
    extra_counts = {}

    def inspect_sleep_sources(self):
        local = self.local_sleep()
        return {'resultCode': 0, 'counts': {
            'core': len(local.get('intervals', [])) + len(local.get('pendingDeletes', [])),
            'stats': local.get('pendingSummaryDeletes', 0),
            'dictionary': len(local.get('dictionaryIntervals', [])) + len(local.get('pendingDictionaryDeletes', [])),
            'syncCache': 0, **self.extra_counts}}

    def sleep_summary(self, value):
        type(self).summary_calls += 1
        if value['dataSource'] != 2:
            raise ValueError('Wrong request source')
        return super().sleep_summary(value)

    def clear_local_sleep(self, start, end):
        self.state['local_sleep']['intervals'] = []
        return {'localCleared': True}

    def retire_acknowledged_sleep_deletes(self, start, end):
        result = super().retire_acknowledged_sleep_deletes(start, end)
        self.state['local_sleep']['dictionaryIntervals'] = []
        return result


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        RepairClient.state = target_snapshot()
        RepairClient.calls = RepairClient.summary_calls = 0
        RepairClient.fail_upload = False
        RepairClient.extra_counts = {}
        for name, value in [('AutomationClient', RepairClient), ('neighbors', lambda *a: copy.deepcopy(NEIGHBORS))]:
            mock = patch.object(repair, name, value)
            mock.start()
            self.addCleanup(mock.stop)
        mock = patch.object(repair.time, 'sleep')
        mock.start()
        self.addCleanup(mock.stop)

    def prepare(self):
        target = self.root / 'target.json'
        repair.save(target, target_snapshot())
        return repair.prepare(CONFIG, target, self.root / 'job', 'estimate')

    def delete_phone(self):
        RepairClient.state['sleep']['detailInfos'] = []
        RepairClient.state['sleep_stats']['professionalSleepTotal'] = []

    def apply(self, path, **kwargs):
        return repair.run(path, apply=True, phone_deleted=True, watch_off=True, **kwargs)

    def test_target_is_copied_without_new_generation_or_score(self):
        original = target_snapshot()
        plan = repair.build_target(CONFIG, original, 'estimate')
        self.assertEqual(plan['sleep']['target'], original['sleep']['detailInfos'])
        self.assertEqual(plan['sleep']['summary']['professionalSleep'], original['sleep_stats']['professionalSleepTotal'][0]['professionalSleep'])
        self.assertEqual(plan['sleep']['summary']['dataSource'], 2)
        self.assertEqual(original['sleep_stats']['professionalSleepTotal'][0]['dataSource'], 1)
        self.assertFalse(plan['score_provenance']['vendor_recalculated'])

    def test_stale_date_bounds_are_rederived(self):
        stale = dict(CONFIG, sleep_query_start_ms=0, sleep_query_end_ms=1, day=1)
        self.assertEqual(repair.build_target(CONFIG, target_snapshot()), repair.build_target(stale, target_snapshot()))

    def test_wrong_summary_missing_category_gaps_and_duplicates_refused(self):
        for variant in ('summary', 'category', 'gap', 'duplicate', 'date'):
            value = target_snapshot()
            if variant == 'summary': value['sleep_stats']['professionalSleepTotal'][0]['professionalSleep']['dreamTime'] += 1
            if variant == 'category': value['sleep_stats']['professionalSleepTotal'][0]['professionalSleep'].pop('deviceCategory')
            if variant == 'gap': value['sleep']['detailInfos'].pop(20)
            if variant == 'duplicate': value['sleep']['detailInfos'].append(copy.deepcopy(value['sleep']['detailInfos'][0]))
            if variant == 'date': value['sleep_stats']['professionalSleepTotal'][0]['recordDay'] += 1
            with self.subTest(variant=variant), self.assertRaises(ValueError): repair.build_target(CONFIG, value)

    def test_prepare_is_read_only_and_apply_verifies_every_batch_and_summary(self):
        path = self.prepare()
        self.assertEqual(RepairClient.calls, 0)
        self.delete_phone()
        with patch.object(RepairClient, 'delete_sleep') as cloud_delete, patch.object(RepairClient, 'upload_sport') as sport:
            result = self.apply(path)
        cloud_delete.assert_not_called()
        sport.assert_not_called()
        self.assertEqual(RepairClient.calls, 2)
        self.assertEqual(RepairClient.summary_calls, 1)
        self.assertTrue(result['cloud_verified'])
        self.assertTrue(repair.run(path)['read_only'])
        self.assertEqual(RepairClient.calls, 2)
        with self.assertRaisesRegex(ValueError, 'Already started'): self.apply(path)

    def test_user_confirmations_required_before_mutation(self):
        path = self.prepare()
        for phone, watch in ((False, True), (True, False)):
            with self.assertRaisesRegex(ValueError, 'Confirm phone'):
                repair.run(path, apply=True, phone_deleted=phone, watch_off=watch)
        self.assertEqual(RepairClient.calls, 0)

    def test_cloud_segments_or_summary_remaining_block_before_marker(self):
        path = self.prepare()
        for field in ('sleep', 'sleep_stats'):
            self.delete_phone()
            RepairClient.state[field] = target_snapshot()[field]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'emptied both'): self.apply(path)
            self.assertFalse((path.parent / 'apply_started_private.json').exists())
        self.assertEqual(RepairClient.calls, 0)

    def test_sport_changes_and_plan_tampering_block_before_write(self):
        path = self.prepare()
        self.delete_phone()
        RepairClient.state['sport_stats']['sportStat'][0]['activeHourBasic']['countActiveHour'] += 1
        with self.assertRaisesRegex(ValueError, 'Sport baseline'): self.apply(path)
        self.assertEqual(RepairClient.calls, 0)
        path.write_text(path.read_text() + ' ', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Pinned plan changed'): self.apply(path)

    def test_pc_cache_or_unacknowledged_source_blocks(self):
        path = self.prepare()
        self.delete_phone()
        RepairClient.extra_counts = {'syncCache': 1}
        with self.assertRaisesRegex(ValueError, 'sync cache'): self.apply(path, allow_pc_cleanup=True)
        RepairClient.extra_counts = {'stats': 1}
        with self.assertRaisesRegex(ValueError, 'source/stat'): self.apply(path)
        self.assertEqual(RepairClient.calls, 0)

    def test_reviewed_pc_cleanup_and_pending_queues_settle_before_upload(self):
        path = self.prepare()
        self.delete_phone()
        scope = load(path)['pc_cleanup_scope']
        RepairClient.state['local_sleep'].update(
            intervals=[scope], pendingSummaryDeletes=1,
            dictionaryIntervals=[scope], pendingDictionaryDeletes=[scope])
        with self.assertRaisesRegex(ValueError, 'explicit --allow-pc-cleanup'): self.apply(path)
        self.assertEqual(RepairClient.calls, 0)
        self.assertTrue(self.apply(path, allow_pc_cleanup=True)['cloud_verified'])

    def test_pc_source_outside_reviewed_scope_is_not_deleted(self):
        path = self.prepare()
        self.delete_phone()
        scope = load(path)['pc_cleanup_scope']
        RepairClient.state['local_sleep']['intervals'] = [{'start': scope['end'], 'end': scope['end'] + 60000}]
        with patch.object(RepairClient, 'clear_local_sleep') as clear:
            with self.assertRaisesRegex(ValueError, 'outside reviewed cleanup'): self.apply(path, allow_pc_cleanup=True)
        clear.assert_not_called()

    def test_upload_failure_preserves_marker_and_is_not_retried(self):
        path = self.prepare()
        self.delete_phone()
        RepairClient.fail_upload = True
        with self.assertRaisesRegex(ValueError, 'Cloud request failed'): self.apply(path)
        self.assertEqual(RepairClient.calls, 1)
        self.assertEqual(RepairClient.summary_calls, 0)
        self.assertTrue((path.parent / 'apply_started_private.json').exists())
        with self.assertRaisesRegex(ValueError, 'Already started'): self.apply(path)
        self.assertEqual(RepairClient.calls, 1)

    def test_returning_tail_and_stage_overwrite_detected_independent_of_ids(self):
        plan = repair.build_target(CONFIG, target_snapshot())
        for variant in ('tail', 'stage'):
            current = target_snapshot()
            if variant == 'tail':
                tail = copy.deepcopy(current['sleep']['detailInfos'][-1])
                for obj in (tail, tail['samplePoints'][0]):
                    obj['startTime'] = obj['endTime']
                    obj['endTime'] = str(int(obj['endTime']) + 60000)
                current['sleep']['detailInfos'].append(tail)
            else: current['sleep']['detailInfos'][0]['samplePoints'][0]['key'] = 'PROFESSIONAL_SLEEP_DEEP'
            with self.subTest(variant=variant), self.assertRaisesRegex(ValueError, 'old tail returned'):
                repair.verify_target(current, plan)

    def test_neighbor_changes_refused(self):
        after = copy.deepcopy(NEIGHBORS)
        after['2000-12-31']['summary']['professionalSleepTotal'] = [{'changed': True}]
        with self.assertRaisesRegex(ValueError, 'Neighbor sleep changed'): repair.verify_neighbors(NEIGHBORS, after)
