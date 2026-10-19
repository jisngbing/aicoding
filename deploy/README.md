# Linux deployment

部署目录：`/opt/aicoding`。前端 Nginx 使用 80 端口，后端 12024 仅在 Docker 网络内访问。
MongoDB 直接连接现有 `rag-databases_default` 网络中的 `rag-mongodb:27017`。

配置文件 `.env-linux` 仅 root 可读；`deploy/.htpasswd` 是网页登录的密码哈希。
前端使用生产静态文件，并关闭 SSE 代理缓冲。容器随 Docker 自动重启，后端以 UID 10001 运行。

首次初始化业务数据库（仅目标文件不存在时复制）：

```sh
cd /opt/aicoding
mkdir -p data logs ai_workspace/projects
test -e data/store.sqlite || cp deploy/seed/store.sqlite data/store.sqlite
chown -R 10001:10001 data logs ai_workspace
chmod 600 .env-linux
docker compose -f deploy/compose.yml up -d --build
```

查看、验证、停止：

```sh
docker compose -f deploy/compose.yml ps
docker compose -f deploy/compose.yml logs --tail=100 backend
docker compose -f deploy/compose.yml exec -T backend python scripts/check_sandbox.py
docker compose -f deploy/compose.yml exec -T backend python scripts/check_mongodb.py
docker compose -f deploy/compose.yml down
```

更新时保留 `.env-linux`、`deploy/.htpasswd`、`data/`、`logs/`、`ai_workspace/`。
MongoDB 中的 checkpoint 和仓库长期记忆由现有 MongoDB 容器持久化。
