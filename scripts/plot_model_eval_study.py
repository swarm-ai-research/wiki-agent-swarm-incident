#!/usr/bin/env python3
"""Plot observed live outcomes from the selected attempt's offline summary."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source',default='data/model_eval/heldout-analysis-a6-summary.json')
    ap.add_argument('--out',default='charts/10-live-retrieval-outcomes')
    args=ap.parse_args();data=json.loads(Path(args.source).read_text());groups=data['groups']
    if not groups or not sum(g['finals'] for g in groups):raise ValueError('No live final answers to plot')
    plt.rcParams.update({'svg.fonttype':'none','svg.hashsalt':'live-wiki-eval-v1','font.family':'DejaVu Sans'})
    fig,ax=plt.subplots(figsize=(11,6.8));fig.subplots_adjust(left=.34,right=.96,top=.76,bottom=.25)
    fig.text(.05,.95,'Live retrieval: answer outcomes',fontsize=20,weight='bold',va='top')
    fig.text(.05,.88,'Authored local tasks · GPT-4.1 Mini and Gemini Flash-Lite · strict format grading',fontsize=11,color='#526175')
    colors=['#197C80','#C64B40','#CFD6DE','#8B5CA6'];left=[0]*len(groups)
    for (field,label),color in zip([('correct','Correct'),('incorrect','Incorrect'),('not_attempted','Abstained'),('operational_stops','Operational stop')],colors):
        values=[100*g[field]/g['agents'] for g in groups]
        ax.barh(range(len(groups)),values,left=left,label=label,color=color,height=.65)
        for i,v in enumerate(values):
            if v>=6:ax.text(left[i]+v/2,i,f'{v:.1f}%',ha='center',va='center',color='#182535' if field=='not_attempted' else 'white',fontsize=10)
        left=[a+b for a,b in zip(left,values)]
    names={'isolated':'Isolated','snapshot':'Snapshot board','append_only':'Append-only board'}
    ax.set_yticks(range(len(groups)),[g['model'].split('/')[1]+'\n'+names[g['mode']] for g in groups]);ax.invert_yaxis()
    ax.set_xlim(0,100);ax.set_xticks([0,25,50,75,100],[f'{n}%' for n in [0,25,50,75,100]])
    for edge in ['top','right','left']:ax.spines[edge].set_visible(False)
    ax.tick_params(axis='y',length=0);fig.legend(loc='lower center',bbox_to_anchor=(.62,.13),ncol=2,frameon=False)
    fig.text(.05,.035,'16 decisions per model/condition; half the tasks have no supported answer, so abstention is expected there.\nThree incorrect labels are literal \"Abstain\" instead of empty answers. Human review pending; not official benchmark scores.',fontsize=9,color='#526175')
    out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(out.with_suffix('.svg'),metadata={'Date':None},facecolor='white')
    svg=out.with_suffix('.svg')
    svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines())+'\n')
    fig.savefig(out.with_suffix('.png'),dpi=160,facecolor='white');plt.close(fig)

if __name__=='__main__':main()
