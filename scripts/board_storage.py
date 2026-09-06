#!/usr/bin/env python3
"""Paired synthetic board-storage/retraction study. No raw corpus bodies required."""
import argparse
import hashlib
import heapq
import json
import math
import random
import statistics
from pathlib import Path

DEFAULT = dict(writers=80, readers=100, duration=90.0, write_delay=20.0,
               correction_time=25.0, evidence_time=65.0, deadline=22.0,
               cache_age=5.0, research_median=45.0, research_accuracy=0.95,
               remember_retractions=True)


class Board:
    def __init__(self, mode):
        if mode not in ('snapshot', 'append_only'): raise ValueError('Invalid storage mode')
        self.mode=mode
        self.visible=set()
        self.seen=set()
        self.removed=0
        self.restored=0

    def commit(self, message, snapshot):
        new=(set(snapshot) if self.mode=='snapshot' else self.visible.copy()) | {message}
        self.removed+=len(self.visible-new)
        self.restored+=len((new-self.visible) & self.seen)
        self.visible=new
        self.seen.update(new)


def choose(visible, messages, remembered=()):
    """Retractions remove support for a target; hypotheses never become evidence."""
    invalid={messages[m]['target'] for m in set(visible)|set(remembered)
             if messages[m]['kind']=='retract'}
    claims=[m for m in visible if messages[m]['kind']=='evidence' and m not in invalid]
    return max(claims,key=lambda m:(messages[m]['created'],m)) if claims else None


def traffic(seed, config):
    c=dict(DEFAULT,**config)
    for k in ('writers','readers'):
        if not isinstance(c[k],int) or c[k]<1: raise ValueError(k)
    for k in ('duration','deadline','research_median'):
        if c[k]<=0: raise ValueError(k)
    for k in ('write_delay','cache_age','correction_time','evidence_time'):
        if c[k]<0: raise ValueError(k)
    if not 0<=c['research_accuracy']<=1: raise ValueError('research_accuracy')
    rng=random.Random(seed)
    messages={
        'bad':dict(kind='evidence',correct=False,created=0.0),
        'hypothesis':dict(kind='hypothesis',correct=True,created=15.0),
        'retract':dict(kind='retract',target='bad',created=c['correction_time']),
        'good':dict(kind='evidence',correct=True,created=c['evidence_time'])}
    writes=[dict(message='bad',read=0.0,delay=0.0),
            dict(message='hypothesis',read=15.0,delay=rng.random()*c['write_delay']),
            dict(message='retract',read=c['correction_time'],delay=rng.random()*c['write_delay']),
            dict(message='good',read=c['evidence_time'],delay=rng.random()*c['write_delay'])]
    for i in range(c['writers']):
        start=rng.random()*c['duration']
        ident=f'noise-{i}'
        messages[ident]=dict(kind='note',created=start)
        writes.append(dict(message=ident,read=start,delay=rng.random()*c['write_delay']))
    readers=[]
    for i in range(c['readers']):
        start=rng.random()*c['duration']
        readers.append(dict(start=start,end=start+c['deadline'],
            final_read=max(start,start+c['deadline']-c['cache_age']),
            work=rng.lognormvariate(math.log(c['research_median']),0.7),
            correct=rng.random()<c['research_accuracy']))
    return c,messages,writes,readers


