# Fin-DataPilot Backend

FastAPI + LangGraph 后端，负责匿名身份、会话/记忆、Agent 编排、Skill 调度、SSE、后台运行恢复和持久化。

## 本地启动

从仓库根目录执行：

```bash
cp .env.example backend/.env
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
./start.sh
```

服务地址：`http://localhost:7860`。

```bash
curl http://localhost:7860/api/health
```

本地 `.env` 至少需要真实的 `LLM_API_KEY` 和 `IWENCAI_API_KEY` 才能完成 LLM 规划和四个问财 Skill 调用。AnySearch 可以匿名使用，也可设置 `ANYSEARCH_API_KEY` 提高限额。

## 运行架构

```text
POST /api/agent/chat/stream
        │
        ├── 校验 Bearer 和 session 所有权
        ├── 保存 user message
        ├── 加载 history + short memory + long-term memory
        ├── 创建持久化 AgentRun
        └── 启动独立 asyncio Task
                  │
                  ▼
       Policy → Planner → Skill Context Loader
              → Router → Executor → Reflector
                           ▲           │
                           └─ 补查/重规划
                                  │
                                  ▼
                             Synthesizer
                                  │
                 SSE + Message + Run + Memory
```

HTTP SSE 连接只是后台任务的订阅者。连接丢失不会取消任务，客户端可以通过 `GET /api/agent/runs/{run_id}` 读取终态。

## Agent 节点

| 节点 | 文件 | 职责 |
|---|---|---|
| Policy | `app/agent/policy.py` | 拒绝真实交易操作；限制连续金融指标窗口 |
| Planner | `app/agent/nodes/planner.py` | 将问题拆成可执行步骤；失败时产生确定性兜底计划 |
| Skill Context Loader | `app/agent/nodes/skill_context_loader.py` | 从固定 `Skills/` 目录加载所需说明和模板 |
| Router | `app/agent/nodes/skill_router.py` | 消费计划/Hint、替换占位符、阻止重复循环 |
| Executor | `app/agent/nodes/executor.py` | 调用 Registry、保存工具审计、检查取消状态 |
| Reflector | `app/agent/nodes/reflector.py` | 核对全部子目标，补查、兜底或触发重规划 |
| Synthesizer | `app/agent/nodes/synthesizer.py` | 截断证据、过滤思维标签、流式生成最终回答和引用 |

图由 `app/agent/graph.py` 编译。当前没有 LangGraph Checkpointer；可恢复运行由 `AgentRun` 和 `events_json` 自行实现。

## Skill 运行时

`app/skills/base.py` 定义：

- `ToolParameter`
- `ToolSpec`
- `ToolResult`
- `Handler`

`app/skills/registry.py` 负责注册、启用状态、参数校验、调用和返回值校验。

当前注册能力：

| 名称 | Handler | 外部数据源 |
|---|---|---|
| `financial-query` | `app/skills/financial_query.py` | 问财 `/v1/query2data` |
| `news-search` | `app/skills/news_search.py` | 问财 `/v1/comprehensive/search` |
| `announcement-search` | `app/skills/announcement_search.py` | 问财 `/v1/comprehensive/search` |
| `report-search` | `app/skills/report_search.py` | 问财 `/v1/comprehensive/search` |
| `anysearch` | `app/skills/anysearch.py` | 仓库内 AnySearch CLI |

所有成功结果必须带 `data`，并可附带 `source`、`as_of`、`citations` 和 `raw_ref`。问财适配器将接口查询时间标为 `as_of`，具体行情/财务业务时点仍以返回数据字段为准。

Prompt-only Skill 上传默认关闭，同时需要 `ENABLE_SKILL_UPLOAD=true` 和管理员密钥。新上传 Python Skill 始终拒绝，不能在 API 进程执行。

## 身份与权限

### 匿名用户身份

`POST /api/auth/anonymous` 签发 HMAC-SHA256 Bearer Token：

```text
v1.anon_<random>.<expires_at>.<signature>
```

生产环境必须设置 `AUTH_SECRET`；否则应用会在创建时直接失败。普通接口通过 `require_user` 从令牌取得 `user_id`，不接受客户端传入所有者 ID。

### 管理员身份

管理员接口使用：

