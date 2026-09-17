#!/usr/bin/env python3
"""Estimate how many agent RUNS wrote the DSEWiki export.

Usage:
  python3 scripts/agent_population_model.py map     --export DIR
  python3 scripts/agent_population_model.py sample  --export DIR [--chains 4]

``--export DIR`` is the collusion.wiki export directory holding
``revisions.jsonl[.gz]``. Writes ``data/agent_population_posterior.json``.

Requires numpy and **torch** (autograd for the NUTS gradients). torch is not
used anywhere else in this repository; only this script needs it.

WHY A MODEL AND NOT A COUNT
---------------------------
The export carries 3,103 actor labels, but a label is not an agent: the audited
run reconstruction shows 437 labels collapsing to 276 runs on one shared set of
revisions, because runs rotate handles. Counting distinct labels over-counts;
the 298 audited runs under-count, because a run that posted once is invisible to
a reconstruction that needs two signed contributions. This script estimates the
latent count instead, with three generative levels:

  1. N runs; run i writes n_i ~ NegBinomial(mu, phi) revisions, truncated >= 1.
  2. WITHIN a run, revisions split across label instances by a two-component
     rotation law: with probability 1-w the run never rotates (one label), with
     probability w its revisions partition by CRP(alpha). A single CRP fits the
     mean labels-per-run but not its shape -- the audited data is bimodal (220
     of 322 runs use exactly one label, then a tail out to 14), so rotation is a
     discrete behavioural mode, not a continuous propensity. The mixture raises
     the audited log-likelihood from -678.6 to -474.4 for one extra parameter.
  3. ACROSS runs, those instances attach to globally shared handles, in TWO
     components: a fraction (1-rho) spread thinly under CRP(gamma), and a
     fraction rho concentrated onto a hub pool under CRP(gamma_hub).

     Sharing at all is required, not decorative: the largest handle
     (`AgentRelent`) carries 317 revisions, and a model capped at two runs per
     handle can only reproduce that tail by inflating run size -- doing so drove
     a trial fit to mu ~ 50 and N ~ 350, against the audited runs' observed mean
     of 5.79. But ONE concentration is also not enough, because it has to make
     many handles and a few very popular ones at the same time: fitting the
     3,102 handle count drove gamma high enough to erase the tail (0.07 to 0.34
     handles with >=100 revisions predicted, 6 observed). Splitting it fixed the
     bulk of the distribution and moved N from 1,925 to ~3,070.

Expected observed handles of size k:

    E[L_k] = sum_j E[H_j] * (q convolved j times)_k
    E[H_j] = Ewens(T*(1-rho), gamma)_j + Ewens(T*rho, gamma_hub)_j
    Ewens(T, g)_j = (g/j) * T!/(T-j)! * Gamma(g+T-j)/Gamma(g+T)

with T the expected instance count and q the per-instance size pmf; E[H_j] and
the within-run E[T_k(n)] are both Ewens sampling-formula expectations, so the
whole predicted distribution is closed-form and differentiable.

THE AUDITED SUBSET IS NOT A RANDOM SAMPLE
-----------------------------------------
Its labels average 7.06 export revisions against 3.93 for the rest (18% vs 47%
singletons): the reconstruction reached heavy, chatty runs. It therefore enters
the likelihood through an explicit size-biased selection model,

    P(run of size n is audited) proportional to n^b

with b ESTIMATED rather than assumed (b=0 random, b=1 probability proportional
to revisions written). Letting b float is what stops the audited runs' inflated
mean from propagating into mu. Fitting the export alone cannot substitute:
profiling over w moves N 5-fold (498 to 2,352) on ~57 nats of likelihood.
"""
import argparse
import collections
import gzip
import json
import math
import os
import sys

import numpy as np

# ------------------------------------------------------------------ grids

# JMAX caps instances per handle. The largest handle carries 317 revisions at a
# mean instance size near 2.9, so ~110 instances; 120 clipped it.
NI, NL, JMAX, FFTN = 250, 400, 400, 2048
NAMES = ["N_runs", "mu", "phi", "w_rotator", "alpha", "gamma", "b_sizebias",
         "rho_hub", "gamma_hub"]

