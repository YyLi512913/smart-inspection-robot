"""Durable image analysis with leases, fencing and atomic result/alert completion."""
import asyncio
import json
import logging
from uuid import uuid4
from . import db, config, equipment, vlm

log=logging.getLogger(__name__)
MAX_ATTEMPTS=3
LEASE_SECONDS=60
HEARTBEAT_SECONDS=15
WORKERS=2


def enqueue(conn,task_no,seq,place,check_item,image_url,snapshot=None):
    conn.execute(db.text("INSERT IGNORE INTO vision_job(task_no,point_seq,place,check_item,image_url,snapshot) VALUES(:t,:s,:p,:c,:i,:m)"),
        {"t":task_no,"s":seq,"p":place,"c":check_item,"i":image_url,"m":json.dumps(snapshot,ensure_ascii=False) if snapshot else None})


def recover_backlog():
    """Upgrade only unclassified received images; never replay existing conclusions."""
    with db.transaction() as conn:
        conn.execute(db.text("""INSERT IGNORE INTO vision_job(task_no,point_seq,place,check_item,image_url,snapshot)
            SELECT t.task_no,te.point_seq,tp.place,tp.check_item,
                   JSON_UNQUOTE(JSON_EXTRACT(te.detail,'$.image_url')),pi.snapshot
            FROM task_event te JOIN task t ON t.id=te.task_id
            JOIN task_point tp ON tp.task_id=t.id AND tp.seq=te.point_seq
            LEFT JOIN task_point_inspection pi ON pi.task_id=t.id AND pi.seq=te.point_seq
            LEFT JOIN inspection_result ir ON ir.task_no=t.task_no AND ir.point_seq=te.point_seq
            WHERE te.event='result_received' AND ir.id IS NULL
              AND JSON_TYPE(JSON_EXTRACT(te.detail,'$.image_url'))='STRING'
              AND JSON_UNQUOTE(JSON_EXTRACT(te.detail,'$.image_url'))<>''"""))


def claim():
    with db.transaction() as conn:
        row=conn.execute(db.text("""SELECT * FROM vision_job
            WHERE (status='pending' AND available_at<=NOW(3))
               OR (status='processing' AND lease_until<=NOW(3))
            ORDER BY available_at,id LIMIT 1 FOR UPDATE SKIP LOCKED""")).mappings().first()
        if not row:return None
        job=dict(row)
        job['exhausted']=job['attempts']>=MAX_ATTEMPTS
        job['attempts']=min(MAX_ATTEMPTS,job['attempts']+1)
        job['lease_token']=uuid4().hex
        conn.execute(db.text("""UPDATE vision_job SET status='processing',attempts=:a,lease_token=:k,
            lease_until=TIMESTAMPADD(SECOND,:l,NOW(3)),updated_at=NOW(3) WHERE id=:i"""),
            {"a":job['attempts'],"k":job['lease_token'],"l":LEASE_SECONDS,"i":job['id']})
        job['snapshot']=equipment.decode(job['snapshot']) if job['snapshot'] else None
        return job


def renew(job):
    with db.transaction() as conn:
        result=conn.execute(db.text("""UPDATE vision_job SET lease_until=TIMESTAMPADD(SECOND,:l,NOW(3))
            WHERE id=:i AND status='processing' AND lease_token=:k AND lease_until>NOW(3)"""),
            {"i":job['id'],"k":job['lease_token'],"l":LEASE_SECONDS})
        return result.rowcount==1


def release(job):
    # A canceled coroutine may still have an HTTP call in a thread. Invalidate its token.
    with db.transaction() as conn:
        conn.execute(db.text("""UPDATE vision_job SET status='pending',lease_token=NULL,lease_until=NULL,
            available_at=NOW(3),updated_at=NOW(3) WHERE id=:i AND status='processing' AND lease_token=:k"""),
            {"i":job['id'],"k":job['lease_token']})


