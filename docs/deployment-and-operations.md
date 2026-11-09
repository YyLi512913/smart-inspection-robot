# 本地部署与运行监控

当前环境：Windows 运行 Python 业务服务，WSL Ubuntu-24.04 运行 ROS 2 Jazzy / Gazebo。MySQL、Redis 和 MQTT 是独立服务，部署脚本不会擅自启动、停止或清理这些共享依赖。现使用 8010，避开本机其他项目的 8000。

## 首次准备

1. 在 backend 执行 uv sync；在 frontend 执行 npm ci。
2. 将 backend/.env.example 复制为 backend/.env，填写自己的数据库和模型配置。真实 .env 已加入忽略规则，不写入示例或日志。既有本地数据库连接已迁入 .env，没有改动数据库密码。
3. 确认 MySQL、Redis（此机 6399）、MQTT（1883）可用。用配置的数据库账户执行初始化，需要首次建库权限；日常运行可用业务库权限。
4. 执行下面的初始化与构建，再启动服务。初始化和启动迁移是增量操作，不删除历史任务或重置账号。

从项目根目录执行（Windows）：

~~~bat
backend\.venv\Scripts\python.exe backend\scripts\init_db.py
backend\.venv\Scripts\python.exe scripts\manage.py build
backend\.venv\Scripts\python.exe scripts\manage.py up
backend\.venv\Scripts\python.exe scripts\manage.py doctor
~~~

Linux 使用 backend/.venv/bin/python 替代上述 Python 路径。若 Node.js 不在 PATH，可运行 build --node 指定实际可执行文件，或设置 NODE_BINARY。构建只使用已安装的锁定依赖，不自动下载软件。

打开 http://127.0.0.1:8010 。业务服务直接托管构建后的前端及地图资源，不需要另开 Vite 开发服务器。首次创建管理员，已有账号和浏览器会话可继续使用。管理员左侧“运行状态”显示完整诊断。

## 启停边界

~~~bat
backend\.venv\Scripts\python.exe scripts\manage.py status
backend\.venv\Scripts\python.exe scripts\manage.py stop
~~~

脚本只管理自己启动且进程身份仍匹配的实例，以 PID、启动时间和可执行文件确认身份，避免 PID 复用误停其他进程。端口被未知服务占用时拒绝替换；已运行的本项目外部实例可以识别，但 stop 不会结束它。需要其他端口时，在 up/status/doctor/stop 后都带相同 --port。

stop 请求正常退出，释放已知分析租约，不强制终止在途模型线程；如果仍在等待请求结束，报告“正在退出”，之后再查 status。停止业务服务不会让机器人自动停车：执行中的任务应先在界面取消并等待机器人确认。机器人离线期间的结果依赖原有本地持久队列恢复。

本版本部署为单业务进程，不能直接增加 uvicorn worker 数：MQTT 客户端身份和聊天进程内会话尚未做多进程协调。后台图片分析已有两个独立协程，不需要增加 Web 进程。

## ROS 仿真

WSL 中单独执行：

~~~bash
sudo apt install python3-pil
bash scripts/start_nav2.sh
~~~

该脚本生成场景与地图、构建工作区，并启动 Nav2 及机器人业务节点。Windows 业务服务和 WSL 相机必须指向同一个持久图片目录。停止仿真使用启动终端的正常中断，先确认没有在途任务；不要仅停止场景渲染或只重启部分节点后混用旧时钟。

## 健康与诊断

- GET /healthz：存活检查，进程能响应时返回 200。
- GET /readyz：就绪检查。数据库、Redis、MQTT 会话、图片目录或后台心跳不可用时返回 503，仅公开 ready 布尔值。
- GET /api/ops/status：管理员诊断，包含依赖、积压、后台心跳和机器人在线情况。查看人员与操作员不能访问。
- manage.py doctor：不需要浏览器登录，检查本地依赖与服务就绪，不输出连接串或密钥。

模型只检查是否配置，不发起付费 API 请求，也不把“有配置”当作上游当前可用。机器人无遥测或存储低于 500 MB 为关注事项；不让独立的机器人离线阻断查看历史任务。历史分析失败计数不会被误当作当前后台进程故障。依赖探测缓存 3 秒，连接与查询探测设有限时。

后台交付每秒更新心跳，超时检查每 10 秒更新；图片分析在等待长请求时每 15 秒续租并更新心跳。探测能识别已停止/卡住的处理循环，而不只查看进程是否存在。

## 日志与排障

业务日志位于 backend/var/logs/service.jsonl，JSON 每行一条，单文件 5 MB，保留 3 个轮转备份。启动异常看 .runtime/bootstrap.log（超过 5 MB 后在下一次启动保留一份旧日志）。

HTTP 日志包含 request_id、方法、路径、状态、耗时与用户 ID，不记录请求体、Cookie 或密码；响应带 X-Request-ID，可用这个编号定位同一次请求。已知模型密钥和数据库连接串会从日志消息中脱敏。

| 现象 | 优先检查 |
| --- | --- |
| 页面打不开 | status、端口占用、bootstrap.log，确认前端已 build |
| 能打开页面但服务未就绪 | doctor 或管理员运行状态页，定位具体依赖 |
| 机器人无遥测 | ROS 业务节点是否存活、MQTT、map/odom、相同 ROS 域 |
| 指令一直待确认 | 交付队列、机器人 ACK、broker 连接；不要直接清占用表 |
| 分析等待或历史失败 | 检测记录的尝试次数、错误、图片文件是否存在 |
| 初始化失败 | .env 数据库连接和账户权限，现有数据不会自动清空 |

## 验证

110 项后端测试通过，覆盖依赖故障、心跳过期、日志脱敏、只读权限、静态前端边界与进程身份验证。真实隔离进程完成启动、重复启动、正常停止；Redis/MQTT 故障下 readyz 返回 503，healthz 仍返回 200。验收记录见 [启动停止与故障诊断](validation/operations-acceptance.json)。生产前端构建通过，仍有现有的大包提示。

本机实际切换后保留 30 条任务和既有管理员账号；原开发入口 5173 的代理也已同步指向 8010。运行状态页经登录会话实际检查，关键依赖和四个后台处理均正常。
