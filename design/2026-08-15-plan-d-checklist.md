# Plan D（部署与运维）遗留清单

> 来源：Plan A/B/C 三次终审的 deferred minors 与 Ruling 成本项汇总。
> 状态：待启动。执行 Plan D 时以此为输入，逐项核对。

## 生产对齐必做（来自 Plan A 终审与历次裁决的成本项）

1. **redis 模式实测**：`uv run arq invoicing.workers.queue.WorkerSettings` CLI 启动验证（arq 0.28 无 `arq.worker.WorkerSettings`，已用普通属性类适配——审查核实 CLI 读取路径成立，但未在真实 redis 上跑过）。
2. **received 状态叙事恢复**：本地模式下 `_store_original` 出生即 parsing（跳过 received）。redis 模式对齐时在 `_parse_invoice` 入口补 received→parsing 转换（仿 `_verify_invoice` 的 parsed→verifying 先例），并恢复 fetch 创建 received。
3. **PG timestamptz / naive-UTC 统一**：全库时间戳目前 naive-UTC（SQLite 字符串比较一致性），`DateTime(timezone=True)` 列在 PG 上会返回 aware datetime 导致减法 TypeError（scheduler、stats 边界）。统一决策：模型改 naive 列或比较改用 aware。
4. **S3 后端联调**：`get_storage()` 的 S3 分支无测试覆盖；`ensure_bucket` 吞裸 Exception 需区分 ClientError 404；下载端点整文件读内存非流式、file 缺失无 404 守卫（终审建议 S3 化前改 StreamingResponse）。
5. **zip bomb 加固**：`unpack_zip` 无条目大小/数量上限；加密 ZIP 成员 `zf.read` 抛 RuntimeError 未兜底（返回 None）。
6. **迁移对齐**：现有 alembic 迁移在 SQLite 下 autogenerate，PG 对齐时重新生成并核对表达式索引/部分唯一索引。

## 部署交付物（设计 §六）

- `deploy/Dockerfile`（backend 镜像，app+worker 共用）、`deploy/docker-compose.yml`（web/app/worker/postgres/redis/minio 六服务）、`deploy/nginx.conf`（前端产物托管 + 反代 /api、/mcp）、`deploy/.env.example`、`deploy/backup.sh`（pg_dump + MinIO 快照）。

## 前端生产化（Plan C 终审 minor）

- Antd 按需引入（bundle 1.58MB / gzip 493KB）；settings 测试补真实断言；重验按钮按状态收敛；errorMessage 处理 422 数组 detail；dayjs 显式声明依赖。

## MCP/其他（Plan B 终审 minor）

- 默认 token "change-me" 无 fail-fast 告警（建议启动时非测试环境检测告警）；mount 路由顺序注释 + 未知路径 404 回归断言 + 显式 `streamable_http_path="/mcp"`；`_mcp_admin_user` id=0 哨兵（Phase 2 写工具接入前必修）。

## 环境约束（重要）

- 本机无 Docker（2026-08 确认），无法本地运行 docker compose；Plan D 的容器化验证需在有 Docker 的环境执行，本机可完成 Dockerfile/compose/nginx 配置与静态检查。
