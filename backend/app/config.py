"""后端配置：全部可用环境变量覆盖，默认值面向本机开发（WSL 服务经 localhost 转发可达）"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
# 注意：Windows 本机已有多个 redis-server.exe 占用 6379/6380，WSL 的 redis 用 6399
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6399/0")

# 遥测键 TTL（秒）：超过 5s 无上报即视为离线（设计文档 4.2 心跳判定）
LIVE_TTL = int(os.getenv("LIVE_TTL", "5"))

# 已知设备清单（离线也要在大屏列出）
ROBOT_IDS = [s.strip() for s in os.getenv("ROBOT_IDS", "robot1").split(",")]

# Agent LLM（OpenAI 兼容网关，可在 .env 换任意服务商）
AGENT_BASE_URL = os.getenv("AGENT_BASE_URL", "https://api.deepseek.com")
AGENT_API_KEY = os.getenv("AGENT_API_KEY", "")
AGENT_MODEL = os.getenv("AGENT_MODEL", "deepseek-chat")

# VLM 视觉检测（M5）：与 Agent 同网关同 key，模型须支持图片输入（deepseek-flash 支持 text+image）
VLM_MODEL = os.getenv("VLM_MODEL", "deepseek-flash")

# 巡检图片目录：WSL 侧 snapshot_node 经 /mnt/f 写入，后端 HTTP 提供 /api/results/img/{name}
RESULTS_DIR = Path(os.getenv(
    "RESULTS_DIR",
    str(Path(__file__).resolve().parent.parent / "static" / "results"),
))

# MySQL（本机 MySQL80 服务，3306）
MYSQL_URL = os.getenv("MYSQL_URL", "")
if not MYSQL_URL:
    raise RuntimeError("请在 backend/.env 配置 MYSQL_URL，参照 .env.example")

MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID", "backend-consumer")
LOG_DIR = Path(os.getenv("LOG_DIR", str(Path(__file__).resolve().parents[1] / "var/logs")))
FRONTEND_DIST = Path(os.getenv("FRONTEND_DIST", str(Path(__file__).resolve().parents[2] / "frontend/dist")))
