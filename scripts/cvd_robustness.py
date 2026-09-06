#!/usr/bin/env python3
"""Paired sensitivity experiment for preparation and hypothetical answer checking."""
import argparse
import json
from pathlib import Path
import cvd_board as board

MODES = ('independent', 'disrupted', 'verified')
CASES = [('no_preparation', {}), ('prepare_15s', {'preparation':15.0}),
         ('prepare_45s', {'preparation':45.0}), ('prepare_120s', {'preparation':120.0}),
         ('corruption_30pct', {'error_rate':0.3}),
         ('slow_check_15s', {'verify_seconds':15.0}),
         ('weak_check_60pct', {'verify_accuracy':0.6}),
         ('prepare_45s_corruption_30pct', {'preparation':45.0,'error_rate':0.3})]


def experiment(calibration, seeds):
    reported = calibration['reported_task_parameters']
    config = dict(board.DEFAULT, agents=calibration['labels'],
                  rounds=reported['rounds_reported'],
                  deadline=max(reported['deadline_seconds']),
                  cadence=max(reported['deadline_seconds'])+max(reported['cooldown_seconds']))
    cases=[]
    for name, overrides in CASES:
        c=dict(config, **overrides)
        runs={mode:[board.simulate(seed,mode,c) for seed in range(seeds)] for mode in MODES}
        delta=[{'correct_gain':a['correct_rate']-b['correct_rate'],
                'wrong_gain':a['wrong_rate']-b['wrong_rate'],
                'effort_gain':a['research_seconds_per_task']-b['research_seconds_per_task']}
               for a,b in zip(runs['verified'],runs['disrupted'])]
        cases.append(dict(case=name,config=c,summary={m:board.summarize(v) for m,v in runs.items()},
                          verified_minus_disrupted=board.summarize(delta),seeds=runs))
    return dict(schema_version=1,simulator_sha256=board.sha(board.__file__),
                runner_sha256=board.sha(__file__),seed_ids=list(range(seeds)),
                calibration=calibration,cases=cases)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--calibration',default='data/cvd_calibration.json')
    ap.add_argument('--seeds',type=int,default=100)
    ap.add_argument('--out',required=True)
    args=ap.parse_args()
    if args.seeds<2: ap.error('Use at least two seeds')
    result=experiment(json.loads(Path(args.calibration).read_text()),args.seeds)
    Path(args.out).write_text(json.dumps(result,indent=2)+'\n')
    print(args.out)


if __name__=='__main__': main()
