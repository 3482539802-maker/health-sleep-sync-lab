"""Reviewed multi-day manifests, all-day preflight, durable progress, no write retries."""
import copy
from datetime import date, timedelta
import hashlib
from pathlib import Path
import secrets
import traceback
from .automation import (AutomationClient, check_baseline, execute, load, private, save,
                         save_failure, settings, snapshot_with_retry)
from .automation_model import build_plan, describe, canonical, validate_automation_plan
from .audit import event, failure_details, runtime_manifest
from .errors import user_message
from .model import check, digest


def selected_dates(start, end):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    check(first <= last, 'Batch end precedes start')
    check((last - first).days < 31, 'Batch supports at most 31 reviewed days')
    return [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]


def day_config(config, day):
    result = copy.deepcopy(config)
    result['date'] = day
    for key in ['sleep_query_start_ms', 'sleep_query_end_ms', 'day_start_ms', 'day_end_ms',
                'record_day', 'day', 'start_ms', 'end_ms']:
        result.pop(key, None)
    return settings(result)


def derived_seed(seed, day, attempt=0):
    return int.from_bytes(hashlib.sha256(f'{seed}:{day}:{attempt}'.encode()).digest()[:8], 'big')


def prepare_batch(config, request, start, end, directory, log=lambda message: None):
    dates = selected_dates(start, end)
    check(bool(request.get('preset')), 'Batch requires a configurable default preset')
    directory = private(directory)
    directory.mkdir(parents=True, exist_ok=False)
    save(directory / 'config_private.json', config)
    save(directory / 'request_private.json', request)
    save(directory / 'prepare_runtime_private.json', runtime_manifest())
    event(directory, 'batch_prepare_begin', dates=dates)
    manifest = {'batch_format_version': 1, 'start': start, 'end': end, 'seed': secrets.randbits(64), 'days': []}
    seen = set()
    try:
        for day in dates:
            log('只读备份并生成 ' + day + ' 的独立计划…')
            child = directory / 'days' / day
            child.mkdir(parents=True)
            cfg = day_config(config, day)
            save(child / 'config_private.json', cfg)
            save(child / 'request_private.json', request)
            save(child / 'prepare_runtime_private.json', runtime_manifest())
            with AutomationClient(cfg) as client:
                snapshot = snapshot_with_retry(client)
                snapshot['local_sleep'] = client.local_sleep()
            save(child / 'backup_private.json', snapshot)
            for attempt in range(100):
                plan = build_plan(snapshot, cfg, request, derived_seed(manifest['seed'], day, attempt))
                signature = canonical([plan['sampled_targets'], plan['sleep']['target_start'] % 86400000,
                                       plan['sleep']['target_end'] - plan['sleep']['target_start']])
                if signature not in seen:
                    break
            else:
                raise ValueError('Preset cannot produce distinct daily plans')
            seen.add(signature)
            path = child / 'plan_private.json'
            save(path, plan)
            save(child / 'review_private.json', describe(plan))
            event(child, 'prepare_complete', plan_sha256=digest(path))
            manifest['days'].append({'date': day, 'path': path.relative_to(directory).as_posix(), 'sha256': digest(path)})
        path = directory / 'batch_plan_private.json'
        save(path, manifest)
        save(directory / 'review_private.json', describe_batch(path))
        event(directory, 'batch_prepare_complete', days=len(dates), manifest_sha256=digest(path))
        log('所有日期计划已保存，尚未上传或删除健康数据。')
        return path
    except Exception as error:
        save_failure(directory, 'prepare_failure_private.json', {'message': user_message(error),
                     'traceback': traceback.format_exc(), 'diagnostics': failure_details(error)})
        event(directory, 'batch_prepare_failed', prepared_days=len(manifest['days']))
        raise


def validate_batch(path):
    path = private(path)
    manifest = load(path)
    check(manifest.get('batch_format_version') == 1, 'Unsupported batch manifest')
    dates = selected_dates(manifest['start'], manifest['end'])
    check([d['date'] for d in manifest['days']] == dates, 'Batch dates differ from reviewed range')
    plans = []
    for entry in manifest['days']:
        expected = f"days/{entry['date']}/plan_private.json"
        check(entry['path'] == expected, 'Batch child path differs')
        child = private(path.parent / expected)
        check(child.is_relative_to(path.parent), 'Batch child outside manifest directory')
        check(digest(child) == entry['sha256'], 'Batch child plan changed')
        plan = validate_automation_plan(load(child))
        check(plan['config']['date'] == entry['date'] and bool(plan['request'].get('preset')), 'Batch child date/preset differs')
        plans.append((child, plan))
    scopes = sorted((p['sleep']['local_delete_start'], p['sleep']['local_delete_end']) for _, p in plans)
    check(all(a[1] <= b[0] for a, b in zip(scopes, scopes[1:])), 'Batch sleep deletion scopes overlap')
    return manifest, plans