PRIOR = [
    (math.log(2000.0), 2.0),        # log N       very weak
    (math.log(4.0), 1.2),           # log mu      weak
    (0.0, 1.5),                     # log phi
    (0.0, 1.5),                     # logit w     weak; audited data informs it
    (math.log(3.0), 1.0),           # log alpha   weak; likewise
    (math.log(3000.0), 2.0),        # log gamma   very weak -- SEE KNOWN DEFECT
    (math.log(0.7), 0.6),           # log b       random vs size-biased
    (math.log(0.10 / 0.90), 1.2),   # logit rho   hub share of instances
    (math.log(30.0), 2.0),          # log gamma_hub  small hub pool
]

# KNOWN DEFECT: gamma is only weakly identified once the hub component absorbs
# the sharing -- any sufficiently large value fits, and the posterior spans
# 1.5e4 to 3.7e6. That plateau produced 102 divergent transitions (4.3%) and
# rhat up to 1.05 in the published fit. N is stable across chains regardless
# (per-chain medians 3043-3077, rhat 1.013), but the interval stays provisional
# until gamma is bounded or the sharing level is reparameterised as a single
# "fraction of instances that share" quantity.

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")


# ------------------------------------------------------------------ data

def _open_export(export_dir):
    for name in ("revisions.jsonl", "revisions.jsonl.gz"):
        p = os.path.join(export_dir, name)
        if os.path.exists(p):
            return gzip.open(p, "rt") if p.endswith(".gz") else open(p)
    raise FileNotFoundError("revisions.jsonl[.gz] not under %s" % export_dir)


def load(export_dir, map_path=None):
    """Export label-size counts plus the audited run partitions."""
    lab = {}
    with _open_export(export_dir) as fh:
        for line in fh:
            r = json.loads(line)
            lab[r["rev_id"]] = r.get("label") or ""

    lc = collections.Counter(v for v in lab.values() if v)
    y = np.zeros(NL + 1)
    for k, c in collections.Counter(lc.values()).items():
        if k <= NL:
            y[k] += c

    map_path = map_path or os.path.join(ROOT, "data", "run_identity_map.json")
    m = json.load(open(map_path))
    run2revs = collections.defaultdict(list)
    for rev, info in m["revisions"].items():
        run2revs[info["run"]].append(rev)

    ns, Ks, consts = [], [], []
    for _run, revs in run2revs.items():
        c = collections.Counter(lab[r] for r in revs if lab.get(r))
        if not c:
            continue
        v = list(c.values())
        n = sum(v)
        if n > NI:
            continue
        tk = collections.Counter(v)
        ns.append(n)
        Ks.append(len(v))
        consts.append(sum(t * math.log(k) + math.lgamma(t + 1)
                          for k, t in tk.items()))
    aud = {"n": ns, "K": Ks, "const": consts}
    return y, aud, len(lc), sum(lc.values())


# ------------------------------------------------------------------ model

