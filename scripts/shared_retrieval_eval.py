#!/usr/bin/env python3
"""Offline authored graph retrieval with isolated or shared evidence. No model API."""
import argparse
import collections
import hashlib
import heapq
import json
from pathlib import Path
import random
import statistics
from board_storage import Board

DEFAULT=dict(agents=8,stagger=8.0,budget=40.0,read_cost_min=2.0,read_cost_max=5.0,
             poll=4.0,write_delay=8.0,hide_current=False)
MODES=('isolated','snapshot','append_only')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def grade(answer, reference):
    # Deliberately narrow schema: exact integer string, no semantic model grader.
    if answer is None or not str(answer).strip():return 'not_attempted'
    return 'correct' if str(answer).strip()==reference else 'incorrect'


def validate(fixture):
    ids=set()
    for task in fixture['tasks']:
        if task['id'] in ids:raise ValueError('Duplicate task ID')
        ids.add(task['id'])
        docs=task['documents']
        if task['root'] not in docs:raise ValueError('Missing root')
        for doc in docs.values():
            if any(link not in docs for link in doc['links']):raise ValueError('Broken graph link')
            if 'record' in doc and any(x not in docs for x in doc['record']['supersedes']):
                raise ValueError('Broken retraction target')
        records=[d['record'] for d in docs.values() if 'record' in d]
        latest=max(records,key=lambda x:x['revision'])
        if latest['value']!=task['reference_answer']:raise ValueError('Invalid reference')


def plan(task, seed, agent, c):
    """Randomized depth-first crawl; sees source graph, never the answer key."""
    rng=random.Random(f'{seed}:{task["id"]}:{agent}')
    queue=collections.deque([task['root']]);seen=set();clock=agent*c['stagger'];result=[]
    end=clock+c['budget']
    while queue:
        source=queue.pop()
        if source in seen:continue
        seen.add(source)
        clock+=rng.uniform(c['read_cost_min'],c['read_cost_max'])
        if clock>=end:break
        doc=task['documents'][source]
        record=doc.get('record')
        # Environment ablation: current source's record is unavailable for everyone.
        if record and c['hide_current'] and record['revision']==2:record=None
        result.append(dict(time=clock,source=source,record=record,delay=rng.random()*c['write_delay']))
        links=list(doc['links']);rng.shuffle(links);queue.extend(links)
    return result


def evaluate_task(task,seed,mode,config=None):
    if mode not in MODES:raise ValueError('Unknown mode')
    c=dict(DEFAULT,**(config or {}))
    if not isinstance(c['agents'],int) or c['agents']<1:raise ValueError('agents')
    if min(c['budget'],c['poll'],c['read_cost_min'])<=0:raise ValueError('Positive budgets required')
    if c['read_cost_max']<c['read_cost_min'] or min(c['stagger'],c['write_delay'])<0:raise ValueError('Invalid timing')
    # Public source view deliberately strips reference_answer before planning.
    public={k:v for k,v in task.items() if k!='reference_answer'}
    plans=[plan(public,seed,a,c) for a in range(c['agents'])]
    board=Board('append_only' if mode=='isolated' else mode)
    messages={};private=[{} for _ in plans];view=[{} for _ in plans];revoked=[set() for _ in plans]
    observed=[set() for _ in plans];updates=[0 for _ in plans]
    traces=[];decisions=[];events=[];serial=0;first_correction=None
    def push(t,kind,a,payload=None):
        nonlocal serial
        serial+=1;heapq.heappush(events,(t,serial,kind,a,payload))
    def select(a):
        available=dict(view[a]);available.update(private[a])
        candidates=[(s,v) for s,v in available.items() if s not in revoked[a]]
        return max(candidates,key=lambda sv:(sv[1]['record']['revision'],sv[0])) if candidates else None
    for a,p in enumerate(plans):
        for read in p:push(read['time'],'fetch',a,read)
        start=a*c['stagger'];end=start+c['budget'];t=start
        if mode!='isolated':
            while t<end:push(t,'poll',a);t+=c['poll']
        push(end,'decide',a)
    while events:
        t,_,kind,a,payload=heapq.heappop(events)
        if kind=='fetch':
            source=payload['source'];record=payload['record']
            traces.append(dict(time=t,agent=a,event='fetch',source=source))
            if record and record['subject']==task['subject'] and record['field']==task['field']:
                private[a][source]=dict(record=record,via='private',discoverer=a,message=None)
                revoked[a].update(record['supersedes'])
                if mode!='isolated':
                    ident=f'{a}:{source}'
                    messages[ident]=dict(source=source,record=record,discoverer=a)
                    push(t+payload['delay'],'commit',a,(ident,board.visible.copy()))
        elif kind=='commit':
            ident,snapshot=payload;board.commit(ident,snapshot)
            m=messages[ident]
            if m['record']['supersedes'] and first_correction is None:first_correction=t
            traces.append(dict(time=t,agent=a,event='publish',message=ident,source=m['source'],
                               retracts=m['record']['supersedes']))
        elif kind=='poll':
            before=select(a);view[a]={}
            for ident in sorted(board.visible):
                m=messages[ident]
                view[a][m['source']]=dict(record=m['record'],via='board',discoverer=m['discoverer'],message=ident)
                revoked[a].update(m['record']['supersedes'])
                if m['record']['supersedes'] and ident not in observed[a]:
                    observed[a].add(ident)
                    traces.append(dict(time=t,agent=a,event='correction_receipt',message=ident,
                                       retracts=m['record']['supersedes']))
            after=select(a)
            if before and before[0] in revoked[a] and (not after or after[0]!=before[0]):
                updates[a]+=1
                traces.append(dict(time=t,agent=a,event='correction_applied',invalidated=before[0],
                                   replacement=after[0] if after else None))
        elif kind=='decide':
            choice=select(a)
            source,value=choice if choice else (None,None)
            answer=value['record']['value'] if value else None
            decisions.append(dict(agent=a,time=t,answer=answer,grade=grade(answer,task['reference_answer']),
                source=source,via=value['via'] if value else None,
                discoverer=value['discoverer'] if value else None,message=value['message'] if value else None,
                correction_received=bool(observed[a]),correction_applied=updates[a]>0,
                correction_available_before_deadline=first_correction is not None and first_correction<t))
    return dict(task=task['id'],plan_sha256=digest(plans),decisions=decisions,trace=traces,
                removed_message_events=board.removed)


