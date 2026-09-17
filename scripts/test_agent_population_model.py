#!/usr/bin/env python3
"""Conservation identities for the agent-population generative model.

Run: python3 scripts/test_agent_population_model.py   (needs numpy + torch)

These are the checks that caught the earlier mis-specification: a sharing model
that cannot conserve instances, or cannot place mass at large handle sizes
without inflating run size, fails here rather than silently returning a wrong
population estimate.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import numpy as np
    import torch
except ImportError as exc:  # pragma: no cover
    raise unittest.SkipTest(f"numpy + torch required: {exc.name} not installed") from exc

import agent_population_model as M


class TestGenerativeModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        make, unpack, els = M.build(torch)
        # staticmethod: a bare function stored on the class would be bound and
        # receive `self` as its first argument
        cls.make = staticmethod(make)
        cls.unpack = staticmethod(unpack)
        cls.els = staticmethod(els)

    def _els(self, N, mu, phi, w, alpha, gamma, rho=1e-6, gamma_hub=30.0):
        args = [torch.tensor(float(v))
                for v in (N, mu, phi, w, alpha, gamma, rho, gamma_hub)]
        return type(self).els(*args)

    def test_revisions_are_conserved(self):
        """Total revisions implied by the handle sizes must equal N * E[n]."""
        for N, mu in [(2500, 4.0), (1200, 2.5), (3000, 1.8)]:
            lam = self._els(N, mu, 1.0, 0.36, 3.4, 3000.0)
            got = float((torch.arange(M.NL + 1).double() * lam).sum())
            pn = torch.exp(self._nb(mu, 1.0))
            En = float((torch.arange(M.NI + 1).double() * pn).sum())
            self.assertAlmostEqual(got / (N * En), 1.0, places=3,
                                   msg="revisions not conserved at N=%d" % N)

    def _nb(self, mu, phi):
        mu_t, phi_t = torch.tensor(float(mu)), torch.tensor(float(phi))
        KI = torch.arange(M.NI + 1)
        n = KI[1:]
        lp = (torch.lgamma(n + phi_t) - torch.lgamma(phi_t) - torch.lgamma(n + 1.0)
              + phi_t * torch.log(phi_t / (phi_t + mu_t))
              + n * torch.log(mu_t / (phi_t + mu_t)))
        lp = lp - torch.logsumexp(lp, 0)
        return torch.cat([torch.full((1,), -60.0, dtype=lp.dtype), lp])

    @staticmethod
    def _instance_mass(T, gamma, jmax=None):
        J = torch.arange(1, (jmax or M.JMAX) + 1).double()
        Tt, gt = torch.tensor(float(T)), torch.tensor(float(gamma))
        log_h = (torch.log(gt) - torch.log(J)
                 + torch.lgamma(Tt + 1.0) - torch.lgamma(Tt - J + 1.0)
                 + torch.lgamma(gt + Tt - J) - torch.lgamma(gt + Tt))
        Hj = torch.where(J <= Tt - 1, torch.exp(log_h), torch.zeros_like(log_h))
        return float((J * Hj).sum()) / float(T)

    def test_sharing_conserves_instances(self):
        """Ewens multiplicities satisfy sum_j j*H_j == T, up to JMAX truncation.

        JMAX caps instances per handle, so conservation is approximate. In the
        regime the posterior actually occupies (gamma of order 1e4) the loss is
        about 0.1%; 0.5% is the tolerance across the sampled range.
        """
        for T, gamma in [(4277.0, 3000.0), (4300.0, 9650.0), (2000.0, 12000.0)]:
            self.assertAlmostEqual(self._instance_mass(T, gamma), 1.0, delta=5e-3,
                                   msg="instances not conserved at gamma=%g" % gamma)

    def test_truncation_documents_its_own_limit(self):
        """Record where the instance truncation breaks down.

        JMAX was raised from 120 to 400 precisely because the hub component
        needs ~110 instances to build the 317-revision handle. Heavier sharing
        still breaks it: a future refit that wanders to low gamma_hub must raise
        JMAX rather than trust the likelihood.
        """
        self.assertLess(self._instance_mass(9000.0, 5.0), 0.85)
        self.assertGreater(self._instance_mass(9000.0, 5.0, jmax=6000), 0.98)

    def test_more_sharing_means_fewer_handles(self):
        """Lower gamma must yield fewer, larger handles at fixed N and mu."""
        hi = self._els(2500, 4.0, 1.0, 0.36, 3.4, 6000.0)
        lo = self._els(2500, 4.0, 1.0, 0.36, 3.4, 500.0)
        self.assertLess(float(lo.sum()), float(hi.sum()))
        self.assertGreater(float(lo[100:].sum()), float(hi[100:].sum()))

    def test_hub_component_is_what_makes_the_tail(self):
        """The regression that motivated the hub level.

        A single thin CRP at the fitted handle count predicts almost nothing
        above 100 revisions (0.07-0.34 against 6 observed). Adding the hub
        component must restore that mass without changing run sizes.
        """
        thin = self._els(1900, 2.8, 0.2, 0.4, 3.5, 10000.0)
        hub = self._els(1900, 2.8, 0.2, 0.4, 3.5, 10000.0, rho=0.10, gamma_hub=8.0)
        self.assertLess(float(thin[100:].sum()), 1.0)
        self.assertGreater(float(hub[100:].sum()), 3.0)
        self.assertGreater(float(hub[200:].sum()), float(thin[200:].sum()))

    def test_no_rotation_means_one_label_per_run(self):
        """With w=0 and no sharing, handles should mirror the run-size pmf."""
        N, mu = 2000.0, 4.0
        lam = self._els(N, mu, 1.0, 0.0, 3.4, 1e9)
        pn = torch.exp(self._nb(mu, 1.0))
        self.assertAlmostEqual(float(lam.sum()) / N, 1.0, places=2)
        k = 3
        self.assertAlmostEqual(float(lam[k]) / (N * float(pn[k])), 1.0, places=2)

    def test_audited_partition_likelihood_matches_known_mle(self):
        """The vectorised Ewens form must reproduce the standalone mixture MLE.

        Fitted independently on the 322 audited partitions: w=0.359,
        alpha=3.382, loglik=-474.4.
        """
        export = os.environ.get("COLLUSION_EXPORT")
        if not export or not os.path.isdir(export):
            self.skipTest("set COLLUSION_EXPORT to the export directory")
        _y, aud, _nl, _nr = M.load(export)
        n = torch.tensor(aud["n"], dtype=torch.float64)
        K = torch.tensor(aud["K"], dtype=torch.float64)
        const = torch.tensor(aud["const"], dtype=torch.float64)
        lg = torch.lgamma(n + 1.0)
        single = torch.tensor([k == 1 for k in aud["K"]])

        w, a = torch.tensor(0.359), torch.tensor(3.382)
        log_ew = lg - torch.lgamma(a + n) + torch.lgamma(a) + K * torch.log(a) - const
        rot = torch.log(w) + log_ew
        non = torch.where(single, torch.log1p(-w).expand_as(rot),
                          torch.full_like(rot, -1e30))
        ll = float(torch.logsumexp(torch.stack([rot, non]), 0).sum())
        self.assertAlmostEqual(ll, -474.4, places=0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
