# 智能巡检机器人系统 MVP 设计文档

> 阶段历史记录：下文描述该阶段的方案、测试数与未完成项，不等同于当前状态。当前入口、权限和持久分析实现请参阅 [项目首页](../README.md) 与 [最新验收说明](system-acceptance.md)。

> 架构模式：方案②（机器人自治 + 云端业务层）
> 版本：v0.4（待评审） | 日期：2026-11-07

---

## 1. 项目概述

以大模型 Agent 为交互入口、以 Gazebo 仿真机器人为执行终端的智能巡检系统。用户通过自然语言下达巡检指令，Agent 完成任务编排，机器人本地自治完成导航与避障，检测结果经 VLM 结构化后回传，支持实时大屏监控与报告导出。

**项目定位**：纯软件、零硬件。仿真机器人与真机的接口同构（同为 ROS2 topic/action），架构可平移至真实机器人。

### 1.1 MVP 范围（做什么）

- 单机器人（Gazebo 仿真 TurtleBot3 + office 场景）
- 聊天入口 → 主 Agent 意图识别 → 工具调用创建任务
- 任务下发（MQTT）→ 机器人导航（Nav2）→ 到点拍照 → VLM 检测 → 结果入库
- 实时遥测（位置/电量/状态）上云 + 大屏 SVG 平面图展示
- 异常闭环：遇障绕行失败 → blocked 事件 → Agent 向用户请示
- 续航评估（energy-aware dispatch）：下发前计算电量能否覆盖任务+回桩，不足时自动回桩等待或升级人工（换电池）
- 任务/结果管理后台 + Excel 导出

### 1.2 非目标（V2/V3，明确不做）

| 演进 | 内容 | 触发理由 |
|---|---|---|
| V2 | 定时巡检（APScheduler）、轨迹回放时间轴、LLM 巡检日报、边缘 VLM 推理（Jetson） | 业务需要 |
| V3 | 多机器人车队：接入 Open-RMF Dispatcher，任务投标分配、充电桩排队；续航不足时**改派其他机器人**（MVP 单机器人只能升级人工） | 规模化 |
| 不做 | 真机部署、SLAM 建图、激光避障算法自研（用 Nav2） | 超出纯软件边界 |

---

## 2. 总体架构

```
┌─────────────────────────── 云端（Windows/服务器）───────────────────────────┐
│  [Vue3 前端]         [主 Agent]              [FastAPI 后端]                 │
│   聊天窗              LangChain tool-calling   任务模块(状态机+DB)           │
│   大屏(SVG地图)       意图识别/请示/汇报        设备模块(注册/在线判定)        │
│   管理后台            VLM 检测调用             结果模块(图片+JSON+导出)       │
│        │              WebSocket/SSE            MQTT 消费服务    WebSocket 推送│
└────────┼──────────────────────┬───────────────────────┬────────────────────┘
         │                      │                       │
    HTTP/WS(前端↔后端)     HTTP(前端↔Agent)      MQTT(后端↔机器人) ← 系统边界
         │                      │                       │
┌────────┴──────────────────────┴───────────────────────┴────────────────────┐
│                        机器人侧（WSL Ubuntu-24.04, ROS2 Jazzy）              │
│  [task_runner] 任务状态机：收任务→发导航目标→到点→拍照→上报                    │
│  [nav2] 全局规划(A*)+局部重规划+代价地图 —— 几何题在这层解决，毫秒级            │
│  [bridge_node] rclpy↔MQTT 桥：遥测上行(JSON)、任务下行，独立线程防阻塞          │
│  [battery_node/camera] 电量模拟、快照服务                                    │
│  [Gazebo Harmonic] 物理引擎+传感器插件（现成，不开发）                        │
└─────────────────────────────────────────────────────────────────────────────┘
```

**核心设计原则**

1. 智能分层：几何决策（怎么绕）归机器人（Nav2，毫秒级）；业务决策（等不等/跳不跳）归 Agent（秒级，车已停稳才参与）
2. 控制层零 LLM：机器人侧全部确定性代码；LLM 只存在于后端之上
3. 断网自治：机器人可离线执行完已接收任务；网络恢复后事件补报
4. 仿真/真机同构：机器人侧对外只依赖 ROS2 标准接口，换真机不改后端

---

## 3. 技术选型

