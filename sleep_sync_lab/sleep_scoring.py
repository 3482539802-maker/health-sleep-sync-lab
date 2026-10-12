"""Explicit score provenance. The local estimate is not a vendor or medical score."""
from .model import check

SHALLOW = 'PROFESSIONAL_SLEEP_SHALLOW'
DEEP = 'PROFESSIONAL_SLEEP_DEEP'
REM = 'PROFESSIONAL_SLEEP_DREAM'


def score_mode(spec):
    mode = spec.get('score_mode', 'original' if spec.get('restore_original_score') else 'none')
    check(mode in ['none', 'original', 'estimate'], 'Unsupported sleep score mode')
    check(not spec.get('restore_original_score') or mode == 'original', 'Conflicting sleep score options')
    return mode


def original_score_value(value):
    check(type(value) is int and 1 <= value <= 100,
          'Original score missing or invalid; backup a positive score before phone deletion')
    return value


def estimate_score(sequence, model='stage_estimate_v2'):
    """Versioned descriptive index of a synthetic night; weights are project choices."""
    check(sequence and all(k in [SHALLOW, DEEP, REM] for k in sequence),
          'Score estimate supports continuous shallow/deep/REM minutes only')
    n = len(sequence)
    check(240 <= n <= 720, 'Score estimate supports 240-720 minutes')
    check(model in ['stage_estimate_v1', 'stage_estimate_v2'], 'Unsupported sleep score model')
    # Duration receives full credit at 7-9h, decreasing linearly outside it.
    duration = max(0.0, 1 - max(420 - n, n - 540, 0) / 180)
    def band(ratio, low, high, width):
        return max(0.0, 1 - max(low - ratio, ratio - high, 0) / width)
    deep = band(sequence.count(DEEP) / n, .15, .30, .15)
    rem = band(sequence.count(REM) / n, .20, .30, .20)
    # Frequent changes are penalized only as a description of the stage timeline.
    changes = sum(a != b for a, b in zip(sequence, sequence[1:]))
    changes_per_hour = changes * 60 / n
    continuity = max(0.0, 1 - max(changes_per_hour - 6, 0) / 12)
    components = {'duration': round(45 * duration, 4), 'deep_ratio': round(20 * deep, 4),
                  'rem_ratio': round(20 * rem, 4), 'stage_continuity': round(15 * continuity, 4)}
    raw = max(1, min(100, round(sum(components.values()))))
    ceiling = 95 if model == 'stage_estimate_v2' else 100
    details = {'mode': 'estimate', 'algorithm': model, 'vendor_recalculated': False,
            'medical_score': False, 'score': min(ceiling, raw),
            'minutes': n, 'stage_changes': changes, 'components': components,
            'limitations': 'Project-defined weights; no sensor quality, respiration, latency or measured sleep efficiency. '
                          'The phone numeric score field cannot display this provenance label.'}
    if model == 'stage_estimate_v2':
        details.update(raw_score=raw, score_ceiling=ceiling)
    return details


def apply_score(sleep, spec):
    mode = score_mode(spec)
    fields = sleep['summary']['professionalSleep']
    if mode == 'original':
        original_score_value(fields.get('sleepScore'))
    if mode == 'estimate':
        sequence = [r['samplePoints'][0]['key'] for r in sleep['target']]
        # Immutable earlier plans without an explicit model retain their V1 result.
        details = estimate_score(sequence, spec.get('score_model', 'stage_estimate_v1'))
        fields['sleepScore'] = details['score']
        sleep['score_provenance'] = details
    elif 'score_mode' in spec:
        sleep['score_provenance'] = {'mode': mode, 'score': fields.get('sleepScore', 0),
                                   'vendor_recalculated': False}
    return sleep
