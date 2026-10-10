"""Configurable private presets. Personal targets never belong in public defaults."""
import copy
from .model import check
from .sleep_generation import weighted_integer


def preset_request(spec):
    check(spec.get('version') == 1, 'Unsupported preset version')
    for field in ['calorie', 'exercise', 'active', 'bedtime', 'wake']:
        check(field in spec, 'Incomplete preset')
    def clocks(values):
        return [f'{v // 60:02}:{v % 60:02}' for v in values]
    return {'preset': copy.deepcopy(spec), 'activity_windows': '00:00-24:00',
            'preserve_active_hours': True, 'increase_only': True, 'exclude_sleep': True,
            'sleep': {'mode': 'random', 'stage_model': 'natural_v2',
                      'bedtime_range': clocks(spec['bedtime']['range']), 'wake_range': clocks(spec['wake']['range']),
                      'duration_range': spec['duration_range'],
                      'restore_original_score': spec.get('restore_original_score', False)}}


def sample_preset(spec, rng):
    check(spec.get('version') == 1, 'Unsupported preset version')
    for field, low, high in [('calorie', 0, 10000), ('exercise', 0, 1440), ('active', 0, 24)]:
        a, b = spec[field]['range']
        check(isinstance(a, int) and isinstance(b, int) and low <= a <= b <= high, 'Invalid preset target bounds')
    result = {k: weighted_integer(spec[k], rng) for k in ['calorie', 'exercise', 'active']}
    result['steps'] = None
    minimum, maximum = map(int, spec['duration_range'])
    check(240 <= minimum <= maximum <= 720, 'Invalid preset sleep duration')
    bed_lo, bed_hi = spec['bedtime']['range']
    wake_lo, wake_hi = spec['wake']['range']
    check(0 <= bed_lo <= bed_hi < wake_lo <= wake_hi < 1440, 'Invalid preset sleep windows')
    feasible_beds = [b for b in range(bed_lo, bed_hi + 1) if max(wake_lo, b + minimum) <= min(wake_hi, b + maximum)]
    check(bool(feasible_beds), 'Sleep/wake windows and total duration do not overlap')
    # Weight the feasible bedtime distribution directly; never retry cloud operations.
    restricted = copy.deepcopy(spec['bedtime'])
    # Feasible beds form a contiguous interval for these noncrossing windows.
    bed = weighted_integer(restricted, rng, min(feasible_beds), max(feasible_beds))
    wake = weighted_integer(spec['wake'], rng, max(wake_lo, bed + minimum), min(wake_hi, bed + maximum))
    return result, bed, wake
