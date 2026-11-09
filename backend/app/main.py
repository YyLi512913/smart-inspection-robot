"""FastAPI 入口：lifespan 启动 MQTT 消费 + 看门狗，挂载遥测与任务接口。

M1 验收：curl /api/robots/robot1/live 看到 Gazebo 小车位置 1Hz 跳动。
M2 验收：curl POST /api/tasks 创建任务 → MQTT 收到下发报文 → 模拟 event → 状态机流转。
"""
import asyncio
import json
import logging
import time
import re
from uuid import uuid4
from contextlib import suppress
from contextlib import asynccontextmanager

import redis
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from .migrations import ensure_delivery_schema

from . import config, state_machine, watchdog, vision_jobs, operations
from .agent.api import router as agent_router
from .mqtt_consumer import MqttConsumer
from .results_api import router as results_router
from .tasks_api import router as tasks_router


consumer = MqttConsumer()
r = redis.Redis.from_url(config.REDIS_URL, decode_responses=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from .logging_setup import configure
    configure()
    operations.configure(consumer.c.is_connected)
    await asyncio.to_thread(ensure_delivery_schema)
    await asyncio.to_thread(vision_jobs.recover_backlog)
    state_machine.set_publisher(consumer.publish)  # 状态机经同一 MQTT 连接下发
    consumer.start()
    wd = asyncio.create_task(watchdog.watchdog_loop())
    sender = asyncio.create_task(watchdog.delivery_loop())
    vision = asyncio.create_task(vision_jobs.run_workers())
    try:
        yield
    finally:
        wd.cancel()
        sender.cancel()
        vision.cancel()
        for background in (wd, sender, vision):
            with suppress(asyncio.CancelledError):
                await background
        consumer.stop()


app = FastAPI(title="机器人巡检平台后端", lifespan=lifespan)
from .auth import guard, router as auth_router
app.middleware("http")(guard)
app.include_router(auth_router)
from .equipment_api import router as equipment_router
from .alerts import router as alerts_router
app.include_router(alerts_router)
app.include_router(equipment_router)
app.include_router(tasks_router)
app.include_router(results_router)
app.include_router(agent_router)
app.include_router(operations.router)


@app.get("/healthz")
async def health():
    return {"ok": True,"service":"robot-inspection"}


@app.get("/readyz")
def readiness():
    state=operations.snapshot()
    return JSONResponse(status_code=200 if state["ready"] else 503,content={"ready":state["ready"]},headers={"Cache-Control":"no-store"})


@app.get("/api/robots")
async def robots():
    """设备列表（设计文档 5.2）：已知配置 + Redis 在线遥测合并，离线也列出。"""
    ids = set(config.ROBOT_IDS)
    for key in r.scan_iter("robot:*:live"):
        ids.add(key.split(":")[1])
    out = []
    for rid in sorted(ids):
        raw = r.get(f"robot:{rid}:live")
        if raw is None:
            out.append({"robot_id": rid, "online": False})
        else:
            live = json.loads(raw)
            out.append({"robot_id": rid, "online": True, **live})
    return out


@app.get("/api/robots/{robot_id}/live")
async def robot_live(robot_id: str):
    """最新遥测（Redis 缓存，TTL 5s）。无数据 = 离线或未上报。"""
    raw = r.get(f"robot:{robot_id}:live")
    if raw is None:
        raise HTTPException(status_code=404, detail=f"{robot_id} 无实时遥测（离线或超过 {config.LIVE_TTL}s 未上报）")
    return json.loads(raw)


@app.exception_handler(state_machine.TaskConflict)
async def task_conflict(request: Request, exc: state_machine.TaskConflict):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.middleware("http")
async def request_logging(request:Request,call_next):
    proposed=request.headers.get('x-request-id','')
    rid=proposed if re.fullmatch(r'[A-Za-z0-9-]{1,64}',proposed) else uuid4().hex
    started=time.monotonic();status=500
    try:
        response=await call_next(request);status=response.status_code
        response.headers['X-Request-ID']=rid
        return response
    finally:
        user=getattr(request.state,'user',None)
        logging.getLogger('http').info('request',extra={'request_id':rid,'method':request.method,'path':request.url.path,
            'status_code':status,'duration_ms':round((time.monotonic()-started)*1000,1),'user_id':user['id'] if user else None})

# Production entry: build once, serve UI and API on the same origin.
app.mount('/assets',StaticFiles(directory=config.FRONTEND_DIST/'assets',check_dir=False),name='frontend-assets')
@app.get('/{path:path}',include_in_schema=False)
def frontend(path:str):
    if path.startswith('api/') or path in {'api','healthz','readyz'}:raise HTTPException(404,'接口不存在')
    base=config.FRONTEND_DIST.resolve()
    candidate=(base/path).resolve()
    try:candidate.relative_to(base)
    except ValueError:raise HTTPException(404,'文件不存在')
    if candidate.is_file():return FileResponse(candidate,headers={'Cache-Control':'no-store'} if candidate.name=='index.html' else {})
    index=base/'index.html'
    if not index.is_file():raise HTTPException(503,'前端尚未构建，请运行部署脚本 build')
    return FileResponse(index,headers={'Cache-Control':'no-store'})