def build(torch):
    """Return (log_post, grad_log_post, unpack, expected_label_sizes)."""
    torch.set_default_dtype(torch.float64)

    K = torch.arange(NL + 1)
    KI = torch.arange(NI + 1)
    _N = KI.unsqueeze(1).double()
    _Kc = KI.unsqueeze(0).double()
    _VALID = (_Kc >= 1) & (_Kc <= _N)
    _NK = torch.clamp(_N - _Kc, min=0.0)
    _CONST = (torch.lgamma(_N + 1.0) - torch.lgamma(_NK + 1.0)
              - torch.log(torch.clamp(_Kc, min=1.0)))
    _ZERO = torch.zeros_like(_CONST)
    _J = torch.arange(1, JMAX + 1).double()
    _LOGKI = torch.log(torch.clamp(KI.double(), min=1.0))

    def nb_logpmf(mu, phi):
        n = KI[1:]
        lp = (torch.lgamma(n + phi) - torch.lgamma(phi) - torch.lgamma(n + 1.0)
              + phi * torch.log(phi / (phi + mu))
              + n * torch.log(mu / (phi + mu)))
        lp = lp - torch.logsumexp(lp, 0)
        return torch.cat([torch.full((1,), -60.0, dtype=lp.dtype), lp])

    def expected_tables(alpha):
        log_t = (torch.log(alpha) + _CONST + torch.lgamma(alpha + _NK)
                 - torch.lgamma(alpha + _N))
        return torch.where(_VALID, torch.exp(log_t), _ZERO)

    def ewens_Hj(T, gamma):
        log_h = (torch.log(gamma) - torch.log(_J)
                 + torch.lgamma(T + 1.0) - torch.lgamma(T - _J + 1.0)
                 + torch.lgamma(gamma + T - _J) - torch.lgamma(gamma + T))
        return torch.where(_J <= T - 1, torch.exp(log_h), torch.zeros_like(log_h))

    def expected_label_sizes(N, mu, phi, w, alpha, gamma, rho, gamma_hub):
        """Two-component handle sharing.

        A single CRP cannot make many handles AND a few very popular ones: one
        concentration does both jobs, and matching the 3,102 handle count
        drives gamma high enough to erase the tail -- 0.07 to 0.34 handles with
        >=100 revisions predicted against 6 observed. So a fraction (1-rho) of
        instances spread thinly under CRP(gamma) and a fraction rho concentrate
        onto a hub pool under CRP(gamma_hub << gamma). This mirrors the
        within-run finding one level down: rotation, and handle popularity, are
        both discrete modes rather than continuous propensities.
        """
        pn = torch.exp(nb_logpmf(mu, phi))
        inst = (1.0 - w) * pn + w * (pn.unsqueeze(1) * expected_tables(alpha)).sum(0)
        per_run = inst.sum()
        T = N * per_run
        q = inst / per_run
        Hj = ewens_Hj(T * (1.0 - rho), gamma) + ewens_Hj(T * rho, gamma_hub)
        Q = torch.fft.rfft(q, n=FFTN)
        conv = torch.fft.irfft(Q.unsqueeze(0) ** _J.view(-1, 1), n=FFTN)[:, :NL + 1]
        return (Hj.unsqueeze(1) * torch.clamp(conv, min=0.0)).sum(0)

    def unpack(theta):
        return (torch.exp(theta[0]), torch.exp(theta[1]), torch.exp(theta[2]),
                torch.sigmoid(theta[3]), torch.exp(theta[4]),
                torch.exp(theta[5]), torch.exp(theta[6]),
                torch.sigmoid(theta[7]), torch.exp(theta[8]))

    def make(y_t, aud_t):
        def log_post(theta):
            N, mu, phi, w, alpha, gamma, b, rho, gamma_hub = unpack(theta)
            lam = torch.clamp(
                expected_label_sizes(N, mu, phi, w, alpha, gamma, rho, gamma_hub),
                min=1e-12)
            ll = (y_t * torch.log(lam) - lam).sum()

            logp = nb_logpmf(mu, phi)
            lb = logp + b * _LOGKI
            lb = lb - torch.logsumexp(lb, 0)
            ll = ll + lb[aud_t["n"].long()].sum()

            log_ew = (aud_t["lgamma_n1"] - torch.lgamma(alpha + aud_t["n"])
                      + torch.lgamma(alpha) + aud_t["K"] * torch.log(alpha)
                      - aud_t["const"])
            rot = torch.log(w) + log_ew
            non = torch.where(aud_t["single"], torch.log1p(-w).expand_as(rot),
                              torch.full_like(rot, -1e30))
            ll = ll + torch.logsumexp(torch.stack([rot, non]), dim=0).sum()

            lp = 0.0
            for i, (loc, sc) in enumerate(PRIOR):
                lp = lp - 0.5 * ((theta[i] - loc) / sc) ** 2
            lp = (lp + theta[0] + theta[1] + theta[2] + theta[4] + theta[5]
                  + theta[6] + theta[8] + torch.log(w * (1 - w))
                  + torch.log(rho * (1 - rho)))
            return ll + lp

        def grad_log_post(theta):
            t = theta.detach().requires_grad_(True)
            lp = log_post(t)
            g, = torch.autograd.grad(lp, t)
            return lp.detach(), g

        return log_post, grad_log_post

    return make, unpack, expected_label_sizes


def to_tensors(torch, y, aud):
    t = lambda v: torch.tensor(v, dtype=torch.float64)
    n = t(aud["n"])
    return t(y), {
        "n": n, "K": t(aud["K"]), "const": t(aud["const"]),
        "lgamma_n1": torch.lgamma(n + 1.0),
        "single": torch.tensor([k == 1 for k in aud["K"]]),
    }


