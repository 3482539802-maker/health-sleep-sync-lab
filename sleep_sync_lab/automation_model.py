"""Deterministic single-day plans from private backups; fabricated sleep is labelled."""
import copy
from datetime import datetime, timedelta
import hashlib
import json
import random
from .model import bounds, check, content, rows, timezone_value, validate

SHALLOW = 'PROFESSIONAL_SLEEP_SHALLOW'
DEEP = 'PROFESSIONAL_SLEEP_DEEP'
REM = 'PROFESSIONAL_SLEEP_DREAM'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def choose(value, rng, low, high):
    if value is None or value == '':
        return None
    if isinstance(value, (list, tuple)):
        check(len(value) == 2, 'Random range needs two limits')
        a, b = value
        check(int(a) == a and int(b) == b and low <= a <= b <= high, 'Invalid random range')
        return rng.randint(int(a), int(b))
    result = int(value)
    check(float(value) == result and low <= result <= high, 'Value outside supported bounds')
    return result


def clock(value):
    parts = str(value).split(':')
    check(len(parts) == 2, 'Use HH:MM')
    h, m = map(int, parts)
    check(0 <= h <= 24 and 0 <= m < 60 and (h < 24 or m == 0), 'Invalid clock')
    return h * 60 + m


def windows(spec):
    result = []
    for value in spec.split(','):
        a, b = value.strip().split('-')
        a, b = clock(a), clock(b)
        check(0 <= a < b <= 1440, 'Activity windows must stay within selected day')
        result.append((a, b))
    check(bool(result), 'At least one activity window required')
    return result


def in_windows(record, base, allowed):
    minute = (int(record['startTime']) - base) // 60000
    return any(a <= minute < b for a, b in allowed)


def strip_ids(record):
    result = copy.deepcopy(record)
    for key in ['recordId', 'dataId', 'version']:
        result.pop(key, None)
    return result


def set_time(record, begin, end):
    record['startTime'], record['endTime'] = str(begin), str(end)
    for point in record.get('samplePoints', []):
        point['startTime'], point['endTime'] = str(begin), str(end)
    return record