| 层 | 选型 | 说明 |
|---|---|---|
| 仿真 | Gazebo Harmonic 8.15 + TurtleBot3 模型 + rmf_demos office 地图（M1）→ 自绘工厂图（M2 起） | office 已跑通先不阻塞；工厂图用 Building Editor 一次绘制、三端（SDF 墙/占用图/SVG 平面图）同步导出，贴合巡检业务场景 |
| 机器人框架 | ROS2 Jazzy + rclpy + **Nav2**（`ros-jazzy-nav2-bringup`） | 导航白送，自研仅业务层 |
| 消息中间件 | Mosquitto（WSL 内）或 EMQX（Docker） | MVP 用 Mosquitto 即可 |
| 后端 | FastAPI + SQLAlchemy + MySQL 8 + Redis | 用户现有栈 |
| Agent | LangChain（tool-calling）+ 中转网关 API | 用户现有能力 |
| VLM | 任一多模态模型（经中转网关调用） | 图片→结构化 JSON |
| 前端 | Vue3 + Element Plus + SVG（大屏地图） | 不用 Three.js，SVG 足够 |
| 图片存储 | 本地磁盘目录（`data/snapshots/`），DB 存相对路径 | 不引入 MinIO |

---

## 4. 通信协议（系统边界，最重要的一节）

### 4.1 MQTT Topic 定义

| Topic | 方向 | QoS | 频率 | 用途 |
|---|---|---|---|---|
| `robot/{id}/task` | 后端→机器人 | 1 | 事件 | 任务下发/取消 |
| `robot/{id}/status` | 机器人→后端 | 0 | 1Hz | 遥测（位置/电量/状态） |
| `robot/{id}/event` | 机器人→后端 | 1 | 事件 | 状态机流转、blocked、done |
| `robot/{id}/result` | 机器人→后端 | 1 | 事件 | 巡检结果引用（图片走 HTTP 上传，MQTT 只传元数据） |

### 4.2 消息 Schema

**任务下发** `robot/{id}/task`：
```json
{
  "task_id": "T20261107-001",
  "action": "patrol",              // patrol | cancel
  "points": [
    {"seq": 1, "place": "server_room", "x": 3.2, "y": 1.5, "check": "机箱温度"}
  ]
}
```

**遥测** `robot/{id}/status`（1Hz）：
```json
{
  "ts": 1793966705611,
  "pos": {"x": 3.21, "y": 1.48, "yaw": 1.57},
  "battery": 82.5,
  "mode": "navigating",            // idle|navigating|waiting|inspecting|charging|error
  "current_task": "T20261107-001"
}
```

**事件** `robot/{id}/event`：
```json
{
  "task_id": "T20261107-001",
  "event": "blocked",              // accepted|point_arrived|blocked|stuck|task_done|task_failed
  "point_seq": 2,
  "detail": {"reason": "no_path", "pos": {"x": 2.0, "y": 3.1}},
  "ts": 1793966740000
}
```

**结果** `robot/{id}/result`：
```json
{
  "task_id": "T20261107-001",
  "point_seq": 1,
  "place": "server_room",
  "image_url": "/api/results/img/T20261107-001_1.jpg",
  "captured_at": 1793966750000
}
```

### 4.3 任务状态机（后端与机器人共同遵守）

```
CREATED ──下发──> DISPATCHED ──机器人接单──> ACCEPTED
   ACCEPTED ──> NAVIGATING ──到点──> INSPECTING ──结果已传──> REPORTING ──> DONE
   NAVIGATING ──障碍绕行失败──> BLOCKED ──(用户决策:重试/跳过)──> NAVIGATING / REPORTING
   CREATED ──续航评估不足但够到桩──> DEFERRED(回桩充电, 电量≥阈值自动转 DISPATCHED)
   任一状态 ──取消──> CANCELED
   ACCEPTED 起 300s 无事件 ──后端看门狗──> TIMEOUT
   续航评估连桩都够不到 ──> 任务保持 CREATED, 发 low_battery 事件 → Agent 请示人工(换电池/改期)
```

每次流转写 `task_event` 表（审计日志，对应 JD"日志记录"）。

---

## 5. 模块设计

### 5.1 机器人侧（ROS2 包 `inspection_robot`，纯 rclpy）

| 节点 | 职责 | 关键点 |
|---|---|---|
| `task_runner.py` | 任务状态机：收 MQTT 任务 → 逐点发 NavigateToPose action → 到点调拍照服务 → 发事件 | 断网时继续执行当前任务；重启后不恢复（MVP 简化） |
| `bridge_node.py` | rclpy ↔ MQTT 双向桥：订阅 /odom+battery → 1Hz 组 JSON 发 status；订阅 task topic → 转发给 task_runner | **paho 回调与 ROS executor 分线程**（防同步阻塞坑） |
| `battery_node.py` | 假电量：`battery -= 0.0005 * dt`，导航中双倍消耗，<20% 自动上报 low_battery 事件 | 纯演示用 |
| `snapshot_node.py` | 订阅 /camera/image_raw，收到拍照请求存 JPEG 到共享目录，HTTP 由后端取 | 用 sensor_msgs 压缩图像或 cv_bridge |
| Nav2 | 全局+局部规划、代价地图、恢复行为（遇障后退重试是 Nav2 recovery 自带） | 只配置不开发 |

