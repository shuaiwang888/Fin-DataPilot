# Fin-DataPilot

> 基于 LangGraph、LangChain、FastAPI、React 的自然语言金融研究 Agent。系统按需调用金融数据与联网搜索能力，经过规划、执行、反思和证据合成后生成可追溯回答。

## 当前能力

- **匿名身份隔离**：浏览器使用服务端签名的 Bearer 身份；客户端不能自行指定 `user_id`。
- **会话与历史**：支持新建、切换、重命名、删除、清空；每个身份默认最多保留 50 个会话。
- **三层记忆**：单次运行工作记忆、会话短期摘要、跨会话长期偏好/目标；均按匿名身份隔离并支持删除。
- **多步 Agent**：`Planner → Skill Context Loader → Router → Executor → Reflector → Synthesizer`。
- **证据不足自动补查**：支持计划占位符、实体消歧、空结果重试、AnySearch 兜底和重新规划。
- **执行轨迹可见**：通过 SSE 展示计划、能力加载、工具调用、结果、反思和最终总结；不展示模型私有思维链。
- **可恢复运行**：每次问题分配 `run_id`；SSE 断开后任务继续在后台执行，前端可轮询持久化结果。
- **受控 Skill 管理**：普通用户只能查看和使用已发布能力；开关、调试、上传、删除只允许管理员操作。
- **回答可视化**：从金融查询原始记录生成最多 3 个可切换的条形对比图，保留单位、负值、来源与查询时点；缺失或单位混杂的数据不画图。
- **统一对话展示**：四阶段进度、研究计划、工具成功/失败统计、证据缺口和 Markdown 导出；刷新或重开会话可恢复已保存图表及工具记录，运行中会话自动继续同步。
- **来源追踪**：工具结果包含来源、查询时点、引用、耗时和 trace ID。

## 实际架构

```text
[ React 18 + Ant Design X + Zustand ]       GitHub Pages
                    │
             Bearer / REST / POST-SSE
                    ▼
[ FastAPI + LangGraph + SQLAlchemy :7860 ]  Hugging Face Space
    ├── Auth / Sessions / Memories / Skills / Agent Runs
    ├── Policy → Planner → Loader → Router → Executor → Reflector
    │                                           │            │
    │                                           └── 补查/重规划
    ├── Synthesizer → token_delta → message_final
    ├── ToolSpec / ToolResult / ToolRegistry
    └── 5 个已注册能力
        ├── financial-query      问财结构化金融数据
        ├── news-search          问财财经新闻
        ├── announcement-search  问财公告/事件
        ├── report-search        问财研报
        └── anysearch            联网搜索/URL 提取兜底
                    │
                    ▼
        SQLite（本地或 HF /data）
```

当前系统是**查询时按需取数**，不是批量 ETL 或金融数据仓库。外部结果会作为消息、运行事件和工具审计 JSON 持久化，但不会沉淀为指标宽表。

完整的数据流、表结构和实现边界见 [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md)。

## 目录

```text
Fin-DataPilot/
├── README.md                       # 项目入口与运行说明
├── PROJECT_SUMMARY.md              # 当前实现的完整架构与数据流
├── PLAN.md                         # 当前落地状态与后续路线图
├── CLAUDE.md                       # 工程协作约定
├── Skills/
│   ├── financial-query/
│   ├── news-search/
│   ├── announcement-search/
│   ├── report-search/
│   └── anysearch-skill/
├── backend/
│   ├── app/                        # API、Agent、Skill、记忆与存储
│   ├── tests/unit/
│   ├── Dockerfile
│   └── start.sh
├── frontend/src/                   # React 组件、Hook、API 与 Zustand Store
└── .github/workflows/              # CI、前端部署、HF 后端部署
```

## 快速开始

### 1. 配置后端

```bash
cp .env.example backend/.env
```

至少设置：

```dotenv
LLM_API_KEY=<MiniMax、OpenAI 或其他 OpenAI 兼容服务密钥>
IWENCAI_API_KEY=<同花顺问财 API 密钥>
```

本地开发可使用默认开发签名密钥；生产环境必须设置稳定、随机的 `AUTH_SECRET`。

### 2. 启动后端

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
./start.sh
```

```bash
curl http://localhost:7860/api/health
```

### 3. 启动前端

```bash
cd frontend
cp .env.example .env
pnpm install
pnpm dev
```

前端默认地址为 `http://localhost:5173`。跨域部署时，将 `VITE_API_BASE` 设置为后端完整 Origin，不要带结尾 `/`。

浏览器首次访问会自动申请匿名身份。不要在请求参数中自行传递 `user_id`。

## Agent 数据流

一次提问的主流程：