def active_hours(snapshot, base):
    result = set()
    for record in rows(snapshot['active']):
        if int(record.get('mergedFlag', 0)) != 0:
            continue
        for point in record.get('samplePoints', []):
            if point.get('key') == 'ACTIVE_HOUR' and json.loads(point['value']).get('isActive') == 1:
                result.add((int(record['startTime']) - base) // 3600000)
    check(all(0 <= h <= 23 for h in result), 'Active records outside selected day')
    return result


def allocate(total, weights):
    check(total >= 0 and bool(weights), 'Cannot distribute target in selected windows')
    weights = [max(1, float(w)) for w in weights]
    raw = [total * w / sum(weights) for w in weights]
    result = [int(x) for x in raw]
    for i in sorted(range(len(raw)), key=lambda i: raw[i] - result[i], reverse=True)[:total - sum(result)]:
        result[i] += 1
    return result


def generated_stages(minutes, rng):
    """Stylised cycles, not clinical inference: early deep sleep, later REM."""
    check(240 <= minutes <= 720, 'Generated duration must be 4–12 hours')
    cycles = max(3, round(minutes / 90))
    lengths = allocate(minutes, [rng.randint(80, 105) for _ in range(cycles)])
    result = []
    for index, length in enumerate(lengths):
        progress = index / max(1, cycles - 1)
        deep = round(length * (0.32 - 0.23 * progress + rng.uniform(-.03, .03)))
        rem = round(length * (0.12 + 0.22 * progress + rng.uniform(-.02, .02)))
        shallow = length - deep - rem
        front = max(5, round(shallow * .52))
        result += [SHALLOW] * front + [DEEP] * deep + [SHALLOW] * (shallow - front) + [REM] * rem
    return result


def sleep_target(snapshot, config, request, rng):
    original = validate(rows(snapshot['sleep']), continuous=True)
    check(int(original[-1]['endTime']) - int(original[0]['startTime']) <= 86400000, 'Multiple nights are not supported')
    mode = request.get('mode', 'shift')
    base, _ = bounds(config)
    if mode == 'shift':
        advance = choose(request.get('advance_minutes', 120), rng, -720, 720)
        check(advance is not None, 'Shift missing')
        start = int(original[0]['startTime']) - advance * 60000
        sequence = [r['samplePoints'][0]['key'] for r in original]
    else:
        check(mode == 'random', 'Unknown sleep mode')
        sleep_lo, sleep_hi = map(clock, request['bedtime_range'])
        wake_lo, wake_hi = map(clock, request['wake_range'])
        # Clock ranges can cross midnight. Evening sleep belongs to the previous day.
        if sleep_hi < sleep_lo:
            sleep_hi += 1440
        if sleep_lo >= 720:
            sleep_lo -= 1440
            sleep_hi -= 1440
        check(0 <= wake_lo <= wake_hi < 1440, 'Wake window must be on selected day')
        min_minutes, max_minutes = map(int, request.get('duration_range', [360, 540]))
        check(240 <= min_minutes <= max_minutes <= 720, 'Sleep duration limits must be 4–12 hours')
        feasible = []
        for bed in range(sleep_lo, sleep_hi + 1):
            left, right = max(wake_lo, bed + min_minutes), min(wake_hi, bed + max_minutes)
            if left <= right:
                feasible.append((bed, left, right))
        check(bool(feasible), 'Sleep/wake windows and total duration do not overlap')
        bed, left, right = rng.choice(feasible)
        wake = rng.randint(left, right)
        start = base + bed * 60000
        sequence = generated_stages(wake - bed, rng)
    end = start + len(sequence) * 60000
    check(base - 86400000 <= start < end <= base + 86400000, 'Sleep must end on the selected day')
    check(end > base, 'Selected date must be the wake date')
    target = []
    for index, stage in enumerate(sequence):
        record = strip_ids(original[min(index, len(original) - 1)])
        set_time(record, start + index * 60000, start + (index + 1) * 60000)
        record['mergedFlag'] = 0
        record['samplePoints'][0]['key'] = stage
        target.append(record)
    validate(target, start, end)
    totals = snapshot['sleep_stats'].get('professionalSleepTotal') or []
    check(len(totals) == 1 and int(totals[0]['recordDay']) == int(config['date'].replace('-', '')), 'Unique sleep summary required')
    summary = copy.deepcopy(totals[0])
    summary.update(dataSource=2, deviceCode='0')
    s = summary['professionalSleep']
    s.update(allSleepTime=len(target), daySleepTime=0, awakeTime=0,
             lightSleepTime=sequence.count(SHALLOW), deepSleepTime=sequence.count(DEEP), dreamTime=sequence.count(REM),
             fallAsleepTime=str(start), goBedTime=str(start), wakeupTime=str(end), risingTime=str(end), wakeupCnt=0)
    s['deepSleepPart'] = sum(stage == DEEP and (i == 0 or sequence[i - 1] != DEEP) for i, stage in enumerate(sequence))
    if not request.get('restore_original_score', False):
        s['sleepScore'] = 0
    local = snapshot.get('local_sleep', {}).get('intervals', [])
    check(all(int(r['start']) >= int(original[0]['startTime']) - 7200000 and
              int(r['end']) <= int(original[-1]['endTime']) + 7200000 for r in local),
          'Local sleep includes another session; inspect selected-night deletion scope')
    return {'mode': mode, 'target': target, 'summary': summary, 'target_start': start, 'target_end': end,
            'delete_start': int(original[0]['startTime']), 'delete_end': int(original[-1]['endTime']),
            'local_delete_start': min([int(original[0]['startTime'])] + [int(r['start']) for r in local]),
            'local_delete_end': max([int(original[-1]['endTime'])] + [int(r['end']) for r in local]),
            'synthetic_not_measured': mode == 'random', 'original_score_not_recalculated': bool(request.get('restore_original_score'))}


def build_plan(snapshot, config, request, seed):
    for response in snapshot.values():
        check(isinstance(response, dict) and response.get('resultCode') == 0, 'Incomplete cloud backup; no write')
    rng = random.Random(seed)
    base, end = bounds(config)
    allowed = windows(request.get('activity_windows', '12:00-14:30,18:00-24:00'))
    stats = snapshot['sport_stats'].get('sportStat') or []
    check(len(stats) == 1 and stats[0].get('sportType') == 0, 'Unique day sport total required')
    stat = copy.deepcopy(stats[0])
    check(int(stat['recordDay']) == int(config['date'].replace('-', '')), 'Sport day differs')
    targets = {name: choose(request.get(name), rng, low, high) for name, low, high in
               [('calorie', 0, 10000), ('exercise', 0, 1440), ('steps', 0, 200000), ('active', 0, 24)]}
    sport_original = rows(snapshot['sport'])
    check(all(base <= int(r['startTime']) < int(r['endTime']) <= end for r in sport_original), 'Sport records outside selected day')
    working = copy.deepcopy(sport_original)
    hours = active_hours(snapshot, base)
    notices = []
    for field, multiplier in [('steps', 1), ('calorie', 1000)]:
        value = targets[field]
        if value is None:
            continue
        adopted = [r for r in working if field in r.get('mergedFields', [])]
        check(all(len(r['sportBasicInfos']) == 1 for r in adopted), 'Multiple sport samples unsupported')
        precise = sum(r['sportBasicInfos'][0][field] for r in adopted)
        residual = stat['sportBasicInfo'][field] - precise if field == 'calorie' else 0
        desired = value * multiplier - residual
        selected = [r for r in adopted if in_windows(r, base, allowed) and
                    (field != 'steps' or not request.get('preserve_active_hours', True) or
                     (int(r['startTime']) - base) // 3600000 in hours)]
        fixed = sum(r['sportBasicInfos'][0][field] for r in adopted if r not in selected)
        check(desired >= fixed, 'Target is below unmodified minutes or retained summary residual; expand windows')
        if desired != precise:
            distributed = allocate(int(desired - fixed), [r['sportBasicInfos'][0][field] for r in selected])
            for r, new in zip(selected, distributed):
                r['sportBasicInfos'][0][field] = new
        if value * multiplier < stat['sportBasicInfo'][field]:
            notices.append(field + ': lower total may be ignored by app/cloud maximum merging')
        stat['sportBasicInfo'][field] = value * multiplier
    sport_changes = [new for old, new in zip(sport_original, working) if content(old) != content(new)]
    intensity = []
    if targets['exercise'] is not None:
        old_minutes = int(stat['exerciseTimeBasic']['totalMidHighIntensity'])
        extra = targets['exercise'] - old_minutes
        if extra > 0:
            templates = rows(snapshot['intensity'])
            check(bool(templates), 'An existing exercise minute is required as source template')
            used = {int(r['startTime']) for r in templates}
            candidates = [base + m * 60000 for a, b in allowed for m in range(a, b)
                          if base + m * 60000 not in used and (not request.get('preserve_active_hours', True) or m // 60 in hours)]
            candidates = sorted(set(candidates))
            check(len(candidates) >= extra, 'Not enough unused exercise minutes in allowed hours')
            for begin in sorted(rng.sample(candidates, extra)):
                r = set_time(strip_ids(templates[0]), begin, begin + 60000)
                r['mergedFlag'] = 0
                r['samplePoints'][0].update(key='EXERCISE_INTENSITY', value='{"exerciseType":1}')
                intensity.append(r)
        elif extra < 0:
            notices.append('exercise: reduction only attempts the total; existing minutes are retained')
        current_map = stat['exerciseTimeBasic'].get('intensityMap', {})
        # Preserve categories when increasing. Reduction distributes total without deleting minutes.
        if extra >= 0:
            current_map['1'] = int(current_map.get('1', 0)) + extra
        else:
            values = allocate(targets['exercise'], list(current_map.values()))
            current_map = dict(zip(current_map, values))
        stat['exerciseTimeBasic'].update(totalMidHighIntensity=targets['exercise'], intensityMap=current_map)
    active = []
    if targets['active'] is not None:
        current = int(stat['activeHourBasic']['countActiveHour'])
        requested_hours = request.get('active_hours', [])
        check(all(isinstance(h, int) and 0 <= h <= 23 for h in requested_hours), 'Invalid active hour')
        missing = sorted(set(requested_hours) - hours)
        need = targets['active'] - current
        check(len(missing) <= max(0, need), 'More selected hours than target increase')
        possible = [h for h in range(24) if h not in hours and h not in missing and
                    any(a <= h * 60 and (h + 1) * 60 <= b for a, b in allowed)]
        check(need <= len(missing) + len(possible), 'Not enough inactive hours in allowed windows')
        if need > 0:
            missing += rng.sample(possible, need - len(missing))
            templates = rows(snapshot['active'])
            check(bool(templates), 'Existing active-hour source required')
            for hour in sorted(missing):
                begin = base + hour * 3600000
                r = set_time(strip_ids(templates[0]), begin, begin + 3599999)
                r['mergedFlag'] = 0
                for p in r['samplePoints']:
                    p.update(key='ACTIVE_HOUR', value='{"isActive":1}')
                    p.pop('fieldsModifyTime', None)
                active.append(r)
        elif need < 0:
            notices.append('active: existing active hours are retained; lower total may be ignored')
        stat['activeHourBasic']['countActiveHour'] = targets['active']
    stat.update(dataSource=2, deviceCode='0')
    sleep = sleep_target(snapshot, config, request['sleep'], rng) if request.get('sleep') else None
    return {'format_version': 2, 'config': copy.deepcopy(config), 'request': copy.deepcopy(request), 'seed': seed,
            'baseline': copy.deepcopy(snapshot), 'baseline_fingerprint': fingerprint(snapshot), 'targets': targets,
            'sport': sport_changes, 'intensity': intensity, 'active': active, 'sport_summary': stat,
            'sleep': sleep, 'notices': notices, 'cloud_and_phone_verification_required': True}


def validate_automation_plan(plan):
    check(plan.get('format_version') == 2, 'Unsupported automation plan')
    expected = build_plan(plan['baseline'], plan['config'], plan['request'], plan['seed'])
    check(canonical(expected) == canonical(plan), 'Plan differs from deterministic backup/request/seed')
    return plan


def describe(plan):
    validate_automation_plan(plan)
    result = {'date': plan['config']['date'], 'targets': plan['targets'],
              'sport_minutes_changed': len(plan['sport']), 'exercise_minutes_added': len(plan['intensity']),
              'active_hours_added': len(plan['active']), 'notices': plan['notices']}
    if plan['sleep']:
        s = plan['sleep']
        tz = timezone_value(plan['config']['timezone'])
        result['sleep'] = {'start': datetime.fromtimestamp(s['target_start'] / 1000, tz).isoformat(),
                           'end': datetime.fromtimestamp(s['target_end'] / 1000, tz).isoformat(),
                           'minutes': len(s['target']), 'synthetic_not_measured': s['synthetic_not_measured'],
                           'stages': {key: s['summary']['professionalSleep'][key] for key in ['lightSleepTime', 'deepSleepTime', 'dreamTime']},
                           'delete_and_rebuild': True, 'phone_local_deletion_not_guaranteed': True}
        result['sleep']['local_delete_start'] = datetime.fromtimestamp(s['local_delete_start'] / 1000, tz).isoformat()
        result['sleep']['local_delete_end'] = datetime.fromtimestamp(s['local_delete_end'] / 1000, tz).isoformat()
    return result
