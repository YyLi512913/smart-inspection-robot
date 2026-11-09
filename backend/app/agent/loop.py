"""主 Agent 编排（设计文档 5.3）：自写 OpenAI 兼容 tool-calling 循环，产出 SSE 事件流。

- 不引 LangChain：循环逻辑就 100 行，事件协议、确认拦截、落库全部自控
- 会话状态进程内 dict（MVP 单进程）；ask_user 暂停本轮，客户端下一条消息带 session_id 恢复
- 危险写工具（cancel/decide_*）框架层强制二次确认，不依赖 LLM 自觉
- 每个事件同步落 agent_trace（seq 会话内递增），聊天"执行过程"数据源
"""
import json
import logging
import time
import urllib.error
import urllib.request

from .. import config, db
from . import tools

logger = logging.getLogger("agent.loop")

MAX_ITER = 8  # 单轮最多 LLM 往返次数（防失控）

# 会话状态：session_id -> {"messages": [...], "pending": {...}|None, "seq": int}
SESSIONS: dict[str, dict] = {}

_CONFIRM = {"确认执行", "确认", "是", "是的", "好", "好的", "行", "执行", "同意",
            "确定", "可以", "yes", "y"}
_DENY = {"取消", "不", "不用", "不要", "别", "算了", "否", "no", "n"}

_SYSTEM_BASE = """你是智能巡检机器人平台的中控 Agent，通过工具管理办公室巡检机器人（robot1）。

当前系统实时状态（每轮自动刷新）：
{context}

行为规则（强制）：
1. 一切操作只能通过提供的工具。你没有直接控制机器人的能力，工具边界即权限边界。
2. 建任务流程：涉及地点先 list_places 确认 → query_robot_status 查电量位置 →
   check_task_feasibility 评估续航 → 把方案用人话告诉用户（含电量与评估结论）→ 用户认可后 create_patrol_task。
   can_reach_dock 时说明"任务将等待充电，当前尚未接入主动回桩，充电后会重新评估续航再下发"；cannot_move 时告知需人工换电池，不要建任务。
3. 地点必须来自 list_places 枚举。用户说的地名映射不到唯一地点时，列出候选让用户选，禁止猜测。
4. 任何歧义（哪个任务、哪个地点、什么范围）必须追问澄清。
5. cancel_task / decide_blocked / decide_low_battery 是危险操作：先用人话复述你要做什么、为什么，
   得到用户明确同意后才调用；系统还会再弹一次确认。一次只调用一个写工具。
6. 用户说"电池换好了"不可轻信：调 decide_low_battery(manual_swap) 或 query_robot_status 核实电量确实回升。
7. 用户询问任务进展/结果用 query_task_status / list_tasks / query_results，据实回答，不编造。
8. 涉及设备时先 list_equipment，按真实 equipment_id 或 observation_id 编排；不要猜测坐标。巡检车间等有设备的区域会展开为多台设备观察点，必须向用户说明设备、点位数量与检查项。检查规则来自台账，不允许用模型改写标准。
9. 始终用简体中文回复，口语化、简洁，先给结论再给依据。"""


def _context_block() -> str:
    """请示类简化（设计决策⑤）：把实时电量与待处理任务注入 system prompt，
    让 Agent 主动请示，不做独立推送通道。"""
    parts = []
    for rid in sorted(set(config.ROBOT_IDS)):
        live = tools._live(rid)
        if live:
            parts.append(f"- {rid}：在线，电量 {live.get('battery', '?')}%，"
                         f"位置 ({live.get('pos', {}).get('x', '?')}, "
                         f"{live.get('pos', {}).get('y', '?')})")
        else:
            parts.append(f"- {rid}：离线（无实时遥测）")
    rows = db.query(
        "SELECT task_no, robot_id, status FROM task "
        "WHERE status IN ('BLOCKED','CREATED','DEFERRED') ORDER BY id DESC LIMIT 10")
    if rows:
        parts.append("- 待处理任务：" + "、".join(
            f"{r['task_no']}({r['robot_id']},{r['status']})" for r in rows))
    else:
        parts.append("- 待处理任务：无")
    # 主动汇报简化版 report_task_done（M5）：最近完成任务 + VLM 异常摘要注入上下文
    done = db.query_one(
        "SELECT task_no, finished_at FROM task WHERE status='DONE' "
        "ORDER BY finished_at DESC, id DESC LIMIT 1")
    if done:
        abn = db.query(
            "SELECT place, reading, anomaly FROM inspection_result "
            "WHERE task_no=:n AND normal=0", {"n": done["task_no"]})
        total = db.query_one(
            "SELECT COUNT(*) AS c FROM inspection_result WHERE task_no=:n",
            {"n": done["task_no"]})
        line = (f"- 最近完成任务：{done['task_no']}（{done['finished_at']}，"
                f"检测 {total['c'] if total else 0} 点，")
        line += (f"异常 {len(abn)} 处：" + "；".join(
            f"{a['place']}：{a['anomaly'] or a['reading']}" for a in abn)
            if abn else "全部正常")
        parts.append(line + "。用户进入会话或询问进展时应主动汇报此结果。")
    return "\n".join(parts)


