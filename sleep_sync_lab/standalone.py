"""Construct day records from targets; missing historical data is a valid input."""
import copy
import random
from .automation_model import (SHALLOW, DEEP, REM, allocate, active_hours,
                               content, in_windows, sleep_target, windows)
from .model import bounds, check, rows
from .sleep_scoring import score_mode, apply_score, original_score_value

SOURCE = '__registered_upload_source__'


def requires_original(request):
    sleep = request.get('sleep') or {}
    return bool(request.get('use_original') or (sleep and sleep.get('mode', 'shift') == 'shift') or
                (sleep and score_mode(sleep) == 'original'))


def local_has_sleep(local):
    return any(local.get(k) for k in ['intervals', 'pendingDeletes', 'pendingSummaryDeletes',
                                     'dictionaryIntervals', 'pendingDictionaryDeletes'])


def sleep_empty(snapshot):
    return not rows(snapshot['sleep']) and not snapshot['sleep_stats'].get('professionalSleepTotal')


def limited_allocate(total, count, limit, rng):
    check(count > 0 and total <= count * limit, 'Target exceeds awake minute capacity')
    # Start with an equal allocation; bounded transfers add variation without
    # producing an implausibly large minute at the edge of capacity.
    values = allocate(total, [1] * count)
    for _ in range(count * 2):
        a, b = rng.randrange(count), rng.randrange(count)
        transfer = rng.randint(0, min(values[a], limit - values[b], max(1, total // count // 3)))
        if a != b:
            values[a] -= transfer
            values[b] += transfer
    return values


def empty_snapshot():
    return {**{k: {'resultCode': 0, 'detailInfos': []} for k in ['sport', 'intensity', 'active', 'sleep']},
            'sport_stats': {'resultCode': 0, 'sportStat': []},
            'sleep_stats': {'resultCode': 0, 'professionalSleepTotal': []},
            'local_sleep': {'resultCode': 0, 'intervals': []}}


def health(config, start, end, kind, key, value):
    return {'type': kind, 'deviceCode': SOURCE, 'startTime': str(start), 'endTime': str(end),
            'timeZone': config['timezone'].replace(':', ''), 'mergedFlag': 0,
            'samplePoints': [{'key': key, 'value': value, 'startTime': str(start), 'endTime': str(end)}]}


def known_totals(snapshot):
    totals = snapshot['sport_stats'].get('sportStat') or []
    check(len(totals) <= 1, 'Unique day sport total required')
    if not totals:
        return {k: None for k in ['calorie', 'exercise', 'active', 'steps']}
    total = totals[0]
    basic = total.get('sportBasicInfo') or {}
    calorie = basic.get('calorie')
    return {'calorie': None if calorie is None else calorie / 1000, 'steps': basic.get('steps'),
            'exercise': (total.get('exerciseTimeBasic') or {}).get('totalMidHighIntensity'),
            'active': (total.get('activeHourBasic') or {}).get('countActiveHour')}


def build(generation, snapshot=None, bound=False):
    config, request = generation['config'], generation['request']
    actual = empty_snapshot() if snapshot is None else snapshot
    check(all(isinstance(v, dict) and v.get('resultCode') == 0 for v in actual.values()),
          'Incomplete cloud backup; no write')
    base, end = bounds(config)
    rng = random.Random(generation['seed'] ^ 0x57495245)
    targets = copy.deepcopy(generation['targets'])
    sampled = copy.deepcopy(targets)
    notices, skipped, comparisons = [], {}, {}
    known = known_totals(actual)
    stat_rows = actual['sport_stats'].get('sportStat') or []
    if stat_rows:
        check(int(stat_rows[0]['recordDay']) == int(config['date'].replace('-', '')) and
              stat_rows[0].get('sportType') == 0, 'Unique day sport total required')
    if request.get('preset') and known['steps'] is None and not any(
            'steps' in r.get('mergedFields', []) for r in rows(actual['sport'])):
        targets['steps'] = min(200000, round((sampled['calorie'] or 0) * rng.uniform(30, 45)))
        notices.append(('如果无原步数，默认方案按活动热量生成配套步数；执行时已有步数则保持。' if snapshot is None else
                        '所选日无原步数，默认方案按活动热量生成配套步数；已有步数则保持。'))
    for field in ['calorie', 'exercise', 'active']:
        if targets[field] is None:
            continue
        if known[field] is None:
            comparisons[field] = {'compared': False, 'reason': 'not_loaded' if snapshot is None else 'no_original_value'}
        else:
            comparisons[field] = {'compared': True, 'original': known[field], 'requested': targets[field]}
            if request.get('increase_only') and targets[field] <= known[field]:
                skipped[field] = {'sampled': targets[field], 'original': known[field], 'reason': 'not_above_original'}
                targets[field] = None
    sleep = None
    if generation['sleep']:
        s = generation['sleep']
        if s['mode'] == 'shift' and bound:
            sleep = sleep_target(actual, config, request['sleep'], rng, frozen=s)
        else:
            check(s['mode'] == 'random' and (bound or score_mode(request['sleep']) != 'original'),
                  'Original data required by selected options')
            start, finish, sequence = s['target_start'], s['target_end'], s['sequence']
            check(base - 43200000 <= start < finish <= base + 43200000,
                  'Synthetic sleep outside selected-night cleanup window')
            summary = {'recordDay': int(config['date'].replace('-', '')), 'dataSource': 2, 'deviceCode': '0',
                       'timeZone': config['timezone'].replace(':', ''), 'professionalSleep': {
                'sleepScore': 0, 'allSleepTime': len(sequence), 'daySleepTime': 0, 'awakeTime': 0,
                'lightSleepTime': sequence.count(SHALLOW), 'deepSleepTime': sequence.count(DEEP), 'dreamTime': sequence.count(REM),
                'fallAsleepTime': str(start), 'goBedTime': str(start), 'wakeupTime': str(finish), 'risingTime': str(finish),
                'wakeupCnt': 0, 'deepSleepPart': sum(k == DEEP and (i == 0 or sequence[i - 1] != DEEP) for i, k in enumerate(sequence))}}
            sleep = {'mode': 'random', 'target_start': start, 'target_end': finish,
                     'target': [health(config, start + i * 60000, start + (i + 1) * 60000, 9, k, '') for i, k in enumerate(sequence)],
                     'summary': summary, 'delete_start': base - 43200000, 'delete_end': base + 43200000,
                     'local_delete_start': base - 43200000, 'local_delete_end': base + 43200000,
                     'synthetic_not_measured': True, 'original_score_not_recalculated': False}
            if score_mode(request['sleep']) == 'original':
                totals = actual['sleep_stats'].get('professionalSleepTotal') or []
                check(len(totals) == 1 and int(totals[0]['recordDay']) == summary['recordDay'] and
                      'sleepScore' in totals[0].get('professionalSleep', {}),
                      'Original score missing; backup before phone deletion')
                summary['professionalSleep']['sleepScore'] = original_score_value(totals[0]['professionalSleep']['sleepScore'])
                sleep['original_score_not_recalculated'] = True
            apply_score(sleep, request['sleep'])
        local = actual.get('local_sleep', {})
        check(all(sleep['local_delete_start'] <= int(r['start']) < int(r['end']) <= sleep['local_delete_end']
                  for key in ['intervals', 'pendingDeletes', 'dictionaryIntervals', 'pendingDictionaryDeletes']
                  for r in local.get(key, [])), 'Local sleep includes another session; inspect selected-night deletion scope')
    allowed = windows(request.get('activity_windows', '00:00-24:00'))
    exclusions = [(int(r['startTime']), int(r['endTime'])) for r in rows(actual['sleep'])]
    if sleep:
        exclusions.append((sleep['target_start'], sleep['target_end']))
    # New generated movement always respects both old and new sleep.
    free = sorted({m for a, b in allowed for m in range(a, b)
                   if not any(base + m * 60000 < y and base + (m + 1) * 60000 > x for x, y in exclusions)})
    free_set = set(free)
    full_hours = [h for h in range(24) if all(m in free_set for m in range(h * 60, (h + 1) * 60))]
    existing_hours = active_hours(actual, base)
    selected_hours = set(existing_hours)
    requested_hours = set(request.get('active_hours', [])) - existing_hours
    check(requested_hours <= set(full_hours), 'Selected active hour overlaps excluded sleep')
    current_active = int(known['active'] if known['active'] is not None else len(existing_hours))
    if targets['active'] is not None:
        need = max(0, targets['active'] - len(existing_hours))
        possible = [h for h in full_hours if h not in existing_hours and h not in requested_hours]
        check(len(requested_hours) <= need, 'More selected hours than target increase')
        if need > len(possible) + len(requested_hours):
            if request.get('preset'):
                capacity = len(existing_hours) + len(possible) + len(requested_hours)
                if known['active'] is not None and capacity <= known['active']:
                    skipped['active'] = {'sampled': sampled['active'], 'original': known['active'],
                                         'reason': 'insufficient_awake_hours'}
                    targets['active'] = None
                    need = 0
                    requested_hours = set()
                else:
                    targets['active'] = capacity
                    need = capacity - len(existing_hours)
                    notices.append('活动小时受清醒整点容量限制，目标调整为 ' + str(targets['active']))
            else:
                raise ValueError('Not enough inactive hours in allowed windows')
        selected_hours |= requested_hours | set(rng.sample(possible, max(0, need - len(requested_hours))))
        if targets['active'] is not None and targets['active'] < current_active:
            notices.append('active: existing hours retained; lower total may be ignored')
    # With no old active-hour template, generate movement in fresh awake hours.
    movement = any(targets[k] is not None and targets[k] > 0 for k in ['calorie', 'steps', 'exercise'])
    if movement and not selected_hours:
        check(targets['active'] != 0, 'Movement conflicts with zero active hours')
        check(bool(free), 'Cannot distribute target in selected windows')
        hour_count = max(6, ((targets['steps'] or 0) + 14399) // 14400,
                         ((targets['exercise'] or 0) + 59) // 60)
        selected_hours = set(rng.sample(full_hours, min(hour_count, len(full_hours)))) if full_hours else {m // 60 for m in free}
    if movement and targets['active'] is None and known['active'] is not None:
        if existing_hours:
            selected_hours = set(existing_hours)
        else:
            check(current_active > 0 and current_active <= len(full_hours), 'Movement conflicts with existing active hour capacity')
            selected_hours = set(rng.sample(full_hours, current_active))
            notices.append('原活动小时仅有总数，按清醒时段补齐对应明细，保留原总数。')
    movement_pool = [m for m in free if m // 60 in selected_hours]
    original_sport = rows(actual['sport'])
    check(all(base <= int(r['startTime']) < int(r['endTime']) <= end for r in original_sport), 'Sport records outside selected day')
    working = copy.deepcopy(original_sport)
    sport_added = {}
    generated_minutes = []
    for hour in sorted(selected_hours):
        candidates = [m for m in movement_pool if m // 60 == hour]
        count = min(len(candidates), rng.randint(22, 35))
        if count:
            offset = rng.randint(0, len(candidates) - count)
            generated_minutes += candidates[offset:offset + count]
    for field, multiplier in [('steps', 1), ('calorie', 1000)]:
        target = targets[field]
        if target is None:
            continue
        adopted = [r for r in working if field in r.get('mergedFields', [])]
        check(all(len(r['sportBasicInfos']) == 1 for r in adopted), 'Multiple sport samples unsupported')
        precise = sum(r['sportBasicInfos'][0].get(field, 0) for r in adopted)
        original_total = known[field] * multiplier if known[field] is not None else precise
        residual = original_total - precise if field == 'calorie' else 0
        selected = [r for r in adopted if in_windows(r, base, allowed) and
                    (int(r['startTime']) - base) // 60000 in free_set and
                    (int(r['startTime']) - base) // 3600000 in selected_hours]
        fixed = sum(r['sportBasicInfos'][0].get(field, 0) for r in adopted if r not in selected)
        amount = int(target * multiplier - residual - fixed)
        check(amount >= 0, 'Target is below unmodified minutes or retained summary residual; expand windows')
        limit = 240 if field == 'steps' else 20000
        added_amount = amount
        if selected:
            selected_amount = min(amount, limit * len(selected))
            values = limited_allocate(selected_amount, len(selected), limit, rng)
            for row, value in zip(selected, values):
                row['sportBasicInfos'][0][field] = value
            added_amount -= selected_amount
        if added_amount:
            check(bool(generated_minutes), 'Cannot distribute target in selected windows')
            if field == 'steps':
                check(added_amount <= 240 * len(movement_pool), 'Step target exceeds awake minute capacity')
                if added_amount > 240 * len(generated_minutes):
                    generated_minutes = list(movement_pool)
            # Avoid overwriting another adopted field in an existing sport row.
            occupied = {(int(r['startTime']) - base) // 60000 for r in original_sport}
            candidates = [m for m in generated_minutes if m not in occupied]
            if added_amount > limit * len(candidates):
                candidates = [m for m in movement_pool if m not in occupied]
            values = limited_allocate(added_amount, len(candidates), limit, rng)
            for minute, value in zip(candidates, values):
                if value == 0:
                    continue
                row = sport_added.setdefault(minute, {'deviceCode': SOURCE, 'sportType': 5, 'appType': 1,
                      'startTime': str(base + minute * 60000), 'endTime': str(base + (minute + 1) * 60000),
                      'timeZone': config['timezone'].replace(':', ''), 'mergedFlag': 0, 'mergedFields': [], 'sportBasicInfos': [{}]})
                row['mergedFields'].append(field)
                row['sportBasicInfos'][0][field] = value
    changes = [b for a, b in zip(original_sport, working) if content(a) != content(b)]
    intensity = []
    if targets['exercise'] is not None:
        old = int(known['exercise'] if known['exercise'] is not None else
                  sum(int(r.get('mergedFlag', 0)) == 0 for r in rows(actual['intensity'])))
        used = {int(r['startTime']) for r in rows(actual['intensity'])}
        pool = [m for m in movement_pool if base + m * 60000 not in used]
        extra = max(0, targets['exercise'] - old)
        if targets['exercise'] < old:
            notices.append('exercise: existing minutes retained; lower total may be ignored')
        check(extra <= len(pool), 'Not enough unused exercise minutes in allowed hours')
        for minute in sorted(rng.sample(pool, extra)):
            intensity.append(health(config, base + minute * 60000, base + (minute + 1) * 60000,
                                    12, 'EXERCISE_INTENSITY', '{"exerciseType":1}'))
    active = [health(config, base + h * 3600000, base + (h + 1) * 3600000 - 1,
                     200005, 'ACTIVE_HOUR', '{"isActive":1}') for h in sorted(selected_hours - existing_hours)]
    if targets['active'] is None and known['active'] is not None and existing_hours:
        # An unselected existing ring is retained, not filled with fabricated hours.
        active = []
    stat = copy.deepcopy(stat_rows[0]) if stat_rows else {'sportBasicInfo': {}, 'exerciseTimeBasic': {}, 'activeHourBasic': {}}
    stat.update(recordDay=int(config['date'].replace('-', '')), sportType=0, dataSource=2, deviceCode='0',
                timeZone=config['timezone'].replace(':', ''))
    for field, multiplier in [('steps', 1), ('calorie', 1000)]:
        if targets[field] is not None:
            stat.setdefault('sportBasicInfo', {})[field] = targets[field] * multiplier
    if targets['exercise'] is not None:
        stat.setdefault('exerciseTimeBasic', {}).update(totalMidHighIntensity=targets['exercise'], intensityMap={'1': targets['exercise']})
    if targets['active'] is not None:
        stat.setdefault('activeHourBasic', {})['countActiveHour'] = targets['active']
    elif active and not stat_rows:
        stat.setdefault('activeHourBasic', {})['countActiveHour'] = len(selected_hours)
    for field, detail in skipped.items():
        notices.append(field + ': skipped (' + detail['reason'] + '); original retained')
    plan = {'format_version': 6 if bound else 5, 'config': copy.deepcopy(config), 'request': copy.deepcopy(request),
            'seed': generation['seed'], 'generation': copy.deepcopy(generation), 'targets': targets,
            'sampled_targets': sampled, 'skipped': skipped, 'comparisons': comparisons,
            'awake_activity_windows': allowed,
            'sport': changes + [sport_added[m] for m in sorted(sport_added)], 'intensity': intensity, 'active': active,
            'sport_summary': stat, 'sleep': sleep, 'notices': notices, 'standalone_generated': True,
            'execution_backup_required': False, 'cloud_and_phone_verification_required': True}
    if snapshot is not None:
        plan['baseline'] = copy.deepcopy(snapshot)
    return plan


def resolve(plan, actual):
    """Use available originals; absent summaries/templates simply take the fresh path."""
    # A required-source plan keeps the deletion-before-upload backup for score/shift.
    if plan['format_version'] == 6:
        combined = copy.deepcopy(actual)
        for domain in ['sleep', 'sleep_stats', 'local_sleep']:
            combined[domain] = copy.deepcopy(plan['baseline'][domain])
        return build(plan['generation'], combined, bound=True)
    return build(plan['generation'], actual, bound=True)


def bind_source(plan, source):
    code = str(source.get('deviceCode', ''))
    check(source.get('resultCode') == 0 and code.isdigit() and 0 < int(code) < 2**63 and
          isinstance(source.get('appType'), int) and source['appType'] > 0,
          'Registered upload source unavailable')
    result = copy.deepcopy(plan)
    for domain in ['sport', 'intensity', 'active']:
        for record in result[domain]:
            if record['deviceCode'] == SOURCE:
                record['deviceCode'] = str(source['deviceCode'])
                if domain == 'sport':
                    record['appType'] = source.get('appType', 1)
    if result['sleep']:
        needs_sleep_source = any(record['deviceCode'] == SOURCE for record in result['sleep']['target'])
        sleep_source = source.get('sleepSource') or {}
        if needs_sleep_source:
            sleep_code = str(sleep_source.get('deviceCode', ''))
            category = sleep_source.get('deviceCategory')
            check(sleep_code.isdigit() and 0 < int(sleep_code) < 2**63 and
                  type(category) is int and category > 0 and
                  sleep_source.get('sourceKind') == 'registered_wearable',
                  'Registered sleep device unavailable or ambiguous')
        for record in result['sleep']['target']:
            if record['deviceCode'] == SOURCE:
                record['deviceCode'] = sleep_code
        if needs_sleep_source:
            result['sleep']['summary']['professionalSleep']['deviceCategory'] = category
            result['sleep']['registered_source'] = copy.deepcopy(sleep_source)
    return result
