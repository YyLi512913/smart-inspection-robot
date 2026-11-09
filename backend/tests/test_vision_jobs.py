"""Real MySQL transaction/lease tests; VLM calls stubbed, production data untouched."""
import asyncio
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4
from app import db,equipment,alerts,vision_jobs as jobs,state_machine as sm
import test_reliability as reliability

class VisionJobTests(unittest.TestCase):
    setUpClass=classmethod(reliability.ReliabilityTests.setUpClass.__func__)
    tearDownClass=classmethod(reliability.ReliabilityTests.tearDownClass.__func__)
    create=reliability.ReliabilityTests.create
    command=reliability.ReliabilityTests.command
    event=reliability.ReliabilityTests.event
    def setUp(self):
        reliability.ReliabilityTests.setUp(self)
        for table in ('alert_event','alert_recheck','alert_result','alert_slot','inspection_alert'):
            db.execute('DELETE FROM '+table)
        equipment.seed_catalog()
        self.point=equipment.expand_targets([{'equipment_id':'AIR-001'}])[0]
        self.no=self.create(points=[self.point])
        self.good={'normal':True,'reading':'0.45 MPa','anomaly':'','error':'','raw':{'checks':[{'code':'pressure_reading','name':'压力','status':'normal','value':.45,'unit':'MPa','evidence':'可读'}]}}
        self.bad={**self.good,'normal':False,'anomaly':'压力超标','raw':{'checks':[{'code':'pressure_reading','name':'压力','status':'abnormal','value':1.2,'unit':'MPa','evidence':'超标'}]}}
        self.error={'normal':None,'reading':'','anomaly':'','raw':None,'error':'gateway unavailable'}
    def enqueue(self):
        with db.transaction() as conn:
            jobs.enqueue(conn,self.no,1,self.point['place'],self.point['check_item'],'/api/results/img/test.jpg',self.point['inspection'])
    def row(self):return db.query_one('SELECT * FROM vision_job WHERE task_no=:t',{'t':self.no})
    def due(self):db.execute("UPDATE vision_job SET available_at=NOW(3)-INTERVAL 1 SECOND")
    def expire(self):db.execute("UPDATE vision_job SET lease_until=NOW(3)-INTERVAL 1 SECOND")
    def photo(self):
        self.event(self.no,'accepted');self.event(self.no,'navigating',1);self.event(self.no,'point_arrived',1)
        payload={'event_id':uuid4().hex,'command_id':self.command(self.no)['command_id'],'task_id':self.no,'point_seq':1,'place':self.point['place'],'image_url':'/api/results/img/test.jpg'}
        return payload
    def test_photo_receipt_atomically_enqueues_and_deduplicates(self):
        p=self.photo();sm.handle_result('robot1',p);sm.handle_result('robot1',p)
        self.assertEqual(len(db.query('SELECT * FROM vision_job')),1)
        self.assertEqual(self.row()['attempts'],0)
        self.assertEqual(equipment.decode(self.row()['snapshot'])['equipment_id'],'AIR-001')
    def test_enqueue_failure_rolls_back_photo_audit_and_inbox(self):
        p=self.photo()
        with patch('app.vision_jobs.enqueue',side_effect=RuntimeError('database fault')):
            with self.assertRaises(RuntimeError):sm.handle_result('robot1',p)
        self.assertEqual(db.query("SELECT * FROM task_event WHERE event='result_received'"),[])
        self.assertIsNone(db.query_one('SELECT * FROM message_inbox WHERE event_id=:e',{'e':p['event_id']}))
        sm.handle_result('robot1',p)
        self.assertIsNotNone(self.row())
    def test_parallel_claims_do_not_share_a_lease(self):
        self.enqueue()
        with ThreadPoolExecutor(max_workers=2) as pool:claimed=list(pool.map(lambda _:jobs.claim(),range(2)))
        self.assertEqual(sum(j is not None for j in claimed),1)
    def test_pending_job_survives_new_worker(self):
        self.enqueue();job=jobs.claim();self.assertEqual(job['task_no'],self.no)
        self.assertTrue(jobs.finish(job,self.good));self.assertEqual(self.row()['status'],'succeeded')
    def test_expired_lease_recovered_and_old_worker_is_fenced(self):
        self.enqueue();old=jobs.claim();self.expire();new=jobs.claim()
        self.assertNotEqual(old['lease_token'],new['lease_token'])
        self.assertFalse(jobs.finish(old,self.bad));self.assertEqual(db.query('SELECT * FROM inspection_result'),[])
        self.assertTrue(jobs.finish(new,self.good));self.assertEqual(alerts.list_alerts(),[])
    def test_successful_renew_preserves_ownership(self):
        self.enqueue();job=jobs.claim();self.assertTrue(jobs.renew(job))
        self.assertEqual(self.row()['lease_token'],job['lease_token'])
        self.assertTrue(jobs.finish(job,self.good))
    def test_result_api_exposes_retry_without_final_failure(self):
        sm.handle_result('robot1',self.photo());job=jobs.claim();jobs.finish(job,self.error)
        from app.results_api import list_results
        result=list_results(task_no=self.no)[0]
        self.assertEqual(result['analysis_status'],'retrying')
        self.assertEqual(result['analysis_attempts'],1)
        self.assertEqual(result['error'],'');self.assertIsNone(result['alert_id'])
    def test_missing_local_image_has_explicit_error(self):
        with patch('app.vlm.urllib.request.urlopen') as http:
            result=jobs.vlm.detect('/api/results/img/missing-'+uuid4().hex+'.jpg','设备','外观')
        http.assert_not_called();self.assertIn('图片不存在',result['error'])
    def test_expired_owner_cannot_renew_or_finish(self):
        self.enqueue();job=jobs.claim();self.expire()
        self.assertFalse(jobs.renew(job));self.assertFalse(jobs.finish(job,self.good))
    def test_release_invalidates_old_token(self):
        self.enqueue();old=jobs.claim();jobs.release(old);new=jobs.claim();jobs.release(old)
        self.assertEqual(self.row()['lease_token'],new['lease_token'])
        self.assertFalse(jobs.finish(old,self.bad));self.assertTrue(jobs.finish(new,self.good))
    def test_retry_backoff_does_not_publish_partial_failure(self):
        self.enqueue();job=jobs.claim();jobs.finish(job,self.error)
        self.assertEqual(self.row()['status'],'pending');self.assertIsNone(jobs.claim())
        self.assertEqual(db.query('SELECT * FROM inspection_result'),[]);self.assertEqual(alerts.list_alerts(),[])
        self.due();job=jobs.claim();jobs.finish(job,self.good)
        self.assertEqual(self.row()['attempts'],2);self.assertEqual(self.row()['status'],'succeeded')
    def test_exhaustion_creates_one_failure_and_review_alert(self):
        self.enqueue()
        for _ in range(3):
            self.due();job=jobs.claim();jobs.finish(job,self.error)
        self.assertEqual(self.row()['status'],'failed');self.assertIsNone(jobs.claim())
        self.assertEqual(alerts.list_alerts()[0]['kind'],'review')
        self.assertFalse(jobs.finish(job,self.bad));self.assertEqual(alerts.list_alerts()[0]['occurrences'],1)
    def test_three_crashes_exhaust_budget_without_fourth_model_call(self):
        self.enqueue()
        for _ in range(3):jobs.claim();self.expire()
        job=jobs.claim();self.assertTrue(job['exhausted']);self.assertEqual(job['attempts'],3)
        with patch('app.vlm.detect') as detector:result=jobs.detect(job)
        detector.assert_not_called();jobs.finish(job,result);self.assertEqual(self.row()['status'],'failed')
    def test_result_alert_and_job_status_roll_back_together(self):
        self.enqueue();job=jobs.claim()
        with patch('app.alerts.ingest',side_effect=RuntimeError('commit interrupted')):
            with self.assertRaises(RuntimeError):jobs.finish(job,self.bad)
        self.assertEqual(self.row()['status'],'processing');self.assertEqual(db.query('SELECT * FROM inspection_result'),[])
        self.assertEqual(alerts.list_alerts(),[])
        jobs.finish(job,self.bad);self.assertEqual(self.row()['status'],'succeeded')
        self.assertEqual(alerts.list_alerts()[0]['occurrences'],1)
        self.assertFalse(jobs.finish(job,self.good))
    def test_upgrade_recovers_missing_analysis_not_existing_conclusions(self):
        p=self.photo();sm.handle_result('robot1',p);db.execute('DELETE FROM vision_job')
        jobs.recover_backlog();jobs.recover_backlog();self.assertEqual(len(db.query('SELECT * FROM vision_job')),1)
        jobs.finish(jobs.claim(),self.good);db.execute('DELETE FROM vision_job');jobs.recover_backlog()
        self.assertEqual(db.query('SELECT * FROM vision_job'),[])
    def test_frozen_rules_are_used_after_catalog_change(self):
        self.enqueue();job=jobs.claim();db.execute("UPDATE observation_check SET definition=JSON_SET(definition,'$.max',10) WHERE observation_id='obs_air001_gauge'")
        with patch('app.vlm.detect',return_value=self.good) as detector:jobs.detect(job)
        self.assertEqual(detector.call_args.kwargs['checks'][0]['max'],.8)
    def test_worker_shutdown_releases_claim_for_restart(self):
        import threading
        self.enqueue();started=threading.Event();proceed=threading.Event()
        def slow(job):started.set();proceed.wait(3);return self.good
        async def check():
            with patch('app.vision_jobs.detect',side_effect=slow):
                task=asyncio.create_task(jobs.worker())
                for _ in range(100):
                    if started.is_set():break
                    await asyncio.sleep(.01)
                self.assertTrue(started.is_set());task.cancel()
                try:await task
                except asyncio.CancelledError:pass
                proceed.set()
                self.assertEqual(self.row()['status'],'pending')
                self.assertEqual(db.query('SELECT * FROM inspection_result'),[])
        asyncio.run(check())

if __name__=='__main__':unittest.main()
