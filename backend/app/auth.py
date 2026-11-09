"""Cookie sessions and server-enforced role checks. No default passwords."""
import hashlib,hmac,ipaddress,os,secrets
from urllib.parse import urlsplit
from fastapi import APIRouter,HTTPException,Request,Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel,Field
from . import db

router=APIRouter(prefix='/api/auth')
COOKIE='inspection_session'
SESSION_SECONDS=8*60*60
ROLES={'viewer','operator','admin'}
PUBLIC={'/api/auth/status','/api/auth/setup','/api/auth/login'}
SAFE={'GET','HEAD','OPTIONS'}


def password_hash(password):
 salt=secrets.token_hex(16)
 digest=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1,dklen=32).hex()
 return 'scrypt$'+salt+'$'+digest


def verify(password,encoded):
 try:
  _,salt,expected=encoded.split('$')
  actual=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1,dklen=32).hex()
  return hmac.compare_digest(actual,expected)
 except (ValueError,TypeError):return False

DUMMY_HASH=password_hash('dummy-nonexistent-user-password')

def digest(token):return hashlib.sha256(token.encode()).hexdigest()

def audit(conn,actor,action,target='',detail=''):
 conn.execute(db.text('INSERT INTO auth_event(actor,action,target,detail) VALUES(:a,:x,:t,:d)'),{'a':actor,'x':action,'t':target,'d':detail})

def public_user(user):return {k:user[k] for k in ('id','username','display_name','role')}

def principal(token_hash):
 return db.query_one("SELECT u.id,u.username,u.display_name,u.role,s.token_hash,s.csrf_token FROM auth_session s JOIN app_user u ON u.id=s.user_id WHERE s.token_hash=:t AND s.expires_at>NOW(3) AND u.enabled=1",{'t':token_hash})

def validate_principal(user,operate=False):
 current=principal(user.get('token_hash','')) if user else None
 if not current:raise HTTPException(401,'登录已失效，请重新登录')
 if operate and current['role'] not in {'operator','admin'}:raise HTTPException(403,'当前账号只有查看权限')
 return current

def allowed_origin(request):
 origin=request.headers.get('origin')
 if not origin:return True
 configured={x.strip().rstrip('/') for x in os.getenv('AUTH_ALLOWED_ORIGINS','http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8010,http://localhost:8010,http://127.0.0.1:8000,http://localhost:8000').split(',')}
 return origin.rstrip('/') in configured

async def guard(request,call_next):
 path=request.url.path
 if path.startswith('/api/'):
  if request.method not in SAFE and not allowed_origin(request):return JSONResponse(status_code=403,content={'detail':'请求来源不允许'})
  if path not in PUBLIC:
   token=request.cookies.get(COOKIE,'')
   user=principal(digest(token)) if token else None
   if not user:return JSONResponse(status_code=401,content={'detail':'请先登录'})
   request.state.user=user
   if request.method not in SAFE:
    if not hmac.compare_digest(request.headers.get('x-csrf-token',''),user['csrf_token']):return JSONResponse(status_code=403,content={'detail':'登录校验已失效，请刷新页面'})
    if path.startswith('/api/auth/users') or path=='/api/auth/events' or path.startswith('/api/ops'):
     if user['role']!='admin':return JSONResponse(status_code=403,content={'detail':'需要管理员权限'})
    elif path not in {'/api/auth/logout','/api/auth/password'} and user['role'] not in {'admin','operator'}:
     return JSONResponse(status_code=403,content={'detail':'当前账号只有查看权限'})
   if (path.startswith('/api/auth/users') or path=='/api/auth/events' or path.startswith('/api/ops')) and user['role']!='admin':return JSONResponse(status_code=403,content={'detail':'需要管理员权限'})
 response=await call_next(request)
 if path.startswith('/api/'):response.headers['Cache-Control']='no-store'
 return response

class Credentials(BaseModel):
 username:str=Field(min_length=3,max_length=32,pattern=r'^[a-zA-Z0-9_.-]+$')
 password:str=Field(min_length=1,max_length=128)

class NewUser(Credentials):
 display_name:str=Field(min_length=1,max_length=80)
 role:str=Field(default='viewer',pattern='^(viewer|operator|admin)$')


def check_password(password):
 if len(password)<12:raise HTTPException(422,'密码至少 12 个字符')


