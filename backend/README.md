# Robot inspection backend

FastAPI 业务服务：任务状态机、事务指令发件箱、登录权限、设备遥测、Agent、持久视觉分析与运行监控。

依赖安装：在本目录运行 uv sync，配置 .env（参照 .env.example，不设置默认数据库密码）。数据库初始化：uv run python scripts/init_db.py，保留既有数据。

从项目根目录使用 backend 的 Python 执行 scripts/manage.py build / up / status / doctor / stop。构建后的前端由同一业务进程托管，默认本机 8010。ROS、MySQL、Redis、MQTT 单独管理。

开发时可直接执行 uv run uvicorn app.main:app --host 127.0.0.1 --port 8010，并在 frontend 运行 npm run dev；开发代理默认指向 8010，可由 VITE_API_TARGET 覆盖。

详见 [部署与运行监控](../docs/deployment-and-operations.md)、[访问控制](../docs/access-control.md)、[视觉恢复](../docs/vision-recovery.md)。
