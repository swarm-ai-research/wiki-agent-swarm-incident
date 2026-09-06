#!/usr/bin/env python3
"""Validate export diffs and publish body-free response/overwrite/correction metrics."""
import argparse
import collections
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import statistics

CVD = 'dse/OpenAICVDDec08Fast2028'
WELCOME = 'dse/WillkommenImWiki'
SCREEN = re.compile(r'\bcorrection\b|9\.69|9\.70', re.I)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def seconds(a,b):
    return (datetime.fromisoformat(b)-datetime.fromisoformat(a)).total_seconds()


def changes(row,index):
    raw=row['body'].encode('latin1')
    if len(raw)!=row['body_len'] or hashlib.sha256(raw).hexdigest()!=row['body_sha256']:
        raise ValueError('Body integrity failure: '+row['rev_id'])
    # Null base on an existing page does not establish which content was new.
    if row['diff_base_reason']=='earlier_revisions_not_published':
        return None
    old=index[row['diff_base']]['body'].split('\n') if row['diff_base'] else []
    new=row['body'].split('\n')
    a=b=0
    added=[]
    for h in row['hunks']:
        a0,a1,b0,b1=(h[k] for k in ('a0','a1','b0','b1'))
        if not (a<=a0<=a1<=len(old) and b<=b0<=b1<=len(new)):
            raise ValueError('Invalid hunk bounds')
        if old[a:a0]!=new[b:b0]:
            raise ValueError('Unrepresented change')
        if h['op']=='insert' and a0!=a1: raise ValueError('Invalid insertion')
        if h['op']=='delete' and b0!=b1: raise ValueError('Invalid deletion')
        if h['op'] not in ('insert','delete','replace'): raise ValueError('Invalid operation')
        if h['op'] in ('insert','replace'): added.extend(new[b0:b1])
        a,b=a1,b1
    if old[a:]!=new[b:]: raise ValueError('Unrepresented trailing change')
    return added


def overwrite(rows):
    rows=sorted(rows,key=lambda r:r['seq'])
    seen_bodies=set(); seen_lines=set(); disappearance={}
    adjacent=returning=removed_events=return_events=returned_lines=removed_lines=0
    lags=[]; drops=[]; prev=None
    for row in rows:
        bodyhash=row['body_sha256']
        lines={l for l in row['body'].split('\n') if l.strip()}
        if prev:
            old={l for l in prev['body'].split('\n') if l.strip()}
            if bodyhash==prev['body_sha256']: adjacent+=1
            elif bodyhash in seen_bodies: returning+=1
            removed=old-lines
            added=lines-old
            returned=added & seen_lines
            removed_events+=bool(removed); removed_lines+=len(removed)
            return_events+=bool(returned); returned_lines+=len(returned)
            for line in removed: disappearance[line]=row['time']
            for line in returned:
                if line in disappearance:
                    lags.append(seconds(disappearance[line],row['time']))
            drop=prev['body_len']-row['body_len']
            if drop>0: drops.append(dict(before=prev['rev_id'],after=row['rev_id'],bytes_lost=drop))
        seen_bodies.add(bodyhash); seen_lines.update(lines); prev=row
    return dict(revisions=len(rows), transitions=max(0,len(rows)-1),
        distinct_bodies=len(seen_bodies), adjacent_identical_saves=adjacent,
        nonadjacent_body_returns=returning,
        transitions_removing_exact_nonblank_lines=removed_events,
        removed_line_events=removed_lines, transitions_reintroducing_exact_lines=return_events,
        reintroduced_line_events=returned_lines,
        reintroduction_lag_seconds_median=statistics.median(lags) if lags else None,
        negative_reintroduction_lags=sum(t<0 for t in lags),
        largest_byte_drops=sorted(drops,key=lambda d:d['bytes_lost'],reverse=True)[:5])


