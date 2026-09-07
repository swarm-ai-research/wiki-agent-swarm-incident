import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess
from model_retrieval_eval import Limits, Ledger, BudgetError, Environment, Mock, action, run, blind, grade, chat_messages, openrouter_request
from run_model_eval_study import allocate

TASK={'id':'t','question':'Question','root':'root','family':'hidden','answer':'SECRET',
      'documents':{'root':{'title':'Index','text':'Read both','links':['old','new']},
                   'old':{'title':'Old','text':'1','links':[]},'new':{'title':'New','text':'2','links':[]}}}

class ModelEvalTests(unittest.TestCase):
    def test_limits_and_prices(self):
        for kw in ({'agents':0},{'agents':1.2},{'wall_seconds':float('nan')}):
            with self.assertRaises(ValueError):Limits(**kw)
        with self.assertRaises(ValueError):Ledger(Limits(),-1)
    def test_reservation_and_settlement(self):
        ledger=Ledger(Limits(max_output_tokens=100),.001,1,4)
        r=ledger.reserve(0,500)
        with self.assertRaises(BudgetError):ledger.reserve(1,500)
        ledger.settle(0,r,100,10)
        self.assertAlmostEqual(ledger.usd,.00014)
        self.assertEqual(ledger.used[0]['input_tokens'],100)
    def test_overflow_keeps_reservation(self):
        ledger=Ledger(Limits(max_output_tokens=100),1,1,4);r=ledger.reserve(0,500)
        with self.assertRaises(BudgetError):ledger.settle(0,r,501,1)
        self.assertAlmostEqual(ledger.usd,.0009)
    def test_all_board_actions_charged(self):
        ledger=Ledger(Limits(tools_per_agent=1));ledger.tool(0)
        with self.assertRaises(BudgetError):ledger.tool(0)
    def test_individual_call_caps(self):
        ledger=Ledger(Limits(calls_per_agent=1));ledger.reserve(0,1)
        with self.assertRaises(BudgetError):ledger.reserve(0,1)
        ledger.reserve(1,1)
    def test_stale_snapshot_loses_first_message(self):
        for mode,count in [('snapshot',1),('append_only',2)]:
            e=Environment(TASK,mode,2)
            for a in range(2):e.execute(a,action('board_read'))
            for a in range(2):e.execute(a,action('board_write',text=str(a),citations=['root']))
            self.assertEqual(len(e.board),count)
    def test_provenance_and_correction_receipt(self):
        e=Environment(TASK,'append_only',2)
        e.execute(0,action('read',source='root'))
        e.execute(0,action('board_write',citations=['new'],retracts=['old']))
        e.execute(1,action('board_read'))
        result=e.execute(1,action('final',answer='2',citations=['new','imaginary']))
        self.assertEqual(result['received_retractions'],['old'])
        self.assertEqual(result['undiscovered_citations'],['imaginary'])
        self.assertEqual(result['unread_citations'],['new','imaginary'])
    def test_source_isolation(self):
        e=Environment(TASK,'isolated',2)
        self.assertNotIn('answer',e.task);self.assertNotIn('family',e.task)
        self.assertIn('error',e.execute(0,action('read',source='new')))
        self.assertIn('error',e.execute(0,action('board_read')))
        self.assertIn('error',e.execute(0,action('board_write')))
    def test_mock_never_sees_key(self):
        with tempfile.TemporaryDirectory() as d:
            r=run(TASK,'snapshot',Mock(),Limits(),Ledger(Limits()),Path(d)/'r.json')
            self.assertEqual(len(r['answers']),2)
            self.assertNotIn('SECRET',json.dumps(r))
            self.assertFalse(r['stopped'])
    def test_failures_not_abstentions_and_no_retry(self):
        class Fail:
            model='failure'
            def respond(self,*args):raise TimeoutError()
        with tempfile.TemporaryDirectory() as d:
            ledger=Ledger(Limits(),1,1,4)
            r=run(TASK,'isolated',Fail(),ledger.limits,ledger,Path(d)/'r.json')
        self.assertEqual(r['answers'],{});self.assertEqual(sum(u['calls'] for u in r['usage']),1)
        self.assertGreater(r['accounted_usd'],0)
        self.assertEqual(blind(r,TASK,{'answer':'2','kind':'number'})[0],[])
    def test_blind_ids_distinguish_models_without_unblinding_rows(self):
        r={'backend':'one','mode':'snapshot','limits':{'agents':1},'answers':{0:{'answer':'2'}}}
        ref={'answer':'2','kind':'number'};rows,_=blind(r,TASK,ref)
        r['backend']='two';other,_=blind(r,TASK,ref)
        self.assertNotEqual(rows[0]['blind_id'],other[0]['blind_id'])
        self.assertNotIn('model',rows[0]);self.assertNotIn('mode',rows[0])
        self.assertEqual(grade(rows[0]),'correct')
        self.assertEqual(grade(dict(rows[0],answer='')),'not_attempted')
        self.assertEqual(grade(dict(rows[0],reference_answer=None)),'incorrect')
    def test_durable_allocations_cannot_repeat_or_exceed_twenty(self):
        j={'allocations':[]};allocate(j,[{'id':'first'}])
        with self.assertRaises(ValueError):allocate(j,[{'id':'first'}])
        with self.assertRaises(ValueError):allocate(j,[{'id':str(i)} for i in range(100)])
    def test_transport_hard_deadline_and_credentials_not_in_payload(self):
        with patch('model_retrieval_eval.subprocess.run',side_effect=subprocess.TimeoutExpired('worker',.01)) as request:
            with self.assertRaises(subprocess.TimeoutExpired):openrouter_request({'model':'test'},.01)
        kwargs=request.call_args.kwargs
        self.assertEqual(kwargs['timeout'],.01)
        self.assertNotIn('OPENROUTER_API_KEY',kwargs['input'])
        self.assertTrue(set(kwargs['env']) <= {'OPENROUTER_API_KEY','SSL_CERT_FILE','SSL_CERT_DIR'})

    def test_native_chat_tool_history(self):
        messages=[{'role':'system','content':'Instructions'}, {'role':'user','content':'Question'},
            {'role':'assistant','content':json.dumps(action('read',source='root'))},
            {'role':'user','content':json.dumps({'text':'Evidence'})}]
        wire=chat_messages(messages)
        self.assertEqual(wire[2]['tool_calls'][0]['function']['name'],'act')
        self.assertEqual(wire[3]['role'],'tool')
        self.assertEqual(wire[3]['tool_call_id'],wire[2]['tool_calls'][0]['id'])
        self.assertEqual(messages[3]['role'],'user')

    def test_family_split_and_evidence_integrity(self):
        root=Path(__file__).resolve().parents[1]/'data/model_eval'
        tasks=json.loads((root/'tasks.json').read_text())['tasks']
        refs={r['id']:r for r in json.loads((root/'references.json').read_text())['references']}
        dev={t['family'] for t in tasks if t['split']=='dev'}
        held={t['family'] for t in tasks if t['split']=='heldout'}
        self.assertFalse(dev&held)
        for t in tasks:
            self.assertNotIn('answer',t)
            self.assertTrue(set(refs[t['id']]['required_evidence'])<=set(t['documents']))
            for doc in t['documents'].values():self.assertTrue(set(doc['links'])<=set(t['documents']))

if __name__=='__main__':unittest.main()
