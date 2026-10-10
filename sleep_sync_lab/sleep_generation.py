"""Offline synthetic blocks; sleep architecture heuristics, never medical inference."""
import argparse
import json
import math
import random
from .model import check

SHALLOW = 'PROFESSIONAL_SLEEP_SHALLOW'
DEEP = 'PROFESSIONAL_SLEEP_DEEP'
REM = 'PROFESSIONAL_SLEEP_DREAM'


def weighted_integer(spec, rng, low=None, high=None):
    """Finite discrete bell mixture: a narrow preferred band plus a broad tail."""
    a, b = map(int, spec['range'])
    p, q = map(int, spec.get('preferred', [a, b]))
    check(a <= p <= q <= b, 'Invalid preferred range')
    a, b = max(a, low) if low is not None else a, min(b, high) if high is not None else b
    check(a <= b, 'No feasible weighted range')
    center = (p + q) / 2
    narrow = max(1, (q - p + 1) / 2.5)
    broad = max(narrow, (spec['range'][1] - spec['range'][0] + 1) / 2)
    values = list(range(a, b + 1))
    # Density normalization makes the preferred band dominant, tails still reachable.
    weights = [.8 * math.exp(-.5 * ((x - center) / narrow) ** 2) / narrow +
               .2 * math.exp(-.5 * ((x - center) / broad) ** 2) / broad for x in values]
    return rng.choices(values, weights=weights, k=1)[0]


def distribute(total, weights):
    raw = [total * w / sum(weights) for w in weights]
    result = [int(x) for x in raw]
    for i in sorted(range(len(raw)), key=lambda i: raw[i] - result[i], reverse=True)[:total - sum(result)]:
        result[i] += 1
    return result


def bounded_blocks(total, weights, minimum, maximum):
    check(minimum * len(weights) <= total <= maximum * len(weights), 'Block total outside bounds')
    result = [minimum] * len(weights)
    remaining = total - sum(result)
    while remaining:
        eligible = [i for i, n in enumerate(result) if n < maximum]
        additions = distribute(remaining, [weights[i] for i in eligible])
        for i, n in zip(eligible, additions):
            result[i] += min(n, maximum - result[i])
        remaining = total - sum(result)
    return result


def generated_stages_v1(minutes, rng):
    """Long variable blocks, early deep and later REM; N1/N2 share shallow label."""
    check(240 <= minutes <= 720, 'Generated duration must be 4–12 hours')
    feasible = [n for n in range(3, 9) if 80 <= minutes / n <= 120]
    cycles = rng.choices(feasible, weights=[math.exp(-.5 * ((minutes / n - 100) / 10) ** 2) for n in feasible], k=1)[0]
    lengths = distribute(minutes, [rng.uniform(90, 110) for _ in range(cycles)])
    deep_total = round(minutes * rng.uniform(.19, .25))
    rem_total = round(minutes * rng.uniform(.22, .27))
    deep_cycles = max(2, min(cycles - 1, round(cycles * .65)), math.ceil(deep_total / 55))
    deep = bounded_blocks(deep_total,
            [rng.uniform(.9, 1.1) * (deep_cycles - i) for i in range(deep_cycles)], 6, 55)
    deep += [0] * (cycles - deep_cycles)
    rem = [6 + n for n in distribute(rem_total - 6 * cycles,
           [(.7 + i * .55) * rng.uniform(.9, 1.1) for i in range(cycles)])]
    sequence = []
    for i, length in enumerate(lengths):
        light = length - deep[i] - rem[i]
        check(light >= 12, 'Synthetic cycle has insufficient shallow sleep')
        if deep[i]:
            front = max(6, min(light - 6, round(light * rng.uniform(.4, .65))))
            sequence += [SHALLOW] * front + [DEEP] * deep[i] + [SHALLOW] * (light - front)
        else:
            sequence += [SHALLOW] * light
        sequence += [REM] * rem[i]
    return sequence


def generated_stages(minutes, rng):
    """V2: preserve architecture/totals, allow brief blocks, soften long runs."""
    base = stage_blocks(generated_stages_v1(minutes, rng))
    result = []
    i = 0
    while i < len(base):
        block = base[i]
        stage = block['stage']
        length = block['end_minute'] - block['start_minute']
        if i + 1 < len(base):
            following = base[i + 1]
            other = following['stage']
            other_length = following['end_minute'] - following['start_minute']
            pieces = math.ceil(length / 48)
            if length > 50 and other_length >= 3 * pieces and (length > 65 or rng.random() < .94):
                # Move a few minutes of the adjacent stage into this long run.
                # This keeps all stage totals and the night's duration unchanged.
                lengths = bounded_blocks(length, [rng.uniform(.8, 1.2) for _ in range(pieces)], 10, 48)
                brief = [rng.randint(3, min(9, (other_length - 3) // (pieces - 1)))
                         for _ in range(pieces - 1)]
                for j, part in enumerate(lengths):
                    result.extend([stage] * part)
                    if j < len(brief):
                        result.extend([other] * brief[j])
                result.extend([other] * (other_length - sum(brief)))
                i += 2
                continue
            if stage == SHALLOW and length >= 18 and 15 <= other_length <= 50 and rng.random() < .18:
                front, brief = rng.randint(5, 9), rng.randint(3, 8)
                result.extend([stage] * front + [other] * brief + [stage] * (length - front) +
                              [other] * (other_length - brief))
                i += 2
                continue
        result.extend([stage] * length)
        i += 1
    return result


def stage_blocks(sequence):
    blocks = []
    for i, stage in enumerate(sequence):
        if blocks and blocks[-1]['stage'] == stage:
            blocks[-1]['end_minute'] = i + 1
        else:
            blocks.append({'stage': stage, 'start_minute': i, 'end_minute': i + 1})
    return blocks


def main():
    parser = argparse.ArgumentParser(description='Offline synthetic sleep samples; no cloud connection')
    parser.add_argument('--count', type=int, default=10)
    parser.add_argument('--minutes', type=int, default=480)
    parser.add_argument('--start', default='00:45')
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    check(1 <= args.count <= 100, 'Sample count must be 1–100')
    h, m = map(int, args.start.split(':'))
    check(0 <= h < 24 and 0 <= m < 60, 'Invalid clock')
    rng = random.Random(args.seed)
    labels = {SHALLOW: '浅睡', DEEP: '深睡', REM: 'REM'}
    samples = []
    for i in range(args.count):
        sequence = generated_stages(args.minutes, rng)
        blocks = stage_blocks(sequence)
        sample = {'sample': i + 1, 'minutes': len(sequence), 'switches': len(blocks) - 1,
                  'totals': {labels[s]: sequence.count(s) for s in labels}, 'blocks': blocks}
        samples.append(sample)
        if not args.json:
            print(f"第{i + 1}次：{args.minutes}分钟，切换{len(blocks) - 1}次，" +
                  '、'.join(f'{k}{v}分钟' for k, v in sample['totals'].items()))
            def clock(offset):
                value = h * 60 + m + offset
                return f'{value // 60 % 24:02}:{value % 60:02}'
            print(' → '.join(f"{clock(b['start_minute'])}–{clock(b['end_minute'])} {labels[b['stage']]}" for b in blocks))
            print()
    if args.json:
        print(json.dumps(samples, ensure_ascii=True))


if __name__ == '__main__':
    main()
