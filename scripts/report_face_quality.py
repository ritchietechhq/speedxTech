#!/usr/bin/env python3
"""Summarize per-face timing JSONL by settings; warmup removed per group."""
import argparse
import json
from collections import defaultdict
from statistics import median


def summarize(records, warmup=5):
    groups = defaultdict(list)
    for record in records:
        groups[json.dumps(record['settings'], sort_keys=True)].append(record['stages_ms'])
    report = []
    for settings, samples in groups.items():
        samples = samples[warmup:]
        stages = {}
        if samples:
            for name in samples[0]:
                values = sorted(sample[name] for sample in samples)
                stages[name] = {'p50_ms': median(values), 'p95_ms': values[min(len(values) - 1, int(len(values) * 0.95))]}
        report.append({'settings': json.loads(settings), 'measured_faces': len(samples), 'stages': stages})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('timings')
    parser.add_argument('--warmup', type=int, default=5)
    args = parser.parse_args()
    if args.warmup < 0:
        parser.error('warmup must be nonnegative')
    with open(args.timings) as handle:
        records = [json.loads(line) for line in handle if line.strip()]
    print(json.dumps(summarize(records, args.warmup), indent=2))


if __name__ == '__main__':
    main()
