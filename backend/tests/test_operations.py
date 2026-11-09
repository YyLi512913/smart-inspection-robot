import json,logging,os,sys,unittest,importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch,Mock
from app import operations as ops,config,db,state_machine as sm
from app.logging_setup import JsonFormatter
import test_reliability as reliability

class OperationsTests(unittest.TestCase):
 def setUp(self):ops.configure(lambda:True)
 def healthy(self):
  for key in ops.EXPECTED:ops.beat(key)
 def test_missing_and_stale_worker_are_unhealthy(self):
  self.assertTrue(all(r['status']=='down' for r in ops.worker_status().values()))
  self.healthy();self.assertTrue(all(r['status']=='ok' for r in ops.worker_status().values()))
  with patch('app.operations.time.monotonic',return_value=ops.time.monotonic()+100):
   self.assertTrue(all(r['status']=='down' for r in ops.worker_status().values()))
 def test_failed_iteration_not_marked_healthy(self):
  self.healthy();ops.beat('delivery',False)
  self.assertEqual(ops.worker_status()['delivery']['status'],'down')
 def test_dependency_failure_means_not_ready(self):
  self.healthy()
  with patch.object(ops,'database_probe',return_value=({'status':'down'},{})),patch.object(ops,'redis_probe',return_value=({'status':'ok'},[])),patch.object(ops,'storage_probe',return_value={'status':'ok'}):
   self.assertFalse(ops.snapshot(force=True)['ready'])
 def test_robot_offline_degrades_without_taking_api_offline(self):
  self.healthy()
  with patch.object(ops,'database_probe',return_value=({'status':'ok'},{})),patch.object(ops,'redis_probe',return_value=({'status':'ok'},[{'robot_id':'r','online':False}])),patch.object(ops,'storage_probe',return_value={'status':'ok'}):
   result=ops.snapshot(force=True)
  self.assertTrue(result['ready']);self.assertEqual(result['status'],'degraded')
 def test_probe_never_calls_model_api(self):
  self.healthy()
  with patch.object(ops,'database_probe',return_value=({'status':'ok'},{})),patch.object(ops,'redis_probe',return_value=({'status':'ok'},[])),patch.object(ops,'storage_probe',return_value={'status':'ok'}),patch('app.vlm.detect') as detector:
   self.assertEqual(ops.snapshot(force=True)['model_probe'],'not_called')
  detector.assert_not_called()
 def test_storage_check_uses_writable_tempfile_and_cleans_it(self):
  with TemporaryDirectory() as tmp,patch.object(config,'RESULTS_DIR',Path(tmp)):
   self.assertIn(ops.storage_probe()['status'],{'ok','degraded'})
   self.assertEqual(list(Path(tmp).iterdir()),[])
   with patch('app.operations.tempfile.TemporaryFile',side_effect=PermissionError('private path')):
    result=ops.storage_probe();self.assertEqual(result['status'],'down');self.assertNotIn('private path',str(result))
 def test_database_failure_does_not_expose_connection_details(self):
  engine=Mock();engine.connect.side_effect=RuntimeError('secret-password')
  with patch.object(ops,'create_engine',return_value=engine):result,_=ops.database_probe()
  self.assertEqual(result['status'],'down');self.assertNotIn('secret',str(result));engine.dispose.assert_called_once()
 def test_json_logs_redact_secret_and_include_request_id(self):
  with patch.object(config,'AGENT_API_KEY','test-private-key'):
   record=logging.LogRecord('test',logging.ERROR,'',1,'upstream test-private-key '+config.MYSQL_URL,(),None)
   record.request_id='request-123';result=JsonFormatter().format(record)
  self.assertNotIn('test-private-key',result);self.assertNotIn(config.MYSQL_URL,result)
  self.assertEqual(json.loads(result)['request_id'],'request-123')

class OperationsDatabaseTests(unittest.TestCase):
 setUpClass=classmethod(reliability.ReliabilityTests.setUpClass.__func__)
 tearDownClass=classmethod(reliability.ReliabilityTests.tearDownClass.__func__)
 def setUp(self):reliability.ReliabilityTests.setUp(self)
 def test_queue_counts_are_database_backed(self):
  sm.create_task('robot1','manual',5,'test',self.points,self.verdict)
  result,counts=ops.database_probe()
  self.assertEqual(result['status'],'ok');self.assertEqual(counts['commands']['pending'],1)
  self.assertEqual(counts['tasks']['DISPATCHED'],1)

class LifecycleTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  spec=importlib.util.spec_from_file_location('local_manage',Path(__file__).resolve().parents[2]/'scripts/manage.py')
  cls.manage=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.manage)
 def test_identity_matches_current_process(self):
  identity=self.manage.identity(os.getpid());self.assertIsNotNone(identity)
  self.assertTrue(self.manage.owned({'pid':os.getpid(),'identity':identity}))
 def test_reused_pid_is_never_treated_as_owned(self):
  self.assertFalse(self.manage.owned({'pid':os.getpid(),'identity':{'created':'wrong','exe':'wrong'}}))
 def test_stop_refuses_unverified_process(self):
  with patch.object(self.manage,'read_state',return_value={'pid':os.getpid(),'identity':{'created':'wrong','exe':'wrong'}}),patch.object(self.manage,'atomic') as writer:
   with self.assertRaises(RuntimeError):self.manage.stop(18080)
   writer.assert_not_called()
 def test_acceptance_cannot_reuse_existing_managed_service(self):
  with patch.object(self.manage,'read_state',return_value={'pid':os.getpid(),'identity':self.manage.identity(os.getpid())}),patch.object(self.manage.subprocess,'Popen') as spawn:
   with self.assertRaises(RuntimeError):self.manage.up(18080,require_new=True)
   spawn.assert_not_called()
 def test_busy_unknown_port_is_not_replaced(self):
  with patch.object(self.manage,'read_state',return_value=None),patch.object(self.manage,'open_port',return_value=True),patch.object(self.manage,'info',return_value={'running':False}),patch.object(self.manage.subprocess,'Popen') as spawn:
   with self.assertRaises(RuntimeError):self.manage.up(18080)
   spawn.assert_not_called()

if __name__=='__main__':unittest.main()