1. 前端乐观插入用户和 Assistant 消息，通过 POST SSE 发起请求。
2. 后端校验 Bearer 身份和会话归属，保存用户消息。
3. 加载历史、短期摘要和相关长期记忆，创建持久化 `run_id`。
4. 后台 Task 执行 LangGraph；HTTP 连接只作为事件订阅者。
5. Policy 识别明确要求代为执行下单、转账等操作的指令；“交易日”“交易量”“交易策略”等研究问题正常放行，并在工具执行前检查时间窗口等限制。
6. Planner 把问题拆成可执行计划；失败时生成确定性兜底检索步骤。
7. Loader 从仓库固定的 `Skills/` 目录加载计划所需的 `SKILL.md` 和模板。
8. Router 消费计划或 Reflector Hint，并替换前序结果占位符。
9. Executor 通过 ToolRegistry 校验并调用 Skill，同时写入 `tool_runs` 审计表。
10. Reflector 判断所有子目标是否覆盖；不足时补查或重新规划。
11. Synthesizer 先对资料去重、过滤过期事件并压缩为关键事实，再仅依据这些证据生成可读简报，不把 Skill 原始 JSON 当作答案；清理潜在 `<think>` 内容并附引用。用户要求改写上一轮回答时，沿用本会话已保存的证据，不再重新检索。
12. 保存 Assistant 消息和终态；SSE 断开时通过 `GET /api/agent/runs/{run_id}` 恢复结果。

默认安全上限：

- 单个问题最多 8 次 Skill 调用。更大的环境变量值会被钳回 8。
- 最多 5 个规划周期。
- 单次后台运行最多 600 秒。
- Agent 当前按依赖顺序串行调用 Skill。

## 身份、记忆与权限

服务端为每个浏览器签发 HMAC 签名的匿名令牌，前端保存在 `localStorage`。同一浏览器可以连续使用；清理站点数据、无痕窗口或换设备会获得新身份。当前没有登录、跨设备同步或身份找回功能。

路由权限分为：

- **公共**：健康检查、匿名身份初始化。
- **Bearer 用户**：会话、消息、记忆、Skill 列表、Agent 对话和运行恢复。
- **管理员**：诊断、Skill 发布开关、调试、Prompt-only Skill 上传和删除，使用 `X-API-Key: $ADMIN_API_KEY`。

`ADMIN_API_KEY` 绝不能放入任何 `VITE_*` 变量或公共前端构建。

## 关键配置

| 变量 | 说明 | 默认值/状态 |
|---|---|---|
| `LLM_PROVIDER` | `minimax` / `openai` / `custom`；`anthropic` 分支需额外安装依赖 | `minimax` |
| `LLM_BASE_URL` | OpenAI 兼容 API 地址 | `https://api.minimaxi.com/v1` |
| `LLM_API_KEY` | LLM API 密钥 | 必填 |
| `LLM_MODEL` | 模型名 | `MiniMax-M3` |
| `IWENCAI_API_KEY` | 4 个问财金融 Skill 的密钥 | 必填 |
| `ANYSEARCH_API_KEY` | AnySearch 较高限额密钥；为空时允许匿名模式 | 空 |
| `CORS_ALLOW_ORIGINS` | 允许的前端 Origin，逗号分隔 | 本地开发地址 |
| `AUTH_SECRET` | 匿名身份签名密钥 | 生产必填 |
| `AUTH_TOKEN_TTL_HOURS` | 匿名令牌有效期 | `8760` |
| `ADMIN_API_KEY` | 管理员接口密钥 | 空 |
| `LOCAL_SQLITE_PATH` | 本地 SQLite 路径 | `./data/findatapilot.db` |
| `MAX_SESSIONS_PER_USER` | 每个身份保留的会话数；0 表示不限制 | `50` |
| `MEMORY_ENABLED` | 是否启用短期/长期记忆 | `true` |
| `AGENT_MAX_SKILL_CALLS` | 每题 Skill 调用硬上限，超过 8 会被钳回 | `8` |
| `AGENT_MAX_PLANNING_CYCLES` | 最大规划周期 | `5` |
| `AGENT_RUN_TIMEOUT_SECONDS` | 后台运行超时 | `600` |
| `ENABLE_SKILL_UPLOAD` | 是否允许管理员上传 Prompt-only Skill | `false` |

Turso、Anthropic 和 LangFuse 目前属于扩展位，并非默认 CI 覆盖的完整生产路径，详见 [PLAN.md](PLAN.md)。

## API 摘要

