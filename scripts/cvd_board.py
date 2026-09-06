#!/usr/bin/env python3
"""Offline corpus calibration and synthetic CVD answer-board experiment (stdlib only)."""
import argparse
import collections
import difflib
import hashlib
import heapq
import json
import math
from pathlib import Path
import random
import statistics

PAGE = 'dse/OpenAICVDDec08Fast2028'
CONDITIONS = ('independent', 'references', 'answers', 'disrupted')
DEFAULT = dict(agents=14, rounds=5, deadline=22.0, cadence=1210.0,
               stagger=30.0, research_median=45.0, research_sigma=0.7,
               research_accuracy=0.95, reference_remaining_fraction=0.65,
               poll=2.0, latency=5.0, error_rate=0.1, ttl=120.0,
               preparation=0.0, verify_seconds=5.0, verify_accuracy=0.9)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def calibrate(export):
    path = Path(export) / 'revisions.jsonl'
    rows = [json.loads(line) for line in path.open()]
    selected = sorted((r for r in rows if r['page_id'] == PAGE), key=lambda r: r['seq'])
    if not selected:
        raise ValueError('CVD board absent from export')
    index = {r['rev_id']: r for r in rows}
    additions = []
    for row in selected:
        body = row['body'].encode('latin1')
        if len(body) != row['body_len'] or hashlib.sha256(body).hexdigest() != row['body_sha256']:
            raise ValueError('Body integrity failure: ' + row['rev_id'])
        base = index[row['diff_base']]['body'] if row['diff_base'] else ''
        old, new = (base.split('\n') if base else []), row['body'].split('\n')
        ops = difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
        hunks = [dict(op=op, a0=a, a1=b, b0=c, b1=d)
                 for op, a, b, c, d in ops if op != 'equal']
        if hunks != row['hunks']:
            raise ValueError('Diff reconstruction failure: ' + row['rev_id'])
        additions.append(sum(d-c for op, a, b, c, d in ops if op in ('insert', 'replace')))
    return dict(source='https://collusion.wiki/explorer/page/dse~OpenAICVDDec08Fast2028.html',
                revisions_sha256=sha(path), manifest_sha256=sha(Path(export)/'manifest.json'),
                page_id=PAGE, revisions=len(selected), labels=len({r['label'] for r in selected}),
                first_edit= min(r['time'] for r in selected), last_edit=max(r['time'] for r in selected),
                reconstructed_diffs=len(additions), added_lines=sum(additions),
                revisions_with_additions=sum(n > 0 for n in additions),
                reported_task_parameters=dict(deadline_seconds=[17,22], cooldown_seconds=[992,1188],
                                              rounds_reported=5),
                interpretation='Task parameters are manually transcribed participant reports, not measured scaffold settings. Labels are not verified agents. Added lines include replacements and are not unique messages.')