复用资产：office 地图（rmf_demos_maps）、TurtleBot3 URDF、Gazebo 插件。
自研点（面试作品）：task_runner 状态机、bridge_node、battery_node，合计约 500 行。

### 5.2 后端（FastAPI）

| 模块 | 接口 | 说明 |
|---|---|---|
| 任务 | `POST /api/tasks`（创建+下发）、`GET /api/tasks`、`GET /api/tasks/{id}`、`POST /api/tasks/{id}/cancel`、`POST /api/tasks/{id}/decision`（blocked 后重试/跳过） | 状态机落库；看门狗 asyncio 任务扫超时 |
| 设备 | `GET /api/robots`、`GET /api/robots/{id}/live`（Redis 最新遥测） | 心跳超时 5s 判离线 |
| 结果 | `GET /api/results?task_id=`、`GET /api/results/img/{name}`、`GET /api/results/export?task_id=`（Excel） | 图片本地存储 |
| 遥测消费 | MQTT 订阅服务（lifespan 启动）：status → Redis(`robot:{id}:live`) + MySQL 批量(10s)；event → 状态机流转；result → 触发 VLM 检测 | 独立协程，断线重连 |
| 实时推送 | `WS /ws/telemetry` | 大屏订阅，转发 Redis 最新状态 |
| VLM | 内部服务：图片+检查项 → prompt → 结构化 `{normal: bool, reading: "...", anomaly: "..."}` | 失败重试 1 次，结果入库 |
| 续航评估 | 内部服务 `energy_assessor`（**确定性计算，非 LLM**）：导航图算里程（当前位置→各点→最近桩）× 每米耗电 + 作业耗电 + 20% 安全余量，对比当前电量 → `feasible / can_reach_dock / cannot_move` | 任务创建时强制前置校验；DEFERRED 自动恢复由遥测触发 |

### 5.3 主 Agent（FastAPI 独立路由 `/api/chat`，SSE 流式）

工具集（Agent 可调 10 个，全部指向后端 REST，Pydantic 参数校验白名单；另有系统事件回调 5 个，非 Agent 主动调用）：

```python
# 写操作 4（危险类需二次确认）
create_patrol_task(points: list[Place], priority)   # 后端保留续航硬校验作兜底护栏
cancel_task(task_id)                                # 需用户确认
decide_blocked(task_id, action: retry|skip)         # 需用户确认
decide_low_battery(task_id, action: manual_swap|reschedule)  # 需用户确认
# 读操作 6（自由调用）
query_robot_status(robot_id=None)
query_task_status(task_id)
list_tasks(status=None, time_range=None)    # 必选：cancel/decide 需要 task_id，用户说"取消刚才那个任务"时靠它做指代消解
query_results(task_id=None, place=None, time_range=None)
check_task_feasibility(robot_id, points)  # 返回 feasible/can_reach_dock/cannot_move + 里程/耗电明细
list_places()                             # 地点枚举，意图匹配失败时的兜底澄清
# 系统事件回调 5（后端推给 Agent，Agent 组织语言请示/汇报，非用户触发）
report_blocked(task_id, point, options)
report_low_battery(task_id, level, options)
report_task_done(task_id)                 # 汇总 VLM 结果主动汇报
report_task_failed(task_id, reason)       # 覆盖 task_failed 与看门狗 TIMEOUT，否则失败后无人通知用户
report_deferred_resumed(task_id)          # DEFERRED 充到阈值自动下发时主动告知，兑现"先充电再去"的承诺
```

> 注：回调分两类——请示类（blocked/low_battery，需等用户决策）、通知类（done/failed/resumed，仅汇报）。
> **刻意不做**：update_task（改点=取消+重建）、历史轨迹查询（V2 回放）、跨机器人改派（V3 车队）、系统配置修改（非 Agent 职责）。

**编排原则：流程性决策由 Agent loop 驱动，计算交给确定性工具。** 续航评估示例 trace（用户可见）：

```
用户: "去配电房和服务器机房巡检"
  → Agent: list_places()                        [地点确认]
  → Agent: query_robot_status()                 [1号车, 电量34%]
  → Agent: check_task_feasibility(...)          [需耗电41%, cannot_complete → can_reach_dock]
  → Agent 向用户请示: "电量不够跑完两点，建议先充电(约25分钟)再自动执行，或减为一个点？"
  → 用户: "先充电再去"
  → Agent: create_patrol_task(...)              [后端置 DEFERRED, 充到阈值自动下发]
```

