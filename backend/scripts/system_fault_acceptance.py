"""Real HTTP/MQTT/MySQL/SQLite drill with a protocol robot and deterministic vision.
Does not move a robot, call a paid model, or touch the production broker/database.
"""
import json,os,queue,shutil,socket,subprocess,sys,tempfile,threading,time
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch
import uvicorn
import paho.mqtt.client as mqtt
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'));sys.path.insert(0,str(ROOT/'backend/tests'))
sys.path.insert(0,str(ROOT/'robot_ws/src/inspection_robot'))
import test_reliability as reliability
from test_auth import Client
from app import config,db,delivery,state_machine as sm,vision_jobs as jobs,alerts
from app.main import app
from app.mqtt_consumer import MqttConsumer
from inspection_robot.durable_store import DurableStore
from acceptance_support import write_report

class Telemetry:
 def __init__(self):self.values={};self.lock=threading.Lock()
 def set(self,key,value,ex=5):
  with self.lock:self.values[key]=(value,time.monotonic()+ex)
 def get(self,key):
  with self.lock:
   value=self.values.get(key)
   return value[0] if value and value[1]>time.monotonic() else None

class Broker:
 def __init__(self):self.process=None;self.meta=None;self.folder=None
 def start(self):
  with socket.socket() as reservation:reservation.bind(('127.0.0.1',0));self.port=reservation.getsockname()[1]
  if os.name=='nt':
   token=uuid4().hex
   code="""import json,subprocess,sys
from pathlib import Path
port,token=sys.argv[1:]
cfg=Path('/tmp')/('inspection-acceptance-'+token+'.conf')
log=cfg.with_suffix('.log')
cfg.write_text(chr(10).join(['listener '+port+' 127.0.0.1','allow_anonymous true','persistence false','']))
with log.open('wb') as output:p=subprocess.Popen(['mosquitto','-c',str(cfg)],stdout=output,stderr=output,start_new_session=True)
print(json.dumps({'pid':p.pid,'config':str(cfg),'log':str(log)}))"""
   result=subprocess.run(['wsl.exe','-d','Ubuntu-24.04','python3','-c',code,str(self.port),token],capture_output=True,text=True,timeout=15)
   if result.returncode:raise RuntimeError('Isolated WSL Mosquitto startup failed')
   self.meta=json.loads(result.stdout)
  else:
   binary=shutil.which('mosquitto')
   if not binary:raise RuntimeError('Mosquitto is required for this drill')
   self.folder=tempfile.TemporaryDirectory();cfg=Path(self.folder.name)/'broker.conf'
   cfg.write_text(f'listener {self.port} 127.0.0.1\nallow_anonymous true\npersistence false\n')
   self.process=subprocess.Popen([binary,'-c',str(cfg)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  end=time.monotonic()+10
  while time.monotonic()<end:
   try:
    with socket.create_connection(('127.0.0.1',self.port),timeout=.3):return
   except OSError:time.sleep(.1)
  raise RuntimeError('Isolated broker did not become reachable')
 def close(self):
  if self.meta:
   code="""import json,os,signal,sys,time
from pathlib import Path
m=json.loads(sys.argv[1]);cfg=Path(m['config']);p=Path('/proc')/str(m['pid'])
assert cfg.parent==Path('/tmp') and cfg.name.startswith('inspection-acceptance-')
try:
 cmd=(p/'cmdline').read_bytes()
 assert b'mosquitto' in cmd and str(cfg).encode() in cmd
 os.kill(m['pid'],signal.SIGTERM)
except FileNotFoundError:pass
cfg.unlink(missing_ok=True);cfg.with_suffix('.log').unlink(missing_ok=True)"""
   subprocess.run(['wsl.exe','-d','Ubuntu-24.04','python3','-c',code,json.dumps(self.meta)],capture_output=True,timeout=10,check=True)
  if self.process:
   self.process.terminate();self.process.wait(timeout=10)
  if self.folder:self.folder.cleanup()

class ProtocolRobot:
 def __init__(self,port,path,rid):
  self.rid=rid;self.path=path;self.store=DurableStore(path);self.commands=queue.Queue();self.started=[];self.received=[];self.cancels=[];self.suppressed=[];self.pause_uplink=False
  self.client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id='protocol-'+uuid4().hex)
  def connected(c,u,f,rc,p):c.subscribe(f'robot/{rid}/task',qos=1);c.subscribe(f'robot/{rid}/ack',qos=1)
  def message(c,u,m):
   payload=json.loads(m.payload)
   if m.topic.endswith('/ack'):self.store.acknowledge(payload['event_id'])
   else:self.commands.put(payload)
  self.client.on_connect=connected;self.client.on_message=message
  self.client.connect('127.0.0.1',port,keepalive=10);self.client.loop_start()
 def pump(self):
  self.client.publish(f'robot/{self.rid}/status',json.dumps({'pos':{'x':2,'y':-3},'battery':100,'mode':'idle','current_task':None}),qos=0)
  while not self.commands.empty():
   payload=self.commands.get();self.received.append(payload)
   if payload['action']=='cancel':
    self.store.cancel(payload);self.cancels.append(payload['task_id']);self.event(payload,'cancel_confirmed')
   else:
    verdict=self.store.begin(payload)
    if verdict=='new':self.started.append(payload);self.event(payload,'accepted')
    elif verdict=='duplicate':self.store.replay(payload['command_id'])
    else:self.suppressed.append((payload['task_id'],verdict))
  item=self.store.next_message()
  if item and not self.pause_uplink:self.client.publish(f"robot/{self.rid}/{item['kind']}",item['payload'],qos=1)
 def event(self,cmd,event,seq=None):
  return self.store.emit({'task_id':cmd['task_id'],'command_id':cmd['command_id'],'event':event,'point_seq':seq},state='done' if event=='task_done' else None)
 def photo(self,cmd):
  point=cmd['points'][0]
  return self.store.enqueue('result',{'task_id':cmd['task_id'],'command_id':cmd['command_id'],'point_seq':point['seq'],'place':point['place'],'image_url':'/api/results/img/protocol-fixture.jpg','image_source':'protocol_fixture','inspection':{'equipment_id':'FORGED'}})
 def restart_journal(self):self.store=DurableStore(self.path)
 def close(self):self.client.disconnect();self.client.loop_stop()


def main():
 started=time.monotonic();broker=Broker();consumer=None;robot=None;server=None;thread=None;fixture=False;temporary=None
 checks={};rid='fault-'+uuid4().hex[:10];telemetry=Telemetry()
 def expect(value,message):
  if not value:raise AssertionError(message)
 def wait(test,timeout=15):
  end=time.monotonic()+timeout
  while time.monotonic()<end:
   delivery.flush_pending()
   if robot:robot.pump()
   if test():return
   time.sleep(.05)
  raise TimeoutError('Drill condition timed out')
 def status(no):return db.query_one('SELECT status FROM task WHERE task_no=:n',{'n':no})['status']
 def connect_backend():
  nonlocal consumer
  config.MQTT_PORT=broker.port;config.MQTT_CLIENT_ID='fault-backend-'+rid
  consumer=MqttConsumer();consumer.r=telemetry;consumer.start();delivery.set_publisher(consumer.publish)
  wait(consumer.c.is_connected)
 try:
  broker.start();reliability.ReliabilityTests.setUpClass();fixture=True
  from app import equipment,tasks_api
  equipment.seed_catalog();tasks_api._r=telemetry
  listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
  server=uvicorn.Server(uvicorn.Config(app,log_level='critical',lifespan='off'))
  thread=threading.Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True);thread.start()
  wait(lambda:server.started)
  admin=Client(f'http://127.0.0.1:{port}')
  expect(admin.request('/api/auth/setup',{'username':'drill_admin','display_name':'drill admin','password':'isolated-drill-password-123'})[0]==200,'admin setup')
  expect(admin.request('/api/auth/users',{'username':'drill_operator','display_name':'operator','password':'isolated-drill-password-123','role':'operator'})[0]==200,'operator setup')
  operator=Client(admin.base);expect(operator.request('/api/auth/login',{'username':'drill_operator','password':'isolated-drill-password-123'})[0]==200,'operator login')
  body={'robot_id':rid,'created_by':'FORGED','targets':[{'equipment_id':'AIR-001'}]}
  expect(operator.request('/api/tasks',body)[0]==409,'offline task must fail');expect(not db.query('SELECT * FROM task'),'offline task leaked')
  checks['offline_creation_blocked']=True
  temporary=tempfile.TemporaryDirectory()
  tmp=temporary.name
  robot=ProtocolRobot(broker.port,str(Path(tmp)/'robot.sqlite3'),rid);connect_backend()
  wait(lambda:telemetry.get(f'robot:{rid}:live') is not None)
  consumer.stop()
  code,result,_=operator.request('/api/tasks',body,headers={'Idempotency-Key':'drill-first'})
  expect(code==200,'create while transport down');no=result['task']['task_no']
  same=operator.request('/api/tasks',body,headers={'Idempotency-Key':'drill-first'})
  expect(same[1]['task']['task_no']==no,'HTTP replay duplicated task')
  cmd=db.query_one("SELECT * FROM mqtt_command WHERE task_no=:n AND action='patrol'",{'n':no});payload=json.loads(cmd['payload'])
  delivery.flush_pending();expect(not robot.started,'offline command executed');expect(cmd['status']=='pending','outbox lost')
  connect_backend();wait(lambda:status(no)=='ACCEPTED')
  consumer.publish(f'robot/{rid}/task',payload);wait(lambda:len(robot.received)>=2)
  expect(len(robot.started)==1,'duplicate patrol executed');checks['disconnect_outbox_and_duplicate_command']=True
  robot.event(payload,'navigating',1);wait(lambda:status(no)=='NAVIGATING')
  robot.event(payload,'point_arrived',1);wait(lambda:status(no)=='INSPECTING')
  wait(lambda:robot.store.next_message() is None)
  consumer.stop();photo=robot.photo(payload);robot.pump();time.sleep(.15)
  expect(robot.store.next_message() is not None,'PUBACK incorrectly removed spool')
  robot.restart_journal();expect(robot.store.next_message() is not None,'journal reopen lost result')
  connect_backend();wait(lambda:bool(db.query('SELECT * FROM vision_job')) and robot.store.next_message() is None)
  expect(len(db.query('SELECT * FROM vision_job'))==1,'photo replay duplicated job')
  robot.event(payload,'task_done');wait(lambda:status(no)=='DONE')
  expect(equipment.decode(db.query_one('SELECT snapshot FROM vision_job')['snapshot'])['equipment_id']=='AIR-001','forged snapshot accepted')
  checks['photo_spool_survives_disconnect_and_journal_reopen']=True
  job=jobs.claim();jobs.finish(job,{'normal':None,'raw':None,'error':'simulated upstream outage','reading':'','anomaly':''})
  expect(not db.query('SELECT * FROM inspection_result') and not alerts.list_alerts(),'intermediate failure became conclusion')
  db.execute('UPDATE vision_job SET available_at=NOW(3)-INTERVAL 1 SECOND')
  def classify(normal):
   job=jobs.claim();expect(job is not None,'missing analysis job')
   parsed=jobs.vlm._parse(json.dumps({'normal':True,'checks':[{'code':'pressure_reading','status':'normal','value':.45 if normal else 1.2,'unit':'MPa','evidence':'deterministic fixture'}]}),job['snapshot']['checks'])
   result={**parsed,'raw':{'checks':parsed['checks']},'error':''}
   expect(jobs.finish(job,result),'analysis commit rejected')
   expect(not jobs.finish(job,result),'duplicate analysis committed twice')
  classify(False);aid=alerts.list_alerts()[0]['id']
  expect(alerts.detail(aid)['occurrences']==1,'duplicate alert')
  checks['vision_retry_atomic_alert_and_numeric_rule']=True
  path='/api/alerts/'+str(aid)
  for action in ('confirm','resolve'):
   expect(operator.request(path+'/actions',{'action':action,'note':'isolated drill','actor':'FORGED'})[0]==200,'alert treatment failed')
  wait(lambda:telemetry.get(f'robot:{rid}:live') is not None)
  code,recheck,_=operator.request(path+'/recheck',{'robot_id':rid,'actor':'FORGED'})
  expect(code==200,'recheck failed');again=recheck['task_no'];wait(lambda:status(again)=='ACCEPTED')
  next_cmd=next(p for p in robot.started if p['task_id']==again)
  robot.event(next_cmd,'navigating',1);wait(lambda:status(again)=='NAVIGATING')
  robot.event(next_cmd,'point_arrived',1);wait(lambda:status(again)=='INSPECTING')
  robot.photo(next_cmd);wait(lambda:len(db.query('SELECT * FROM vision_job'))==2)
  classify(True);expect(alerts.detail(aid)['status']=='rechecking','image closed alert before task completion')
  robot.event(next_cmd,'task_done');wait(lambda:status(again)=='DONE');alerts.settle_rechecks()
  expect(alerts.detail(aid)['status']=='closed','recheck did not close alert')
  expect(db.query_one('SELECT created_by FROM task WHERE task_no=:n',{'n':again})['created_by']=='drill_operator','forged creator accepted')
  checks['authenticated_treatment_recheck_and_close']=True
  wait(lambda:robot.store.next_message() is None and telemetry.get(f'robot:{rid}:live') is not None)
  consumer.stop();code,third,_=operator.request('/api/tasks',body);expect(code==200,'third task creation')
  canceled=third['task']['task_no'];delayed=json.loads(db.query_one("SELECT payload FROM mqtt_command WHERE task_no=:n AND action='patrol'",{'n':canceled})['payload'])
  expect(operator.request('/api/tasks/'+canceled+'/cancel',{})[0]==200,'cancel failed')
  rejected=operator.request('/api/tasks',body);expect(rejected[0]==409 and '占用' in rejected[1]['detail'],'robot freed before cancel ACK')
  robot.pause_uplink=True;connect_backend();wait(lambda:canceled in robot.cancels)
  expect(db.query_one('SELECT task_no FROM robot_execution WHERE robot_id=:r',{'r':rid})['task_no']==canceled,'cancel receipt freed robot before confirmation')
  robot.restart_journal();robot.pause_uplink=False
  wait(lambda:db.query_one('SELECT task_no FROM robot_execution WHERE robot_id=:r',{'r':rid})['task_no'] is None)
  before=len(robot.started);consumer.publish(f'robot/{rid}/task',delayed)
  wait(lambda:any(n==canceled and verdict=='canceled' for n,verdict in robot.suppressed))
  expect(len(robot.started)==before,'late patrol revived canceled task')
  expect(not db.query("SELECT te.id FROM task_event te JOIN task t ON t.id=te.task_id WHERE t.task_no=:n AND te.event='accepted'",{'n':canceled}),'canceled task accepted')
  checks['cancel_confirmation_holds_ownership_and_blocks_late_patrol']=True
  expect(len(db.query('SELECT * FROM inspection_result'))==2,'unexpected result count')
  expect(len(alerts.list_alerts())==1 and alerts.list_alerts()[0]['occurrences']==1,'unexpected alert count')
  report={'scenario':'cross-module-fault-drill','passed':True,'checks':checks,'duration_seconds':round(time.monotonic()-started,2),'real_components':['HTTP authentication','MQTT broker and reconnect','MySQL transactions','SQLite journal'],
          'simulated_components':['robot protocol peer; no navigation or physical motion','deterministic vision fixture; no paid model'],
          'accelerated_test_clock':['retry available_at moved forward after first simulated model failure'],
          'results':2,'alerts':1,'closed_alerts':1,'live_database_or_broker_modified':False}
  write_report('system-fault-acceptance.json',report);print(json.dumps(report,ensure_ascii=False))
 finally:
  if consumer:consumer.stop()
  if robot:robot.close()
  if temporary:temporary.cleanup()
  delivery.set_publisher(None)
  if server:server.should_exit=True
  if thread:thread.join(10)
  broker.close()
  if fixture:
   if thread and thread.is_alive():raise RuntimeError('HTTP test server still running; isolated DB retained')
   reliability.ReliabilityTests.tearDownClass()

if __name__=='__main__':main()