```http
X-API-Key: <ADMIN_API_KEY>
```

`ADMIN_API_KEY` 未配置时，管理员接口返回 503；错误密钥返回 403。旧变量 `API_KEY` 仅作为兼容别名。

### 限流

SlowAPI 当前按远端 IP 限流，典型限制包括：

- 匿名身份：12 次/小时
- Agent 流：12 次/分钟
- 停止：30 次/分钟
- 运行查询：60 次/分钟
- 上传：5 次/小时

## API

### 公共接口

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/health` | 最小存活检查：`ok`、`version` |
| GET | `/api/echo?text=...` | CORS 往返检查 |
| POST | `/api/auth/anonymous` | 创建或续期匿名身份 |

### Bearer 用户接口

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/auth/me` | 当前匿名身份 |
| GET | `/api/skills` | 已注册 Skill、启用态和环境变量状态 |
| GET | `/api/sessions` | 当前用户会话列表 |
| POST | `/api/sessions` | 创建会话 |
| GET | `/api/sessions/{session_id}` | 返回会话和消息列表 |
| PATCH | `/api/sessions/{session_id}` | 修改标题 |
| DELETE | `/api/sessions/{session_id}` | 删除单个会话 |
| DELETE | `/api/sessions` | 删除当前用户全部会话 |
| POST | `/api/sessions/{session_id}/messages` | 手工写入消息；正常聊天流不需要调用 |
| GET | `/api/memories` | 查看长期记忆 |
| DELETE | `/api/memories/{memory_id}` | 删除单条长期记忆 |
| DELETE | `/api/memories` | 清空长期记忆和会话摘要 |
| POST | `/api/agent/chat/stream` | 启动 Agent 并返回 POST SSE |
| POST | `/api/agent/chat/stop` | 请求停止属于当前用户的运行 |
| GET | `/api/agent/runs/{run_id}` | 读取运行状态、事件和最终文本 |

### 管理员接口

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/diag` | 数据库位置、文件大小和 `/data` 挂载诊断 |
| GET | `/api/diag/anysearch` | AnySearch 路径、运行时和密钥诊断 |
| PATCH | `/api/skills/{name}` | 全局发布/禁用 Skill |
| POST | `/api/skills/{name}/debug` | 绕过 Agent 直接调试 Skill |
| POST | `/api/skills/upload` | 上传 Prompt-only Skill ZIP |
| DELETE | `/api/skills/{name}` | 删除上传的 Skill |

## SSE 事件

主要事件顺序：

```text
session? → run → ping → think(plan/load_skills)
→ tool_call → tool_result → reflection → ...
→ preamble? → summary_start → token_delta*
→ message_final → done → run_status
```

另有 `heartbeat` 和 SSE 注释 keep-alive。数据库最多保留每个运行最近 128 条事件；最终文本单独保存在 `AgentRun.final_text`。

## 存储模型

`app/storage/models.py` 包含：

- `Session`：匿名用户会话，删除时级联消息、运行和短期摘要。
- `Message`：正文、Tool Call JSON、Thinking JSON。
- `ToolRun`：每次 Skill 调用的参数、结果、耗时、trace。
- `AgentRun`：后台运行状态、事件、最终文本和错误。
- `SkillPref`：使用 `__system__` 用户保存全局发布状态。
- `SessionMemory`：默认 30 天有效的会话摘要。
- `LongTermMemory`：按用户和去重键唯一的稳定事实。

启动时使用 `Base.metadata.create_all()` 创建缺失表，当前没有数据库迁移工具。修改已有表结构前应先引入 Alembic。

## SQLite 与 Hugging Face 持久化

HF Space 生产环境至少设置：

```dotenv
DATA_PILOT_ENV=production
AUTH_SECRET=<稳定随机值>
LLM_API_KEY=<secret>
IWENCAI_API_KEY=<secret>
CORS_ALLOW_ORIGINS=<前端 origin>
```

需要管理员诊断或 Skill 管理时再设置 `ADMIN_API_KEY`。这些值必须通过 HF Secrets 注入，不能写入镜像或公共前端变量。

本地默认数据库：

```text
./data/findatapilot.db
```

在 HF Space 中优先使用：

```text
/data/findatapilot.db
```

只有 `/data` 是单独挂载的可读写持久卷时，数据库才能跨重建保留。否则应用会回退到可写的临时目录，并在日志中警告数据会丢失。

管理员可以验证：

```bash
curl -H "X-API-Key: $ADMIN_API_KEY" \
  https://<space-host>/api/diag
