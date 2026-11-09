# 第一阶段：任务与通信可靠性

> 阶段历史记录：下文描述该阶段的方案、测试数与未完成项，不等同于当前状态。当前入口、权限和持久分析实现请参阅 [项目首页](../README.md) 与 [最新验收说明](system-acceptance.md)。

完成日期：2026-11-09。导航仍使用现有 RMF 执行适配器，Nav2 与实时相机为后续阶段。

## 已完成的行为

1. 同一机器人最多保留一个业务任务占用。新任务遇到占用返回 HTTP 409；当前阶段不提供任务排队。
2. 创建任务、更新状态、写审计、创建下行指令在同一 MySQL 事务内完成。发送循环只读已提交的 mqtt_command。
3. 下行指令有 command_id；每 5 秒重发未确认指令，使用原编号。人工重试阻塞任务会生成新编号，旧代指令不再改变状态。
4. 机器人 SQLite 日志记录已接收、拒绝及结束的指令。重复报文不重启执行，被拒绝的指令也不能在空闲后突然开始。
5. 机器人事件和结果先写磁盘；桥接器一次发送队首一条，后端事务提交后返回应用确认。收到确认才删除，因此后端重启、数据库暂不可用或丢失确认均可补报。
6. 到点事件与拍照请求一同写入 SQLite 事务。拍照节点从磁盘队列取请求，结果入队与请求完成标记原子提交。
7. 后端严格校验设备归属、指令代次、点位执行顺序和状态前置条件。重复事件只记一次；旧代照片不能覆盖新一次到点结果。
8. 取消或看门狗超时会写入撤销指令，并保留机器人占用，直到执行节点确认取消。任务详情展示确认等待、发送次数和确认时间。
9. POST /api/tasks 支持 Idempotency-Key；同 key、同参数返回原任务，参数不同返回 409。Agent 以会话号与工具调用号生成内部幂等键。
10. 等待充电的任务恢复时，重新核对当前位置、电量及需求，不能仅凭固定电量阈值下发。
11. 当前任务遥测改为任务号；过期定位不再不断续写成在线数据。公开 REST 点位必须与工厂点位坐标一致。

## 持久化结构

新增表（保留已有业务数据）：

- robot_execution：每个机器人一行，用行锁串行化占用。
- mqtt_command：事务发件箱，记录 pending / acked / superseded、发送次数及确认时间。
- message_inbox：业务事件去重。
- task_request：创建接口幂等键和请求指纹。

迁移位于 backend/migrations/001_reliable_delivery.sql。后端启动时幂等创建上述表。
本机已应用迁移，两次重复执行成功，已有 25 条历史任务的状态数量未变。

机器人默认日志路径为 ~/.local/state/inspection_robot/<robot_id>.sqlite3。
同一机器人三个节点必须使用同一机器人编号与 journal_path。
日志包含指令、运行快照、取消标记、上报队列与拍照请求；不可随意删除它来解除占用。

## 协议 v2（两端须同步升级）

下行 robot/<id>/task：
```json
{"protocol_version":2,"command_id":"唯一编号","task_id":"任务号","action":"patrol","points":[{"seq":1,"place":"power_room","x":14.75,"y":-11.0,"check":"检查外观"}]}
```

事件与结果增加 event_id、command_id。后端对 robot/<id>/event 和 result 成功处理后，向 robot/<id>/ack 回传 {"event_id":"..."}。
MQTT PUBACK 只表示 broker 收到了报文，不能当作机器人接单或业务入库成功。

新增事件：navigating、cancel_confirmed、command_rejected、recovery_required。
旧的无指令编号报文不能驱动新状态机。不要混跑旧节点与新后端。

## 取消与重启语义

CANCELED 表示业务已请求取消，TIMEOUT 表示任务事件超时；两者不等于执行端已经确认撤销。
详情字段 execution_held 和 delivery 显示真实交付状态。

执行节点重启后，持久化的活动指令进入恢复核对，不自动重新导航。
如果已保存 RMF dispatch_id，可以通过后端取消请求重新撤销它。
若崩溃发生在提交 RMF 请求后、收到 dispatch_id 前，不能证明远端是否已接单；保持占用，要求人工核对 RMF 调度端。当前没有自动强制解锁接口。
RMF 的取消服务成功确认代表调度任务已撤销，不能等同于真机物理急停或零速度证明。

## 运行与验证

后端：
```text
cd backend
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

ROS 工作空间：
```bash
source /opt/ros/jazzy/setup.bash
source ~/rmf_demos_ws/install/setup.bash
cd /mnt/f/robot-inspection/robot_ws
colcon build --symlink-install
source install/setup.bash
ros2 launch inspection_robot m2.launch.py
```

数据库回归：
```text
cd backend
.venv\Scripts\python.exe -m unittest discover -s tests -v
```
测试自动创建独立命名的临时 MySQL 数据库，测试结束只删除该测试库。连接账号需要 CREATE/DROP DATABASE 权限。

真实 MQTT + ROS 冒烟测试（无需 Gazebo，不移动机器人）：
1. WSL 终端运行 mosquitto -p 18884（独立测试端口）。
2. 另一个 WSL 终端运行 bash /mnt/f/robot-inspection/scripts/test_reliability_ros.sh（ROS_DOMAIN_ID=211，临时 SQLite 日志）。
3. Windows 在 backend 目录运行 .venv\Scripts\python.exe scripts/reliability_mqtt_smoke.py。
4. 测试后用 Ctrl+C 结束上述两个测试进程。

已验证：
- 27 项回归测试通过，包含真实 MySQL 并发和事务测试、SQLite 重启恢复测试、ACK 时序和输入校验。
- 真实 Mosquitto + ROS2 节点通过：只接单一次、阻塞只记一次、跳过最后一点、取消确认、后端断开后重新连接。
- 前端生产构建通过；保留 Element Plus 大包体积提示。
- 迁移后 API 健康检查、任务列表和历史任务详情返回 200。

## 尚未覆盖

本阶段未验证 Gazebo 行驶中的真实 RMF 撤销行为，未启动真实机器人。
Nav2、自主回桩、实时相机、主动通知和轨迹回放尚未实施。
目前图片仍为样例图。DONE 表示机器人点位执行结束，视觉检测可能随后完成。
VLM 推理仍运行在线程池，尚未加入持久化推理任务队列。
无认证的本地开发接口与 MQTT 仍需在后续部署阶段加固。
