# 系统架构与设计取舍

这是机器人上层应用与业务后端：系统负责设备检查定义、任务生命周期、交付可靠性和异常闭环；Nav2 负责导航执行，模型负责受约束的语言或图像理解。当前为单机器人、单业务进程的仿真验证项目。

## 运行边界

~~~mermaid
flowchart LR
    Browser[Vue 工作台] --> API[FastAPI 身份与业务接口]
    API --> Agent[受控工具调用助手]
    Agent --> Rules[权限 / 点位 / 续航校验]
    API --> Rules
    Rules --> DB[(MySQL 任务 / 审计 / 发件箱)]
    Delivery[指令交付循环] --> DB
    Delivery --> MQTT[MQTT broker]
    MQTT <--> Bridge[ROS 2 桥接]
    Bridge <--> Journal[(机器人 SQLite 日志与补报队列)]
    Journal <--> Runner[Nav2 执行适配]
    Runner --> Nav[AMCL / Nav2 Action]
    Nav --> GZ[Gazebo 差速机器人]
    Journal <--> Camera[新鲜图像采集]
    GZ --> Camera
    Camera --> Images[(持久图片目录)]
    MQTT --> Consumer[后端消息消费]
    Consumer --> DB
    Consumer --> Telemetry[(Redis 短时遥测)]
    API --> Telemetry
    DB --> Jobs[持久视觉任务 worker]
    Jobs --> Images
    Jobs --> VLM[视觉模型网关]
    Jobs --> DB
    API --> Alerts[告警 / 人工处理 / 复检]
    Alerts --> DB
~~~

图中 MySQL 是业务状态来源，Redis 只保存带 TTL 的即时遥测，SQLite 属于机器人执行端。浏览器会话也保存在 MySQL，不依赖 Redis。Windows 与 WSL 通过共享目录访问图片；图片落盘与数据库事务不是分布式原子事务，需要分别保证落盘顺序和幂等补报。

LLM 不获得 MQTT 发布、速度控制或任意坐标下发工具。任务创建需要后端再次做确定性校验；模型的口头确认不能替代电量、占用或权限校验。

## 从任务到照片

~~~mermaid
sequenceDiagram
    actor User as 已登录操作员
    participant API as 业务后端
    participant DB as MySQL
    participant Sender as 交付循环
    participant Broker as MQTT
    participant Robot as ROS执行端 / SQLite
    participant Nav as Nav2
    participant Camera as 相机节点
    User->>API: 设备目标与创建意图
    API->>API: 角色、台账、遥测与续航校验
    API->>DB: 事务写任务、占用、规则快照和指令
    DB-->>API: 提交
    API-->>User: 返回任务号
    Sender->>DB: 读取已提交发件箱
    Sender->>Broker: 原 command_id 可重发
    Broker->>Robot: 下发巡检
    Robot->>Robot: 指令去重并持久化
    Robot->>Nav: 持久目标UUID后请求导航
    Nav-->>Robot: 到达终态
    Robot->>Robot: 新鲜速度接近零；记录到点与拍照请求
    Camera->>Camera: 校验新鲜帧、原子写JPEG
    Camera->>Robot: 结果入补报队列并完成拍照请求
    Robot->>Broker: 上报事件 / 图片元数据
    Broker->>API: 传递结果
    API->>DB: 同一事务记录结果事件与待分析任务
    DB-->>API: 提交
    API->>Broker: 应用ACK
    Broker->>Robot: 可删除对应补报消息
~~~

图片本身由共享文件目录保存，MQTT 传递图片地址及元数据。真实模型调用时，后端读取图片并转换为图像输入。task DONE 表示点位执行结束，不能直接当作视觉结论已完成。

## 核心数据

| 数据 | 作用及约束 | 代码入口 |
| --- | --- | --- |
| equipment / observation_point / observation_check | 设备、停车朝向和固定检查项 | [equipment.py](../backend/app/equipment.py) |
| task / task_point / task_point_inspection | 生命周期、点位与创建时冻结的规则 | [state_machine.py](../backend/app/state_machine.py) |
| robot_execution | 以机器人行锁串行化占用，不是 FIFO 任务队列 | [任务状态机](../backend/app/state_machine.py) |
| task_request / message_inbox | 创建意图幂等、事件去重 | [任务接口](../backend/app/tasks_api.py) |
| mqtt_command | 事务发件箱及应用接单状态 | [delivery.py](../backend/app/delivery.py) |
| 机器人 SQLite | 指令、取消墓碑、运行信息、补报和拍照请求 | [durable_store.py](../robot_ws/src/inspection_robot/inspection_robot/durable_store.py) |
| vision_job / inspection_result | 持久分析租约与最终检测结果 | [vision_jobs.py](../backend/app/vision_jobs.py) |
| inspection_alert / alert_result / alert_recheck / alert_event | 活跃告警合并、来源去重、复检关联与只增处理记录 | [alerts.py](../backend/app/alerts.py) |
| app_user / auth_session / auth_event | 密码哈希、会话撤销、角色与账号审计 | [auth.py](../backend/app/auth.py) |

