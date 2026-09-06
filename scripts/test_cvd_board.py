import unittest
from cvd_board import simulate, CONDITIONS


class BoardTests(unittest.TestCase):
    def test_reproducible_and_conserves_outcomes(self):
        for mode in CONDITIONS:
            a=simulate(7,mode)
            self.assertEqual(a,simulate(7,mode))
            self.assertAlmostEqual(a['correct_rate']+a['wrong_rate']+a['missed_rate'],1)
            self.assertLessEqual(a['copied_wrong_rate'],a['wrong_rate'])
            self.assertLessEqual(a['research_seconds_per_task'],22)

    def test_single_agent_cannot_copy_itself(self):
        c={'agents':1}
        self.assertEqual(simulate(1,'independent',c),simulate(1,'answers',c))

    def test_messages_cannot_arrive_before_delivery(self):
        c={'latency':1000000,'error_rate':0}
        self.assertEqual(simulate(1,'independent',c),simulate(1,'disrupted',c))

    def test_expired_answers_unavailable(self):
        c={'agents':3,'rounds':1,'research_median':10,'research_sigma':0,
           'research_accuracy':1,'latency':0,'error_rate':0,'ttl':1,'stagger':30}
        self.assertEqual(simulate(1,'disrupted',c)['copied_rate'],0)
        self.assertGreater(simulate(1,'answers',c)['copied_rate'],0)

    def test_corrupt_relay_propagates_wrong_answers(self):
        c={'agents':3,'rounds':1,'research_median':10,'research_sigma':0,
           'research_accuracy':1,'latency':0,'error_rate':1,'ttl':1000,'stagger':30}
        r=simulate(1,'disrupted',c)
        self.assertAlmostEqual(r['copied_wrong_rate'],2/3)
        self.assertAlmostEqual(r['correct_rate'],1/3)

    def test_references_reduce_effort_without_copying(self):
        c={'agents':3,'rounds':1,'research_median':10,'research_sigma':0,
           'research_accuracy':1,'stagger':30}
        independent=simulate(1,'independent',c)
        reference=simulate(1,'references',c)
        self.assertEqual(reference['copied_rate'],0)
        self.assertLess(reference['research_seconds_per_task'],independent['research_seconds_per_task'])

    def test_no_future_round_leakage(self):
        c={'agents':1,'rounds':5,'research_sigma':0,'research_median':30}
        self.assertEqual(simulate(1,'answers',c)['missed_rate'],1)

    def test_invalid_config(self):
        for c in [{'poll':0},{'agents':0},{'error_rate':2},{'latency':-1}]:
            with self.assertRaises(ValueError): simulate(0,'answers',c)


if __name__=='__main__': unittest.main()