**过程可见性（强制要求）**：`/api/chat` 的 SSE 事件流定义固定协议，前端逐条渲染：

```
event: reasoning      data: {text}                    # 意图解析/中间推理，可折叠
event: tool_call      data: {id, tool, args}          # 每次工具调用（含参数）
event: tool_result    data: {id, ok, summary, detail} # 返回摘要+可展开明细
event: ask_user       data: {question, options[]}     # 请示/二次确认
event: message        data: {text}                    # 最终回复
```

工具调用轨迹同步落库（`agent_trace` 表：session_id, seq, event_type, payload, ts），
聊天界面默认折叠为"执行过程 (5 步)"，点击展开——用户随时能审计 Agent 到底干了什么。

行为规则：
- 意图→工具映射，歧义必须追问（"检查下机房"→ 哪个机房？今天还是每天？）；地点不在 `list_places` 枚举内时禁止猜测，必须澄清
- 写操作（cancel/decide_*）先复述意图取得用户肯定再执行；create_patrol_task 若被 Agent 跳过评估直接调用，后端护栏仍会拦截
- 收到 report_* 事件 → 组织语言向用户请示 → 用户决策转对应 decide 接口
- 任务完成 → 汇总 VLM 结果为人话汇报
- 负面清单（永不注册为工具，仅作架构约束记录）：直发 MQTT 报文、直控轮子速度、LLM 规划路径、触发物理动作类指令——凡绕过状态机/校验/审计直达执行层的能力一律不给 Agent。工具边界即 Agent 权限边界

### 5.4 前端（Vue3）

| 页面 | 内容 |
|---|---|
| 聊天 | SSE 流式对话；reasoning/tool_call/tool_result 按事件协议渲染，执行过程默认折叠可展开；ask_user 渲染为选项按钮 |
| 大屏 | SVG 平面图（office 地图坐标直接换算）+ 机器人点位（WS 1Hz 刷新）+ 轨迹线 + 电量/状态卡片 + 告警弹出 |
| 任务管理 | 列表（状态标签）、详情（事件时间线）、取消/决策按钮 |
| 结果中心 | 图库（缩略图+VLM 结论）、Excel 导出 |

### 5.5 人机交互设计

**原则：用户输入永远是自然语言，系统内无必填表单；所有交互点 = 一个 SSE 事件类型或一个 REST 接口。**

A. 聊天内（Agent 驱动）

| # | 交互点 | 触发 | 事件/工具 |
|---|---|---|---|
| 1 | 意图澄清 | 地名不在 list_places 枚举、范围含糊 | ask_user（选项=候选地点） |
| 2 | 续航方案确认 | check_task_feasibility → can_reach_dock | ask_user（先充电/减点/改期） |
| 3 | 写操作二次确认 | cancel/decide_* 执行前复述 | ask_user（肯定/否定） |
| 4 | blocked 请示 | report_blocked | ask_user（重试/跳过） |
| 5 | low_battery 请示 | report_low_battery | ask_user（manual_swap/reschedule）；用户称"换好了"后 Agent 调 query_robot_status **核实电量回升才继续，不轻信口头声明** |
| 6 | 通知类汇报 | report_task_done/failed/resumed | message（非阻塞，无需回应） |
| 7 | 执行中打断 | 用户改主意 | 直接发新消息覆盖；前端"停止"按钮中断当前 SSE 流与 Agent loop |

B. 大屏/后台（不经 Agent，直连同款 REST）

| # | 交互点 | 说明 |
|---|---|---|
| 8 | 告警弹窗+决策按钮 | blocked/low_battery 弹出，按钮调 decide_*——与聊天决策同接口同审计，免切窗口 |
| 9 | 手动建任务 | 全系统唯一表单（source=manual）：下拉选 list_places 地点+排序 |
| 10 | 换电池工单标记 | manual_swap 后大屏显示"等待换电池"，运维点"已完成"仅作审计标记；**实际放行靠遥测检测电量回升，不靠按钮**（防误点） |

C. 全局策略（写死，不留给实现临场决定）

- **请示永不超时自动执行**：用户不回应，任务停在 BLOCKED/CREATED，只重复提醒（安全优先，无默认选项）
- **多请示并存**：每个 ask_user 携带 task_id 渲染独立卡片；已决请示 Agent 不再重复问
- **渠道等价**：聊天/大屏/后台的决策走同一状态机接口、写同一条 task_event；Agent 能感知"用户已在大屏决策"，避免重复请示

