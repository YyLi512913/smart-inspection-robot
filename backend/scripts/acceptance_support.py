"""Shared acceptance workers and run-specific reports. Never starts production ROS."""
import asyncio,json,os,threading,sys,re
from pathlib import Path
if sys.flags.optimize:raise RuntimeError("验收必须启用断言，请勿使用 -O 或 PYTHONOPTIMIZE")
ROOT=Path(__file__).resolve().parents[2]

def scrub(data):
 if isinstance(data,dict):return {k:scrub(v) for k,v in data.items()}
 if isinstance(data,list):return [scrub(v) for v in data]
 if not isinstance(data,str):return data
 data=re.sub(r'(mysql(?:\+pymysql)?://[^:\s]+:)[^@\s]+@',r'\1[redacted]@',data)
 try:
  from app import config
  for secret in (config.AGENT_API_KEY,config.MYSQL_URL):
   if secret:data=data.replace(secret,'[redacted]')
 except (ImportError,RuntimeError):pass
 return data

def write_report(name,data):
 directory=Path(os.getenv('ACCEPTANCE_REPORT_DIR',str(ROOT/'docs/validation')))
 directory.mkdir(parents=True,exist_ok=True)
 path=directory/name
 path.write_text(json.dumps(scrub(data),ensure_ascii=False,indent=2,default=str),encoding='utf-8')
 return path

class AnalysisWorkers:
 def __init__(self,enabled):self.enabled=enabled;self.stop_event=threading.Event();self.thread=None;self.error=None
 def start(self):
  if not self.enabled:return
  async def run():
   from app import vision_jobs
   task=asyncio.create_task(vision_jobs.run_workers())
   try:
    while not self.stop_event.is_set():
     if task.done():task.result();raise RuntimeError('analysis workers stopped')
     await asyncio.sleep(.1)
   finally:
    task.cancel();await asyncio.gather(task,return_exceptions=True)
  def target():
   try:asyncio.run(run())
   except BaseException as exc:self.error=exc
  self.thread=threading.Thread(target=target);self.thread.start()
 def close(self):
  self.stop_event.set()
  if self.thread:
   self.thread.join(240)
   if self.thread.is_alive():raise RuntimeError('Analysis threads still running; test DB retained')
  if self.error:raise RuntimeError('Analysis worker failed') from self.error
