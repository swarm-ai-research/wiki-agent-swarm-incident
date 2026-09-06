import unittest
from board_storage import Board, choose, simulate


class StorageTests(unittest.TestCase):
    def test_stale_snapshot_loses_concurrent_message(self):
        b=Board('snapshot');base=b.visible.copy()
        b.commit('a',base);b.commit('b',base)
        self.assertEqual(b.visible,{'b'})
        self.assertEqual(b.removed,1)

    def test_append_preserves_concurrent_messages(self):
        b=Board('append_only');base=b.visible.copy()
        b.commit('a',base);b.commit('b',base)
        self.assertEqual(b.visible,{'a','b'})
        self.assertEqual(b.removed,0)

    def test_old_snapshot_restores_claim_and_erases_retraction(self):
        b=Board('snapshot');b.commit('bad',set());old=b.visible.copy()
        b.commit('retract',old);b.commit('note',old)
        self.assertNotIn('retract',b.visible)
        b.commit('only',set());b.commit('next',old)
        self.assertIn('bad',b.visible)
        self.assertGreater(b.restored,0)

    def test_retraction_does_not_promote_hypothesis(self):
        m={'bad':dict(kind='evidence',created=0),
           'r':dict(kind='retract',target='bad'),
           'h':dict(kind='hypothesis',created=2)}
        self.assertIsNone(choose(set(m),m))
        self.assertEqual(choose({'bad','h'},m),'bad')
        self.assertIsNone(choose({'bad','h'},m,{'r'}))

    def test_retraction_only_invalidates_target(self):
        m={'bad':dict(kind='evidence',created=0),
           'good':dict(kind='evidence',created=2),
           'r':dict(kind='retract',target='bad')}
        self.assertEqual(choose(set(m),m),'good')

    def test_paired_traffic_and_reproduction(self):
        a=simulate(3,'snapshot');b=simulate(3,'append_only')
        self.assertEqual(a,simulate(3,'snapshot'))
        self.assertEqual(a['traffic_sha256'],b['traffic_sha256'])
        for r in [a,b]:
            m=r['metrics'];self.assertAlmostEqual(m['correct_rate']+m['wrong_rate']+m['missed_rate'],1)
        self.assertEqual(b['metrics']['final_message_survival'],1)
        self.assertEqual(b['metrics']['correction_missing_time_fraction'],0)
        self.assertEqual(b['metrics']['post_retraction_bad_exposure_rate'],0)

    def test_zero_write_delay_agrees(self):
        a=simulate(3,'snapshot',{'write_delay':0})
        b=simulate(3,'append_only',{'write_delay':0})
        self.assertEqual(a,b)

    def test_future_evidence_cannot_be_used(self):
        r=simulate(3,'append_only',{'evidence_time':10000})
        self.assertNotIn('good',[d['source'] for d in r['decisions']])

    def test_fresh_reads_have_no_stale_decisions(self):
        for mode in ('snapshot','append_only'):
            self.assertEqual(simulate(3,mode,{'cache_age':0})['metrics']['stale_decision_rate'],0)

    def test_invalid_parameters(self):
        for c in [{'write_delay':-1},{'readers':0},{'research_accuracy':2}]:
            with self.assertRaises(ValueError):simulate(0,'snapshot',c)


if __name__=='__main__':unittest.main()
