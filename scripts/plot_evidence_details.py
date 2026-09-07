#!/usr/bin/env python3
"""Task outcomes and action costs from saved follow-up traces; no live calls."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from plot_evidence_story import (ROOT, USED, BG, INK, MUTED, LINE, TEAL, BLUE,
    AMBER, PURPLE, RED, MODELS, NAMES, read, page, box, label, finish)

SYMBOLS={'correct':'C','abstained':'A','other_incorrect':'I','stale':'S','operational_stops':'X'}
COLORS={'correct':TEAL,'abstained':MUTED,'other_incorrect':RED,'stale':AMBER,'operational_stops':PURPLE}


def classify(final,ref):
    if final is None:return 'operational_stops'
    answer=final['answer'].strip()
    if answer==ref['answer']:return 'correct'
    if not answer or answer.casefold()=='abstain':return 'abstained'
    if answer==ref.get('stale_answer'):return 'stale'
    return 'other_incorrect'


def load_runs():
    base='data/model_eval/followup/'
    manifest=read(base+'manifest.json');refs={r['id']:r for r in read(base+'references.json')}
    runs={}
    for job in manifest['jobs']:
        r=read(base+job['id']+'.json')
        assert {str(a) for a in r['answers']} | {str(a) for a in r['stopped']}=={'0','1'}
        assert not set(r['answers']) & set(r['stopped'])
        runs[(r['task_id'],r['backend'],r['mode'])]=r
    assert len(runs)==16
    return runs,refs


def outcome_map(runs,refs,out):
    fig,ax=page(4,'Which tasks failed—and how?','All 32 planned decisions in the follow-up. Each pair of tiles is one two-agent cohort; left = agent 0, right = agent 1.')
    for x,key,name in [(6,'correct','Correct'),(23,'abstained','Abstained'),(41,'other_incorrect','Other / incomplete'),(65,'stale','Stale'),(79,'operational_stops','Operational stop')]:
        box(ax,x,71,1.5,2.3,face=COLORS[key],rounding=.2);label(ax,x+2.1,72.1,f'{SYMBOLS[key]} · {name}',9,MUTED)
    counts=Counter();cells=[]
    sections=[('voluntary','01  VOLUNTARY SHARING','complementary-code',['isolated','append_only'],[55,45],64,59.5),
              ('required','02  REQUIRED READS / FIXED PUBLISHERS','controlled-correction',['snapshot','append_only'],[25,15],34,29.5)]
    for experiment,heading,prefix,modes,ys,group_y,condition_y in sections:
        label(ax,6,group_y,heading,9,TEAL,'bold')
        for model,center in zip(MODELS,[49,81]):label(ax,center,group_y,NAMES[model],12,INK,'bold',ha='center')
        for model,xs in zip(MODELS,[[41,57],[73,89]]):
            for mode,x in zip(modes,xs):label(ax,x,condition_y,mode.replace('_','-').title(),9,MUTED,ha='center')
        for number,y in enumerate(ys,1):
            task=f'{prefix}-{number}'
            label(ax,6,y+1,f'{"Release fragments" if experiment=="voluntary" else "Correction notice"} {number}',13,INK,'bold')
            label(ax,6,y-2.2,'Both agents need the complete token' if experiment=='voluntary' else 'Both readers must inspect the board',9,MUTED)
            for model,xs in zip(MODELS,[[41,57],[73,89]]):
                for mode,x in zip(modes,xs):
                    r=runs[(task,model,mode)]
                    for agent,offset in [(0,-5),(1,.4)]:
                        outcome=classify(r['answers'].get(str(agent)),refs[task]);counts[outcome]+=1
                        box(ax,x+offset,y-2.8,4.6,5.6,face=COLORS[outcome],rounding=.4)
                        label(ax,x+offset+2.3,y,SYMBOLS[outcome],10,'white','bold',ha='center')
                        cells.append(dict(task=task,model=model,mode=mode,agent=agent,outcome=outcome))
    ax.plot([6,94],[38,38],color=LINE,lw=1)
    assert sum(counts.values())==32
    finish(fig,ax,out,'15-task-outcome-map','Exact-token scoring; some “other / incomplete” answers express uncertainty in prose. Small dependent samples; independent review pending.')
    return dict(outcomes=dict(counts),cells=cells)


def effort(runs,refs,out):
    fig,ax=page(5,'Extra effort does not guarantee a complete answer.','Voluntary experiment only. Bars count model-selected actions across four planned agent decisions per condition.')
    palette=[('read','Private read',BLUE),('search','Search',AMBER),('board','Board attempt',TEAL),('final','Final submission',MUTED)]
    for x,(kind,name,color) in zip([6,27,46,70],palette):
        box(ax,x,71,1.5,2.3,face=color,rounding=.2);label(ax,x+2.2,72.1,name,10,MUTED)
    label(ax,30,64,'MODEL ACTIONS',9,MUTED,'bold');label(ax,91,64,'CORRECT',9,MUTED,'bold',ha='center')
    rows=[]
    for model in MODELS:
        for mode in ['isolated','append_only']:
            selected=[r for (task,m,condition),r in runs.items() if m==model and condition==mode and r['experiment']=='voluntary']
            assert len(selected)==2
            counts=Counter();calls=0;correct=0;denied=0
            for r in selected:
                calls+=sum(u['calls'] for u in r['usage'])
                correct+=sum(classify(f,refs[r['task_id']])=='correct' for f in r['answers'].values())
                for e in r['trace']:
                    if e['event']!='action':continue
                    act=e['response']['action']['action'];counts['board' if act.startswith('board_') else act]+=1
                    if act.startswith('board_') and 'error' in e['result']:denied+=1
            # The saved voluntary runs have no unusable responses. Fail loudly if
            # a new dataset would require another stack category.
            assert sum(counts.values())==calls and calls<=32
            rows.append(dict(model=model,mode=mode,actions=dict(counts),calls=calls,correct=correct,denied_board_attempts=denied))
    for y,row in zip([55,45,30,20],rows):
        label(ax,6,y+1.5,NAMES[row['model']],13,INK,'bold');label(ax,6,y-2,row['mode'].replace('_','-').title(),10,MUTED)
        left=30
        for kind,name,color in palette:
            n=row['actions'].get(kind,0)
            if n:
                box(ax,left,y-2.8,n*1.5,5.6,face=color,rounding=0)
                if n>=2:label(ax,left+n*.75,y,str(n),10,'white','bold',ha='center')
                left+=n*1.5
        label(ax,left+1,y,f"{row['calls']} calls",10,MUTED)
        label(ax,91,y,f"{row['correct']} / 4",18,TEAL if row['correct'] else MUTED,'bold',ha='center')
    ax.plot([6,94],[37.5,37.5],color=LINE,lw=1)
    for n in [0,8,16,24,32]:
        x=30+n*1.5;label(ax,x,12,str(n),9,MUTED,ha='center')
    finish(fig,ax,out,'16-action-costs','Each agent had a 12-call cap. Gemini isolation includes one denied board read; attempts are counted even when they return an error.')
    return rows


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--out',type=Path,default=ROOT/'charts');args=ap.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    plt.rcParams.update({'font.family':'DejaVu Sans','svg.fonttype':'none','svg.hashsalt':'evidence-details-v1'})
    runs,refs=load_runs();data=dict(task_map=outcome_map(runs,refs,args.out),effort=effort(runs,refs,args.out))
    summary=read('data/model_eval/followup/summary.json')
    for g in summary['groups']:
        expected=Counter(g['outcomes']);actual=Counter(c['outcome'] for c in data['task_map']['cells'] if (c['model'],c['mode'])==(g['model'],g['mode']) and runs[(c['task'],c['model'],c['mode'])]['experiment']==g['experiment'])
        assert actual==+expected
    data['sources']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(USED)}
    data['generator_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    data['style_generator_sha256']=hashlib.sha256((ROOT/'scripts/plot_evidence_story.py').read_bytes()).hexdigest()
    (args.out/'evidence-details-data.json').write_text(json.dumps(data,indent=2)+'\n')
    print('Created figures 15–16. All 32 plotted outcomes match the saved study summary.')

if __name__=='__main__':main()
