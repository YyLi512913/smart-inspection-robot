"""Rotating JSON logs with request IDs and local-secret redaction."""
import json,logging
from datetime import datetime,timezone
from logging.handlers import RotatingFileHandler
from . import config,db

class JsonFormatter(logging.Formatter):
 def format(self,record):
  message=record.getMessage()
  if record.exc_info:message+='\n'+self.formatException(record.exc_info)
  secrets=[config.AGENT_API_KEY,config.MYSQL_URL]
  password=db.engine.url.password
  if password and len(password)>=8:secrets.append(password)
  for secret in secrets:
   if secret:message=message.replace(secret,'[redacted]')
  result={'time':datetime.now(timezone.utc).isoformat(),'level':record.levelname,'logger':record.name,'message':message}
  for key in ('request_id','method','path','status_code','duration_ms','user_id'):
   if hasattr(record,key):result[key]=getattr(record,key)
  return json.dumps(result,ensure_ascii=False)


def configure():
 config.LOG_DIR.mkdir(parents=True,exist_ok=True)
 formatter=JsonFormatter()
 file=RotatingFileHandler(config.LOG_DIR/'service.jsonl',maxBytes=5*1024*1024,backupCount=3,encoding='utf-8')
 console=logging.StreamHandler()
 for handler in (file,console):handler.setFormatter(formatter)
 root=logging.getLogger()
 for handler in root.handlers[:]:root.removeHandler(handler);handler.close()
 root.setLevel(logging.INFO);root.addHandler(file);root.addHandler(console)
 for name in ('uvicorn','uvicorn.error','uvicorn.access'):
  logger=logging.getLogger(name);logger.handlers.clear();logger.propagate=True