def create_user(conn,body):
 check_password(body.password)
 if not body.display_name.strip():raise HTTPException(422,'显示名称不能为空')
 if conn.execute(db.text('SELECT 1 FROM app_user WHERE username=:n'),{'n':body.username}).first():raise HTTPException(409,'用户名已存在')
 return conn.execute(db.text('INSERT INTO app_user(username,display_name,role,password_hash) VALUES(:n,:d,:r,:p)'),{'n':body.username,'d':body.display_name.strip(),'r':body.role,'p':password_hash(body.password)}).lastrowid


def session_response(conn,user,response):
 token=secrets.token_urlsafe(32);csrf=secrets.token_hex(32)
 conn.execute(db.text('INSERT INTO auth_session(token_hash,user_id,csrf_token,expires_at) VALUES(:t,:u,:c,TIMESTAMPADD(SECOND,:s,NOW(3)))'),{'t':digest(token),'u':user['id'],'c':csrf,'s':SESSION_SECONDS})
 response.set_cookie(COOKIE,token,httponly=True,secure=os.getenv('AUTH_COOKIE_SECURE','false').lower()=='true',samesite='strict',max_age=SESSION_SECONDS,path='/')
 response.headers['Cache-Control']='no-store'
 return {'user':public_user(user),'csrf_token':csrf}

@router.get('/status')
def status():return {'needs_setup':db.query_one('SELECT id FROM app_user LIMIT 1') is None}

@router.post('/setup')
def setup(body:NewUser,request:Request,response:Response):
 try:local=ipaddress.ip_address(request.client.host).is_loopback
 except ValueError:local=False
 if not local or os.getenv('AUTH_ALLOW_SETUP','true').lower()!='true':raise HTTPException(403,'首次管理员请在本机初始化')
 with db.transaction() as conn:
  conn.execute(db.text('SELECT id FROM auth_guard WHERE id=1 FOR UPDATE')).first()
  if conn.execute(db.text('SELECT id FROM app_user LIMIT 1')).first():raise HTTPException(409,'系统已初始化，请登录')
  body=body.model_copy(update={'role':'admin'})
  uid=create_user(conn,body)
  user={'id':uid,'username':body.username,'display_name':body.display_name.strip(),'role':'admin'}
  audit(conn,body.username,'setup',body.username)
  return session_response(conn,user,response)


def throttle(username,ip):
 key=digest(ip+'|'+username.lower())
 with db.transaction() as conn:
  conn.execute(db.text('INSERT IGNORE INTO auth_throttle(throttle_key,window_end) VALUES(:k,NOW(3)+INTERVAL 5 MINUTE)'),{'k':key})
  row=conn.execute(db.text('SELECT attempts,window_end<=NOW(3) AS expired FROM auth_throttle WHERE throttle_key=:k FOR UPDATE'),{'k':key}).mappings().one()
  if not row['expired'] and row['attempts']>=5:raise HTTPException(429,'登录尝试过多，请 5 分钟后重试')
  conn.execute(db.text('UPDATE auth_throttle SET attempts=IF(window_end<=NOW(3),1,attempts+1),window_end=IF(window_end<=NOW(3),NOW(3)+INTERVAL 5 MINUTE,window_end) WHERE throttle_key=:k'),{'k':key})
 return key

@router.post('/login')
def login(body:Credentials,request:Request,response:Response):
 key=throttle(body.username,request.client.host)
 user=db.query_one('SELECT * FROM app_user WHERE username=:n',{'n':body.username})
 valid=verify(body.password,user['password_hash'] if user else DUMMY_HASH)
 if not user or not valid or not user['enabled']:
  with db.transaction() as conn:audit(conn,'anonymous','login_failed',body.username)
  raise HTTPException(401,'用户名或密码错误')
 with db.transaction() as conn:
  # Recheck after hashing so a concurrent disable/reset cannot mint a usable old-password session.
  current=conn.execute(db.text('SELECT * FROM app_user WHERE id=:i FOR UPDATE'),{'i':user['id']}).mappings().one()
  if not current['enabled'] or current['password_hash']!=user['password_hash']:raise HTTPException(401,'用户名或密码错误')
  conn.execute(db.text('DELETE FROM auth_throttle WHERE throttle_key=:k'),{'k':key})
  conn.execute(db.text('DELETE FROM auth_session WHERE expires_at<=NOW(3)'))
  audit(conn,user['username'],'login',user['username'])
  return session_response(conn,current,response)

