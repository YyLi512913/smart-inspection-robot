"""VLM 视觉检测（设计文档 5.2/5.4）：图片 + 检查项 → 结构化结论 {normal, reading, anomaly}。

- 与 Agent 同网关同 key（config.AGENT_BASE_URL/AGENT_API_KEY），模型 VLM_MODEL 须支持图片输入
- 网关无法访问私网 URL：本地图经 RESULTS_DIR 直读、外地图 urllib 下载，统一转 base64 data URL 上传
- 失败重试 1 次（共 2 次尝试），最终失败返回 error，由调用方入库（normal=NULL）
"""
import base64
import json
import logging
import math
import re
import time
import urllib.request

from . import config

logger = logging.getLogger("vlm")

_PROMPT = """你是巡检视觉检测助手。图片拍摄目标：{place}。检查说明：{check}。
固定检查规则（是判定标准，不代表画面中的实际状态）：
{rules}

仅依据照片中可见内容判断，不能猜测被遮挡或读不清的部件。
每一条规则都返回一个 checks 条目；数值检查必须填写 value 和 unit。
看不清、目标未入镜、无法确认时 status="unreadable"，不要判为正常。
仅输出 JSON：
{{"normal": true或false或null, "reading": "简短观察", "anomaly": "异常说明或空字符串",
"checks": [{{"code":"规则code","status":"normal或abnormal或unreadable",
"value":null或数字,"unit":"单位或空字符串","evidence":"照片中具体可见证据"}}]}}
"""

def _image_to_data_url(image_url: str) -> str:
    """图片 → base64 data URL。本后端静态图直读磁盘，其余走 HTTP 下载。"""
    name = image_url.rstrip("/").split("/")[-1].split("?")[0]
    local = (config.RESULTS_DIR / name).resolve()
    if local.is_file() and str(local).startswith(str(config.RESULTS_DIR.resolve())):
        data = local.read_bytes()
    elif image_url.startswith('/api/results/img/'):
        raise FileNotFoundError(f'巡检图片不存在或已被移除: {name}')
    else:
        with urllib.request.urlopen(image_url, timeout=15) as resp:
            data = resp.read()
    suffix = local.suffix.lower()
    mime = {".png": "image/png", ".webp": "image/webp"}.get(suffix, "image/jpeg")
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def _parse(text: str, checks=None) -> dict:
    match=re.search(r"\{.*\}",text,re.S)
    if not match:raise ValueError("回复中无 JSON")
    obj=json.loads(match.group(0))
    if "normal" not in obj or (obj["normal"] is not None and type(obj["normal"]) is not bool):
        raise ValueError("normal 必须为布尔值或 null")
    output={"normal":obj["normal"],"reading":str(obj.get("reading",""))[:255],
            "anomaly":str(obj.get("anomaly",""))[:255],"checks":[]}
    if not checks:return output
    reported={item.get("code"):item for item in obj.get("checks",[]) if isinstance(item,dict)}
    for rule in checks:
        item=reported.get(rule["code"],{})
        status=item.get("status","unreadable")
        if status not in {"normal","abnormal","unreadable"}:status="unreadable"
        value=item.get("value")
        unit=str(item.get("unit",""))
        evidence=str(item.get("evidence","未返回可核对证据"))[:200]
        if rule["kind"]=="visual" and not str(item.get("evidence","")).strip():
            status="unreadable"
        if rule["kind"]=="numeric":
            if type(value) not in (float,int) or not math.isfinite(value) or unit!=rule.get("unit"):
                status="unreadable"
            else:
                status="normal" if rule["min"]<=value<=rule["max"] else "abnormal"
                if status=="abnormal":
                    evidence=f"读数 {value} {unit} 超出 [{rule['min']}, {rule['max']}] {unit}"
        output["checks"].append({"code":rule["code"],"name":rule["name"],"status":status,
                                 "value":value if type(value) in (float,int) and math.isfinite(value) else None,
                                 "unit":unit,"evidence":evidence})
    states=[item["status"] for item in output["checks"]]
    output["normal"]=False if "abnormal" in states else None if "unreadable" in states else True
    problems=[item["name"]+"："+item["evidence"] for item in output["checks"] if item["status"]=="abnormal"]
    output["anomaly"]="；".join(problems)[:255]
    return output


def detect(image_url: str, place: str, check_item: str, checks=None) -> dict:
    """单图检测。返回 {normal, reading, anomaly, raw, error}；失败时 normal=None。"""
    check = check_item or "检查是否有明显异常"
    try:
        data_url = _image_to_data_url(image_url)
    except Exception as e:  # noqa: BLE001 图片获取失败也计入 error
        return {"normal": None, "reading": "", "anomaly": "", "raw": None,
                "error": f"图片获取失败: {e}"}

    url = config.AGENT_BASE_URL.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": config.VLM_MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": _PROMPT.format(place=place or "未知点位", check=check, rules=json.dumps(checks or [],ensure_ascii=False))},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]}],
        "temperature": 0.1,
        # 不设 max_tokens：deepseek-flash 是推理模型，思考+正文共用预算，截断会导致正文为空
    }).encode("utf-8")

    last_err = ""
    for attempt in range(2):  # 失败重试 1 次（设计文档 5.4）
        try:
            req = urllib.request.Request(url, data=body, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config.AGENT_API_KEY}",
            })
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            msg = data["choices"][0]["message"]
            # 推理模型兜底：正文为空时尝试从 reasoning_content 提取结论
            text = (msg.get("content") or "") or (msg.get("reasoning_content") or "")
            out = _parse(text,checks)
            out["raw"] = {"response":msg,"checks":out["checks"]}
            out["error"] = ""
            logger.info("VLM 检测完成 place=%s normal=%s reading=%s",
                        place, out["normal"], out["reading"])
            return out
        except Exception as e:  # noqa: BLE001 网关偶发 5xx/超时
            last_err = str(e)[:255]
            logger.warning("VLM 第 %s 次调用失败: %s", attempt + 1, e)
            if attempt == 0:
                time.sleep(2)
    return {"normal": None, "reading": "", "anomaly": "", "raw": None,
            "error": f"VLM 调用失败: {last_err}"}