```

关键字段：

```json
{
  "db_path": "/data/findatapilot.db",
  "data_dir_is_separate_mount": true
}
```

`true` 表示 `/data` 与根文件系统使用不同设备，符合持久卷特征。

### Turso 状态

配置类包含 `TURSO_DATABASE_URL` 和 `TURSO_AUTH_TOKEN`，但 `sqlalchemy-libsql/libsql` 依赖当前未安装，CI 也没有覆盖远程 libSQL。请不要把它视为已经验证的“设置变量即可使用”路径；正式启用前需要补充驱动、URL 构造、集成测试和迁移策略。

## LLM Provider 状态

- `minimax`、`openai`、`custom`：通过 `ChatOpenAI` 兼容层。
- `anthropic`：代码分支存在，但生产 requirements 当前没有 `langchain-anthropic`；启用前需补依赖和测试。
- LangFuse：只有配置字段，尚未接入实际 trace。

## 测试

```bash
cd backend
ruff check app tests
mypy app
pytest -q
```

Pytest 回归覆盖：

- 身份和会话隔离
- 记忆召回与删除
- Planner 和计划占位符
- 多步反思与重新规划
- Router 循环保护
- 策略检查
- Skill 参数/结果契约
- AnySearch 命令构造
- Prompt-only 上传安全
- 后台任务断线执行和取消
- SQLite/HF 路径选择
- Synthesizer 截断与思维标签清理

## 目录

```text
backend/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── security.py
│   ├── db_init.py
│   ├── agent/
│   │   ├── graph.py
│   │   ├── policy.py
│   │   ├── runtime.py
│   │   ├── state.py
│   │   └── nodes/
│   ├── api/
│   ├── memory/
│   ├── skills/
│   ├── storage/
│   ├── llm/
│   └── utils/
├── tests/unit/
├── Dockerfile
├── start.sh
├── requirements.txt
└── requirements-dev.txt
```

## 已知工程边界

- Agent 当前串行执行 Skill，未实现并行子图。
- LangGraph 未配置 Checkpointer。
- SSE token/heartbeat 不写入 SQLite，只保存公开阶段事件与最终答案。
- SSE 订阅断开后停止投递流式事件，后台执行和持久化继续。
- 新版消息通过 thinking JSON 保存公开阶段事件、来源、图表与完整工具参数，供前端恢复；不保存私有推理。
- 上传端点拒绝新代码 Skill，但启动恢复逻辑仍保留旧 `.py` Skill 导入路径，生产应清理。
- `anysearch` 当前未加入上传模块的不可删除名称集合，管理员删除接口需增加保护。

详细路线图见仓库根目录 `PLAN.md`。

## 回答展示（v1）

新增公开事件 `phase`（`planning/researching/checking/summarizing`）与可执行计划 `plan.sub_tasks`。`tool_result` 包含原始 `args`，`message_final.presentation` 包含：

```json
{"version":1,"status":"ready","successful_tools":1,"total_tools":1,"warnings":[],"charts":[]}
```

`status` 表示工具证据是否取得（`ready/partial/empty`），不是投资结论可信度评分。图表由 `app/agent/presentation.py` 确定性生成：只支持金融记录中同一指标、同一单位的条形对比，排除标识符、空值、非有限数值及重复标签。图表最多 3 个，每图前 12 条数据，标明实际返回总条数与查询时点。

`RunTranscript` 收集公开执行元数据，`token_delta` 不进入持久化事件日志，最终正文保存在 `message_final` 和运行终态。没有最终答案的运行标记为失败。会话详情额外返回 `active_run_id`（可能为空），用于刷新/切换后继续轮询。

用户请求的执行限制按明确操作指令判断，不以“交易”等词单独拦截。“对比上一个交易日的数据”、交易记录查询、策略分析和规则咨询可正常进入研究链路；明确要求系统代为下单、转账、提现或撤单仍返回 `EXECUTION_NOT_SUPPORTED`。普通买入/卖出研究建议保留研究用途提示。
