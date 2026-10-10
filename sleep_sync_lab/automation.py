"""Private persistent jobs. No write retries, immutable plan, read-back result reporting."""
import copy
from datetime import datetime
import json
from pathlib import Path
import secrets
import time
import traceback
from .automation_client import AutomationClient
from .automation_model import build_plan, canonical, describe, fingerprint, validate_automation_plan
from .model import bounds, check, content, digest, rows, timezone_value
from .errors import user_message
from .audit import event, failure_details, runtime_manifest
from .input_validation import validate_request, date_input, InputValidationError

REPOSITORY = Path(__file__).resolve().parents[1]


def private(value):
    path = Path(value).resolve()
    check(not path.is_relative_to(REPOSITORY), 'Real data must be outside the public repository')
    return path


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        __import__('os').fsync(handle.fileno())
    event(path.parent, 'artifact_saved', file=path.name, sha256=digest(path))


def save_failure(directory, name, value):
    # Preserve earlier failures even when connection preparation is retried before writes.
    path = directory / name
    if path.exists():
        path = directory / (Path(name).stem + '_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.json')
    save(path, value)


def successful(value):
    check(isinstance(value, dict) and value.get('resultCode') == 0, 'Cloud request failed; inspect private response')


def settings(config):
    config = copy.deepcopy(config)
    date_input(config.get('date', ''))
    try:
        timezone_value(config.get('timezone'))
    except (ValueError, TypeError):
        raise InputValidationError('私有配置.timezone', config.get('timezone', ''), '请填写带正负号的HH:MM时区') from None
    start, end = bounds(config)
    config.setdefault('transport_class', 'lvv')
    config.setdefault('gson_factory_class', 'oxm')
    config.setdefault('sleep_query_start_ms', start - 43200000)
    config.setdefault('sleep_query_end_ms', start + 43200000 - 1)
    return config


def snapshot_with_retry(client):
    # Read-only retry after initial app login/network readiness; no mutation retry.
    for attempt in range(3):
        value = client.snapshot()
        if all(isinstance(v, dict) and v.get('resultCode') == 0 for v in value.values()):
            return value
        if attempt < 2:
            time.sleep(1)
    raise ValueError('Cloud backup incomplete; no writes performed')


def prepare(config, request, directory, log=lambda message: None):
    directory = private(directory)
    directory.mkdir(parents=True, exist_ok=False)
    try:
        save(directory / 'request_private.json', request)
        validate_request(request)
        config = settings(config)
        save(directory / 'config_private.json', config)
        log('输入检查通过，正在检查ADB并隔离启动电脑原版…')
        save_failure(directory, 'prepare_runtime_private.json', runtime_manifest())
        event(directory, 'prepare_begin')
        with AutomationClient(config) as client:
            event(directory, 'session_ready', phase='prepare')
            save(directory / 'session_guard_private.json', client.guard_state())
            snapshot = snapshot_with_retry(client)
            if request.get('sleep'):
                snapshot['local_sleep'] = client.local_sleep()
        save(directory / 'backup_private.json', snapshot)
        plan = build_plan(snapshot, config, request, secrets.randbits(64))
        save(directory / 'plan_private.json', plan)
        save(directory / 'review_private.json', describe(plan))
        log('备份和固定计划已保存；尚未修改健康记录。')
        event(directory, 'prepare_complete', plan_sha256=digest(directory / 'plan_private.json'))
        return directory / 'plan_private.json'
    except Exception as error:
        save_failure(directory, 'prepare_failure_private.json', {'traceback': traceback.format_exc(), 'message': user_message(error), 'diagnostics': failure_details(error)})
        event(directory, 'prepare_failed', error_type=type(error).__name__)
        raise


def sport_key(record):
    return tuple(str(record.get(k, '')) for k in ['deviceCode', 'startTime', 'endTime', 'sportType', 'appType'])


def health_key(record):
    return (str(record.get('deviceCode')), int(record['startTime']), int(record['endTime']), int(record['type']))


def semantic_health(record):
    # Server may add fieldsModifyTime/fieldsMetadata. Compare meaningful samples.
    result = []
    for p in record['samplePoints']:
        value = p.get('value', '')
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            pass
        result.append((p['key'], int(p['startTime']), int(p['endTime']), canonical(value)))
    return (health_key(record), tuple(result))


