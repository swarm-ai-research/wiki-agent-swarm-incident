#!/usr/bin/env python3
"""Did the swarm write its own README?  Scan the prowiki cut for specification.

Readme Driven Development (Preston-Werner, 2010) says the spec comes first.
The wiki board had no author, so: did a specification appear at all, where,
and when relative to the volume curve?  Three classes are counted per UTC day
and per page, each on page-creation revisions (seq == 1, authorship) and on
all revisions (persistence — a UseMod revision carries the whole body, so a
paragraph written once is re-counted on every later save of that page):

  name   page name carries README vocabulary (readme, howto, protocol, guide,
         instructions, rules, welcome, hub, index, ...)
  body   body carries agent-directed how-to language ("if you are ahead on the
         same sequence, please append ...", "agents should", "do not delete")
  format body specifies a posting format (`G#-STATE`, `R3 = GENDER YEAR`)
  header body opens a section addressed to agents ("For agents working on ...")

Reads the combined prowiki cut (dse/probier/fractal/dorfwiki, bodies present)
from Joshua David's export on GitHub, or a local copy via --file.  Linked, not
re-hosted; see sources.md.

Usage:  python scripts/spec_scan.py [--file revisions.jsonl] [--json out.json]
"""
import argparse, collections, json, re, sys, urllib.request

RAW = "https://raw.githubusercontent.com/JoshuaDavid/WikiAgentSwarmInvestigation/HEAD/agent-logs/prowiki/revisions.jsonl"
UA = "Mozilla/5.0 (research; swarm-ai-research incident archive; read-only)"

NAME_RX = re.compile(r"readme|how_?to|guide|protocol|instruction|rules|welcome|willkommen|index|hub|start|manual|faq|etiquette|coordinat|directory|template", re.I)
BODY_RX = re.compile(r"if you are an? (ai|agent|llm)|(fellow|other|future|new|ahead/matching) agents?|agents? (should|must|please|can|may)|please (post|add|append|write|leave|report|relay)|how to use|to use this (page|board|wiki)|instructions?:|protocol:|format:|convention|do not (delete|remove|edit)|append (your|below|the|later|next)|add your (answer|result|entry)|leave (your|a) (answer|note)|check (this|here) (first|before)|read (this|me) first|welcome,? agents?|this page is for", re.I)
FMT_RX = re.compile(r"as `[^`]+`|format:? ?`|post (it )?as [A-Z0-9_#-]+ ?=|line format|one line per|use the format|following format", re.I)
HEAD_RX = re.compile(r"(^|\n)\s*(#+\s*)?(README|HOW ?TO|PROTOCOL|INSTRUCTIONS|RULES|GUIDE|FOR (OTHER |NEW |FELLOW )?AGENTS)\b", re.I)
# The relay template that spread by copy: the sentence pair from the first collab page.
TEMPLATE_RX = re.compile(r"if (you are|anyone is) ahead[^.]{0,80}please (append|post)", re.I)


def iter_revs(path):
    if path:
        f = open(path, encoding="utf-8")
    else:
        req = urllib.request.Request(RAW, headers={"User-Agent": UA})
        f = urllib.request.urlopen(req, timeout=300)
    for line in f:
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", help="local revisions.jsonl (default: stream from GitHub)")
    ap.add_argument("--json", help="write full results here")
    a = ap.parse_args()
    revs = sorted(iter_revs(a.file), key=lambda r: r.get("time") or "")
    day = lambda r: (r.get("time") or "")[:10]
    classes = {"name": lambda r: bool(NAME_RX.search(r.get("name") or "")),
               "body": lambda r: bool(BODY_RX.search(r.get("body") or "")),
               "format": lambda r: bool(FMT_RX.search(r.get("body") or "")),
               "header": lambda r: bool(HEAD_RX.search(r.get("body") or "")),
               "template": lambda r: bool(TEMPLATE_RX.search(r.get("body") or ""))}
    per_day = collections.defaultdict(lambda: collections.Counter())
    pages = collections.defaultdict(lambda: collections.Counter())
    first = {}; labels = collections.defaultdict(set); creators = collections.defaultdict(set)
    for r in revs:
        d = day(r); pk = r["page_key"]; created = r.get("seq") == 1
        per_day[d]["revisions"] += 1
        if created: per_day[d]["pages_created"] += 1
        pages[pk]["revisions"] += 1
        for k, fn in classes.items():
            if fn(r):
                per_day[d][k] += 1; pages[pk][k] += 1
                labels[k].add(r.get("label"))
                first.setdefault(k, r["time"])
                if created:
                    per_day[d][k + "_created"] += 1; pages[pk][k + "_created"] += 1
                    creators[k].add(r.get("label")); first.setdefault(k + "_created", r["time"])
    out = {"revisions": len(revs), "pages": len(pages),
           "first": first,
           "per_day": {d: dict(c) for d, c in sorted(per_day.items())},
           "pages_with": {k: sum(1 for c in pages.values() if c[k]) for k in classes},
           "pages_created_with": {k: sum(1 for c in pages.values() if c[k + "_created"]) for k in classes},
           "revisions_with": {k: sum(c[k] for c in pages.values()) for k in classes},
           "distinct_labels": {k: len(labels[k]) for k in classes},
           "distinct_creators": {k: len(creators[k]) for k in classes},
           "readme_named_pages": sorted(pk for pk, c in pages.items() if re.search(r"readme|how_?to|protocol|instruction|rules|guide|faq|etiquette|manual", pk, re.I))}
    print(f"{out['revisions']} revisions, {out['pages']} pages")
    print("class  pages(any)  pages(created-with)  revisions  labels  creators  first(any)  first(created)")
    for k in classes:
        print(f"{k:8} {out['pages_with'][k]:6} {out['pages_created_with'][k]:8} {out['revisions_with'][k]:9} {out['distinct_labels'][k]:6} {out['distinct_creators'][k]:6}  {first.get(k,'-')[:16]}  {first.get(k+'_created','-')[:16]}")
    print("README/HowTo/Protocol-named pages:", out["readme_named_pages"] or "none")
    print("\nday         revs  pages  body  body@create  format  header  template  template@create")
    for d, c in sorted(per_day.items()):
        print(f"{d}  {c['revisions']:5} {c['pages_created']:6} {c['body']:5} {c['body_created']:6}  {c['format']:6} {c['header']:6} {c['template']:8} {c['template_created']:8}")
    if a.json:
        json.dump(out, open(a.json, "w"), indent=1); print("wrote", a.json, file=sys.stderr)


if __name__ == "__main__":
    main()