建表结构见 [schema.sql](../backend/schema.sql)，增量结构位于 backend/migrations。部分跨表关系通过业务校验与唯一键维护，不是所有逻辑关联都声明了外键。

## 关键取舍

### 业务事务与消息发送分开

任务、占用和下发指令在 MySQL 同一事务提交，交付循环随后读取发件箱发送。解决的是“任务已提交但指令没保存”的双写空隙，而不是把数据库和 MQTT 变成一个原子提交。

发送后崩溃仍可能重复投递，因此机器人必须按 command_id 去重。后端也按 event_id 去重。MQTT PUBACK 仅说明 broker 收到，不说明机器人执行或业务已入库；机器人补报队列需要等应用 ACK 才删除。

代价是增加队列表和重试循环；可获得可追溯、可补发的至少一次交付。不能将这套设计表述成所有外部操作“恰好执行一次”。

### 取消是跨端确认流程

业务 CANCELED / TIMEOUT 先记录撤销意图，并保留 robot_execution 占用。Nav2 适配器等待导航终态和新鲜零速度证明后才发 cancel_confirmed；占用到此才释放。未知导航状态不当作已经停止，也没有强制清占用接口。

SQLite 取消墓碑阻止延迟的旧巡检指令重新启动。执行进程恢复时要求核对持久化目标，不直接重新导航。这些语义通过 [取消与通信测试](../backend/tests/test_reliability.py) 和 [Nav2 组件测试](../robot_ws/src/inspection_robot/test/test_nav2_components.py)验证。

### 视觉任务有租约，提交有归属校验

两个 worker 通过行锁与 SKIP LOCKED 领取任务，60 秒租约、15 秒续租。调用模型期间不持有数据库锁；提交时再次核对有效租约和随机 token。旧 worker 即使迟到，也不能覆盖新持有者的结果。

结果、告警与任务完成状态同一事务提交。失败有限重试三轮，前两轮分别等待 10 秒、30 秒；客户端每轮内部最多两次 HTTP 调用，因此不是最多三次模型请求。进程中断也消耗执行预算。达到上限后失败进入复核，不能无限重试。

模型 API 仍可能被重复调用，原始图片也仍需持久保存。具体见 [恢复设计](vision-recovery.md) 与 [独立进程验收](validation/vision-recovery-acceptance.json)。

### 检查规则冻结，模型结果受规则约束

任务创建时保存设备和检查规则快照。机器人传回的设备描述不能覆盖该快照；修改台账也不会改变历史任务的判定范围。

数值读数由模型提取，范围与单位由服务端验证；读不清、缺项或单位不符进入复核。视觉检查仍依赖模型可见证据，没有把图像理解变成确定性算法。首轮漏检和柜门限制见 [设备验收](equipment-inspection.md)。

### 正常结果不直接关闭旧告警

同一观察点持续异常合并成一个活跃告警，保留每次发现。操作员记录处理后创建关联复检，只有任务成功完成、冻结检查项逐项正常，且期间没有新增异常，才能关闭。

图像结果早于 task_done 时先等待；普通巡检正常不会自动关闭旧告警。误报走独立人工路径，并保存原始结论和处理说明。

### 当前选择单业务进程

数据库保存可恢复的交付、分析和身份状态，但聊天上下文与确认流程仍是进程内会话，MQTT 客户端也有固定服务身份。当前不直接增加 Web worker 数；多进程需要进一步协调聊天状态、订阅和实例标识。

本机生产入口同源托管前端与 API；依赖故障影响 readyz，就算进程仍能响应 healthz，也不能当作服务已就绪。模型配置检查不会触发付费请求，也不能证明上游模型当前可用。

## 代码导航

- [HTTP 入口与服务生命周期](../backend/app/main.py)
- [自然语言循环](../backend/app/agent/loop.py) · [受控工具](../backend/app/agent/tools.py)
- [续航近似模型](../backend/app/energy_assessor.py)
- [Nav2 执行适配](../robot_ws/src/inspection_robot/inspection_robot/nav2_task_runner.py)
- [相机采集](../robot_ws/src/inspection_robot/inspection_robot/snapshot_node.py) · [ROS/MQTT 桥接](../robot_ws/src/inspection_robot/inspection_robot/bridge_node.py)
- [依赖与心跳](../backend/app/operations.py) · [启动管理](../scripts/manage.py)
- [统一验收入口](../backend/scripts/run_acceptance.py)

## 下一层工程问题

现有续航是包含回桩距离的直线段近似，加上点位耗电和余量，不使用 Nav2 实际绕障路径；模拟电量也不能验证真实续航。真机部署需要里程计/传感器标定、电池曲线、实际路径能耗与制动验证。

多角度和遮挡视觉评估、长期负载、多机器人资源协调、外部服务认证仍需进一步工作。上述问题不因当前自动测试通过而视为已解决。
