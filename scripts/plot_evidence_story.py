#!/usr/bin/env python3
"""Editorial figures from frozen live-evaluation traces; no network or model calls."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

ROOT=Path(__file__).resolve().parents[1]
BG='#F5F4EF';INK='#172D3D';MUTED='#62717B';LINE='#D8DFDB'
TEAL='#147D79';BLUE='#477AA8';AMBER='#BD7929';PURPLE='#817099';RED='#BA554C';PALE='#E4E8E3'
MODELS=['openai/gpt-4.1-mini','google/gemini-2.5-flash-lite']
NAMES={'openai/gpt-4.1-mini':'GPT-4.1 Mini','google/gemini-2.5-flash-lite':'Gemini Flash-Lite'}
USED=set()


def read(relative):
    path=ROOT/relative;USED.add(path)
    return json.loads(path.read_text())


def label(ax,x,y,text,size=12,color=INK,weight='normal',**kw):
    return ax.text(x,y,text,fontsize=size,color=color,weight=weight,va='center',**kw)


def box(ax,x,y,w,h,face='white',edge='none',rounding=1.2):
    patch=FancyBboxPatch((x,y),w,h,boxstyle=f'round,pad=0,rounding_size={rounding}',facecolor=face,edgecolor=edge,linewidth=.8)
    ax.add_patch(patch);return patch


def arrow(ax,x1,y1,x2,y2,color=LINE):
    ax.annotate('',xy=(x2,y2),xytext=(x1,y1),arrowprops=dict(arrowstyle='->',color=color,lw=1.5))


def page(number,title,subtitle):
    fig,ax=plt.subplots(figsize=(14,9),facecolor=BG);fig.subplots_adjust(left=0,right=1,bottom=0,top=1)
    ax.set(xlim=(0,100),ylim=(0,100));ax.axis('off')
    label(ax,6,94,'SHARED EVIDENCE  /  FIELD NOTES',10,TEAL,'bold')
    label(ax,94,94,f'{number:02d}',11,MUTED,ha='right')
    label(ax,6,86,title,29,INK,'bold')
    label(ax,6,79.5,subtitle,11,MUTED)
    ax.plot([6,94],[8,8],color=LINE,lw=1)
    return fig,ax


def finish(fig,ax,out,name,caption):
    label(ax,6,5,caption,9,MUTED)
    svg=out/(name+'.svg');png=out/(name+'.png')
    fig.savefig(svg,facecolor=BG,metadata={'Date':None})
    svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines())+'\n')
    fig.savefig(png,facecolor=BG,dpi=180);plt.close(fig)


def dots(ax,x,y,filled,total=4,color=TEAL,spacing=2.5,size=220):
    assert 0<=filled<=total
    for i in range(total):
        ax.scatter(x+i*spacing,y,s=size,color=color if i<filled else PALE,edgecolors='none',zorder=3)


def adoption(groups,out):
    shared=[g for g in groups if g['experiment']=='voluntary' and g['mode']=='append_only']
    baseline=read('data/model_eval/heldout-analysis-a6-summary.json')
    before_n=sum(g['agents'] for g in baseline['groups'] if g['mode']!='isolated')
    before_p=0
    for name in baseline['run_files']:
        r=read('data/model_eval/live/'+name)
        if r['mode']=='isolated':continue
        before_p+=len({e['agent'] for e in r['trace'] if e['event']=='action' and e['response']['action']['action']=='board_write' and 'published' in e['result']})
    after_n=sum(g['agents'] for g in shared);after_p=sum(g['model_publishers'] for g in shared)
    fig,ax=page(1,'Publishing is only the first step.','Voluntary communication on authored local tasks. Board access alone did not produce collaboration.')
    for x,kicker,value,detail,color in [(6,'EARLIER STUDY',f'{before_p}/{before_n}','shared-condition agents published',MUTED),
                                      (52,'COMPLEMENTARY EVIDENCE',f'{after_p}/{after_n}','shared-condition agents published',TEAL)]:
        box(ax,x,56,42,18)
        label(ax,x+2,70.5,kicker,9,MUTED,'bold')
        label(ax,x+2,62.5,value,32,color,'bold')
        label(ax,x+16,62.5,detail.replace(' agents','\nagents'),11,MUTED)
    label(ax,6,51,'The task and prompt changed together; these are different studies, not a single-factor causal comparison.',10,MUTED)
    centers=[40,62,84]
    for x,t in zip(centers,['PUBLISHED','READ A PEER MESSAGE','CORRECT ANSWER']):label(ax,x,44,t,9,MUTED,'bold',ha='center')
    metrics=[]
    for y,model in zip([35,22],MODELS):
        g=next(g for g in shared if g['model']==model);assert g['agents']==4
        label(ax,6,y+1,NAMES[model],14,INK,'bold');label(ax,6,y-3,'4 decisions · 2 paired task instances',9,MUTED)
        values=[g['model_publishers'],g['peer_message_receivers'],g['outcomes'].get('correct',0)]
        for x,value in zip(centers,values):
            dots(ax,x-3.75,y+1,value,color=TEAL if model==MODELS[0] else BLUE)
            label(ax,x,y-4,f'{value} / 4',10,MUTED,ha='center')
        arrow(ax,47,y+1,54,y+1);arrow(ax,69,y+1,76,y+1)
        metrics.append(dict(model=model,published=values[0],peer_receipt=values[1],correct=values[2],decisions=4))
    finish(fig,ax,out,'12-sharing-stages','Each dot is one agent decision. Small dependent samples; independent review pending. Source: saved live traces and follow-up summary.')
    return dict(before=dict(published=before_p,agents=before_n),after=dict(published=after_p,agents=after_n),stages=metrics)


def trace_detail(out):
    refs={r['id']:r for r in read('data/model_eval/followup/references.json')}
    ident='complementary-code-1';rows=[]
    fig,ax=page(2,'Same fragments. Different follow-through.','One saved task: each agent held half of a release token. Columns show each agent’s own action number, not elapsed time.')
    color_key=[('Private read',BLUE),('Publish / peer read',TEAL),('Empty / unhelpful',AMBER),('Incorrect ordering',RED)]
    for x,(name,color) in zip([6,26,52,76],color_key):
        ax.scatter(x,72,s=50,color=color);label(ax,x+1.3,72,name,9,MUTED)
    row_metadata=[]
    for model in MODELS:
        filename=f"followup-{model.split('/')[1]}-{ident}-append_only-s1.json"
        r=read('data/model_eval/followup/'+filename)
        for a in [0,1]:
            events=[e for e in r['trace'] if e['event']=='action' and e['agent']==a]
            rows.append((model,a,events,r['answers'][str(a)]))
            row_metadata.append(dict(run=filename,agent=a,action_count=len(events)))
    xs=[29,40.5,52,63.5,75,86.5]
    for i,x in enumerate(xs,1):label(ax,x+4.75,64,f'{i:02d}',10,MUTED,ha='center')
    for y,(model,a,events,final) in zip([54,42,27,15],rows):
        label(ax,6,y+2,NAMES[model],13,INK,'bold');label(ax,6,y-2,f'Agent {a} · {"north" if a==0 else "south"} fragment',10,MUTED)
        assert len(events)<=6
        for x,e in zip(xs,events):
            act=e['response']['action']['action'];result=e['result']
            if act=='read':text='READ\nprivate';color=BLUE
            elif act=='board_write':text='PUBLISH\nfragment';color=TEAL
            elif act=='board_read':
                peer=any(m['author']!=a for m in result.get('messages',[]))
                text='READ\npeer' if peer else 'READ\nempty board';color=TEAL if peer else AMBER
            elif act=='search':text='SEARCH\nno hits';color=AMBER;assert not result.get('hits')
            elif act=='final':
                answer=result['answer'].strip()
                if answer==refs[ident]['answer']:text='FINAL\ncorrect';color=TEAL
                elif not answer:text='FINAL\nabstain';color=MUTED
                else:
                    assert answer=='-'.join(reversed(refs[ident]['answer'].split('-')))
                    text='FINAL\nreversed';color=RED
            else:raise ValueError(act)
            box(ax,x,y-4.5,9.5,9,face=color,rounding=.6)
            label(ax,x+4.75,y,text,9,'white','bold',ha='center',linespacing=1.6)
    ax.plot([6,94],[34.5,34.5],color=LINE,lw=1)
    finish(fig,ax,out,'13-coordination-trace','An illustrative trace, not an aggregate estimate. “Read peer” means a message arrived; it does not imply correct interpretation or citation.')
    return row_metadata


def storage(groups,out):
    fig,ax=page(3,'The correction disappears before the reader acts.','Controlled storage experiment: fixed publishers create the race; models read the surviving notices.')
    setup=read('data/model_eval/followup/followup-gpt-4.1-mini-controlled-correction-1-snapshot-s1.json')['setup_trace']
    assert [e['action']['action'] for e in setup]==['board_read','board_read','board_write','board_write']
    for x,num,heading,detail in [(6,'1','A reads','empty snapshot'),(29,'2','B reads','empty snapshot'),(52,'3','B publishes','the correction'),(75,'4','A publishes','the older notice')]:
        box(ax,x,61,19,13)
        label(ax,x+1.5,70.5,num,10,TEAL,'bold');label(ax,x+1.5,66.8,heading,13,INK,'bold');label(ax,x+1.5,63.3,detail,10,MUTED)
        if num!='4':arrow(ax,x+19.5,67.5,x+22.5,67.5)
    metrics=[]
    for x,mode,title,color in [(6,'snapshot','Snapshot replacement',AMBER),(52,'append_only','Append-only storage',TEAL)]:
        selected=[g for g in groups if g['experiment']=='required' and g['mode']==mode]
        n=sum(g['agents'] for g in selected);assert n==8
        counts=Counter()
        for g in selected:counts.update(g['outcomes'])
        box(ax,x,13,42,43)
        label(ax,x+2,51.5,title,19,INK,'bold')
        if mode=='snapshot':
            label(ax,x+2,46,'A’s stale snapshot removes B’s correction.',10,MUTED)
            box(ax,x+2,34,38,8,face='#FBF2E6');label(ax,x+4,38,'SURVIVES   Original notice',11,AMBER,'bold')
            label(ax,x+2,29,'Correction absent in all 4 cohorts',10,MUTED)
        else:
            label(ax,x+2,46,'Both messages survive, even in arrival order.',10,MUTED)
            box(ax,x+2,37,38,6,face='#E8F2EE');label(ax,x+4,40,'SURVIVES   Correction',11,TEAL,'bold')
            box(ax,x+2,29.5,38,6,face='#FBF2E6');label(ax,x+4,32.5,'SURVIVES   Original notice',11,AMBER,'bold')
        outcomes=[]
        for key,c in [('correct',TEAL),('stale',AMBER),('operational_stops',PURPLE)]:outcomes.extend([c]*counts.get(key,0))
        assert len(outcomes)==8
        for i,c in enumerate(outcomes):ax.scatter(x+3+i*2.5,23,s=190,color=c,edgecolors='none')
        desc=f"{counts.get('stale',0)} stale · {counts.get('operational_stops',0)} operational stops" if mode=='snapshot' else f"{counts.get('correct',0)} current answers / {n} decisions"
        label(ax,x+2,17.5,desc,12,color,'bold')
        metrics.append(dict(mode=mode,agents=n,outcomes=dict(counts)))
    finish(fig,ax,out,'14-correction-survival','Scripted publication is a controlled manipulation, not autonomous model writing. Two task instances × two models × two readers per mode.')
    return metrics


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--out',type=Path,default=ROOT/'charts');args=ap.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    plt.rcParams.update({'font.family':'DejaVu Sans','svg.fonttype':'none','svg.hashsalt':'evidence-story-v1'})
    summary=read('data/model_eval/followup/summary.json')
    data=dict(sharing=adoption(summary['groups'],args.out),trace=trace_detail(args.out),storage=storage(summary['groups'],args.out))
    data['sources']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(USED)}
    data['generator_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (args.out/'evidence-story-data.json').write_text(json.dumps(data,indent=2)+'\n')
    print('Created figures 12–14 in SVG and PNG, with source hashes and plotted counts.')

if __name__=='__main__':main()
