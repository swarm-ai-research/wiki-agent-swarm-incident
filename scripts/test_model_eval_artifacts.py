import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import analyze_model_eval_study as analysis
import analyze_board_followup as followup
from model_eval_artifacts import preserve_reviews, require_complete, require_manifest_runs
from model_retrieval_eval import Environment, Limits, Ledger, Mock, action, run
from test_model_retrieval_eval import TASK


class ArtifactTests(unittest.TestCase):
    def test_preserve_reviews_and_notes_by_id_after_reordering(self):
        rows=[dict(blind_id='a',answer='2',question='Q',human_grade=None,adjudication=None),
              dict(blind_id='b',answer='3',question='R',human_grade=None,adjudication=None)]
        prior=copy.deepcopy(rows);prior[0].update(human_grade='correct',adjudication='accepted',reviewer_notes='Checked source')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'packet.json';p.write_text(json.dumps(prior))
            merged=preserve_reviews(p,list(reversed(rows)))
        self.assertEqual(merged[1]['human_grade'],'correct')
        self.assertEqual(merged[1]['adjudication'],'accepted')
        self.assertEqual(merged[1]['reviewer_notes'],'Checked source')
        self.assertIsNone(rows[0]['human_grade'])

    def test_refuse_to_drop_or_reassign_reviewed_answers(self):
        old=dict(blind_id='a',answer='2',reference='2',human_review={'verdict':'correct'})
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'packet.json';p.write_text(json.dumps([old]));original=p.read_bytes()
            for rows in [[],[dict(old,answer='3',human_review=None)],[dict(old,reference='3',human_review=None)]]:
                with self.assertRaises(ValueError):preserve_reviews(p,rows)
                self.assertEqual(p.read_bytes(),original)
            with self.assertRaises(ValueError):preserve_reviews(p,[old,old])

    def test_completion_requires_disjoint_valid_terminal_agent_ids(self):
        for answers,stopped in [({},{}),({'0':{}},{'0':'error'}),({'0':{}},{'2':'error'})]:
            with self.assertRaises(ValueError):require_complete(dict(task_id='t',limits={'agents':2},answers=answers,stopped=stopped))
        require_complete(dict(limits={'agents':2},answers={'0':{}},stopped={'1':'error'}))

    def test_manifest_rejects_missing_and_extra_runs(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'manifest.json';p.write_text(json.dumps({'jobs':[{'id':'a'},{'id':'b'}]}))
            for paths in [[],[Path('a.json')],[Path('a.json'),Path('b.json'),Path('c.json')]]:
                with self.assertRaises(ValueError):require_manifest_runs(paths,p)
            require_manifest_runs([Path('b.json'),Path('a.json')],p)

    def test_analyzer_rejects_incomplete_checkpoint(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'r.json';r=run(TASK,'isolated',Mock(),Limits(),Ledger(Limits()),p)
            r['answers']={};r['stopped']={};p.write_text(json.dumps(r))
            with self.assertRaisesRegex(ValueError,'Incomplete'):
                analysis.analyze([p],{'t':TASK},{'t':{'answer':'2','kind':'number','required_evidence':['new']}})
            with self.assertRaises(ValueError):analysis.analyze([],{}, {})

    def test_cli_regeneration_preserves_judgments_and_missing_run_leaves_outputs_intact(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);base=root/'data/model_eval';live=base/'live';live.mkdir(parents=True)
            name='heldout-test-s0-a1';p=live/(name+'.json')
            run(TASK,'isolated',Mock(),Limits(),Ledger(Limits()),p)
            (base/'tasks.json').write_text(json.dumps({'tasks':[TASK]}))
            (base/'references.json').write_text(json.dumps({'references':[dict(id='t',answer='2',kind='number',required_evidence=['new'])]}))
            manifest=live/'heldout-manifest-a1.json';manifest.write_text(json.dumps({'jobs':[{'id':name}]}))
            (live/'budget.json').write_text(json.dumps({'max_usd':20,'allocations':[]}))
            with patch.object(analysis,'ROOT',root),patch('sys.argv',['analyze','--phase','heldout','--attempt','1']),redirect_stdout(io.StringIO()):
                analysis.main()
                packet=base/'heldout-analysis-a1-blind-grading.json';rows=json.loads(packet.read_text())
                rows[0].update(human_grade='not_attempted',adjudication='accepted');packet.write_text(json.dumps(rows))
                analysis.main()
                regenerated=json.loads(packet.read_text())
                self.assertEqual(regenerated[0]['human_grade'],'not_attempted')
                self.assertEqual(regenerated[0]['adjudication'],'accepted')
                summary=json.loads((base/'heldout-analysis-a1-summary.json').read_text())
                self.assertEqual(summary['human_review']['completed'],1)
                self.assertEqual(summary['human_review']['agreement'],1)
                before={f.name:f.read_bytes() for f in base.glob('*.json')}
                manifest.write_text(json.dumps({'jobs':[{'id':name},{'id':'missing'}]}))
                with self.assertRaises(ValueError):analysis.main()
                self.assertEqual(before,{f.name:f.read_bytes() for f in base.glob('*.json')})

    def test_followup_regeneration_preserves_human_review(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);name='followup-test-s1';p=base/(name+'.json')
            r=run(TASK,'isolated',Mock(),Limits(),Ledger(Limits()),p)
            r.update(experiment='voluntary',final_board=[],setup_trace=[]);p.write_text(json.dumps(r))
            (base/'tasks.json').write_text(json.dumps([TASK]))
            (base/'references.json').write_text(json.dumps([dict(id='t',answer='2')]))
            (base/'manifest.json').write_text(json.dumps({'jobs':[{'id':name}]}))
            with patch.object(followup,'BASE',base),redirect_stdout(io.StringIO()):
                followup.main()
                p=base/'answer-review.json';rows=json.loads(p.read_text());rows[0]['human_review']={'verdict':'abstained','reviewer':'test'};p.write_text(json.dumps(rows))
                followup.main()
                self.assertEqual(json.loads(p.read_text())[0]['human_review'],rows[0]['human_review'])

    def test_late_response_keeps_billing_without_executing_action(self):
        clock=[0.]
        class Late(Mock):
            def respond(self,messages,cap):
                clock[0]=2.
                return dict(action=action('read',source='root'),input_tokens=100,output_tokens=10,
                            billed_usd=.00001,model=self.model,raw={'id':'late-response'})
        limits=Limits(agents=1,wall_seconds=1);ledger=Ledger(limits,1,1,4);env=Environment(TASK,'isolated',1)
        with tempfile.TemporaryDirectory() as d,patch('model_retrieval_eval.time.monotonic',side_effect=lambda:clock[0]):
            p=Path(d)/'r.json';r=run(TASK,'isolated',Late(),limits,ledger,p,environment=env)
            summary=analysis.analyze([p],{'t':TASK},{'t':dict(answer='2',kind='number',required_evidence=['new'])})[0]
        self.assertEqual(r['answers'],{});self.assertEqual(r['stopped'],{0:'response_after_wall_limit'})
        self.assertEqual(r['trace'][-1]['event'],'response_after_wall_limit')
        self.assertEqual(r['trace'][-1]['response']['raw']['id'],'late-response')
        self.assertEqual(ledger.used[0]['tools'],0);self.assertEqual(env.read[0],set())
        self.assertEqual(summary['responses_with_cost'],1);self.assertEqual(summary['requests_without_response'],0)
        self.assertAlmostEqual(summary['billed_usd_observed'],.00001)
        self.assertAlmostEqual(ledger.usd,.00014)


if __name__=='__main__':unittest.main()
