"""Kill/restart a real worker against an isolated DB; classifier response is a test stub."""
import argparse,asyncio,json,os,subprocess,sys,time
from pathlib import Path
from tempfile import TemporaryDirectory
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
sys.path.insert(0,str(ROOT/'backend/tests'))
from app import db,vision_jobs as jobs,equipment,state_machine as sm
from acceptance_support import write_report
from sqlalchemy import create_engine

RESULT={'normal':False,'reading':'1.20 MPa','anomaly':'压力超标','error':'','raw':{'checks':[{'code':'pressure_reading','name':'压力','status':'abnormal','value':1.2,'unit':'MPa','evidence':'1.20 MPa'}]}}

def stop(child):
 if child.poll() is not None:return
 if os.name=='nt':subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],capture_output=True,check=False)
 else:child.kill()
 child.wait(timeout=10)

def child_main(args):
 db.engine.dispose();db.engine=create_engine(db.engine.url.set(database=args.database),pool_pre_ping=True)
 jobs.LEASE_SECONDS=2;jobs.HEARTBEAT_SECONDS=.3
 def detector(*a,**k):
  Path(args.marker).write_text('entered',encoding='utf-8')
  if args.block:
   while True:time.sleep(.2)
  return RESULT
 jobs.vlm.detect=detector
 asyncio.run(jobs.worker())

def wait_until(test,timeout=15):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  value=test()
  if value:return value
  time.sleep(.1)
 raise TimeoutError('acceptance condition timed out')

def main():
 import test_reliability as reliability
 reliability.ReliabilityTests.setUpClass()
 children=[]
 try:
  equipment.seed_catalog();sm.set_publisher(lambda *a:None)
  point=equipment.expand_targets([{'equipment_id':'AIR-001'}])[0]
  no=sm.create_task('robot1','manual',5,'recovery-acceptance',[point],{'verdict':'feasible'})
  with db.transaction() as c:jobs.enqueue(c,no,1,point['place'],point['check_item'],'/api/results/img/test.jpg',point['inspection'])
  with TemporaryDirectory() as tmp:
   marker=str(Path(tmp)/'entered')
   base=[sys.executable,'-X','utf8',str(Path(__file__).resolve()),'--child','--database',reliability.ReliabilityTests.database,'--marker',marker]
   first=subprocess.Popen(base+['--block'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);children.append(first)
   wait_until(lambda:Path(marker).exists())
   old=db.query_one('SELECT * FROM vision_job');stop(first)
   assert old['status']=='processing'
   assert not db.query('SELECT * FROM inspection_result')
   second=subprocess.Popen(base,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);children.append(second)
   final=wait_until(lambda:(lambda r:r if r['status']=='succeeded' else None)(db.query_one('SELECT * FROM vision_job')))
   assert final['attempts']==2
   stale={**old,'snapshot':equipment.decode(old['snapshot'])}
   assert jobs.finish(stale,{**RESULT,'normal':True}) is False
   assert len(db.query('SELECT * FROM inspection_result'))==1
   assert len(db.query('SELECT * FROM inspection_alert'))==1
   assert db.query_one('SELECT occurrences FROM inspection_alert')['occurrences']==1
   stop(second)
   report={'scenario':'forced-process-kill-and-restart','isolated_database':True,'classifier':'deterministic stub; not a visual accuracy test','lease_seconds_in_test':2,'production_lease_seconds':60,'job_status':final['status'],'attempts':final['attempts'],'old_worker_fenced':True,'result_rows':1,'alert_rows':1,'alert_occurrences':1,'passed':True}
   write_report('vision-recovery-acceptance.json',report)
   print(json.dumps(report,ensure_ascii=False))
 finally:
  for child in children:stop(child)
  reliability.ReliabilityTests.tearDownClass()

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--child',action='store_true');p.add_argument('--block',action='store_true');p.add_argument('--database');p.add_argument('--marker');args=p.parse_args()
 if args.child:child_main(args)
 else:main()
