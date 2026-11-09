"""Local production UI/API lifecycle. Never stops unowned or reused process IDs."""
import argparse,ctypes,json,os,secrets,shutil,socket,subprocess,sys,threading,time,urllib.request,urllib.error
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/'.runtime'


def identity(pid):
 try:
  if os.name=='nt':
   from ctypes import wintypes
   kernel=ctypes.WinDLL('kernel32',use_last_error=True)
   kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
   kernel.CloseHandle.argtypes=[wintypes.HANDLE]
   kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
   kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
   handle=kernel.OpenProcess(0x1000,False,pid)
   if not handle:return None
   try:
    times=[wintypes.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(handle,*[ctypes.byref(t) for t in times]):return None
    size=wintypes.DWORD(32768);buffer=ctypes.create_unicode_buffer(size.value)
    if not kernel.QueryFullProcessImageNameW(handle,0,buffer,ctypes.byref(size)):return None
    return {'created':str((times[0].dwHighDateTime<<32)|times[0].dwLowDateTime),'exe':os.path.normcase(buffer.value)}
   finally:kernel.CloseHandle(handle)
  stat=(Path('/proc')/str(pid)/'stat').read_text();created=stat[stat.rfind(')')+2:].split()[19]
  return {'created':created,'exe':str((Path('/proc')/str(pid)/'exe').resolve())}
 except (OSError,ValueError):return None


def state_path(port):return RUNTIME/f'service-{port}.json'
def stop_path(port):return RUNTIME/f'stop-{port}.json'

def read_state(port):
 try:return json.loads(state_path(port).read_text(encoding='utf-8'))
 except (OSError,ValueError):return None

def owned(state):
 return bool(state and isinstance(state.get('pid'),int) and state.get('identity') and identity(state['pid'])==state['identity'])

def atomic(path,data):
 path.parent.mkdir(parents=True,exist_ok=True)
 temporary=path.with_suffix('.tmp')
 temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(path)


def request(port,path):
 try:
  with urllib.request.urlopen(f'http://127.0.0.1:{port}'+path,timeout=8) as response:
   return response.status,json.loads(response.read())
 except urllib.error.HTTPError as error:
  try:return error.code,json.loads(error.read())
  except ValueError:return error.code,None
 except (OSError,ValueError):return None,None


def open_port(port):
 try:
  with socket.create_connection(('127.0.0.1',port),timeout=1):return True
 except OSError:return False


def info(port):
 state=read_state(port);code,health=request(port,'/healthz');ready_code,ready=request(port,'/readyz') if code==200 else (None,None)
 return {'managed':owned(state),'running':code==200 and isinstance(health,dict) and health.get('service')=='robot-inspection',
         'ready':ready_code==200 and bool(ready and ready.get('ready')),'url':f'http://127.0.0.1:{port}',
         'pid':state.get('pid') if owned(state) else None}


def up(port,require_new=False):
 current=read_state(port)
 if owned(current) or open_port(port):
  if require_new:raise RuntimeError('验收要求独立实例，端口已有服务，不会接管')
  result=info(port)
  if not result['running']:raise RuntimeError('端口已被占用；不会停止或替换未知服务')
  return result
 RUNTIME.mkdir(parents=True,exist_ok=True)
 instance=secrets.token_hex(16)
 bootstrap=RUNTIME/'bootstrap.log'
 if bootstrap.exists() and bootstrap.stat().st_size>5*1024*1024:bootstrap.replace(RUNTIME/'bootstrap.previous.log')
 with bootstrap.open('ab') as log:
  options={'cwd':str(ROOT),'stdin':subprocess.DEVNULL,'stdout':log,'stderr':log}
  if os.name=='nt':
   options['creationflags']=subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.DETACHED_PROCESS
   options['startupinfo']=subprocess.STARTUPINFO();options['startupinfo'].dwFlags|=subprocess.STARTF_USESHOWWINDOW;options['startupinfo'].wShowWindow=0
  else:options['start_new_session']=True
  subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'_serve','--port',str(port),'--instance',instance],**options)
 deadline=time.monotonic()+20
 while time.monotonic()<deadline:
  state=read_state(port)
  if state and state.get('instance')==instance:
   code,health=request(port,'/healthz')
   if code==200 and isinstance(health,dict) and health.get('service')=='robot-inspection':return info(port)
  time.sleep(.2)
 raise RuntimeError('启动未完成，请检查 .runtime/bootstrap.log；不会清理数据库或重置任务')


