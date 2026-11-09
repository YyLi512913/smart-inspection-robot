"""HTTP permission tests against an isolated MySQL database and local ASGI server."""
import hashlib,json,socket,threading,time,unittest,urllib.request,urllib.error,http.cookiejar
from unittest.mock import patch,Mock
from uuid import uuid4
from app import db,auth,equipment,alerts,state_machine as sm
from app.main import app
import uvicorn
import test_reliability as reliability

class Client:
 def __init__(self,base):
  self.base=base;self.jar=http.cookiejar.CookieJar();self.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar));self.csrf=''
 def request(self,path,body=None,method=None,headers=None,csrf=True):
  h={'Content-Type':'application/json'}
  if csrf:h['X-CSRF-Token']=self.csrf
  h.update(headers or {})
  req=urllib.request.Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers=h,method=method or ('POST' if body is not None else 'GET'))
  try:r=self.opener.open(req,timeout=10)
  except urllib.error.HTTPError as e:r=e
  raw=r.read()
  try:data=json.loads(raw)
  except (ValueError,UnicodeDecodeError):data=None
  if r.status==200 and isinstance(data,dict) and 'csrf_token' in data:self.csrf=data['csrf_token']
  return r.status,data,r.headers

class AuthTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  reliability.ReliabilityTests.setUpClass.__func__(cls)
  sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen()
  cls.base='http://127.0.0.1:'+str(sock.getsockname()[1])
  cls.server=uvicorn.Server(uvicorn.Config(app,log_level='critical',lifespan='off'))
  cls.thread=threading.Thread(target=cls.server.run,kwargs={'sockets':[sock]},daemon=True);cls.thread.start()
  for _ in range(100):
   if cls.server.started:break
   time.sleep(.02)
  if not cls.server.started:raise RuntimeError('test HTTP server failed')
 @classmethod
 def tearDownClass(cls):
  cls.server.should_exit=True;cls.thread.join(10)
  reliability.ReliabilityTests.tearDownClass.__func__(cls)
 def setUp(self):
  reliability.ReliabilityTests.setUp(self)
  for table in ('agent_session_owner','auth_session','auth_event','auth_throttle','app_user','alert_event','alert_recheck','alert_result','alert_slot','inspection_alert'):
   db.execute('DELETE FROM '+table)
  equipment.seed_catalog()
  self.admin=Client(self.base)
  status,_,_=self.admin.request('/api/auth/setup',{'username':'admin','display_name':'验收管理员','password':'test-admin-password-123'})
  self.assertEqual(status,200)
 def user(self,role='operator',name='operator'):
  status,data,_=self.admin.request('/api/auth/users',{'username':name,'display_name':name,'password':'test-account-password-123','role':role})
  self.assertEqual(status,200)
  client=Client(self.base);self.assertEqual(client.request('/api/auth/login',{'username':name,'password':'test-account-password-123'})[0],200)
  return client,data['id']
 def test_readiness_http_status_and_no_internal_details(self):
  with patch('app.operations.snapshot',return_value={'ready':False,'components':{'database':'secret'}}):
   status,data,_=Client(self.base).request('/readyz')
  self.assertEqual(status,503);self.assertEqual(data,{'ready':False})
 def test_admin_can_read_operations(self):
  with patch('app.operations.snapshot',return_value={'ready':True}):
   self.assertEqual(self.admin.request('/api/ops/status')[0],200)
 def test_production_spa_and_missing_api_are_distinct(self):
  self.assertEqual(Client(self.base).request('/equipment')[0],200)
  self.assertEqual(self.admin.request('/api/not-existing')[0],404)
  self.assertEqual(Client(self.base).request('/../backend/.env')[0],404)
 def test_public_health_and_bootstrap_is_once_only(self):
  c=Client(self.base)
  self.assertEqual(c.request('/healthz')[0],200)
  self.assertFalse(c.request('/api/auth/status')[1]['needs_setup'])
  self.assertEqual(c.request('/api/auth/setup',{'username':'other','display_name':'Other','password':'test-account-password-123'})[0],409)
 def test_anonymous_cannot_read_or_operate_or_fetch_images(self):
  c=Client(self.base)
  for path,body in [('/api/tasks',None),('/api/results',None),('/api/results/img/test.jpg',None),('/api/tasks',{'robot_id':'robot1'}),('/api/chat',{'message':'hello'})]:self.assertEqual(c.request(path,body)[0],401)
 def test_viewer_reads_but_cannot_mutate_or_manage_users(self):
  c,_=self.user('viewer','viewer')
  self.assertEqual(c.request('/api/equipment')[0],200)
  for path,body in [('/api/tasks',{}),('/api/chat',{'message':'巡检'}),('/api/alerts/1/actions',{'action':'confirm','note':'伪造','actor':'admin'}),('/api/auth/users',None),('/api/auth/events',None),('/api/ops/status',None)]:self.assertEqual(c.request(path,body)[0],403)
 def test_operator_cannot_manage_accounts(self):
  c,_=self.user()
  self.assertEqual(c.request('/api/auth/users',{'username':'evil','password':'test-account-password-123','display_name':'evil','role':'admin'})[0],403)
 def test_csrf_and_cross_origin_are_enforced(self):
  self.assertEqual(self.admin.request('/api/auth/logout',{},csrf=False)[0],403)
  self.assertEqual(self.admin.request('/api/auth/logout',{},headers={'Origin':'https://evil.example'})[0],403)
  c=Client(self.base)
  self.assertEqual(c.request('/api/auth/login',{'username':'admin','password':'test-admin-password-123'},headers={'Origin':'https://evil.example'})[0],403)
 def test_sessions_are_hashed_and_cookie_is_http_only(self):
  c=Client(self.base);status,data,headers=c.request('/api/auth/login',{'username':'admin','password':'test-admin-password-123'})
  self.assertEqual(status,200);self.assertIn('HttpOnly',headers['Set-Cookie']);self.assertIn('SameSite=strict',headers['Set-Cookie'])
  token=next(cookie.value for cookie in c.jar)
  self.assertIsNone(db.query_one('SELECT * FROM auth_session WHERE token_hash=:t',{'t':token}))
  self.assertIsNotNone(db.query_one('SELECT * FROM auth_session WHERE token_hash=:t',{'t':hashlib.sha256(token.encode()).hexdigest()}))
  self.assertNotIn('password_hash',data['user'])
 def test_logout_invalidates_server_session(self):
  token=next(cookie.value for cookie in self.admin.jar)
  self.assertEqual(self.admin.request('/api/auth/logout',{})[0],200)
  self.assertEqual(self.admin.request('/api/tasks')[0],401)
  self.assertIsNone(auth.principal(auth.digest(token)))
 def test_expired_session_rejected(self):
  db.execute('UPDATE auth_session SET expires_at=NOW(3)-INTERVAL 1 SECOND')
  self.assertEqual(self.admin.request('/api/auth/me')[0],401)
 def test_disable_or_role_change_revokes_sessions(self):
  c,uid=self.user()
  self.assertEqual(self.admin.request('/api/auth/users/'+str(uid),{'role':'viewer','enabled':False})[0],200)
  self.assertEqual(c.request('/api/tasks')[0],401)
  self.assertEqual(Client(self.base).request('/api/auth/login',{'username':'operator','password':'test-account-password-123'})[0],401)
 def test_last_admin_cannot_be_disabled_or_demoted(self):
  uid=self.admin.request('/api/auth/me')[1]['user']['id']
  for role,enabled in [('viewer',True),('admin',False)]:self.assertEqual(self.admin.request('/api/auth/users/'+str(uid),{'role':role,'enabled':enabled})[0],409)
 def test_password_change_revokes_all_sessions(self):
  c=Client(self.base);c.request('/api/auth/login',{'username':'admin','password':'test-admin-password-123'})
  self.assertEqual(self.admin.request('/api/auth/password',{'current_password':'test-admin-password-123','new_password':'changed-test-password-456'})[0],200)
  self.assertEqual(c.request('/api/auth/me')[0],401)
  self.assertEqual(Client(self.base).request('/api/auth/login',{'username':'admin','password':'test-admin-password-123'})[0],401)
  self.assertEqual(Client(self.base).request('/api/auth/login',{'username':'admin','password':'changed-test-password-456'})[0],200)
 def test_password_reset_revokes_other_sessions(self):
  c,uid=self.user()
  self.assertEqual(self.admin.request('/api/auth/users/'+str(uid)+'/password',{'password':'reset-test-password-456'})[0],200)
  self.assertEqual(c.request('/api/auth/me')[0],401)
 def test_login_rate_limit_and_generic_errors(self):
  c=Client(self.base)
  for _ in range(5):self.assertEqual(c.request('/api/auth/login',{'username':'admin','password':'wrong'})[:2],(401,{'detail':'用户名或密码错误'}))
  self.assertEqual(c.request('/api/auth/login',{'username':'admin','password':'test-admin-password-123'})[0],429)
  db.execute('UPDATE auth_throttle SET window_end=NOW(3)-INTERVAL 1 SECOND')
  self.assertEqual(c.request('/api/auth/login',{'username':'admin','password':'test-admin-password-123'})[0],200)
 def test_task_creator_and_cancel_operator_cannot_be_forged(self):
  c,_=self.user()
  with patch('app.tasks_api._r.get',return_value=json.dumps({'pos':{'x':2,'y':-3},'battery':100})):
   status,data,_=c.request('/api/tasks',{'robot_id':'robot1','created_by':'admin','targets':[{'equipment_id':'AIR-001'}]})
  self.assertEqual(status,200);self.assertEqual(data['task']['created_by'],'operator')
  no=data['task']['task_no'];self.assertEqual(c.request('/api/tasks/'+no+'/cancel',{})[0],200)
  event=db.query_one("SELECT detail FROM task_event WHERE event='cancel'")
  self.assertEqual(json.loads(event['detail'])['operator'],'operator')
 def test_alert_actor_comes_from_session(self):
  c,_=self.user();point=equipment.expand_targets([{'equipment_id':'AIR-001'}])[0]
  no=sm.create_task('robot1','manual',5,'system',[point],{'verdict':'feasible'})
  with db.transaction() as conn:alerts.ingest(conn,no,1,point['inspection'],{'normal':False,'anomaly':'异常','raw':{}})
  aid=alerts.list_alerts()[0]['id']
  status,data,_=c.request('/api/alerts/'+str(aid)+'/actions',{'action':'confirm','note':'确认','actor':'forged-admin'})
  self.assertEqual(status,200);self.assertEqual(data['events'][-1]['actor'],'operator')
 def test_chat_session_cannot_be_taken_by_another_user(self):
  c,_=self.user();other,_=self.user('operator','other')
  sid=uuid4().hex
  with patch('app.agent.loop.run_turn',return_value=iter([('message',{'text':'test'})])):
   self.assertEqual(c.request('/api/chat',{'session_id':sid,'message':'查询'})[0],200)
   self.assertEqual(other.request('/api/chat',{'session_id':sid,'message':'确认'})[0],403)
 def test_agent_tool_rechecks_role_and_rejects_forged_actor(self):
  from app.agent import tools
  c,_=self.user();token=next(cookie.value for cookie in c.jar);user=auth.principal(auth.digest(token))
  handler=Mock(return_value={'ok':True})
  with patch.dict(tools._HANDLERS,{'cancel_task':handler}):
   tools.execute_tool('cancel_task',{'task_no':'x','actor':'forged'},principal=user)
   self.assertEqual(handler.call_args.kwargs['actor'],'operator')
   handler.reset_mock();c.request('/api/auth/logout',{})
   result=tools.execute_tool('cancel_task',{'task_no':'x'},principal=user)
   self.assertFalse(result['ok']);handler.assert_not_called()
 def test_viewer_agent_tool_is_denied_before_handler(self):
  from app.agent import tools
  c,_=self.user('viewer','viewer');token=next(cookie.value for cookie in c.jar);user=auth.principal(auth.digest(token))
  handler=Mock()
  with patch.dict(tools._HANDLERS,{'create_patrol_task':handler}):
   result=tools.execute_tool('create_patrol_task',{'points':[]},principal=user)
   self.assertFalse(result['ok']);handler.assert_not_called()
 def test_recheck_task_and_event_use_logged_in_identity(self):
  c,_=self.user();point=equipment.expand_targets([{'equipment_id':'AIR-001'}])[0]
  no=sm.create_task('robot1','manual',5,'system',[point],{'verdict':'feasible'})
  with db.transaction() as conn:alerts.ingest(conn,no,1,point['inspection'],{'normal':False,'anomaly':'异常','raw':{}})
  db.execute("UPDATE task SET status='DONE' WHERE task_no=:t",{'t':no});db.execute('UPDATE robot_execution SET task_no=NULL')
  aid=alerts.list_alerts()[0]['id'];path='/api/alerts/'+str(aid)
  c.request(path+'/actions',{'action':'confirm','note':'确认'});c.request(path+'/actions',{'action':'resolve','note':'处理完成'})
  with patch('app.tasks_api._r.get',return_value=json.dumps({'pos':{'x':2,'y':-3},'battery':100})):
   status,result,_=c.request(path+'/recheck',{'robot_id':'robot1','actor':'forged-admin'})
  self.assertEqual(status,200)
  self.assertEqual(db.query_one('SELECT created_by FROM task WHERE task_no=:t',{'t':result['task_no']})['created_by'],'operator')
  self.assertEqual(alerts.detail(aid)['events'][-1]['actor'],'operator')
 def test_short_password_not_accepted(self):
  self.assertEqual(self.admin.request('/api/auth/users',{'username':'short','display_name':'short','password':'123','role':'viewer'})[0],422)

if __name__=='__main__':unittest.main()
