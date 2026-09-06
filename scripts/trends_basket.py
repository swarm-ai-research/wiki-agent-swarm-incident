#!/usr/bin/env python3
"""Replicate the zosoproject Google Trends basket signal (analysis/trends-basket-signal.md).

Pulls weekly worldwide interest for the named basket phrases, two control baskets,
and the retailer probes; repeats the basket pull under different sessions to
measure sampler noise; pulls related queries in both directions; and pulls
interest-by-region for one basket phrase and one probe. Writes CSV/JSON into
--out. Google Trends indices are normalised to 100 per request, so compare shapes
and ratios, not absolute values across files.

    python3 scripts/trends_basket.py --out runs/trends
"""
import argparse, json, os, time, warnings
warnings.filterwarnings("ignore")
import pandas as pd
from pytrends.request import TrendReq

BASKET = ["docker containerization", "software testing strategies",
          "api design principles", "natural language processing"]
CONTROL_A = ["kubernetes orchestration", "database indexing strategies",
             "microservices architecture patterns", "distributed systems consensus"]
CONTROL_B = ["object oriented programming", "continuous integration pipeline",
             "message queue systems", "functional programming concepts"]
PROBES = ["lidl near me", "walmart near me"]
TF = "2025-01-01 2026-09-06"


def session(**kw):
    return TrendReq(timeout=(10, 30), **kw)


def retry(fn, tag, tries=4):
    for a in range(tries):
        try:
            return fn()
        except Exception as e:  # rate limits mostly
            print(tag, "ERR", str(e)[:80], flush=True)
            time.sleep(30 + 30 * a)


def iot(pt, kws, tf=TF, geo=""):
    pt.build_payload(kws, timeframe=tf, geo=geo)
    return pt.interest_over_time().drop(columns="isPartial")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="runs/trends")
    out = ap.parse_args().out; os.makedirs(out, exist_ok=True)
    pt = session(hl="en-US", tz=0)
    frames = []
    for kws, tag in [(BASKET, "basket"), (CONTROL_A, "control_a"), (CONTROL_B, "control_b"), (PROBES, "probes")]:
        df = retry(lambda: iot(pt, kws), tag); df.to_csv(f"{out}/{tag}.csv"); frames.append(df); time.sleep(8)
    pd.concat(frames, axis=1).to_csv(f"{out}/weekly_all.csv")
    # sampler noise: a fresh session with a different locale and a shifted window
    for name, kw, tf in [("de", dict(hl="de-DE", tz=-60), "2025-01-01 2026-09-05"),
                         ("fr", dict(hl="fr-FR", tz=60), "2024-12-29 2026-09-06")]:
        df = retry(lambda: iot(session(**kw), BASKET, tf), name); df.to_csv(f"{out}/basket_var_{name}.csv"); time.sleep(8)
    # related queries both directions
    for kw in ["lidl near me", "docker containerization"]:
        def rq():
            p = session(hl="en-US", tz=0); p.build_payload([kw], timeframe=TF)
            r = p.related_queries()[kw]
            return {k: (v.to_dict("records") if v is not None else None) for k, v in r.items()}
        json.dump(retry(rq, kw), open(f"{out}/related_{kw.replace(' ', '_')}.json", "w"), indent=1); time.sleep(8)
    # interest by region (fresh session each time: pytrends keeps the last geo otherwise)
    for kw in ["docker containerization", "lidl near me"]:
        def ibr():
            p = session(hl="en-US", tz=0); p.build_payload([kw], timeframe="2026-02-01 2026-09-06", geo="")
            return p.interest_by_region(resolution="COUNTRY", inc_low_vol=True)
        retry(ibr, kw).to_csv(f"{out}/region_{kw.replace(' ', '_')}.csv"); time.sleep(8)


if __name__ == "__main__":
    main()