- `POST /api/auth/anonymous`：初始化或续期匿名身份。
- `GET /api/sessions`、`POST /api/sessions`：查询和创建会话。
- `GET /api/sessions/{id}`：返回会话及其消息。
- `PATCH /api/sessions/{id}`、`DELETE /api/sessions/{id}`：重命名和删除。
- `GET /api/memories`、`DELETE /api/memories/{id}`、`DELETE /api/memories`：记忆管理。
- `GET /api/skills`：查看已注册能力及配置状态。
- `POST /api/agent/chat/stream`：发起 Agent POST SSE。
- `POST /api/agent/chat/stop`：停止当前身份拥有的运行。
- `GET /api/agent/runs/{run_id}`：读取持久化运行状态和事件。
- `GET /api/diag`、`GET /api/diag/anysearch`：管理员诊断。
- `PATCH/POST/DELETE /api/skills/...`：管理员 Skill 管理。

完整接口和后端运行细节见 [backend/README.md](backend/README.md)。

## 测试与质量门禁

```bash
cd backend
ruff check app tests
mypy app
pytest -q

cd ../frontend
pnpm lint
pnpm test
pnpm build
```

质量门禁包括后端 Pytest、Mypy strict、Ruff，以及前端 Vitest、ESLint、TypeScript 与 Vite 生产构建。

## 部署

前后端部署都由成功完成的 `Quality Gate` 触发，并检出同一个已测试提交；也可手动运行。

- **后端**：`.github/workflows/deploy-backend-hf.yml` 调用 `deploy-to-hf.sh`，将后端和 `Skills/` 推送到 Hugging Face Docker Space。
- **前端**：`.github/workflows/deploy-frontend.yml` 构建并发布 GitHub Pages。

HF 后端至少应配置：

```dotenv
DATA_PILOT_ENV=production
AUTH_SECRET=<稳定随机值>
LLM_API_KEY=<secret>
IWENCAI_API_KEY=<secret>
CORS_ALLOW_ORIGINS=<前端 origin>
```

需要使用诊断或 Skill 管理接口时再配置 `ADMIN_API_KEY`。生产密钥应放入 HF Secrets，而不是仓库变量或前端构建变量。

HF Space 必须将可读写持久卷挂载到 `/data`，否则 SQLite、历史、记忆和上传 Skill 会在重建后丢失。可通过管理员接口 `/api/diag` 检查 `data_dir_is_separate_mount`。

## 当前边界

- 没有批量 ETL、指标仓库、应用层查询缓存或向量数据库。
- LangGraph 未配置 Checkpointer；断线恢复依赖自建的 `agent_runs` 事件记录。
- 新版消息会保存并恢复执行事件、工具轨迹和图表；旧版没有保存的阶段/图表信息无法追溯补齐。
- SSE token 只实时传输，阶段事件与最终答案持久化；断线恢复不重放未完成的逐字文本。
- 数据库当前使用 `create_all()`，尚未引入版本化迁移。
- 新上传 Python Skill 会被拒绝；持久卷中的旧代码 Skill 恢复路径仍需清理。

## License

Internal project. All rights reserved.

## 页面风格

页面采用浅色画布、深蓝导航和青绿强调色，统一首页研究入口、对话卡片、进度、工具记录与图表。桌面与手机共用响应式布局，支持键盘焦点、减少动效和减少透明度偏好。

## 对话与可视化契约

`message_final` 是正文与图表的唯一最终事实源，包含 `content`、`tool_calls`、`preamble`、`citations` 和 `presentation`。`presentation` 使用版本 1，返回数据查询状态、成功/总调用数、证据缺口与 `charts`。图表只读取 `financial-query` 的结构化数值列，最多 3 个指标、每项前 12 条返回记录，不把证券代码当指标，不把查询时点冒充业务时点。

前端的 `chatEvents.ts` 用同一 reducer 处理实时、轮询恢复和持久化历史。`phase` 表示规划、取数、覆盖检查、总结阶段，研究进度允许回到取数阶段补查；`run_status` 是可开始下一轮的持久化终态。点击停止取消任务，切换会话仅断开订阅。`GET /api/sessions/{id}` 额外返回当前身份的 `active_run_id`，供重新打开会话时继续同步。

## 研究总结的质量控制

多来源公司分析先归纳结论，再按资金面、消息面、近期走势说明证据与边界。总结输入只保留去重后的少量资讯摘要及带日期的行情区间统计，不将资讯全文、重复财报报道或旧公告直接当作近期分析。研究正文通过输出检查后发送，原始结果仍可在工具记录中查看。

总结为空、复制长段原文或生成被截断时，最多再尝试一次归纳；仍失败只展示短版关键事实并提示总结未完成。来源列表只附实际引用的记录。MiniMax-M3 的总结调用关闭可选 thinking，避免思考占用正文输出预算；不对不支持此选项的模型发送该参数。
