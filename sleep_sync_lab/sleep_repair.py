"""Restore one fixed, previously verified sleep snapshot in an independent job."""
import argparse
import copy
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sys
import time
import traceback

from .automation import (load, private, save, settings, snapshot_with_retry,
                         successful, semantic_health)
from .automation_client import AutomationClient
from .audit import runtime_manifest, failure_details
from .model import bounds, check, content, digest, rows, validate
from .sleep_preflight import validate_sleep_upload

DOMAINS = ('sport', 'sport_stats', 'intensity', 'active')


def config_for(config, selected_date=None):
    value = copy.deepcopy(config)
    for key in ('day', 'start_ms', 'end_ms', 'sleep_query_start_ms', 'sleep_query_end_ms'):
        value.pop(key, None)
    if selected_date:
        value['date'] = selected_date
    return settings(value)


def select_day(client, config, selected_date):
    cfg = config_for(config, selected_date)
    start, end = bounds(cfg)
    client.configure(date=selected_date, day_start_ms=start, day_end_ms=end,
                     record_day=int(selected_date.replace('-', '')),
                     sleep_query_start_ms=cfg['sleep_query_start_ms'], sleep_query_end_ms=cfg['sleep_query_end_ms'])


def neighbors(client, config):
    selected = date.fromisoformat(config['date'])
    result = {}
    try:
        for offset in (-1, 1):
            day = (selected + timedelta(days=offset)).isoformat()
            select_day(client, config, day)
            segments = client.health_read(9, client.config['sleep_query_start_ms'], client.config['sleep_query_end_ms'])
            total = client.stats()
            successful(segments)
            successful(total)
            result[day] = {'segments': segments, 'summary': total}
    finally:
        select_day(client, config, config['date'])
    return result


def verify_neighbors(before, after):
    check(set(before) == set(after), 'Neighbor date selection differs')
    check(all(sorted(map(content, rows(before[d]['segments']))) == sorted(map(content, rows(after[d]['segments'])))
              and before[d]['summary'] == after[d]['summary'] for d in before), 'Neighbor sleep changed; stop')


def build_target(config, snapshot, score_origin='unknown'):
    cfg = config_for(config)
    check(score_origin in ('original', 'estimate', 'unknown'), 'Unknown score provenance')
    for key in (*DOMAINS, 'sleep', 'sleep_stats'):
        successful(snapshot[key])
    target = validate(rows(snapshot['sleep']))
    start, end = int(target[0]['startTime']), int(target[-1]['endTime'])
    check(cfg['sleep_query_start_ms'] <= start < end <= cfg['sleep_query_end_ms'] + 1,
          'Target outside selected overnight query window; multi-night/afternoon repair unsupported')
    totals = snapshot['sleep_stats'].get('professionalSleepTotal') or []
    check(len(totals) == 1, 'One previously verified sleep summary required')
    summary = copy.deepcopy(totals[0])
    summary['dataSource'] = 2
    plan = {'format_version': 1, 'scope': 'sleep-only-fixed-snapshot', 'config': cfg,
            'sleep': {'target': target, 'target_start': start, 'target_end': end, 'summary': summary},
            'score_provenance': {'origin': score_origin, 'copied_from_backup': True, 'vendor_recalculated': False},
            'sport_requests': [], 'cloud_delete_requests': []}
    source = {'resultCode': 0, 'sleepSource': {'deviceCode': str(target[0]['deviceCode']),
              'deviceCategory': summary['professionalSleep'].get('deviceCategory'), 'sourceKind': 'registered_wearable'}}
    validate_sleep_upload(plan, source)
    return plan


def check_sport(before, after):
    check(all(before[k] == after[k] for k in DOMAINS), 'Sport baseline changed; no sport writes permitted')


def check_empty(snapshot):
    check(not rows(snapshot['sleep']) and not snapshot['sleep_stats'].get('professionalSleepTotal'),
          'Phone deletion has not emptied both cloud sleep segments and summary')


def check_local_empty(local, inspection):
    successful(local)
    successful(inspection)
    check(all(not local.get(k) for k in ('intervals', 'pendingDeletes', 'pendingSummaryDeletes',
                                       'dictionaryIntervals', 'pendingDictionaryDeletes')), 'PC source or deletion queue remains')
    counts = inspection.get('counts')
    check(isinstance(counts, dict) and set(counts) == {'core', 'stats', 'dictionary', 'syncCache'}
          and all(type(v) is int and v == 0 for v in counts.values()), 'PC source/stat/dictionary/cache remains; inspect privately')