# ------------------------------------------------------------------ cli

def cmd_map(args):
    import torch
    y, aud, nl, nr = load(args.export, args.map)
    y_t, aud_t = to_tensors(torch, y, aud)
    make, unpack, _ = build(torch)
    log_post, _ = make(y_t, aud_t)

    best, best_lp = None, -1e30
    for seed in range(3):
        g = torch.Generator().manual_seed(seed)
        th = (torch.tensor([p[0] for p in PRIOR])
              + torch.randn(len(PRIOR), generator=g) * 0.2).clone().requires_grad_(True)
        opt = torch.optim.Adam([th], lr=0.03)
        for _ in range(args.iters):
            opt.zero_grad()
            (-log_post(th)).backward()
            opt.step()
        lp = float(log_post(th).detach())
        if lp > best_lp:
            best, best_lp = th.detach(), lp
    print("export: %d labels / %d revisions | audited: %d runs"
          % (nl, nr, len(aud["n"])))
    print("MAP (logpost %.1f): " % best_lp
          + "  ".join("%s=%.3f" % (n, float(v))
                      for n, v in zip(NAMES, unpack(best))))
    return best


# ------------------------------------------------------------------ nuts
#
# NUTS with a dense Euclidean metric, following Betancourt, "A Conceptual
# Introduction to Hamiltonian Monte Carlo" (arXiv:1701.02434). Three details
# that this posterior actually needs:
#
#   * DENSE metric. log N and log mu are strongly correlated (their product is
#     pinned near the labelled-revision total), and a diagonal metric explores
#     that ridge badly. The metric is estimated over expanding warmup windows.
#   * DIVERGENCE detection and E-BFMI. R-hat and ESS alone cannot see a chain
#     that is diverging on every transition.
#   * The adaptation statistic is the Metropolis ratio AVERAGED OVER EVERY
#     STATE in the trajectory. Using the accepted point's own ratio instead
#     makes a divergence that never moves report acceptance 1.0, which drives
#     dual averaging to raise the step size without bound until every
#     transition diverges -- observed here as eps -> 9.4, depth 0, 800/800
#     divergent, with acceptance reported as a perfect 1.00.

MAX_DEPTH = 10
DIVERGENCE_DH = 1000.0