def check_baseline(actual, expected, plan, phone_deleted=False):
    domains = ['sport', 'sport_stats', 'intensity', 'active']
    if plan['sleep'] and not phone_deleted:
        domains += ['sleep', 'sleep_stats', 'local_sleep']
    for domain in domains:
        successful(actual[domain])
        check(actual[domain] == expected[domain], 'Selected cloud data changed after preview; create a new plan')
    if plan['sleep'] and phone_deleted:
        successful(actual['sleep'])
        successful(actual['sleep_stats'])
        check(not rows(actual['sleep']) and not (actual['sleep_stats'].get('professionalSleepTotal') or []),
              'Phone night deletion and empty cloud segments/summary required before rebuild')


def execute(plan_path, allow_sleep_delete=False, log=lambda message: None, phone_deleted=False):
    plan_path = private(plan_path)
    directory = plan_path.parent
    plan = validate_automation_plan(load(plan_path))
    plan_hash = digest(plan_path)
    check(not (directory / 'apply_started_private.json').exists(), 'An execution marker exists; do not repeat this plan')
    check(not plan['sleep'] or allow_sleep_delete is True, 'Explicit selected-night deletion confirmation required')
    check(not plan['sleep'] or phone_deleted is True,
          'Phone night deletion and empty cloud segments/summary required before rebuild')
    lock = directory.parent / '.health_panel.lock'
    try:
        with lock.open('x', encoding='utf-8') as handle:
            handle.write(str(directory))
    except FileExistsError:
        raise ValueError('Another job or unfinished session lock exists; inspect before proceeding')
    wrote = False
    try:
        save_failure(directory, 'apply_runtime_private.json', runtime_manifest())
        event(directory, 'apply_begin', plan_sha256=plan_hash, sleep_delete_authorized=bool(allow_sleep_delete))
        with AutomationClient(plan['config']) as client:
            event(directory, 'session_ready', phase='apply')
            save(directory / 'session_guard_private.json', client.guard_state())
            current = snapshot_with_retry(client)
            if plan['sleep']:
                current['local_sleep'] = client.local_sleep()
            save(directory / 'before_apply_private.json', current)
            check_baseline(current, plan['baseline'], plan, phone_deleted=phone_deleted)
            check(digest(plan_path) == plan_hash, 'Plan changed while checking backup')
            event(directory, 'baseline_verified', plan_sha256=plan_hash)
            save(directory / 'apply_started_private.json', {'plan_sha256': plan_hash,
                 'sleep_delete_authorized': bool(allow_sleep_delete), 'phone_deleted_confirmed': bool(phone_deleted), 'local_time': datetime.now().astimezone().isoformat()})
            wrote = True

            def operation(name, fn):
                save(directory / (name + '_started_private.json'), {'plan_sha256': plan_hash})
                event(directory, 'operation_begin', operation=name)
                response = fn()
                save(directory / (name + '_response_private.json'), response)
                successful(response)
                event(directory, 'operation_response_ok', operation=name)
                return response

            client.configure(write_scope={'start': client.config['day_start_ms'], 'end': client.config['day_end_ms']})
            for domain, size, send in [('sport', 30, client.upload_sport), ('intensity', 200, client.upload_health), ('active', 200, client.upload_health)]:
                target = plan[domain]
                for batch, offset in enumerate(range(0, len(target), size)):
                    items = target[offset:offset + size]
                    log('正在提交 ' + domain + ' 第' + str(batch + 1) + '批…')
                    operation(domain + '_' + str(batch), lambda items=items: send(items))
                    after = snapshot_with_retry(client)
                    save(directory / (domain + '_' + str(batch) + '_readback_private.json'), after)
                    if domain == 'sport':
                        lookup = {sport_key(r): r for r in rows(after['sport'])}
                        for item in items:
                            actual = lookup.get(sport_key(item))
                            check(actual is not None, 'Uploaded sport minute missing')
                            for field in ['steps', 'calorie']:
                                check(actual['sportBasicInfos'][0][field] == item['sportBasicInfos'][0][field], 'Sport minute not adopted')
                    else:
                        domain_response = after['intensity' if domain == 'intensity' else 'active']
                        actual = {semantic_health(r) for r in rows(domain_response)}
                        check(all(semantic_health(r) in actual for r in items), 'Uploaded health minute/hour not adopted')
                    event(directory, 'readback_verified', operation=domain + '_' + str(batch), count=len(items))
            if any(v is not None for v in plan['targets'].values()):
                summary = copy.deepcopy(plan['sport_summary'])
                now = int(time.time() * 1000)
                if plan['targets']['exercise'] is not None:
                    summary['exerciseTimeBasic']['generateTime'] = str(now)
                if plan['targets']['active'] is not None:
                    summary['activeHourBasic']['generateTime'] = str(now)
                operation('sport_summary', lambda: client.sport_summary(summary))
            if plan['sleep']:
                s = plan['sleep']
                scope = {'start': s['delete_start'], 'end': s['delete_end']}
                local_scope = {'start': s['local_delete_start'], 'end': s['local_delete_end']}
                client.configure(delete_scope=local_scope)
                # Clear PC old segments too, rather than leave a known stale upload source.
                save(directory / 'local_sleep_clear_started_private.json', {'scope': local_scope})
                event(directory, 'operation_begin', operation='local_sleep_clear', scope=local_scope)
                if current['local_sleep'].get('intervals') or current['local_sleep'].get('pendingDeletes'):
                    local = client.clear_local_sleep(local_scope['start'], local_scope['end'])
                else:
                    # A previous stopped repair may already have cleared core rows.
                    # Do not treat the native "nothing to delete" false as corruption.
                    local = {'localCleared': True, 'localClearNotNeeded': True, 'phoneLocalCleared': False}
                save(directory / 'local_sleep_clear_response_private.json', local)
                check(local.get('localCleared') is True, 'PC local deletion was not accepted')
                after_local = client.local_sleep()
                save(directory / 'local_sleep_clear_readback_private.json', after_local)
                successful(after_local)
                check(not any(int(r['start']) < local_scope['end'] and int(r['end']) > local_scope['start']
                              for r in after_local['intervals']), 'PC old sleep remains after local deletion')
                event(directory, 'local_sleep_clear_verified')
                client.configure(delete_scope=scope)
                # Phone UI deletion already propagated. Do not queue a second cloud deletion.
                remaining = client.health_read(9, client.config['sleep_query_start_ms'], client.config['sleep_query_end_ms'])
                save(directory / 'sleep_after_delete_private.json', remaining)
                successful(remaining)
                check(not rows(remaining), 'Old sleep still in cloud; no rebuild')
                empty_stats = client.stats()
                save(directory / 'sleep_summary_after_phone_delete_private.json', empty_stats)
                successful(empty_stats)
                check(not (empty_stats.get('professionalSleepTotal') or []),
                      'Phone night deletion and empty cloud segments/summary required before rebuild')
                event(directory, 'sleep_cloud_empty_verified')
                client.configure(delete_scope=local_scope, sleep_cloud_empty_verified=True)
                operation('sleep_retire_acknowledged_local_deletes', lambda: client.retire_acknowledged_sleep_deletes(local_scope['start'], local_scope['end']))
                settled = client.local_sleep()
                save(directory / 'sleep_local_settled_private.json', settled)
                successful(settled)
                check(not settled.get('pendingDeletes') and not settled.get('pendingSummaryDeletes') and
                      not settled.get('pendingDictionaryDeletes'),
                      'Pending sleep deletions remain; rebuild refused')
                check(not settled['intervals'] and not settled.get('dictionaryIntervals'), 'PC old sleep remains after local deletion')
                client.configure(write_scope={'start': s['target_start'], 'end': s['target_end']})
                for batch, offset in enumerate(range(0, len(s['target']), 200)):
                    items = s['target'][offset:offset + 200]
                    operation('sleep_' + str(batch), lambda items=items: client.upload_health(items))
                    value = client.health_read(9, s['target_start'], s['target_end'])
                    save(directory / ('sleep_' + str(batch) + '_readback_private.json'), value)
                    successful(value)
                    actual = {semantic_health(r) for r in rows(value)}
                    check(all(semantic_health(r) in actual for r in s['target'][:offset + 200]), 'Sleep rebuild incomplete; stop')
                    event(directory, 'readback_verified', operation='sleep_' + str(batch), rebuilt_count=offset + len(items))
                total = copy.deepcopy(s['summary'])
                total['generateTime'] = str(int(time.time() * 1000))
                operation('sleep_summary', lambda: client.sleep_summary(total))
                # Verify the entire old+new window twice; a target-only read hides old tails.
                for index in range(2):
                    time.sleep(2)
                    stable = client.health_read(9, client.config['sleep_query_start_ms'], client.config['sleep_query_end_ms'])
                    save(directory / ('sleep_full_window_stability_' + str(index) + '_private.json'), stable)
                    successful(stable)
                    check({semantic_health(r) for r in rows(stable)} == {semantic_health(r) for r in s['target']},
                          'Sleep changed or old tail returned during verification')
                    local_settled = client.local_sleep()
                    save(directory / ('sleep_local_stability_' + str(index) + '_private.json'), local_settled)
                    successful(local_settled)
                    check(not local_settled.get('pendingDeletes') and not local_settled.get('pendingSummaryDeletes') and
                          not local_settled.get('pendingDictionaryDeletes'),
                          'Pending sleep deletions remain; rebuild refused')
            final = snapshot_with_retry(client)
            save(directory / 'final_private.json', final)
            total = final['sport_stats']['sportStat'][0]
            measured = {'calorie': total['sportBasicInfo']['calorie'] / 1000,
                        'steps': total['sportBasicInfo']['steps'],
                        'exercise': total['exerciseTimeBasic']['totalMidHighIntensity'],
                        'active': total['activeHourBasic']['countActiveHour']}
            result = {'writes_performed': True, 'targets': plan['targets'], 'cloud_totals': measured,
                      'target_adopted': {k: measured[k] == v for k, v in plan['targets'].items() if v is not None},
                      'phone_verification_pending': True, 'sleep_phone_local_deletion_not_guaranteed': bool(plan['sleep']),
                      'notices': plan['notices']}
            if plan['sleep']:
                final_sleep = client.health_read(9, plan['sleep']['target_start'], plan['sleep']['target_end'])
                save(directory / 'sleep_final_scoped_private.json', final_sleep)
                successful(final_sleep)
                result['sleep_cloud_segments_match'] = {semantic_health(r) for r in rows(final['sleep'])} == {semantic_health(r) for r in plan['sleep']['target']}
                totals = final['sleep_stats'].get('professionalSleepTotal') or []
                result['sleep_cloud_summary_match'] = len(totals) == 1 and all(
                    totals[0]['professionalSleep'].get(k) == plan['sleep']['summary']['professionalSleep'].get(k)
                    for k in ['allSleepTime', 'lightSleepTime', 'deepSleepTime', 'dreamTime', 'fallAsleepTime', 'wakeupTime', 'sleepScore'])
                check(result['sleep_cloud_segments_match'] and result['sleep_cloud_summary_match'],
                      'Sleep full window or summary changed; verification failed')
            save(directory / 'result_private.json', result)
            save(directory / 'guard_finish_private.json', client.guard_state())
            event(directory, 'apply_complete', phone_verification_pending=True)
            log('执行已结束；请按结果核对云端采用情况，并在手机正常同步验收。')
            return result
    except Exception as error:
        save_failure(directory, 'apply_failure_private.json', {'writes_may_have_started': wrote, 'traceback': traceback.format_exc(), 'message': user_message(error), 'diagnostics': failure_details(error)})
        event(directory, 'apply_failed', writes_may_have_started=wrote, error_type=type(error).__name__)
        raise
    finally:
        lock.unlink(missing_ok=True)
        event(directory, 'job_lock_released')


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    preview = sub.add_parser('prepare')
    for name in ['config', 'request', 'job']:
        preview.add_argument('--' + name, required=True)
    apply = sub.add_parser('apply')
    apply.add_argument('--plan', required=True)
    apply.add_argument('--allow-sleep-delete', action='store_true')
    apply.add_argument('--phone-night-deleted', action='store_true')
    review = sub.add_parser('review')
    review.add_argument('--plan', required=True)
    args = parser.parse_args()
    try:
        if args.action == 'prepare':
            path = prepare(load(private(args.config)), load(private(args.request)), args.job)
            result = describe(load(path))
        elif args.action == 'review':
            result = describe(load(private(args.plan)))
        else:
            result = execute(args.plan, allow_sleep_delete=args.allow_sleep_delete, phone_deleted=args.phone_night_deleted)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except Exception:
        print('Stopped. Inspect the private job directory; retain write markers.', file=__import__('sys').stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