def _llm(messages: list[dict]) -> dict:
    """OpenAI 兼容非流式调用（urllib，免新依赖）。返回 choices[0].message。"""
    url = config.AGENT_BASE_URL.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": config.AGENT_MODEL,
        "messages": messages,
        "tools": tools.TOOL_SCHEMAS,
        "tool_choice": "auto",
        "temperature": 0.2,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.AGENT_API_KEY}",
    })
    last_err: Exception | None = None
    backoff = (2, 5)  # 上游（DeepSeek）偶发 503，退避重试最多 3 次
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code < 500:
                raise
        except urllib.error.URLError as e:
            last_err = e
        if attempt < 2:
            logger.warning("LLM 调用失败(%s)，%ss 后重试", last_err, backoff[attempt])
            time.sleep(backoff[attempt])
    raise last_err


def _emit(sid: str, etype: str, payload: dict, tool_name: str = "") -> tuple:
    """产出一个 SSE 事件并落 agent_trace。"""
    sess = SESSIONS[sid]
    seq = sess["seq"]
    sess["seq"] = seq + 1
    try:
        db.execute(
            "INSERT INTO agent_trace (session_id, seq, event_type, tool_name, payload) "
            "VALUES (:s, :q, :e, :t, :p)",
            {"s": sid, "q": seq, "e": etype, "t": tool_name,
             "p": json.dumps(payload, ensure_ascii=False, default=str)})
    except Exception:  # noqa: BLE001 审计落库失败不阻断对话
        logger.exception("agent_trace 落库失败 sid=%s seq=%s", sid, seq)
    return (etype, payload)


def _interpret(text: str) -> str | None:
    t = text.strip().rstrip("。！!.")
    if t in _CONFIRM:
        return "confirm"
    if t in _DENY:
        return "deny"
    return None


def run_turn(sid: str, text: str, principal=None):
    """处理一条用户消息，生成 (event_type, payload) 序列。"""
    sess = SESSIONS.setdefault(sid, {"messages": [], "pending": None, "seq": 0})

    pending = sess["pending"]
    if pending:
        # 上一轮停在危险操作确认：先消化本次回复
        sess["pending"] = None
        decision = _interpret(text)
        if decision == "confirm":
            yield _emit(sid, "reasoning", {"text": f"用户已确认，执行 {pending['tool']}"})
            result = tools.execute_tool(pending["tool"], pending["args"], principal=principal)
            yield _emit(sid, "tool_result", {"id": pending["id"], **result},
                        pending["tool"])
            sess["messages"].append({
                "role": "tool", "tool_call_id": pending["id"],
                "content": json.dumps(result, ensure_ascii=False, default=str)})
        else:
            reason = "用户否决" if decision == "deny" else "用户未确认，视为否决"
            sess["messages"].append({
                "role": "tool", "tool_call_id": pending["id"],
                "content": json.dumps({"ok": False, "summary": f"{reason}，操作未执行",
                                       "detail": None}, ensure_ascii=False)})
            if decision is None:
                sess["messages"].append({"role": "user", "content": text})
    else:
        sess["messages"].append({"role": "user", "content": text})

    for _ in range(MAX_ITER):
        messages = ([{"role": "system",
                      "content": _SYSTEM_BASE.format(context=_context_block())}]
                    + sess["messages"])
        try:
            msg = _llm(messages)
        except Exception as e:  # noqa: BLE001 LLM 故障必须让用户看见而不是静默断流
            logger.exception("LLM 调用失败")
            yield _emit(sid, "message", {"text": f"模型调用失败：{e}"})
            return

        tool_calls = msg.get("tool_calls") or []
        content = (msg.get("content") or "").strip()

        if tool_calls:
            if content:
                yield _emit(sid, "reasoning", {"text": content})
            sess["messages"].append({
                "role": "assistant", "content": msg.get("content"),
                "tool_calls": tool_calls})
            for i, tc in enumerate(tool_calls):
                fn = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                yield _emit(sid, "tool_call", {"id": tc["id"], "tool": fn,
                                               "args": args}, fn)
                if fn in tools.DANGEROUS:
                    # 截断 assistant 消息只含到本条为止的调用，暂停等确认
                    sess["messages"][-1]["tool_calls"] = tool_calls[: i + 1]
                    sess["pending"] = {"id": tc["id"], "tool": fn, "args": args}
                    yield _emit(sid, "ask_user", {
                        "question": tools.confirm_question(fn, args),
                        "options": ["确认执行", "取消"],
                        "tool": fn, "args": args}, fn)
                    return
                result = tools.execute_tool(fn, args, request_key=f"{sid}:{tc['id']}", principal=principal)
                yield _emit(sid, "tool_result", {"id": tc["id"], **result}, fn)
                sess["messages"].append({
                    "role": "tool", "tool_call_id": tc["id"],
                    "content": json.dumps(result, ensure_ascii=False, default=str)})
            continue

        reply = content or "（没有可回复的内容）"
        sess["messages"].append({"role": "assistant", "content": reply})
        yield _emit(sid, "message", {"text": reply})
        return

    yield _emit(sid, "message", {"text": "已达到最大执行步数，请换个说法再试。"})
