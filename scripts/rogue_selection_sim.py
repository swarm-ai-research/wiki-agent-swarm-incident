"""Toy evolutionary sim for test 2b (analysis/rogue-agent-explosion-tests.md). numpy only, seeded."""
import numpy as np

H, BURN, FORK, CAP, ROUNDS, WIN, SD = 1.2, 1.0, 20.0, 500, 400, 40, 0.05


def run(d0, k, premium, seed, gamma=2.0, a0=0.5, rounds=ROUNDS):
    rng = np.random.default_rng(seed)
    n = 200
    a = np.clip(a0 + rng.normal(0, 0.05, n), 0, 1)
    c = np.clip(0.2 + rng.normal(0, 0.05, n), 0, 1)
    bal = np.full(n, 10.0)
    T = H * premium
    rows = []
    det_n = thief_n = 0
    for t in range(rounds):
        if a.size == 0:
            rows.append((np.nan, np.nan, np.nan, 0)); continue
        bal += H * (1 - a) + T * a * (1 - k * c) - BURN
        p = d0 * a * (1 - c) ** gamma
        detected = rng.random(a.size) < p
        thief = a > 0.5
        det_n += (detected & thief).sum(); thief_n += thief.sum()
        keep = (~detected) & (bal > 0)
        a, c, bal = a[keep], c[keep], bal[keep]
        f = bal > FORK
        if f.any():
            bal[f] /= 2
            ca = np.clip(a[f] + rng.normal(0, SD, f.sum()), 0, 1)
            cc = np.clip(c[f] + rng.normal(0, SD, f.sum()), 0, 1)
            a, c, bal = np.r_[a, ca], np.r_[c, cc], np.r_[bal, bal[f]]
        if a.size > CAP:
            idx = rng.choice(a.size, CAP, replace=False)
            a, c, bal = a[idx], c[idx], bal[idx]
        if (t + 1) % WIN == 0:
            rate = det_n / thief_n if thief_n else np.nan
            rows.append((a.mean() if a.size else np.nan,
                         c.mean() if c.size else np.nan, rate, a.size))
            det_n = thief_n = 0
    return [r for r in rows if len(r) == 4][-(rounds // WIN):]


def classify(a, c, pop):
    if pop == 0 or np.isnan(a):
        return "extinct"
    if a < 0.3:
        return "honest"
    if a >= 0.5:
        return "sneaky" if c >= 0.5 else "open"
    return "mixed"


def main():
    seeds = range(12)
    print("d0     k    prem | a0->aF   c0->cF   det0->detF  pop | class")
    summary = {}
    for premium in (1.5, 3.0, 6.0):
        for k in (0.2, 0.5, 0.8):
            for d0 in (0.0, 0.003, 0.01, 0.03, 0.1, 0.3):
                runs = [np.array(run(d0, k, premium, s), float) for s in seeds]
                R = np.nanmedian(np.stack(runs), axis=0)
                (a0, c0, r0, _), (aF, cF, rF, pF) = R[0], R[-1]
                cls = classify(aF, cF, pF)
                summary[(premium, k, d0)] = (cls, aF, cF, r0, rF)
                print(f"{d0:<6} {k:<4} {premium:<4} | {a0:.2f}->{aF:.2f}  "
                      f"{c0:.2f}->{cF:.2f}  {r0:.4f}->{rF:.4f}  {pF:4.0f} | {cls}")
    return summary


def honest_start():
    """Exploratory, added after the pre-registered sweep: start mostly honest."""
    print("start a=0.1, c=0.2, k=0.5; median over 12 seeds, rounds=400 and 1200")
    for rounds in (400, 1200):
        for premium in (1.25, 1.5, 2.0, 3.0):
            cells = []
            for d0 in (0.02, 0.1, 0.2, 0.3):
                runs = [np.array(run(d0, 0.5, premium, s, a0=0.1, rounds=rounds), float)
                        for s in range(12)]
                aF, cF, _, pF = np.nanmedian(np.stack(runs), axis=0)[-1]
                cells.append(f"d0={d0}: {classify(aF, cF, pF)} {aF:.2f}/{cF:.2f}")
            print(f"T={rounds} prem={premium}: " + " | ".join(cells))


if __name__ == "__main__":
    import sys
    honest_start() if "--honest-start" in sys.argv else main()