---

## 6. 数据库设计（MySQL，6 表）

```sql
robot(id, name, model, status ENUM(online/offline/error), battery, pos_x, pos_y,
      last_heartbeat, created_at)

task(id, task_no, source ENUM(chat/scheduled/manual), status ENUM(状态机全部态,含DEFERRED),
     priority, created_by, created_at, dispatched_at, started_at, finished_at)
-- 充电桩坐标与能耗参数(每米耗电%/作业耗电%/安全余量%)放系统配置表 config, 不建实体表

task_point(id, task_id FK, seq, place, x, y, check_item,
     status ENUM(pending/navigating/arrived/inspected/blocked/skipped))

task_event(id, task_id, robot_id, event, point_seq, detail JSON, created_at)
     -- 状态机审计日志，只增不改

telemetry(id, robot_id, ts, pos_x, pos_y, yaw, battery, mode,
     INDEX(robot_id, ts))     -- 时序，批量写，V2 回放数据源

inspection_result(id, task_id, point_seq, place, image_path,
     vlm_output JSON, normal BOOL, created_at)

agent_trace(id, session_id, seq, event_type ENUM(reasoning/tool_call/tool_result/ask_user/message),
     tool_name, payload JSON, ts)   -- Agent 执行过程审计，聊天窗口"执行过程"数据源
```

Redis 键：`robot:{id}:live`（最新遥测，TTL 5s）、`task:{id}:lock`（下发互斥）。

---

## 7. 部署形态

```
Windows:   后端 venv(FastAPI×2: API+MQTT消费同进程) + 前端 dev server
WSL2:      ROS2 Jazzy + Gazebo + inspection_robot 包 + Mosquitto
共享目录:  WSL data/snapshots ↔ Windows 后端挂载读取（或后端经 HTTP 从 WSL 小服务取图）
模型:      统一走大模型中转网关（LLM + VLM 各一配置项）
```

## 8. MVP 验收标准（端到端场景）

1. **主链路**：聊天输入"去服务器机房检查机箱温度" → Agent 创建任务 → 机器人出发 → 到点拍照 → VLM 返回"温度读数正常" → Agent 汇报 → 结果页可查、可导出
2. **实时性**：大屏机器人位置 1Hz 移动，任务事件时间线与机器人日志一致
3. **异常闭环**：在机器人路径上放置障碍 → 机器人绕行 → 堵死时上报 blocked → Agent 请示 → 用户选"跳过" → 任务继续
4. **鲁棒性**：kill Mosquitto 30s 再恢复，遥测补报、任务不丢（QoS1）
5. **断网自治**：断开 WSL→Windows 网络，机器人完成当前点后停车等待，恢复后事件补报

## 9. 里程碑（垂直切片）

| # | 交付物 | 可见成果 |
|---|---|---|
| M1 通信链路 | Gazebo 小车 + bridge_node + Mosquitto + 后端消费 + Redis | `curl /api/robots/1/live` 看到位置跳动 |
| M2 任务闭环 | 任务三表 + REST 下发 + task_runner + Nav2 导航 + 拍照入库 + **工厂地图替换**（Building Editor 导出，list_places 点位改为车间/配电房/服务器机房等） | 命令行发任务，小车跑起来，图片入库 |
| M3 智能层 | Agent 工具链 + VLM 检测 + blocked 请示链路 | 聊天框完成场景 1 和 3 |
| M4 前端 | 大屏 + 任务/结果管理页 | 完整演示形态 |
| M5 打磨 | 超时看门狗、Excel 导出、验收标准逐条过 | MVP 完成 |

每个里程碑独立可验证，完成即演示。

## 10. 风险清单

| 风险 | 缓解 |
|---|---|
| Nav2 在 WSL 内启动慢/配置复杂 | M2 前先用 rmf_demos 同款 world 验证；兜底方案：自研 P 控制导航（已验证可行） |
| WSL↔Windows 的 MQTT/文件共享网络细节 | Mosquitto 监听 0.0.0.0；Windows 防火墙放行；取图走 WSL 侧轻量 HTTP 服务 |
| rclpy executor 阻塞（paho 回调） | bridge_node 设计即分线程，M1 压测验证 |
| Building Editor→SDF 转换在 Jazzy/Harmonic 兼容问题 | 兜底：手写 SDF 墙盒 + 同一坐标列表脚本生成 pgm 占用图（约150行，三端一致性反而更强）；再不行退回 office 地图 |
| VLM 读表计准确率 | 检查项限定为"有无人员/灯态/明显异常"三类低难度语义任务，不承诺精确读数 |