def simulate(seed,mode,config=None):
    c,messages,writes,readers=traffic(seed,config or {})
    board=Board(mode);events=[];serial=0
    def push(t,kind,key,payload=None):
        nonlocal serial
        serial+=1;heapq.heappush(events,(t,serial,kind,key,payload))
    for w in writes:push(w['read'],'read_write',w['message'],w['delay'])
    for i,r in enumerate(readers):
        push(r['start'],'read',i)
        if r['final_read'] != r['start']: push(r['final_read'],'read',i)
        push(r['end'],'decide',i)
    local={i:set() for i in range(len(readers))};memory={i:set() for i in local}
    counts=dict(correct=0,wrong=0,missed=0,board_answers=0,post_retraction_reads=0,
                unsupported_bad_exposures=0,stale_decisions=0)
    first_retract=None;missing_seconds=0.0;last=0.0
    for_reader=[]
    while events:
        t,_,kind,key,payload=heapq.heappop(events)
        if first_retract is not None and 'retract' not in board.visible:
            missing_seconds+=t-last
        last=t
        if kind=='read_write':
            push(t+payload,'commit',key,board.visible.copy())
        elif kind=='commit':
            board.commit(key,payload)
            if key=='retract': first_retract=t
        elif kind=='read':
            local[key]=board.visible.copy()
            if c['remember_retractions']:
                memory[key].update(m for m in local[key] if messages[m]['kind']=='retract')
            if first_retract is not None:
                counts['post_retraction_reads']+=1
                active=choose(local[key],messages,memory[key])
                counts['unsupported_bad_exposures']+=active=='bad'
        elif kind=='decide':
            r=readers[key]
            if r['work']<=c['deadline']:
                correct=r['correct'];source='private'
            else:
                source=choose(local[key],messages,memory[key])
                correct=messages[source]['correct'] if source else None
                counts['board_answers']+=source is not None
                if source:
                    counts['stale_decisions']+=source!=choose(board.visible,messages,memory[key])
            counts['missed' if correct is None else ('correct' if correct else 'wrong')]+=1
            for_reader.append(dict(reader=key,source=source,correct=correct))
    n=len(readers)
    identity=json.dumps(dict(writes=writes,readers=readers,messages=messages),sort_keys=True).encode()
    return dict(traffic_sha256=hashlib.sha256(identity).hexdigest(),
        metrics=dict(correct_rate=counts['correct']/n,wrong_rate=counts['wrong']/n,
        missed_rate=counts['missed']/n,board_answer_rate=counts['board_answers']/n,
        stale_decision_rate=counts['stale_decisions']/n,
        post_retraction_bad_exposure_rate=(counts['unsupported_bad_exposures']/counts['post_retraction_reads']
                                          if counts['post_retraction_reads'] else 0.0),
        post_retraction_reads=counts['post_retraction_reads'],
        removed_message_events=board.removed,restored_message_events=board.restored,
        final_message_survival=len(board.visible)/len(messages),
        correction_final_present=int('retract' in board.visible),
        correction_missing_time_fraction=(missing_seconds/(last-first_retract)
                                         if first_retract is not None and last>first_retract else 0.0)),
        decisions=for_reader)


CASES=[('baseline',{}),('no_write_delay',{'write_delay':0.0}),
       ('slow_writes',{'write_delay':40.0}),('fresh_readers',{'cache_age':0.0}),
       ('stale_readers',{'cache_age':20.0}),('late_retraction',{'correction_time':65.0}),
       ('no_retraction_memory',{'remember_retractions':False}),
       ('late_evidence',{'evidence_time':100.0})]


def summary(rows):
    return {k:dict(mean=statistics.mean(r[k] for r in rows),seed_sd=statistics.stdev(r[k] for r in rows))
            for k in rows[0]}


def experiment(seeds):
    cases=[]
    for name,config in CASES:
        runs={mode:[simulate(s,mode,config) for s in range(seeds)] for mode in ('snapshot','append_only')}
        for a,b in zip(runs['snapshot'],runs['append_only']):
            assert a['traffic_sha256']==b['traffic_sha256']
        delta=[{k:b['metrics'][k]-a['metrics'][k] for k in a['metrics']}
               for a,b in zip(runs['snapshot'],runs['append_only'])]
        cases.append(dict(case=name,config=dict(DEFAULT,**config),
            summary={m:summary([r['metrics'] for r in v]) for m,v in runs.items()},
            append_minus_snapshot=summary(delta),
            seeds={m:[dict(traffic_sha256=r['traffic_sha256'],metrics=r['metrics']) for r in v]
                   for m,v in runs.items()}))
    return dict(schema_version=1,code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                seed_ids=list(range(seeds)),cases=cases,
                grounding='Motivated by analysis/raw-log-findings.md; traffic and parameters are synthetic, not fitted.')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--seeds',type=int,default=100);ap.add_argument('--out',required=True)
    args=ap.parse_args()
    if args.seeds<2:ap.error('At least two seeds required')
    Path(args.out).write_text(json.dumps(experiment(args.seeds),indent=2)+'\n')
    print(args.out)


if __name__=='__main__':main()