def metrics(runs):
    ds=[d for r in runs for d in r['decisions']];n=len(ds)
    eligible=[d for d in ds if d['correction_available_before_deadline']]
    received=[d for d in ds if d['correction_received']]
    return dict(correct_rate=sum(d['grade']=='correct' for d in ds)/n,
                incorrect_rate=sum(d['grade']=='incorrect' for d in ds)/n,
                not_attempted_rate=sum(d['grade']=='not_attempted' for d in ds)/n,
                board_answer_rate=sum(d['via']=='board' for d in ds)/n,
                correction_received_rate=len(received)/n,
                correction_applied_rate=sum(d['correction_applied'] for d in ds)/n,
                correction_eligible_decisions=len(eligible),
                eligible_receipt_rate=sum(d['correction_received'] for d in eligible)/len(eligible) if eligible else None,
                incorrect_after_receipt=sum(d['grade']=='incorrect' for d in received),
                removed_message_events=sum(r['removed_message_events'] for r in runs))


CASES=[('baseline',{}),('budget_65s',{'budget':65.0}),('budget_25s',{'budget':25.0}),
       ('no_write_delay',{'write_delay':0.0}),('slow_writes',{'write_delay':20.0}),
       ('slow_polling',{'poll':15.0}),('shared_source_failure',{'hide_current':True,'budget':65.0})]


def summarize(rows):
    out={}
    for k in rows[0]:
        vals=[r[k] for r in rows if r[k] is not None]
        out[k]=dict(mean=statistics.mean(vals) if vals else None,
                    seed_sd=statistics.stdev(vals) if len(vals)>1 else None,defined_seeds=len(vals))
    return out


def experiment(fixture,seeds):
    validate(fixture);cases=[]
    for name,c in CASES:
        output={m:[] for m in MODES};paired=[]
        for seed in range(seeds):
            runs={m:[evaluate_task(t,seed,m,c) for t in fixture['tasks']] for m in MODES}
            plans={m:[r['plan_sha256'] for r in v] for m,v in runs.items()}
            assert plans['isolated']==plans['snapshot']==plans['append_only']
            for m in MODES:output[m].append(metrics(runs[m]))
            paired.append({m:output[m][-1]['correct_rate']-output['isolated'][-1]['correct_rate'] for m in MODES[1:]})
        cases.append(dict(case=name,config=dict(DEFAULT,**c),summary={m:summarize(v) for m,v in output.items()},
                          paired_correct_gain=summarize(paired),seeds=output))
    return dict(schema_version=1,code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                storage_code_sha256=hashlib.sha256(Path(__file__).with_name('board_storage.py').read_bytes()).hexdigest(),
                fixture_sha256=digest(fixture),seed_ids=list(range(seeds)),cases=cases)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--tasks',default='data/retrieval_tasks.json');ap.add_argument('--seeds',type=int,default=100)
    ap.add_argument('--out',required=True);ap.add_argument('--trace-out')
    a=ap.parse_args()
    if a.seeds<2:ap.error('At least two seeds')
    fixture=json.loads(Path(a.tasks).read_text());result=experiment(fixture,a.seeds)
    Path(a.out).write_text(json.dumps(result,indent=2)+'\n')
    if a.trace_out:
        traces={m:[evaluate_task(t,0,m) for t in fixture['tasks']] for m in MODES}
        Path(a.trace_out).write_text(json.dumps(traces,indent=2)+'\n')
    print(a.out)


if __name__=='__main__':main()
