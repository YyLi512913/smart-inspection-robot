"""Ollama tool-calling 冒烟测试：验证指定模型能否正确发起 function call。

用法: python test_ollama_tools.py [model]
模拟巡检 Agent 的真实场景：给 create_patrol_task 工具，输入"去服务器机房巡检"，
期望模型返回 tool_calls 而不是纯文本。
"""
import json
import sys
import urllib.request

MODEL = sys.argv[1] if len(sys.argv) > 1 else "qwen3.5:2b"
URL = "http://localhost:11434/api/chat"

TOOLS = [{
    "type": "function",
    "function": {
        "name": "create_patrol_task",
        "description": "创建并下发巡检任务，机器人按点位顺序导航巡检",
        "parameters": {
            "type": "object",
            "properties": {
                "points": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "巡检地点英文名列表，按顺序执行",
                },
                "priority": {"type": "integer", "description": "优先级 0-10"},
            },
            "required": ["points"],
        },
    },
}]

payload = {
    "model": MODEL,
    "messages": [{"role": "user", "content": "去服务器机房巡检一下"}],
    "tools": TOOLS,
    "stream": False,
    "options": {"temperature": 0},
}

req = urllib.request.Request(URL, data=json.dumps(payload).encode(),
                             headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=300) as resp:
    out = json.loads(resp.read())

msg = out.get("message", {})
if msg.get("tool_calls"):
    tc = msg["tool_calls"][0]["function"]
    print(f"PASS [{MODEL}] tool={tc['name']} args={tc['arguments']}")
else:
    print(f"FAIL [{MODEL}] 未发起工具调用，回复: {msg.get('content', '')[:200]}")