@router.get('/me')
def me(request:Request,response:Response):
 response.headers['Cache-Control']='no-store'
 return {'user':public_user(request.state.user),'csrf_token':request.state.user['csrf_token']}

@router.post('/logout')
def logout(request:Request,response:Response):
 with db.transaction() as conn:
  conn.execute(db.text('DELETE FROM auth_session WHERE token_hash=:t'),{'t':request.state.user['token_hash']})
  audit(conn,request.state.user['username'],'logout')
 response.delete_cookie(COOKIE,path='/');return {'ok':True}

class PasswordChange(BaseModel):
 current_password:str=Field(max_length=128)
 new_password:str=Field(max_length=128)

@router.post('/password')
def change_password(body:PasswordChange,request:Request,response:Response):
 check_password(body.new_password)
 with db.transaction() as conn:
  u=conn.execute(db.text('SELECT * FROM app_user WHERE id=:i FOR UPDATE'),{'i':request.state.user['id']}).mappings().one()
  if not verify(body.current_password,u['password_hash']):raise HTTPException(400,'当前密码不正确')
  conn.execute(db.text('UPDATE app_user SET password_hash=:p WHERE id=:i'),{'p':password_hash(body.new_password),'i':u['id']})
  conn.execute(db.text('DELETE FROM auth_session WHERE user_id=:i'),{'i':u['id']})
  audit(conn,u['username'],'password_changed',u['username'])
 response.delete_cookie(COOKIE,path='/');return {'ok':True}

@router.get('/users')
def users():return db.query('SELECT id,username,display_name,role,enabled,created_at FROM app_user ORDER BY id')

@router.post('/users')
def add_user(body:NewUser,request:Request):
 with db.transaction() as conn:
  conn.execute(db.text('SELECT id FROM auth_guard WHERE id=1 FOR UPDATE')).first()
  uid=create_user(conn,body);audit(conn,request.state.user['username'],'user_created',body.username,body.role)
 return {'id':uid}

class UserUpdate(BaseModel):
 role:str=Field(pattern='^(viewer|operator|admin)$')
 enabled:bool

@router.post('/users/{uid}')
def update_user(uid:int,body:UserUpdate,request:Request):
 with db.transaction() as conn:
  conn.execute(db.text('SELECT id FROM auth_guard WHERE id=1 FOR UPDATE')).first()
  u=conn.execute(db.text('SELECT * FROM app_user WHERE id=:i FOR UPDATE'),{'i':uid}).mappings().first()
  if not u:raise HTTPException(404,'账号不存在')
  if u['role']=='admin' and u['enabled'] and (body.role!='admin' or not body.enabled):
   count=conn.execute(db.text("SELECT COUNT(*) FROM app_user WHERE role='admin' AND enabled=1")).scalar_one()
   if count<=1:raise HTTPException(409,'必须保留至少一个启用的管理员')
  conn.execute(db.text('UPDATE app_user SET role=:r,enabled=:e WHERE id=:i'),{'r':body.role,'e':body.enabled,'i':uid})
  conn.execute(db.text('DELETE FROM auth_session WHERE user_id=:i'),{'i':uid})
  audit(conn,request.state.user['username'],'user_updated',u['username'],body.role+(' enabled' if body.enabled else ' disabled'))
 return {'ok':True}

class ResetPassword(BaseModel):
 password:str=Field(max_length=128)

@router.post('/users/{uid}/password')
def reset_password(uid:int,body:ResetPassword,request:Request):
 check_password(body.password)
 with db.transaction() as conn:
  u=conn.execute(db.text('SELECT * FROM app_user WHERE id=:i FOR UPDATE'),{'i':uid}).mappings().first()
  if not u:raise HTTPException(404,'账号不存在')
  conn.execute(db.text('UPDATE app_user SET password_hash=:p WHERE id=:i'),{'p':password_hash(body.password),'i':uid})
  conn.execute(db.text('DELETE FROM auth_session WHERE user_id=:i'),{'i':uid})
  audit(conn,request.state.user['username'],'password_reset',u['username'])
 return {'ok':True}

@router.get('/events')
def events():return db.query('SELECT * FROM auth_event ORDER BY id DESC LIMIT 200')
