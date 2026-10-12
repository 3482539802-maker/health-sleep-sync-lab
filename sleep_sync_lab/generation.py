"""Offline targets, frozen sleep stages, and later binding to a private backup."""
import copy
from datetime import datetime
import random
from .automation_model import (SHALLOW, DEEP, REM, choose, clock, canonical,
                               build_plan, legacy_generated_stages)
from .input_validation import validate_request, date_input
from .model import bounds, check, timezone_value
from .presets import sample_preset
from .sleep_generation import generated_stages, generated_stages_v1


def generate_legacy(config, request, seed):
    validate_request(request)
    date_input(config['date'])
    base, _ = bounds(config)
    rng = random.Random(seed)
    preset = request.get('preset')
    if preset:
        check(request.get('increase_only') is True and request.get('exclude_sleep') is True and
              request.get('sleep', {}).get('mode') == 'random' and not request.get('steps'),
              'Preset requires increase-only rings, sleep exclusion and unchanged steps')
        targets, bed, wake = sample_preset(preset, rng)
    else:
        targets = {name: choose(request.get(name), rng, low, high) for name, low, high in
                   [('calorie', 0, 10000), ('exercise', 0, 1440), ('steps', 0, 200000), ('active', 0, 24)]}
    sleep = None
    spec = request.get('sleep')
    if spec:
        model = spec.get('stage_model')
        check(model in [None, 'natural_v1', 'natural_v2'], 'Unsupported sleep stage model')
        mode = spec.get('mode', 'shift')
        if mode == 'shift':
            sleep = {'mode': mode, 'advance_minutes': choose(spec.get('advance_minutes', 120), rng, -720, 720)}
        else:
            if not preset:
                lo, hi = map(clock, spec['bedtime_range'])
                if hi < lo:
                    hi += 1440
                if lo >= 720:
                    lo -= 1440
                    hi -= 1440
                wl, wh = map(clock, spec['wake_range'])
                minimum, maximum = spec.get('duration_range', [360, 540])
                feasible = [(b, max(wl, b + minimum), min(wh, b + maximum)) for b in range(lo, hi + 1)
                            if max(wl, b + minimum) <= min(wh, b + maximum)]
                check(bool(feasible), 'Sleep/wake windows and total duration do not overlap')
                bed, left, right = rng.choice(feasible)
                wake = rng.randint(left, right)
            generator = {'natural_v1': generated_stages_v1, 'natural_v2': generated_stages}.get(model, legacy_generated_stages)
            sleep = {'mode': mode, 'target_start': base + bed * 60000, 'target_end': base + wake * 60000,
                     'sequence': generator(wake - bed, rng)}
            check(base - 86400000 <= sleep['target_start'] < sleep['target_end'] <= base + 86400000 and
                  sleep['target_end'] > base, 'Sleep must end on the selected day')
    return {'format_version': 3, 'config': copy.deepcopy(config), 'request': copy.deepcopy(request),
            'seed': seed, 'targets': targets, 'sleep': sleep, 'offline_generated': True,
            'execution_backup_required': True}


def generate(config, request, seed):
    generation = generate_legacy(config, request, seed)
    if request.get('planning_mode') == 'standalone':
        from .standalone import build, requires_original
        if not requires_original(request):
            return build(generation)
    return generation


def validate_generation(plan):
    check(plan.get('format_version') == 3, 'Unsupported automation plan')
    check(canonical(generate_legacy(plan['config'], plan['request'], plan['seed'])) == canonical(plan),
          'Plan differs from deterministic backup/request/seed')
    return plan


def bind_plan(generation, snapshot):
    validate_generation(generation)
    if generation['request'].get('planning_mode') == 'standalone':
        from .standalone import build
        return build(generation, snapshot, bound=True)
    plan = build_plan(snapshot, generation['config'], generation['request'], generation['seed'], frozen=generation)
    plan.update(format_version=4, generation=copy.deepcopy(generation))
    return plan


def describe_generation(plan):
    validate_generation(plan)
    notices = ['离线计划已固定；执行前备份／核验会读取原数据并计算修改明细，不重新抽样。']
    if plan['request'].get('increase_only'):
        notices.append('三环只增、清醒小时容量及运动避开原睡眠，在执行前备份／核验时检查。')
    result = {'date': plan['config']['date'], 'targets': plan['targets'], 'offline_generated': True,
              'sport_minutes_changed': None, 'exercise_minutes_added': None, 'active_hours_added': None,
              'notices': notices, 'execution_backup_required': True}
    if plan['sleep']:
        s = plan['sleep']
        if s['mode'] == 'shift':
            result['sleep'] = {'mode': 'shift', 'advance_minutes': s['advance_minutes'], 'original_required': True}
            notices.append('整体提前已固定分钟数；具体起止和原阶段将在执行前读取备份后显示。')
        else:
            tz = timezone_value(plan['config']['timezone'])
            result['sleep'] = {'mode': 'random',
                'start': datetime.fromtimestamp(s['target_start'] / 1000, tz).isoformat(),
                'end': datetime.fromtimestamp(s['target_end'] / 1000, tz).isoformat(),
                'minutes': len(s['sequence']), 'synthetic_not_measured': True,
                'stages': {k: s['sequence'].count(stage) for k, stage in
                           [('lightSleepTime', SHALLOW), ('deepSleepTime', DEEP), ('dreamTime', REM)]},
                'local_delete_start': '执行前核验后确定', 'local_delete_end': '执行前核验后确定'}
        if plan['request']['sleep'].get('restore_original_score'):
            notices.append('保留原评分需要在手机删除前完成执行前备份；离线生成不读取或编造评分。')
    return result
