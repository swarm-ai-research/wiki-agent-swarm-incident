#!/usr/bin/env python3
"""Render retrieval-evaluation figures from saved aggregates; no simulation rerun."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

COLORS={'correct_rate':'#197C80','incorrect_rate':'#C64B40','not_attempted_rate':'#CFD6DE'}
MODES={'isolated':'Isolated','snapshot':'Snapshot board','append_only':'Append-only board'}
MODE_COLORS={'isolated':'#68778B','snapshot':'#BF7540','append_only':'#197C80'}


def render(source,out):
    data=json.loads(Path(source).read_text());cases={c['case']:c for c in data['cases']}
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.titleweight':'bold',
        'axes.spines.top':False,'axes.spines.right':False,'axes.spines.left':False,
        'axes.edgecolor':'#D1D7DE','text.color':'#182535','axes.labelcolor':'#334155',
        'xtick.color':'#526175','ytick.color':'#334155','svg.fonttype':'none','svg.hashsalt':'wiki-retrieval-v1'})
    def finish(fig,name):
        fig.savefig(out/(name+'.svg'),facecolor='white',metadata={'Date':None})
        fig.savefig(out/(name+'.png'),facecolor='white',dpi=160)
        plt.close(fig)
    def title(fig,heading,sub):
        fig.text(.055,.95,heading,fontsize=20,weight='bold',va='top')
        fig.text(.055,.89,sub,fontsize=11,color='#526175',va='top')
    def foot(fig,text):
        fig.text(.055,.045,text,fontsize=9,color='#526175',va='bottom')
    fig,axes=plt.subplots(2,1,figsize=(11,7.6))
    fig.subplots_adjust(left=.22,right=.95,bottom=.16,top=.76,hspace=.75)
    title(fig,'Sharing can spread answers—and shared-source errors',
          'Authored synthetic retrieval tasks • scripted policy • 100 paired seeds')
    for ax,case,heading in zip(axes,['baseline','shared_source_failure'],
                               ['Baseline: 40-second search budget','Revised source unavailable to all: 65-second budget']):
        left=np.zeros(3)
        for metric,label in [('correct_rate','Correct'),('incorrect_rate','Incorrect'),('not_attempted_rate','Not attempted')]:
            values=np.array([100*cases[case]['summary'][m][metric]['mean'] for m in MODES])
            ax.barh(np.arange(3),values,left=left,color=COLORS[metric],height=.58,label=label)
            for i,v in enumerate(values):
                if v>=6:ax.text(left[i]+v/2,i,f'{v:.1f}%',ha='center',va='center',fontsize=10,
                                color='#182535' if metric=='not_attempted_rate' else 'white',weight='bold')
            left+=values
        ax.set_yticks(range(3),MODES.values());ax.invert_yaxis();ax.set_xlim(0,100)
        ax.set_xticks([0,25,50,75,100],[f'{n}%' for n in [0,25,50,75,100]])
        ax.set_title(heading,loc='left',pad=12,fontsize=12);ax.tick_params(axis='y',length=0)
    handles,labels=axes[0].get_legend_handles_labels()
    fig.legend(handles,labels,loc='lower center',bbox_to_anchor=(.57,.085),ncol=3,frameon=False)
    foot(fig,'Mean outcome shares; 4,800 decisions per condition per setting. Panels differ in budget and source availability.\nSynthetic mechanism test, not an OpenAI benchmark score. Source: data/shared_retrieval_results.json')
    finish(fig,'07-retrieval-outcomes')

    names={'baseline':'Baseline · 40s budget','budget_65s':'Longer budget · 65s',
           'budget_25s':'Shorter budget · 25s','no_write_delay':'Immediate writes',
           'slow_writes':'Write delay · up to 20s','slow_polling':'Board polling · every 15s',
           'shared_source_failure':'Revised source unavailable · 65s'}
    fig,ax=plt.subplots(figsize=(11,7.8));fig.subplots_adjust(left=.33,right=.94,top=.79,bottom=.19)
    title(fig,'The benefit depends on time and source availability',
          'Correct answers by setting • dots show means; bars show ±1 across-seed standard deviation')
    y=np.arange(len(names))
    for offset,(mode,label) in zip([-.2,0,.2],MODES.items()):
        mean=[100*cases[k]['summary'][mode]['correct_rate']['mean'] for k in names]
        sd=[100*cases[k]['summary'][mode]['correct_rate']['seed_sd'] for k in names]
        ax.errorbar(mean,y+offset,xerr=sd,fmt='o',ms=6,capsize=3,color=MODE_COLORS[mode],label=label,lw=1.4)
    ax.set_yticks(y,names.values());ax.invert_yaxis();ax.set_xlim(-5,110)
    ax.set_xticks([0,25,50,75,100],[f'{n}%' for n in [0,25,50,75,100]])
    ax.grid(axis='x',color='#E8ECF0');ax.set_axisbelow(True);ax.tick_params(axis='y',length=0)
    ax.legend(loc='upper center',bbox_to_anchor=(.43,-.10),ncol=3,frameon=False)
    foot(fig,'100 paired seeds on six templated fictional tasks. SD bars describe seed variation, not confidence intervals;\nthey may extend beyond 0–100%. No model API. Source: data/shared_retrieval_results.json')
    finish(fig,'08-retrieval-sensitivity')

    fig,ax=plt.subplots(figsize=(10,6));fig.subplots_adjust(left=.12,right=.95,top=.75,bottom=.23)
    title(fig,'Correction receipt and changes in selected evidence',
          'Baseline • percentage of all task decisions • synthetic policy events')
    modes=['snapshot','append_only'];x=np.arange(2)
    for offset,metric,label,color in [(-.18,'correction_received_rate','Received a correction','#197C80'),
                                     (.18,'correction_applied_rate','Changed selected source','#BD8549')]:
        vals=[100*cases['baseline']['summary'][m][metric]['mean'] for m in modes]
        bars=ax.bar(x+offset,vals,width=.32,label=label,color=color)
        ax.bar_label(bars,labels=[f'{v:.1f}%' for v in vals],padding=5,fontsize=12,weight='bold')
    ax.set_xticks(x,[MODES[m] for m in modes]);ax.set_ylim(0,105)
    ax.set_yticks([0,25,50,75,100],[f'{n}%' for n in [0,25,50,75,100]])
    ax.grid(axis='y',color='#E8ECF0');ax.set_axisbelow(True)
    ax.legend(loc='upper center',bbox_to_anchor=(.5,-.13),ncol=2,frameon=False)
    foot(fig,'A changed choice requires invalidating a previously selected source during a board poll. Some recipients already\nhave current evidence, so a lower change rate is not necessarily worse. Source: data/shared_retrieval_results.json')
    finish(fig,'09-retrieval-correction-uptake')


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source',default='data/shared_retrieval_results.json')
    ap.add_argument('--out',default='charts');args=ap.parse_args();render(args.source,args.out)
