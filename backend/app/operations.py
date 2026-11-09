"""Bounded dependency probes and local worker heartbeats; no model API calls."""
import shutil,tempfile,threading,time
from datetime import datetime,timezone
from sqlalchemy import create_engine,text
from sqlalchemy.pool import NullPool
import redis
from fastapi import APIRouter
from . import db,config

router=APIRouter(prefix='/api/ops')
_lock=threading.Lock();_workers={};_mqtt=lambda:False
_STARTED=time.monotonic();_cache=None;_cached_at=0
EXPECTED={'delivery':30,'watchdog':40,'vision_0':45,'vision_1':45}


def configure(mqtt_connected):
 global _mqtt,_cache
 _mqtt=mqtt_connected
 with _lock:_workers.clear();_cache=None


def beat(name,ok=True):
 with _lock:_workers[name]={'at':time.monotonic(),'ok':ok}


def worker_status():
 with _lock:rows={k:dict(v) for k,v in _workers.items()}
 now=time.monotonic()
 return {name:{'status':'ok' if name in rows and rows[name]['ok'] and now-rows[name]['at']<=age else 'down',
               'age_seconds':round(now-rows[name]['at'],1) if name in rows else None}
         for name,age in EXPECTED.items()}


def database_probe():
 engine=create_engine(db.engine.url,poolclass=NullPool,connect_args={'connect_timeout':2,'read_timeout':2,'write_timeout':2})
 try:
  with engine.connect() as conn:
   conn.execute(text('SELECT 1'))
   counts={}
   for label,table in [('vision','vision_job'),('commands','mqtt_command'),('tasks','task')]:
    counts[label]={r['status']:r['n'] for r in conn.execute(text(f'SELECT status,COUNT(*) AS n FROM {table} GROUP BY status')).mappings()}
   counts['oldest_command_seconds']=conn.execute(text("SELECT COALESCE(TIMESTAMPDIFF(SECOND,MIN(created_at),NOW()),0) FROM mqtt_command WHERE status='pending'")).scalar_one()
   counts['oldest_analysis_seconds']=conn.execute(text("SELECT COALESCE(TIMESTAMPDIFF(SECOND,MIN(created_at),NOW()),0) FROM vision_job WHERE status IN ('pending','processing')")).scalar_one()
   counts['active_alerts']=conn.execute(text("SELECT COUNT(*) FROM inspection_alert WHERE status NOT IN ('closed','false_positive')")).scalar_one()
   return {'status':'ok'},counts
 except Exception:return {'status':'down','message':'数据库连接或查询失败'},{}
 finally:engine.dispose()


def redis_probe():
 client=redis.Redis.from_url(config.REDIS_URL,decode_responses=True,socket_connect_timeout=2,socket_timeout=2)
 try:
  client.ping()
  robots=[]
  for rid in config.ROBOT_IDS:
   robots.append({'robot_id':rid,'online':client.exists(f'robot:{rid}:live')==1})
  return {'status':'ok'},robots
 except Exception:return {'status':'down','message':'实时缓存连接失败'},[]
 finally:client.close()


def storage_probe():
 try:
  config.RESULTS_DIR.mkdir(parents=True,exist_ok=True)
  with tempfile.TemporaryFile(dir=config.RESULTS_DIR) as probe:probe.write(b'probe');probe.flush()
  free=shutil.disk_usage(config.RESULTS_DIR).free//(1024*1024)
  return {'status':'ok' if free>=500 else 'degraded','free_mb':free,'message':'可读写' if free>=500 else '剩余空间低于 500 MB'}
 except Exception:return {'status':'down','message':'图片目录不可写'}


def snapshot(force=False):
 global _cache,_cached_at
 if not force and _cache is not None and time.monotonic()-_cached_at<3:return _cache
 database,counts=database_probe();cache,robots=redis_probe();storage=storage_probe();workers=worker_status()
 try:mqtt_ok=bool(_mqtt())
 except Exception:mqtt_ok=False
 components={'database':database,'redis':cache,'mqtt':{'status':'ok' if mqtt_ok else 'down'},'storage':storage,
             'workers':{'status':'ok' if all(w['status']=='ok' for w in workers.values()) else 'down'}}
 ready=all(c['status']!='down' for c in components.values())
 degraded=not config.AGENT_API_KEY or any(not r['online'] for r in robots) or storage['status']=='degraded'
 result={'checked_at':datetime.now(timezone.utc).isoformat(),'uptime_seconds':int(time.monotonic()-_STARTED),
         'ready':ready,'status':'down' if not ready else 'degraded' if degraded else 'ok',
         'components':components,'workers':workers,'queues':counts,'robots':robots,
         'model_configured':bool(config.AGENT_API_KEY),'model_probe':'not_called'}
 _cache=result;_cached_at=time.monotonic();return result

@router.get('/status')
def status():return snapshot()
