"""POST /api/chat：SSE 流式对话（设计文档 5.3 事件协议）。

事件流：event: session → reasoning/tool_call/tool_result/ask_user/message。
ask_user 后本轮流结束；客户端带同一 session_id 发下一条消息恢复（确认/否决/改口）。
"""
import json
import uuid
import threading

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from .. import db

from . import loop

router = APIRouter(prefix="/api")
_SESSION_LOCKS={}


class ChatIn(BaseModel):
    session_id: str = Field(default="",pattern=r"^([a-f0-9]{32})?$")
    message: str = Field(min_length=1,max_length=8000)


@router.post("/chat")
def chat(body: ChatIn, request: Request):
    sid = body.session_id or uuid.uuid4().hex
    principal=request.state.user
    with db.transaction() as conn:
        conn.execute(db.text("INSERT IGNORE INTO agent_session_owner(session_id,user_id) VALUES(:s,:u)"),{"s":sid,"u":principal["id"]})
        owner=conn.execute(db.text("SELECT user_id FROM agent_session_owner WHERE session_id=:s"),{"s":sid}).scalar_one()
        if owner!=principal["id"]:raise HTTPException(403,"该会话不属于当前账号")
    text = body.message.strip()
    if not text:
        raise HTTPException(422, "message 不能为空")

    def sse():
        lock=_SESSION_LOCKS.setdefault(sid,threading.Lock())
        if not lock.acquire(blocking=False):
            yield 'event: message\ndata: '+json.dumps({'text':'当前会话正在执行，请稍后再试'},ensure_ascii=False)+'\n\n'
            return
        try:
            yield f"event: session\ndata: {json.dumps({'session_id': sid})}\n\n"
            for etype, payload in loop.run_turn(sid, text, principal=principal):
                yield (f"event: {etype}\n"
                       f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n")
        finally:
            lock.release()

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
