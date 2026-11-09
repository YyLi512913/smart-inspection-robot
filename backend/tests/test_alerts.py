import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from fastapi import HTTPException
from app import db, alerts, equipment, state_machine as sm
import test_reliability as reliability

class AlertTests(unittest.TestCase):
    setUpClass=classmethod(reliability.ReliabilityTests.setUpClass.__func__)
    tearDownClass=classmethod(reliability.ReliabilityTests.tearDownClass.__func__)
    create=reliability.ReliabilityTests.create
    def setUp(self):
        reliability.ReliabilityTests.setUp(self)
        for table in ('alert_event','alert_recheck','alert_result','alert_slot','inspection_alert'):
            db.execute('DELETE FROM '+table)
        equipment.seed_catalog()
        self.point=equipment.expand_targets([{'equipment_id':'AIR-001'}])[0]
        self.snapshot=self.point['inspection']
        self.bad={'normal':False,'anomaly':'压力超标','raw':{'checks':[{'code':'pressure_reading','status':'abnormal'}]}}
        self.good={'normal':True,'raw':{'checks':[{'code':'pressure_reading','status':'normal'}]}}
    def ingest(self,no,result=None):
        with db.transaction() as conn:alerts.ingest(conn,no,1,self.snapshot,result or self.bad)
    def opened(self):
        no=self.create(points=[self.point]);self.ingest(no)
        db.execute("UPDATE task SET status='DONE' WHERE task_no=:t",{'t':no})
        db.execute("UPDATE robot_execution SET task_no=NULL WHERE robot_id='robot1'")
        return alerts.list_alerts()[0]['id']
    def action(self,aid,action,note='已核实并处理'):
        return alerts.act(aid,alerts.Action(action=action,note=note))
    def start_recheck(self,aid):
        with patch('app.tasks_api._r.get',return_value=json.dumps({'pos':{'x':2,'y':-3},'battery':100})):
            return alerts.recheck(aid,alerts.Recheck())['task_no']
    def prepared(self):
        aid=self.opened();self.action(aid,'confirm');self.action(aid,'resolve')
        return aid
    def test_duplicate_result_and_persistent_abnormal_merge(self):
        aid=self.opened();self.ingest('second');self.ingest('second')
        row=alerts.detail(aid)
        self.assertEqual(row['occurrences'],2)
        self.assertEqual(len(alerts.list_alerts()),1)
    def test_parallel_results_share_one_active_alert(self):
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(self.ingest,['a','b']))
        self.assertEqual(len(alerts.list_alerts()),1)
        self.assertEqual(alerts.list_alerts()[0]['occurrences'],2)
    def test_unreadable_and_detection_failure_require_review(self):
        self.ingest('unknown',{'normal':None,'error':'读不清','raw':{}})
        self.assertEqual(alerts.list_alerts()[0]['kind'],'review')
    def test_routine_normal_never_closes_alert(self):
        aid=self.opened();self.ingest('normal',self.good)
        self.assertEqual(alerts.detail(aid)['status'],'open')
    def test_notes_required_and_resolve_requires_confirmation(self):
        aid=self.opened()
        for action,note in [('resolve','处理好了'),('confirm','   ')]:
            with self.assertRaises(HTTPException):self.action(aid,action,note)
        self.assertEqual(alerts.detail(aid)['status'],'open')
    def test_false_positive_keeps_history_and_next_detection_opens_new(self):
        aid=self.opened();self.action(aid,'false_positive','反光误判')
        self.ingest('again')
        self.assertEqual(len(alerts.list_alerts()),2)
        with self.assertRaises(HTTPException):self.action(aid,'note')
    def test_recheck_transaction_idempotent_and_frozen_rules(self):
        aid=self.prepared();no=self.start_recheck(aid)
        self.assertEqual(self.start_recheck(aid),no)
        self.assertEqual(len(db.query('SELECT * FROM alert_recheck')),1)
        snapshot=db.query_one('SELECT snapshot FROM task_point_inspection pi JOIN task t ON t.id=pi.task_id WHERE t.task_no=:t',{'t':no})['snapshot']
        self.assertEqual(equipment.decode(snapshot)['checks'],self.snapshot['checks'])
        db.execute("UPDATE task SET status='DONE' WHERE task_no=:t",{'t':no})
        self.ingest(no,self.good)
        self.assertEqual(alerts.detail(aid)['status'],'closed')
    def test_normal_image_waits_for_successful_task_completion(self):
        aid=self.prepared();no=self.start_recheck(aid);self.ingest(no,self.good)
        self.assertEqual(alerts.detail(aid)['status'],'rechecking')
        self.assertEqual(alerts.detail(aid)['rechecks'][0]['outcome'],'awaiting_task')
        db.execute("UPDATE task SET status='DONE' WHERE task_no=:t",{'t':no})
        alerts.settle_rechecks()
        self.assertEqual(alerts.detail(aid)['status'],'closed')

    def test_failed_execution_without_image_returns_to_processing(self):
        aid=self.prepared();no=self.start_recheck(aid)
        db.execute("UPDATE task SET status='FAILED' WHERE task_no=:t",{'t':no})
        alerts.settle_rechecks()
        self.assertEqual(alerts.detail(aid)['status'],'confirmed')

    def test_recheck_abnormal_remains_open_and_records_attempt(self):
        aid=self.prepared();no=self.start_recheck(aid);self.ingest(no)
        a=alerts.detail(aid)
        self.assertEqual(a['status'],'confirmed');self.assertEqual(a['rechecks'][0]['outcome'],'failed')
    def test_incomplete_checks_cannot_close(self):
        aid=self.prepared();no=self.start_recheck(aid)
        self.ingest(no,{'normal':True,'raw':{'checks':[]}})
        self.assertEqual(alerts.detail(aid)['status'],'confirmed')
    def test_newer_anomaly_blocks_old_normal_recheck(self):
        aid=self.prepared();no=self.start_recheck(aid)
        self.ingest('newer');self.ingest(no,self.good)
        self.assertEqual(alerts.detail(aid)['status'],'confirmed')
    def test_offline_recheck_rolls_back_everything(self):
        aid=self.prepared()
        with patch('app.tasks_api._r.get',return_value=None):
            with self.assertRaises(HTTPException):alerts.recheck(aid,alerts.Recheck())
        self.assertEqual(alerts.detail(aid)['status'],'awaiting_recheck')
        self.assertEqual(db.query('SELECT * FROM alert_recheck'),[])
    def test_busy_robot_does_not_leave_orphan_recheck(self):
        aid=self.prepared();self.create(robot='robot1')
        with self.assertRaises(sm.TaskConflict):self.start_recheck(aid)
        self.assertEqual(db.query('SELECT * FROM alert_recheck'),[])
        self.assertEqual(alerts.detail(aid)['status'],'awaiting_recheck')
    def test_cancelled_recheck_cannot_close_and_can_retry(self):
        aid=self.prepared();no=self.start_recheck(aid)
        db.execute("UPDATE task SET status='CANCELED' WHERE task_no=:t",{'t':no})
        self.ingest(no,self.good)
        self.assertEqual(alerts.detail(aid)['status'],'confirmed')
    def test_vlm_worker_persists_result_and_alert_together(self):
        no=self.create(points=[self.point])
        result={**self.bad,'reading':'1.2 MPa','error':''}
        with patch('app.vlm.detect',return_value=result):
            sm._vlm_job(no,1,self.point['place'],self.point['check_item'],'/api/results/img/test.jpg',self.snapshot)
        self.assertEqual(db.query_one('SELECT normal FROM inspection_result')['normal'],0)
        self.assertEqual(len(alerts.list_alerts()),1)
        with patch('app.vlm.detect',return_value={**self.good,'reading':'0.45 MPa','error':'','anomaly':''}):
            sm._vlm_job(no,1,self.point['place'],self.point['check_item'],'/api/results/img/test.jpg',self.snapshot)
        self.assertEqual(db.query_one('SELECT normal FROM inspection_result')['normal'],0)

    def test_assessment_and_alert_rollback_together(self):
        with self.assertRaises(Exception):
            alerts.save_assessment('rollback',1,self.snapshot,self.bad,'INSERT INTO nonexistent_table(x) VALUES(1)',{})
        self.assertEqual(alerts.list_alerts(),[])

if __name__=='__main__':unittest.main()
