"""Exercise real managed start/stop and degraded readiness with an isolated DB."""
import json,os,subprocess,sys,time,urllib.request,urllib.error,tempfile,socket
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'));sys.path.insert(0,str(ROOT/'backend/tests'))
import test_reliability as reliability
from app import db
from acceptance_support import write_report

PORT=None

def fetch(path):
 try:
  with urllib.request.urlopen(f'http://127.0.0.1:{PORT}'+path,timeout=10) as r:return r.status,r.read(),dict(r.headers)
 except urllib.error.HTTPError as e:return e.code,e.read(),dict(e.headers)

def main():
 global PORT
 with socket.socket() as reservation:
  reservation.bind(('127.0.0.1',0));PORT=reservation.getsockname()[1]
 started_owned=False
 reliability.ReliabilityTests.setUpClass()
 env=dict(os.environ)
 env.update(MYSQL_URL=db.engine.url.render_as_string(hide_password=False),REDIS_URL='redis://127.0.0.1:65533/0',MQTT_PORT='65534',MQTT_CLIENT_ID='operations-isolated-acceptance',AGENT_API_KEY='')
 command=[sys.executable,'-X','utf8',str(ROOT/'scripts/manage.py')]
 try:
  with tempfile.TemporaryDirectory() as tmp:
   env.update(LOG_DIR=str(Path(tmp)/'logs'),RESULTS_DIR=str(Path(tmp)/'results'))
   started=subprocess.run(command+['up','--port',str(PORT),'--require-new'],env=env,capture_output=True,text=True,timeout=35)
   if started.returncode:raise RuntimeError(started.stderr)
   state=json.loads(started.stdout)
   assert state['managed'] and state['running']
   started_owned=True
   assert fetch('/healthz')[0]==200
   ready_code,ready,_=fetch('/readyz');assert ready_code==503 and json.loads(ready)=={'ready':False}
   assert fetch('/login')[0]==200
   assert fetch('/factory_map.json')[0]==200
   assert fetch('/api/ops/status')[0]==401
   _,_,headers=fetch('/healthz');assert headers.get('x-request-id') or headers.get('X-Request-ID')
   again=subprocess.run(command+['up','--port',str(PORT)],env=env,capture_output=True,text=True,timeout=15)
   assert json.loads(again.stdout)['pid']==state['pid']
   stopped=subprocess.run(command+['stop','--port',str(PORT)],env=env,capture_output=True,text=True,timeout=15)
   assert stopped.returncode==0,stopped.stderr+stopped.stdout
   assert not (ROOT/'.runtime'/f'service-{PORT}.json').exists()
   logs=(Path(tmp)/'logs/service.jsonl').read_text(encoding='utf-8').splitlines()
   parsed=[json.loads(line) for line in logs]
   assert any(row.get('request_id') for row in parsed)
   report={'isolated_database':True,'managed_start':True,'idempotent_start':True,'graceful_stop':True,'static_frontend':True,'anonymous_ops_denied':True,'dependency_faults':{'redis':'isolated unreachable port','mqtt':'isolated unreachable port'},'readiness_http':ready_code,'liveness_http':200,'json_logs_with_request_id':True,'passed':True}
   write_report('operations-acceptance.json',report)
   print(json.dumps(report,ensure_ascii=False))
 finally:
  if started_owned:
   subprocess.run(command+['stop','--port',str(PORT)],env=env,capture_output=True,timeout=15)
   marker=ROOT/'.runtime'/f'service-{PORT}.json'
   if marker.exists():raise RuntimeError('Isolated instance still present; DB retained for cleanup')
  reliability.ReliabilityTests.tearDownClass()

if __name__=='__main__':main()