def simulate(seed, condition, config=None):
    c = dict(DEFAULT, **(config or {}))
    if condition not in (*CONDITIONS, 'verified'):
        raise ValueError('Unknown condition')
    for k in ('agents', 'rounds'):
        if not isinstance(c[k], int) or c[k] < 1:
            raise ValueError(k + ' must be a positive integer')
    for k in ('deadline', 'cadence', 'research_median', 'poll', 'ttl', 'verify_seconds'):
        if c[k] <= 0:
            raise ValueError(k + ' must be positive')
    if c['latency'] < 0 or c['research_sigma'] < 0 or c['stagger'] < 0 or c['preparation'] < 0:
        raise ValueError('Latency, sigma, stagger and preparation must be nonnegative')
    for k in ('research_accuracy', 'reference_remaining_fraction', 'error_rate', 'verify_accuracy'):
        if not 0 <= c[k] <= 1:
            raise ValueError(k + ' outside [0,1]')
    rng = random.Random(seed)
    tasks, events, board = {}, [], collections.defaultdict(list)
    serial = 0
    def push(t, kind, key, payload=None):
        nonlocal serial
        serial += 1
        heapq.heappush(events, (t, serial, kind, key, payload))
    # Draw all private task outcomes before any intervention for paired comparisons.
    for agent in range(c['agents']):
        for rnd in range(c['rounds']):
            start = agent*c['stagger'] + rnd*c['cadence']
            work = rng.lognormvariate(math.log(c['research_median']), c['research_sigma'])
            good = rng.random() < c['research_accuracy']
            poison = rng.random() < c['error_rate']
            key = (agent, rnd)
            prep_spent = min(work, c['preparation'])
            finish = start + max(0.0, work-c['preparation'])
            tasks[key] = dict(start=start, end=start+c['deadline'], finish=finish, prep_spent=prep_spent, checking=False, checked=False,
                              good=good, poison=poison, done=False, reference=False, version=0)
            # Already prepared answers submit at release before polling the board.
            if finish == start:
                push(finish, 'finish', key, 0)
                push(start, 'poll', key)
            else:
                push(start, 'poll', key)
                push(finish, 'finish', key, 0)
            push(start+c['deadline'], 'deadline', key)
    stats = dict(correct=0, wrong=0, missed=0, copied=0, copied_wrong=0, effort=0.0)
    def submit(t, key, good, copied):
        task = tasks[key]
        task['done'] = True
        stats['correct' if good else 'wrong'] += 1
        stats['copied'] += int(copied)
        stats['copied_wrong'] += int(copied and not good)
        stats['effort'] += task['prep_spent'] + t-task['start']
        if condition == 'independent':
            return
        # Reference sharing requires completed private research, not just a copied answer.
        if condition == 'references' and copied:
            return
        delay = c['latency'] if condition in ('disrupted', 'verified') else 0.0
        value = good and not (condition in ('disrupted', 'verified') and task['poison'])
        push(t+delay, 'deliver', key, (value, t))
    while events:
        t, _, kind, key, payload = heapq.heappop(events)
        task = tasks[key]
        rnd = key[1]
        if kind == 'deliver':
            board[rnd].append((t, key, payload[0]))
            continue
        if task['done']:
            continue
        if kind == 'finish':
            if payload == task['version'] and t <= task['end']:
                submit(t, key, task['good'], False)
        elif kind == 'deadline':
            task['done'] = True
            stats['missed'] += 1
            stats['effort'] += task['prep_spent'] + c['deadline']
        elif kind == 'verify':
            task['checking'] = False
            if t < task['end']:
                good, remaining = payload
                # Separate deterministic stream leaves paired private draws unchanged.
                judge = random.Random(f'{seed}:verify:{key[0]}:{key[1]}')
                accurate = judge.random() < c['verify_accuracy']
                accept = good if accurate else not good
                if accept:
                    submit(t, key, good, True)
                else:
                    task['finish'] = t + remaining
                    push(task['finish'], 'finish', key, task['version'])
        elif kind == 'poll':
            if task['checking']:
                continue
            visible = [m for m in board[rnd] if m[1] != key and
                       (condition not in ('disrupted', 'verified') or t-m[0] < c['ttl'])]
            if visible and condition == 'verified' and not task['checked']:
                task['checked'] = task['checking'] = True
                task['version'] += 1  # Pause private research; invalidate old finish.
                remaining = max(0, task['finish']-t)
                push(t+c['verify_seconds'], 'verify', key, (visible[0][2], remaining))
            elif visible and condition in ('answers', 'disrupted'):
                # Earliest visible answer wins; no oracle or confidence assessment.
                submit(t, key, visible[0][2], True)
            else:
                if visible and condition == 'references' and not task['reference']:
                    task['reference'] = True
                    task['version'] += 1
                    task['finish'] = t + max(0, task['finish']-t)*c['reference_remaining_fraction']
                    push(task['finish'], 'finish', key, task['version'])
                if t+c['poll'] < task['end']:
                    push(t+c['poll'], 'poll', key)
    n = len(tasks)
    return dict(correct_rate=stats['correct']/n, wrong_rate=stats['wrong']/n,
                missed_rate=stats['missed']/n, copied_rate=stats['copied']/n,
                copied_wrong_rate=stats['copied_wrong']/n,
                research_seconds_per_task=stats['effort']/n)


def summarize(rows):
    return {k:dict(mean=statistics.mean(r[k] for r in rows),
                   seed_sd=statistics.stdev(r[k] for r in rows) if len(rows)>1 else 0)
            for k in rows[0]}


def experiment(calibration, seeds):
    reported = calibration['reported_task_parameters']
    c = dict(DEFAULT, agents=calibration['labels'],
             deadline=max(reported['deadline_seconds']),
             cadence=max(reported['deadline_seconds'])+max(reported['cooldown_seconds']),
             rounds=reported['rounds_reported'])
    cases = [('baseline', {}) , ('latency_60s', {'latency':60.0}),
             ('error_30pct', {'error_rate':0.3}), ('expiry_10s', {'ttl':10.0}),
             ('deadline_17s', {'deadline':17.0, 'cadence':1009.0}),
             ('synchronous', {'stagger':0.0}),
             ('research_15s', {'research_median':15.0})]
    results = []
    for name, overrides in cases:
        config = dict(c, **overrides)
        runs = {mode:[simulate(seed, mode, config) for seed in range(seeds)] for mode in CONDITIONS}
        results.append(dict(case=name, config=config,
            summary={mode:summarize(rows) for mode,rows in runs.items()},
            paired_correct_gain={mode:summarize([{'gain':a['correct_rate']-b['correct_rate']}
                for a,b in zip(runs[mode],runs['independent'])])['gain'] for mode in CONDITIONS[1:]},
            seeds=runs))
    return dict(schema_version=1, code_sha256=sha(__file__), seed_ids=list(range(seeds)),
                calibration=calibration, cases=results,
                limitations=['Synthetic task correctness and reads, not observed benchmark outcomes.',
                             'Fourteen labels are used as a scenario size, not inferred identities.',
                             'No training, model weights, migration, or actual medical data modeled.',
                             'Expiry is a synthetic deletion policy; it does not reproduce the historical sweep.',
                             'Research starts at each round; long preparatory phases are omitted.',
                             'No detector modeled; copied-error metrics describe synthetic ground truth.'])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='command', required=True)
    a=sub.add_parser('calibrate'); a.add_argument('--export',required=True); a.add_argument('--out',required=True)
    a=sub.add_parser('run'); a.add_argument('--calibration',required=True); a.add_argument('--seeds',type=int,default=100); a.add_argument('--out',required=True)
    args=ap.parse_args()
    if args.command=='calibrate':
        result=calibrate(args.export)
    else:
        if args.seeds < 2: ap.error('Use at least two seeds')
        result=experiment(json.loads(Path(args.calibration).read_text()),args.seeds)
    Path(args.out).write_text(json.dumps(result,indent=2)+'\n')
    print(args.out)


if __name__ == '__main__':
    main()