def stop(port):
 state=read_state(port)
 if not owned(state):raise RuntimeError('没有可验证的受管进程；不会终止外部服务或复用的 PID')
 atomic(stop_path(port),{'instance':state['instance']})
 deadline=time.monotonic()+10
 while time.monotonic()<deadline:
  if not owned(state):return {'stopped':True}
  time.sleep(.2)
 return {'stopped':False,'message':'正在退出，等待在途分析线程结束；未强制终止进程'}


def serve(port,instance):
 if len(instance)!=32 or any(c not in '0123456789abcdef' for c in instance):raise ValueError('invalid instance')
 sys.path.insert(0,str(ROOT/'backend'))
 import uvicorn
 marker={'pid':os.getpid(),'identity':identity(os.getpid()),'instance':instance}
 if not marker['identity']:raise RuntimeError('无法记录进程身份')
 atomic(state_path(port),marker)
 server=uvicorn.Server(uvicorn.Config('app.main:app',host='127.0.0.1',port=port,workers=1,access_log=False))
 done=threading.Event()
 def watch():
  while not done.wait(.3):
   try:message=json.loads(stop_path(port).read_text(encoding='utf-8'))
   except (OSError,ValueError):continue
   if message.get('instance')==instance:server.should_exit=True;return
 thread=threading.Thread(target=watch,daemon=True);thread.start()
 try:server.run()
 finally:
  done.set();thread.join(1)
  if (read_state(port) or {}).get('instance')==instance:
   state_path(port).unlink(missing_ok=True);stop_path(port).unlink(missing_ok=True)


def build(node=None):
 binary=node or os.getenv('NODE_BINARY') or shutil.which('node')
 if not binary:raise RuntimeError('找不到 Node.js，请用 build --node 指定路径或设置 NODE_BINARY')
 vite=ROOT/'frontend/node_modules/vite/bin/vite.js'
 if not vite.is_file():raise RuntimeError('请先在 frontend 执行 npm ci 安装锁定依赖')
 subprocess.run([binary,str(vite),'build'],cwd=ROOT/'frontend',check=True)
 return {'built':True,'directory':'frontend/dist'}


def doctor(port):
 sys.path.insert(0,str(ROOT/'backend'))
 from app import operations,config
 database,_=operations.database_probe();cache,_=operations.redis_probe();storage=operations.storage_probe()
 try:
  with socket.create_connection((config.MQTT_HOST,config.MQTT_PORT),timeout=2):mqtt_tcp=True
 except OSError:mqtt_tcp=False
 return {'service':info(port),'database':database,'redis':cache,'storage':storage,'mqtt_port_reachable':mqtt_tcp,
         'frontend_built':(config.FRONTEND_DIST/'index.html').is_file(),'model_configured':bool(config.AGENT_API_KEY)}


def main():
 parser=argparse.ArgumentParser(description='巡境本地生产服务：构建、启动、状态、诊断和停止')
 commands=parser.add_subparsers(dest='action',required=True)
 for name in ('up','status','stop','doctor','_serve'):
  cmd=commands.add_parser(name);cmd.add_argument('--port',type=int,default=8010)
  if name=='_serve':cmd.add_argument('--instance',required=True)
  if name=='up':cmd.add_argument('--require-new',action='store_true')
 cmd=commands.add_parser('build');cmd.add_argument('--node')
 args=parser.parse_args()
 if hasattr(args,'port') and not 1024<=args.port<=65535:parser.error('port must be 1024..65535')
 try:
  if args.action=='_serve':serve(args.port,args.instance);return
  result=build(args.node) if args.action=='build' else up(args.port,args.require_new) if args.action=='up' else globals()[args.action if args.action!='status' else 'info'](args.port)
  print(json.dumps(result,ensure_ascii=False,indent=2))
  if args.action=='stop' and not result['stopped']:raise SystemExit(1)
  if args.action=='doctor' and not result['service']['ready']:raise SystemExit(1)
 except (OSError,RuntimeError,subprocess.CalledProcessError) as exc:
  print(str(exc),file=sys.stderr);raise SystemExit(1)

if __name__=='__main__':main()
