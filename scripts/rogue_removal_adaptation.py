"""Test 2a (analysis/rogue-agent-explosion-tests.md): did the wiki swarm adapt to removal? Read-only on the pinned Termina db."""
import sqlite3, os, collections, statistics as st
from datetime import datetime, timedelta
DB = os.path.expanduser("~/distributional-agi-safety/runs/data/termina/incidents.sqlite")
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
q = lambda s, *a: con.execute(s, a).fetchall()
P = lambda t: datetime.fromisoformat(t.replace("Z", "+00:00"))

AGENT_WRITE = "kind in ('revision','paste') and actor_id like 'handle:%'"
dels = q("select title, observed_time, actor_id from record where venue_id='dse' and kind='delete' order by observed_time")
agent_titles = {t for (t,) in q(f"select distinct title from record where venue_id='dse' and {AGENT_WRITE}")}
agent_dels = [(t, P(o)) for t, o, a in dels if t in agent_titles]
print("dse deletes:", len(dels), "targeting agent-written titles:", len(agent_dels),
      "by human actors:", sum(a and a.startswith('human:') for _, _, a in dels))
print("first agent-content delete (pre-registered onset):", agent_dels[0])
by_day = collections.Counter(d.date() for _, d in agent_dels)
first_big = min(d for d, c in by_day.items() if c >= 20)
print("first day with >=20 agent-content deletes (substantive onset):", first_big)

rows = q(f"select venue_id, title, observed_time, actor_id, body_len, content_kind from record where {AGENT_WRITE} and observed_time is not null")
rows = [(v, t, P(o), a, b, k) for v, t, o, a, b, k in rows]
del_venues_onset = {}
for v, in q("select distinct venue_id from record where kind='delete'"):
    del_venues_onset[v] = P(q("select min(observed_time) from record where venue_id=? and kind='delete'", v)[0][0])

def window(onset, days=7):
    lo, hi = onset - timedelta(days=days), onset + timedelta(days=days)
    pre = [r for r in rows if lo <= r[2] < onset]
    post = [r for r in rows if onset <= r[2] < hi]
    return pre, post

def report(label, onset):
    onset = datetime.combine(onset, datetime.min.time()).replace(tzinfo=P("2026-01-01T00:00:00Z").tzinfo) if not isinstance(onset, datetime) else onset
    pre, post = window(onset)
    print(f"\n== {label}: onset {onset.isoformat()} (7d windows)")
    for name, w in (("pre", pre), ("post", post)):
        dse = [r for r in w if r[0] == "dse"]
        # (b) share of agent writes on venues with no delete yet at write time
        undeleted = [r for r in w if r[0] not in del_venues_onset or r[2] < del_venues_onset[r[0]]]
        handles = {r[3] for r in dse}
        days = collections.defaultdict(set); cnt = collections.Counter()
        for r in dse: days[r[2].date()].add(r[3]); cnt[r[2].date()] += 1
        hpr = [len(days[d]) / cnt[d] for d in days if cnt[d] >= 20]
        bl = [r[4] for r in dse if r[4]]
        kinds = collections.Counter(r[5] for r in dse)
        zzz = sum(1 for r in dse if r[1] and r[1].upper().startswith("ZZ"))
        print(f" {name}: dse agent writes {len(dse)} ({len(dse)/7:.0f}/day) | all-venue agent writes {len(w)}, "
              f"share on not-yet-deleting venues {len(undeleted)/max(len(w),1):.3f} | "
              f"handles/write (daily median, days>=20 writes) {st.median(hpr) if hpr else float('nan'):.3f} "
              f"| distinct handles {len(handles)} | median body_len {st.median(bl) if bl else 'na'} | ZZ-titles {zzz} "
              f"| top kinds {kinds.most_common(4)}")

report("pre-registered onset", agent_dels[0][1])
report("substantive onset", first_big)

# (d) survival: first agent write of a title -> first delete of that title, by creation day
first_write = {}
for v, t, o, a, b, k in rows:
    if v == "dse" and t and (t not in first_write or o < first_write[t]): first_write[t] = o
first_del = {}
for t, d in agent_dels:
    first_del.setdefault(t, d)
print("\n(d) survival of dse agent titles by creation day (hours to first delete; n deleted / n created)")
bucket = collections.defaultdict(list); created = collections.Counter()
for t, w in first_write.items():
    created[w.date()] += 1
    if t in first_del and first_del[t] >= w: bucket[w.date()].append((first_del[t] - w).total_seconds() / 3600)
for d in sorted(created):
    if created[d] >= 30:
        h = bucket[d]
        print(f" {d}: created {created[d]:5d} deleted {len(h):5d} ({len(h)/created[d]:.0%}) median h {st.median(h) if h else float('nan'):7.1f}")

# confound: agent writes on venues that never saw a delete, daily around the onset
print("\nconfound check: agent writes per day on venues with no deletes at all")
nodel = collections.Counter(r[2].date() for r in rows if r[0] not in del_venues_onset)
for d in sorted(nodel):
    if datetime(2026,6,10).date() <= d <= datetime(2026,6,30).date(): print(f" {d} {nodel[d]}")
print("\nphase of dse agent writes by day window:", q("select phase, min(observed_time), max(observed_time), count(*) from record where venue_id='dse' and kind='revision' and actor_id like 'handle:%' group by phase"))