def describe_batch(path):
    manifest, plans = validate_batch(path)
    return {'batch': True, 'start': manifest['start'], 'end': manifest['end'],
            'days': [describe(p) for _, p in plans], 'stop_on_first_failure': True,
            'all_days_preflight_required': True, 'writes_performed': False}


def execute_batch(path, allow_sleep_delete=False, log=lambda message: None, phone_deleted=False):
    path = private(path)
    manifest, plans = validate_batch(path)
    directory = path.parent
    check(allow_sleep_delete is True, 'Explicit selected-night deletion confirmation required')
    check(phone_deleted is True, 'Phone night deletion and empty cloud segments/summary required before rebuild')
    check(not (directory / 'batch_apply_started_private.json').exists(), 'An execution marker exists; do not repeat this plan')
    check(all(not (child.parent / 'apply_started_private.json').exists() for child, _ in plans),
          'An execution marker exists; do not repeat this plan')
    lock = directory.parent / '.health_panel.lock'
    with lock.open('x', encoding='utf-8') as handle:
        handle.write(str(directory))
    started = False
    completed = []
    manifest_hash = digest(path)
    child_hashes = {str(child): digest(child) for child, _ in plans}
    try:
        save_failure(directory, 'apply_runtime_private.json', runtime_manifest())
        event(directory, 'batch_preflight_begin', days=len(plans))
        # Every selected day is checked before the first write, each child checks again at execution.
        for child, plan in plans:
            log('执行前只读核验 ' + plan['config']['date'] + '…')
            with AutomationClient(plan['config']) as client:
                current = snapshot_with_retry(client)
                current['local_sleep'] = client.local_sleep()
            save_failure(child.parent, 'batch_preflight_private.json', current)
            check_baseline(current, plan['baseline'], plan, phone_deleted=True)
        check(digest(path) == manifest_hash and all(digest(c) == child_hashes[str(c)] for c, _ in plans),
              'Plan changed while checking backup')
        save(directory / 'batch_apply_started_private.json', {'manifest_sha256': manifest_hash,
             'dates': [e['date'] for e in manifest['days']], 'sleep_delete_authorized': True})
        started = True
        event(directory, 'batch_all_baselines_verified')
        for child, plan in plans:
            check(digest(child) == child_hashes[str(child)], 'Batch child plan changed')
            day = plan['config']['date']
            event(directory, 'batch_day_begin', date=day)
            result = execute(child, allow_sleep_delete=True, log=lambda msg: log(day + '：' + msg), phone_deleted=True)
            save(directory / ('day_' + day + '_result_private.json'), result)
            check(all(result['target_adopted'].values()) and result.get('sleep_cloud_segments_match') is True and
                  result.get('sleep_cloud_summary_match') is True, 'Batch day not adopted; later dates stopped')
            completed.append(day)
            event(directory, 'batch_day_verified', date=day)
        result = {'batch': True, 'completed_dates': completed, 'phone_verification_pending': True,
                  'days': [load(directory / ('day_' + day + '_result_private.json')) for day in completed]}
        save(directory / 'result_private.json', result)
        event(directory, 'batch_apply_complete', completed_dates=completed)
        return result
    except Exception as error:
        save_failure(directory, 'apply_failure_private.json', {'writes_may_have_started': started,
                     'completed_dates': completed, 'message': user_message(error),
                     'traceback': traceback.format_exc(), 'diagnostics': failure_details(error)})
        event(directory, 'batch_apply_failed', writes_may_have_started=started, completed_dates=completed)
        raise
    finally:
        lock.unlink(missing_ok=True)
        event(directory, 'batch_job_lock_released')


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    preview = sub.add_parser('prepare')
    for name in ['config', 'request', 'start', 'end', 'job']:
        preview.add_argument('--' + name, required=True)
    review = sub.add_parser('review')
    review.add_argument('--plan', required=True)
    apply = sub.add_parser('apply')
    apply.add_argument('--plan', required=True)
    apply.add_argument('--allow-sleep-delete', action='store_true')
    apply.add_argument('--phone-nights-deleted', action='store_true')
    args = parser.parse_args()
    try:
        if args.action == 'prepare':
            path = prepare_batch(load(private(args.config)), load(private(args.request)), args.start, args.end, args.job)
            result = describe_batch(path)
        elif args.action == 'review':
            result = describe_batch(args.plan)
        else:
            result = execute_batch(args.plan, args.allow_sleep_delete, phone_deleted=args.phone_nights_deleted)
        print(__import__('json').dumps(result, ensure_ascii=True))
        return 0
    except Exception as error:
        print(user_message(error), file=__import__('sys').stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