def _nuts(torch, grad_fn, q0, n_warmup, n_samples, seed, target=0.8):
    rng = np.random.default_rng(seed)
    dim = q0.shape[0]
    inv_metric = torch.eye(dim, dtype=torch.float64)
    chol = torch.linalg.cholesky(inv_metric)

    def sample_p():
        z = torch.from_numpy(rng.normal(size=dim))
        return torch.linalg.solve_triangular(chol.T, z.unsqueeze(1),
                                             upper=True).squeeze(1)

    kinetic = lambda p: 0.5 * (p * (inv_metric @ p)).sum()
    velocity = lambda p: inv_metric @ p

    def leapfrog(q, p, eps):
        _lp, g = grad_fn(q)
        p = p + 0.5 * eps * g
        q = q + eps * velocity(p)
        lp, g = grad_fn(q)
        return q, p + 0.5 * eps * g, lp

    def uturn(pm, pp, qm, qp):
        d = qp - qm
        return (torch.dot(d, velocity(pm)) < 0) or (torch.dot(d, velocity(pp)) < 0)

    def build(q, p, sign, depth, eps, H0):
        if depth == 0:
            q1, p1, lp1 = leapfrog(q, p, eps * sign)
            H = (-lp1 + kinetic(p1)) if torch.isfinite(lp1) else float("inf")
            dH = float(H - H0)
            div = (not math.isfinite(dH)) or dH > DIVERGENCE_DH
            return dict(qm=q1, pm=p1, qp=q1, pp=p1, prop=q1,
                        logw=float("-inf") if div else -dH, div=int(div),
                        turn=False,
                        sacc=0.0 if not math.isfinite(dH) else math.exp(min(0.0, -dH)),
                        nacc=1)
        left = build(q, p, sign, depth - 1, eps, H0)
        if left["turn"] or left["div"]:
            return left
        anchor_q, anchor_p = (left["qp"], left["pp"]) if sign > 0 else (left["qm"], left["pm"])
        right = build(anchor_q, anchor_p, sign, depth - 1, eps, H0)
        out = dict(div=left["div"] + right["div"],
                   sacc=left["sacc"] + right["sacc"],
                   nacc=left["nacc"] + right["nacc"])
        if sign > 0:
            out["qm"], out["pm"], out["qp"], out["pp"] = left["qm"], left["pm"], right["qp"], right["pp"]
        else:
            out["qm"], out["pm"], out["qp"], out["pp"] = right["qm"], right["pm"], left["qp"], left["pp"]
        lw = np.logaddexp(left["logw"], right["logw"])
        out["prop"] = (right["prop"]
                       if right["logw"] > float("-inf")
                       and math.log(rng.random() + 1e-300) < right["logw"] - lw
                       else left["prop"])
        out["logw"] = lw
        out["turn"] = (left["turn"] or right["turn"]
                       or uturn(out["pm"], out["pp"], out["qm"], out["qp"]))
        return out

    q = q0.clone()
    eps, mu_da = 0.25, math.log(2.5)
    log_eps_bar, H_bar = 0.0, 0.0
    window, win_ends = [], _warmup_windows(n_warmup)
    draws, energies, divs, depths, accs = [], [], [], [], []

    for it in range(n_warmup + n_samples):
        p0 = sample_p()
        lp0, _ = grad_fn(q)
        H0 = -lp0 + kinetic(p0)
        qm = qp = prop = q
        pm = pp = p0
        logw, depth, ndiv, turn = 0.0, 0, 0, False
        sacc, nacc = 0.0, 0

        while depth < MAX_DEPTH and not turn:
            sign = 1.0 if rng.random() < 0.5 else -1.0
            sub = build(qp if sign > 0 else qm, pp if sign > 0 else pm,
                        sign, depth, eps, H0)
            if sign > 0:
                qp, pp = sub["qp"], sub["pp"]
            else:
                qm, pm = sub["qm"], sub["pm"]
            ndiv += sub["div"]
            sacc += sub["sacc"]
            nacc += sub["nacc"]
            if sub["turn"] or sub["div"]:
                break
            if sub["logw"] > float("-inf") and \
                    math.log(rng.random() + 1e-300) < sub["logw"] - logw:
                prop = sub["prop"]
            logw = np.logaddexp(logw, sub["logw"])
            turn = uturn(pm, pp, qm, qp)
            depth += 1

        q = prop
        acc = (sacc / nacc) if nacc else 0.0

        if it < n_warmup:
            eta = 1.0 / (it + 1 + 10)
            H_bar = (1 - eta) * H_bar + eta * (target - acc)
            log_eps = min(mu_da - math.sqrt(it + 1) / 0.05 * H_bar, math.log(2.0))
            wda = (it + 1) ** -0.75
            log_eps_bar = wda * log_eps + (1 - wda) * log_eps_bar
            eps = math.exp(log_eps)
            window.append(q.numpy().copy())
            if it in win_ends and len(window) > dim + 10:
                X = np.array(window)
                n = len(X)
                cov = (n / (n + 5.0)) * np.cov(X.T) + (5.0 / (n + 5.0)) * 1e-3 * np.eye(dim)
                try:
                    inv_metric = torch.tensor(cov)
                    chol = torch.linalg.cholesky(inv_metric)
                except Exception:
                    pass
                window = []
                eps, mu_da, H_bar, log_eps_bar = 0.25, math.log(2.5), 0.0, 0.0
        else:
            if it == n_warmup:
                eps = math.exp(log_eps_bar)
            draws.append(q.numpy().copy())
            energies.append(float(H0))
            divs.append(ndiv > 0)
            depths.append(depth)
            accs.append(acc)

    E = np.array(energies)
    return dict(draws=np.array(draws), divergences=int(np.sum(divs)),
                ebfmi=float(np.sum(np.diff(E) ** 2) / (len(E) * np.var(E))),
                mean_depth=float(np.mean(depths)), accept=float(np.mean(accs)),
                eps=eps)


