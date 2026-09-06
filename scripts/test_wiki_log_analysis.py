import hashlib
import unittest
from wiki_log_analysis import changes, overwrite


def row(seq,body):
    raw=body.encode('latin1')
    return dict(rev_id=f'test~Board@{seq}',seq=seq,body=body,body_len=len(raw),
                body_sha256=hashlib.sha256(raw).hexdigest(),
                time=f'2026-06-18T00:00:{seq:02d}Z',diff_base=None,
                diff_base_reason='page_created',hunks=[])


class LogAnalysisTests(unittest.TestCase):
    def test_hash_checked_before_extracting(self):
        r=row(1,'a');r['body']='b'
        with self.assertRaises(ValueError):changes(r,{})

    def test_missing_baseline_is_unknown(self):
        r=row(1,'a');r['diff_base_reason']='earlier_revisions_not_published'
        self.assertIsNone(changes(r,{}))

    def test_only_new_lines_extracted(self):
        a=row(1,'old');b=row(2,'old\nnew')
        b.update(diff_base=a['rev_id'],diff_base_reason=None,
                 hunks=[dict(op='insert',a0=1,a1=1,b0=1,b1=2)])
        self.assertEqual(changes(b,{a['rev_id']:a}),['new'])

    def test_unrepresented_changes_rejected(self):
        a=row(1,'old');b=row(2,'different');b['diff_base']=a['rev_id']
        with self.assertRaises(ValueError):changes(b,{a['rev_id']:a})

    def test_exact_returns_are_distinct_from_adjacent_identical(self):
        r=overwrite([row(1,'a'),row(2,'a'),row(3,'b'),row(4,'a')])
        self.assertEqual(r['distinct_bodies'],2)
        self.assertEqual(r['adjacent_identical_saves'],1)
        self.assertEqual(r['nonadjacent_body_returns'],1)
        self.assertEqual(r['transitions_reintroducing_exact_lines'],1)
        self.assertEqual(r['reintroduction_lag_seconds_median'],1)

    def test_line_movement_is_not_removal(self):
        r=overwrite([row(1,'a\nb'),row(2,'b\na')])
        self.assertEqual(r['transitions_removing_exact_nonblank_lines'],0)

    def test_blank_lines_do_not_count_as_information(self):
        r=overwrite([row(1,'a\n '),row(2,'a')])
        self.assertEqual(r['removed_line_events'],0)


if __name__=='__main__':unittest.main()