def verify_target(snapshot, plan, count=None, summary=True):
    expected = plan['sleep']['target'][:count]
    actual = rows(snapshot['sleep'])
    check(len(actual) == len(expected) and {semantic_health(r) for r in actual} ==
          {semantic_health(r) for r in expected}, 'Sleep content/source mismatch or old tail returned; stop without retry')
    if summary:
        totals = snapshot['sleep_stats'].get('professionalSleepTotal') or []
        check(len(totals) == 1 and totals[0]['recordDay'] == plan['sleep']['summary']['recordDay']
              and totals[0]['professionalSleep'] == plan['sleep']['summary']['professionalSleep'],
              'Complete sleep summary/score/category mismatch')


def review(plan):
    fields = plan['sleep']['summary']['professionalSleep']
    return {'date': plan['config']['date'], 'target_start_ms': plan['sleep']['target_start'],
            'target_end_ms': plan['sleep']['target_end'], 'minutes': len(plan['sleep']['target']),
            'score': fields['sleepScore'], 'score_provenance': plan['score_provenance'],
            'sleep_only': True, 'sport_requests': [], 'cloud_delete_requests': [],
            'phone_selected_night_delete_and_sync_required': True, 'watch_powered_off_required': True,
            'pc_cleanup_scope': plan.get('pc_cleanup_scope')}


def snapshot(config, directory):
    out = private(directory)
    out.mkdir(parents=True, exist_ok=False)
    cfg = config_for(config)
    cfg['audit_dir'] = str(out)
    save(out / 'runtime_private.json', runtime_manifest())
    try:
        with AutomationClient(cfg) as client:
            value = snapshot_with_retry(client)
            save(out / 'snapshot_private.json', value)
            save(out / 'local_private.json', client.local_sleep())
            save(out / 'source_counts_private.json', client.inspect_sleep_sources())
            save(out / 'guard_finish_private.json', client.guard_state())
        save(out / 'result_private.json', {'read_only': True, 'saved': True, 'target_correctness_requires_review': True})
        return out / 'snapshot_private.json'
    except Exception as error:
        save(out / 'failure_private.json', {'writes_may_have_started': False,
             'traceback': traceback.format_exc(), 'diagnostics': failure_details(error)})
        raise


def prepare(config, target_snapshot, directory, score_origin='unknown'):
    target_path = private(target_snapshot)
    plan = build_target(config, load(target_path), score_origin)
    out = private(directory)
    out.mkdir(parents=True, exist_ok=False)
    plan['target_snapshot_sha256'] = digest(target_path)
    cfg = dict(plan['config'], audit_dir=str(out))
    save(out / 'runtime_private.json', runtime_manifest())
    try:
        with AutomationClient(cfg) as client:
            current = snapshot_with_retry(client)
            old = rows(current['sleep'])
            totals = current['sleep_stats'].get('professionalSleepTotal') or []
            check(len(totals) <= 1 and all(t.get('recordDay') == int(plan['config']['date'].replace('-', ''))
                                          for t in totals), 'Ambiguous selected-night baseline')
            if old:
                validate(old, allow_wake=True)
            local = client.local_sleep()
            successful(local)
            save(out / 'baseline_private.json', current)
            save(out / 'neighbors_private.json', neighbors(client, plan['config']))
            save(out / 'local_private.json', local)
            save(out / 'source_counts_private.json', client.inspect_sleep_sources())
            edges = [(plan['sleep']['target_start'], plan['sleep']['target_end'])]
            edges += [(int(r['startTime']), int(r['endTime'])) for r in old]
            plan['pc_cleanup_scope'] = {'start': min(a for a, b in edges), 'end': max(b for a, b in edges)}
            client.configure(upload_require_sleep_source=True,
                             sleep_upload_device_code=str(plan['sleep']['target'][0]['deviceCode']))
            source = client.upload_source()
            validate_sleep_upload(plan, source)
            save(out / 'registered_source_private.json', source)
            save(out / 'guard_finish_private.json', client.guard_state())
        save(out / 'plan_private.json', plan)
        save(out / 'plan_hash_private.json', {'sha256': digest(out / 'plan_private.json')})
        save(out / 'review_private.json', review(plan))
        return out / 'plan_private.json'
    except Exception as error:
        save(out / 'failure_private.json', {'writes_may_have_started': False,
             'traceback': traceback.format_exc(), 'diagnostics': failure_details(error)})
        raise