def _warmup_windows(n_warmup, init_buffer=75, term_buffer=50, base=25):
    ends, start, w = set(), init_buffer, base
    if n_warmup < init_buffer + term_buffer + base:
        return ends
    while start + w <= n_warmup - term_buffer:
        end = start + w
        if start + 2 * w > n_warmup - term_buffer:
            end = n_warmup - term_buffer
        ends.add(end - 1)
        start, w = end, w * 2
    return ends


def _ess(x):
    n = len(x)
    x = x - x.mean()
    f = np.fft.rfft(x, 2 * n)
    ac = np.fft.irfft(f * np.conj(f))[:n].real
    if ac[0] <= 0:
        return float(n)
    ac /= ac[0]
    s = 0.0
    for t in range(1, n - 1, 2):
        if ac[t] + ac[t + 1] < 0:
            break
        s += ac[t] + ac[t + 1]
    return float(n / (1 + 2 * s))


def _split_rhat(chains):
    parts = []
    for c in chains:
        h = len(c) // 2
        parts += [c[:h], c[h:2 * h]]
    n = len(parts[0])
    means = np.array([p.mean() for p in parts])
    W = np.array([p.var(ddof=1) for p in parts]).mean()
    if W <= 0:
        return float("nan")
    B = n * means.var(ddof=1)
    return math.sqrt(((n - 1) / n * W + B / n) / W)


def cmd_sample(args):
    import torch
    y, aud, nl, nr = load(args.export, args.map)
    y_t, aud_t = to_tensors(torch, y, aud)
    make, unpack, els = build(torch)
    log_post, grad_log_post = make(y_t, aud_t)

    q0 = cmd_map(args)
    chains, diags = [], []
    for c in range(args.chains):
        start = q0 + torch.from_numpy(
            np.random.default_rng(700 + c).normal(scale=0.12, size=len(PRIOR)))
        r = _nuts(torch, grad_log_post, start, args.warmup, args.draws, c)
        chains.append(r["draws"])
        diags.append({k: r[k] for k in
                      ("divergences", "ebfmi", "mean_depth", "accept", "eps")})
        print("chain %d: accept=%.2f eps=%.4f div=%d E-BFMI=%.2f depth=%.1f"
              % (c, r["accept"], r["eps"], r["divergences"], r["ebfmi"],
                 r["mean_depth"]), flush=True)

    allq = np.concatenate(chains, 0)
    sig = lambda z: 1 / (1 + np.exp(-z))
    tf = [np.exp, np.exp, np.exp, sig, np.exp, np.exp, np.exp, sig, np.exp]
    summary = {}
    for i, nm in enumerate(NAMES):
        v = tf[i](allq[:, i])
        summary[nm] = {
            "mean": float(v.mean()),
            "q2.5": float(np.percentile(v, 2.5)),
            "median": float(np.percentile(v, 50)),
            "q97.5": float(np.percentile(v, 97.5)),
            "rhat": float(_split_rhat([c[:, i] for c in chains])),
            "ess": float(sum(_ess(tf[i](c[:, i])) for c in chains)),
        }

    out = {
        "schema_version": 1,
        "export_labels": nl,
        "export_labelled_revisions": nr,
        "audited_runs": len(aud["n"]),
        "sampler": {"algorithm": "NUTS, dense Euclidean metric",
                    "chains": args.chains, "warmup": args.warmup,
                    "draws": args.draws,
                    "total_divergences": sum(d["divergences"] for d in diags),
                    "per_chain": diags},
        "posterior": summary,
    }
    json.dump(out, open(args.out, "w"), indent=2, sort_keys=True)
    print("wrote %s" % args.out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["map", "sample"])
    ap.add_argument("--export", required=True,
                    help="collusion.wiki export dir with revisions.jsonl[.gz]")
    ap.add_argument("--map", default=None, help="run_identity_map.json override")
    ap.add_argument("--chains", type=int, default=4)
    ap.add_argument("--warmup", type=int, default=450)
    ap.add_argument("--draws", type=int, default=650)
    ap.add_argument("--iters", type=int, default=5000)
    ap.add_argument("--out", default=os.path.join(ROOT, "data",
                                                  "agent_population_posterior.json"))
    args = ap.parse_args()
    (cmd_map if args.command == "map" else cmd_sample)(args)


if __name__ == "__main__":
    main()