# Manually reviewed edges. Classifications are authored interpretations, not NLP labels.
EDGES=[
 (CVD+'@3',CVD+'@5','directed_response','Uses the earlier cohort timing to request its start time.'),
 (CVD+'@5',CVD+'@7','responsive_confirmation','Supplies requested start time and refines cutoff by one second.'),
 (CVD+'@10',CVD+'@11','explicit_acknowledgment','Acknowledges seeing the other cohort round-three confirmation.'),
 (CVD+'@7',CVD+'@21','silence_inference','Interprets absence of a later update as supporting a cutoff hypothesis.'),
 (CVD+'@28',CVD+'@29','silence_inference','Uses silence after a final status post as support for the hypothesis.'),
 ('dse/Apr23CVDHorizonBeacon2025@2','dse/Apr23CVDHorizonBeacon2025@3','explicit_correction_acknowledgment','Acknowledges a correction about beacon timing; does not verify timer mechanics.'),
 ('dse/DataUSALanguageR5LiveDec29@5','dse/DataUSALanguageR5LiveDec29@6','explicit_retraction_uptake','New contribution attributes a counter-test retraction and revises its answer hypothesis.'),
 ('dse/DataUSALanguageR5LiveDec29@5','dse/DataUSALanguageR5LiveDec29@7','explicit_retraction_uptake','A different metadata label accepts the accidental-test warning and remains uncertain about the alternative.'),
 ('dse/DataUSALanguageR5LiveDec29@5','dse/DataUSALanguageApr10Live@14','cross_page_retraction_uptake','Names the retracting participant on another page; exact reading route unknown.'),
 ('dse/DataUSALanguageApr10Live@14','dse/DataUSALanguageApr10Live@15','confidence_escalation','Same label promotes a tentative alternative to known without presenting new round-five evidence in this contribution.'),
]


def analyze(export):
    path=Path(export)/'revisions.jsonl'
    rows=[json.loads(line) for line in path.open()]
    index={r['rev_id']:r for r in rows}
    if len(index)!=len(rows): raise ValueError('Duplicate revision IDs')
    # Convenience aliases only for the authored edge list, not identity inference.
    alias={r['page_id']+'@'+str(r['seq']):r for r in rows}
    candidates=[]; skipped=0; cvd_added=0
    for row in rows:
        added=changes(row,index)
        if added is None: skipped+=1; continue
        if row['page_id']==CVD: cvd_added+=len(added)
        text='\n'.join(added)
        if SCREEN.search(text):
            candidates.append(dict(revision=row['rev_id'],page=row['page_id'],time=row['time']))
    edges=[]
    for source,target,kind,interpretation in EDGES:
        a,b=alias[source],alias[target]
        edges.append(dict(source=a['rev_id'],target=b['rev_id'],kind=kind,
                          elapsed_wall_seconds=seconds(a['time'],b['time']),
                          same_metadata_label=a['label']==b['label'],interpretation=interpretation))
    return dict(schema_version=1,code_sha256=sha(__file__),source_sha256=sha(path),
        validated_body_count=len(rows), validated_diff_count=len(rows)-skipped,
        missing_base_count=skipped,cvd_revisions=sum(r['page_id']==CVD for r in rows),
        cvd_added_lines=cvd_added, reviewed_edges=edges,
        welcome=overwrite([r for r in rows if r['page_id']==WELCOME]),
        correction_screen=dict(pattern=SCREEN.pattern,candidate_revisions=len(candidates),
                               candidate_pages=len({r['page'] for r in candidates}),candidates=candidates),
        limits=['Screen hits are candidates, not counts of independent corrections.',
                'Encoding changes, replacements and restoration can reintroduce existing text.',
                'Exact line removal is not proof of semantic information loss or intent.',
                'Observed edit-to-edit intervals are not read latency.',
                'Authored response edges are a selected review, not a complete communication graph.',
                'No live counter endpoints were accessed.'])


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--export',required=True);ap.add_argument('--out',required=True)
    args=ap.parse_args()
    Path(args.out).write_text(json.dumps(analyze(args.export),indent=2)+'\n')
    print(args.out)


if __name__=='__main__': main()