def persist_assessment(conn,job,res):
    from .alerts import save_assessment
    save_assessment(job['task_no'],job['point_seq'],job.get('snapshot'),res,
        "INSERT INTO inspection_result (task_no,point_seq,place,check_item,image_url,normal,reading,anomaly,raw,vlm_model,error) "
        "VALUES(:n,:s,:p,:c,:i,:no,:r,:a,:w,:v,:e) "
        "ON DUPLICATE KEY UPDATE normal=VALUES(normal),reading=VALUES(reading),anomaly=VALUES(anomaly),"
        "raw=VALUES(raw),vlm_model=VALUES(vlm_model),error=VALUES(error),image_url=VALUES(image_url)",
        {"n":job['task_no'],"s":job['point_seq'],"p":job['place'],"c":job['check_item'],"i":job['image_url'],
         "no":None if res.get('normal') is None else int(res['normal']),"r":res.get('reading','')[:255],
         "a":res.get('anomaly','')[:255],"w":json.dumps(res.get('raw'),ensure_ascii=False) if res.get('raw') else None,
         "v":config.VLM_MODEL,"e":res.get('error','')[:255]},connection=conn)


def finish(job,res):
    with db.transaction() as conn:
        row=conn.execute(db.text("SELECT * FROM vision_job WHERE id=:i FOR UPDATE"),{"i":job['id']}).mappings().first()
        valid=conn.execute(db.text("SELECT 1 FROM vision_job WHERE id=:i AND lease_until>NOW(3)"),{"i":job['id']}).first()
        if not row or row['status']!='processing' or row['lease_token']!=job['lease_token'] or not valid:
            return False
        error=str(res.get('error') or '')[:500]
        if error and row['attempts']<MAX_ATTEMPTS:
            delay=10*3**(row['attempts']-1)
            conn.execute(db.text("""UPDATE vision_job SET status='pending',last_error=:e,
                available_at=TIMESTAMPADD(SECOND,:d,NOW(3)),lease_token=NULL,lease_until=NULL,
                updated_at=NOW(3) WHERE id=:i"""),{"e":error,"d":delay,"i":job['id']})
            return True
        persist_assessment(conn,job,res)
        conn.execute(db.text("""UPDATE vision_job SET status=:s,last_error=:e,lease_token=NULL,
            lease_until=NULL,finished_at=NOW(3),updated_at=NOW(3) WHERE id=:i"""),
            {"s":"failed" if error else "succeeded","e":error,"i":job['id']})
        return True


def detect(job):
    if job.get('exhausted'):
        return {'normal':None,'raw':None,'error':'分析执行多次中断，自动重试次数已用尽','reading':'','anomaly':''}
    meta=job.get('snapshot') or {}
    return vlm.detect(job['image_url'],meta.get('equipment_name',job['place']),job['check_item'],checks=meta.get('checks'))


async def worker(name=None):
    from .operations import beat
    if name:beat(name)
    while True:
        job=None
        try:
            job=await asyncio.to_thread(claim)
            if name:beat(name)
            if not job:
                await asyncio.sleep(1)
                continue
            call=asyncio.create_task(asyncio.to_thread(detect,job))
            call.add_done_callback(lambda task: None if task.cancelled() else task.exception())
            owned=True
            while not call.done():
                done,_=await asyncio.wait({call},timeout=HEARTBEAT_SECONDS)
                if not done:
                    owned=await asyncio.to_thread(renew,job) and owned
                    if name:beat(name,owned)
            try:res=call.result()
            except Exception as exc:
                res={'normal':None,'raw':None,'error':f'分析异常: {exc}'[:500],'reading':'','anomaly':''}
            if owned:await asyncio.to_thread(finish,job,res)
        except asyncio.CancelledError:
            if job:
                try:await asyncio.to_thread(release,job)
                except Exception:log.exception('Cannot release analysis lease; it will expire')
            raise
        except Exception:
            # A DB failure leaves the lease on disk. Expiration makes it recoverable.
            if name:beat(name,False)
            log.exception('Vision worker failed; durable job retained')
            await asyncio.sleep(1)


async def run_workers():
    workers=[asyncio.create_task(worker(f"vision_{i}")) for i in range(WORKERS)]
    try:await asyncio.gather(*workers)
    finally:
        for task in workers:task.cancel()
        await asyncio.gather(*workers,return_exceptions=True)