def run(plan_path, apply=False, phone_deleted=False, watch_off=False, allow_pc_cleanup=False):
    path = private(plan_path)
    root = path.parent
    check(digest(path) == load(root / 'plan_hash_private.json')['sha256'], 'Pinned plan changed')
    stored = load(path)
    # Revalidate the frozen minute timeline and all copied summary fields offline.
    reconstructed = {'sleep': {'resultCode': 0, 'detailInfos': stored['sleep']['target']},
                     'sleep_stats': {'resultCode': 0, 'professionalSleepTotal': [stored['sleep']['summary']]},
                     **{k: {'resultCode': 0} for k in DOMAINS}}
    plan = build_target(stored['config'], reconstructed, stored['score_provenance']['origin'])
    check(plan['sleep'] == stored['sleep'] and stored['scope'] == plan['scope'] and
          stored['sport_requests'] == [] and stored['cloud_delete_requests'] == [], 'Pinned scope changed')
    plan = stored
    if apply:
        check(phone_deleted and watch_off, 'Confirm phone selected-night deletion/sync and watch powered off first')
        check(not (root / 'apply_started_private.json').exists(), 'Already started; preserve marker and inspect, never rerun')
    out = root / (('apply_' if apply else 'verify_') + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    out.mkdir()
    cfg = dict(plan['config'], audit_dir=str(out))
    save(out / 'runtime_private.json', runtime_manifest())
    baseline = load(root / 'baseline_private.json')
    neighbor_baseline = load(root / 'neighbors_private.json')
    wrote = False
    try:
        with AutomationClient(cfg) as client:
            before = snapshot_with_retry(client)
            save(out / 'before_private.json', before)
            check_sport(baseline, before)
            current_neighbors = neighbors(client, plan['config'])
            save(out / 'neighbors_before_private.json', current_neighbors)
            verify_neighbors(neighbor_baseline, current_neighbors)
            client.configure(upload_require_sleep_source=True,
                             sleep_upload_device_code=str(plan['sleep']['target'][0]['deviceCode']))
            source = client.upload_source()
            save(out / 'registered_source_private.json', source)
            validate_sleep_upload(plan, source)
            local = client.local_sleep()
            inspection = client.inspect_sleep_sources()
            save(out / 'local_private.json', local)
            save(out / 'source_counts_private.json', inspection)
            successful(local)
            successful(inspection)
            counts = inspection.get('counts') or {}
            check(set(counts) == {'core', 'stats', 'dictionary', 'syncCache'} and
                  all(type(v) is int and v >= 0 for v in counts.values()), 'Source inspection incomplete')
            if apply:
                check(counts['syncCache'] == 0, 'PC sync cache remains; inspect privately before cleanup/upload')
                check_empty(before)
                time.sleep(3)
                second = snapshot_with_retry(client)
                save(out / 'empty_second_private.json', second)
                check_empty(second)
                check_sport(baseline, second)
                scope = plan['pc_cleanup_scope']
                for value in ('intervals', 'pendingDeletes', 'dictionaryIntervals', 'pendingDictionaryDeletes'):
                    check(all(scope['start'] <= r['start'] <= r['end'] <= scope['end'] for r in local.get(value, [])),
                          'PC source outside reviewed cleanup scope; prepare a new scope, no upload')
                dirty = any(local.get(k) for k in ('intervals', 'pendingDeletes', 'pendingSummaryDeletes',
                                                   'dictionaryIntervals', 'pendingDictionaryDeletes'))
                check(not dirty or allow_pc_cleanup, 'Reviewed PC cleanup needed; explicit --allow-pc-cleanup required')
                if not dirty:
                    check_local_empty(local, inspection)
                check(digest(path) == load(root / 'plan_hash_private.json')['sha256'], 'Plan changed during preflight')
                save(root / 'apply_started_private.json', {'at': datetime.now().astimezone().isoformat(),
                     'plan_sha256': digest(path), 'phone_deleted_user_confirmed': True, 'watch_off_user_confirmed': True})
                wrote = True
                if dirty:
                    client.configure(delete_scope=scope, sleep_cloud_empty_verified=True)
                    if local.get('intervals') or local.get('pendingDeletes'):
                        save(out / 'pc_clear_started_private.json', scope)
                        response = client.clear_local_sleep(scope['start'], scope['end'])
                        save(out / 'pc_clear_response_private.json', response)
                        check(response.get('localCleared') is True, 'PC cleanup refused')
                    save(out / 'pc_retire_started_private.json', scope)
                    response = client.retire_acknowledged_sleep_deletes(scope['start'], scope['end'])
                    save(out / 'pc_retire_response_private.json', response)
                    successful(response)
                    settled = client.local_sleep()
                    inspected = client.inspect_sleep_sources()
                    save(out / 'pc_settled_private.json', settled)
                    save(out / 'pc_source_counts_settled_private.json', inspected)
                    check_local_empty(settled, inspected)
                    empty_after = snapshot_with_retry(client)
                    save(out / 'empty_after_pc_cleanup_private.json', empty_after)
                    check_empty(empty_after)
                    check_sport(baseline, empty_after)
                client.configure(write_scope={'start': plan['sleep']['target_start'], 'end': plan['sleep']['target_end']})
                target = plan['sleep']['target']
                for batch, offset in enumerate(range(0, len(target), 200)):
                    items = target[offset:offset + 200]
                    prefix = 'sleep_' + str(batch)
                    save(out / (prefix + '_request_private.json'), items)
                    save(out / (prefix + '_started_private.json'), {'count': len(items)})
                    response = client.upload_health(items)
                    save(out / (prefix + '_response_private.json'), response)
                    successful(response)
                    actual = snapshot_with_retry(client)
                    save(out / (prefix + '_readback_private.json'), actual)
                    verify_target(actual, plan, offset + len(items), summary=False)
                    check_sport(baseline, actual)
                summary = copy.deepcopy(plan['sleep']['summary'])
                summary['generateTime'] = str(int(time.time() * 1000))
                save(out / 'summary_request_private.json', summary)
                save(out / 'summary_started_private.json', {'dataSource': 2})
                response = client.sleep_summary(summary)
                save(out / 'summary_response_private.json', response)
                successful(response)
                for index in range(2):
                    if index:
                        time.sleep(3)
                    actual = snapshot_with_retry(client)
                    save(out / ('final_' + str(index) + '_private.json'), actual)
                    verify_target(actual, plan)
                    check_sport(baseline, actual)
            else:
                verify_target(before, plan)
                check_local_empty(local, inspection)
            final_neighbors = neighbors(client, plan['config'])
            save(out / 'neighbors_after_private.json', final_neighbors)
            verify_neighbors(neighbor_baseline, final_neighbors)
            save(out / 'guard_finish_private.json', client.guard_state())
        result = {'cloud_verified': True, 'sport_unchanged': True, 'neighbors_unchanged': True,
                  'phone_verification_pending': True, 'watch_restart_and_long_term_unverified': True,
                  'score_provenance': plan['score_provenance'], 'read_only': not apply}
        save(out / 'result_private.json', result)
        return result
    except Exception as error:
        save(out / 'failure_private.json', {'writes_may_have_started': wrote,
             'traceback': traceback.format_exc(), 'diagnostics': failure_details(error)})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    shot = sub.add_parser('snapshot', help='Guarded six-domain read; does not label the snapshot correct')
    shot.add_argument('--config', required=True)
    shot.add_argument('--out', required=True)
    prep = sub.add_parser('prepare', help='New independent job from your previously verified snapshot')
    for flag in ('config', 'target-snapshot', 'job'):
        prep.add_argument('--' + flag, required=True)
    prep.add_argument('--score-origin', choices=['original', 'estimate', 'unknown'], default='unknown')
    for command in ('review', 'apply', 'verify'):
        item = sub.add_parser(command)
        item.add_argument('--plan', required=True)
        if command == 'apply':
            item.add_argument('--phone-night-deleted', action='store_true')
            item.add_argument('--watch-powered-off', action='store_true')
            item.add_argument('--allow-pc-cleanup', action='store_true')
    args = parser.parse_args()
    try:
        if args.command == 'snapshot':
            result = {'snapshot_path': str(snapshot(load(private(args.config)), args.out))}
        elif args.command == 'prepare':
            result = {'plan_path': str(prepare(load(private(args.config)), args.target_snapshot, args.job, args.score_origin))}
        elif args.command == 'review':
            result = review(load(private(args.plan)))
        else:
            result = run(args.plan, apply=args.command == 'apply',
                         phone_deleted=getattr(args, 'phone_night_deleted', False),
                         watch_off=getattr(args, 'watch_powered_off', False),
                         allow_pc_cleanup=getattr(args, 'allow_pc_cleanup', False))
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except Exception:
        print('Stopped. Inspect your private job; retain markers, do not repeat writes blindly.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
