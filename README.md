# Fin-DataPilot

> 自然语言金融数据 Agent 平台。基于 LangGraph + LangChain + FastAPI + React，以 Skill 为工具，4 个核心 skill 全部由本项目的 Agent 统一调度。

## ✨ 特性

- **隔离会话 + 历史查询**：每个浏览器获得服务端签发的匿名 Bearer 身份，会话不再共享。
- **三层 Agent 记忆**：单次运行工作记忆、会话级短期摘要、跨会话长期偏好/目标；全部按匿名浏览器身份隔离并可在界面管理、删除。
- **思考过程可见**：SSE 实时推送 `think → tool_call → tool_result → reflection → summary` 事件链。
- **受控 Skill 管理**：普通用户只能使用已发布 Skill；调试、开关、上传仅限管理员。上传默认关闭，代码 Skill 不在服务进程执行。
- **多步反思循环**：Agent 逐步执行、每次取数后反思，最多 8 次 Skill 调用。
- **可追踪运行**：每次提问有 `run_id`，支持服务端停止和完成后查询运行事件。
- **流式回答**：`token_delta` 事件，前端 rAF 合并渲染。

## 🏗️ 架构

```
[ React + Ant Design X ]              ← GitHub Pages / Cloudflare Pages
        │ HTTPS + SSE
        ▼
[ FastAPI + LangGraph :7860 ]         ← HuggingFace Spaces (Docker)
    ├─ /api/auth/anonymous  /api/agent/chat/stream  /api/agent/chat/stop
   ├─ /api/sessions     /api/skills   /api/health
   ├─ ToolSpec / ToolRegistry
   └─ 4 Skills:
       ├─ financial-query    (金融数据查询 - 同花顺问财 query2data)
       ├─ news-search        (财经资讯全文检索)
       ├─ announcement-search(公告/事件检索)
       └─ report-search      (研报全文检索)
```

## 📁 目录

```
Fin-DataPilot/
├── PLAN.md                          # 详细实施计划
├── Skills/                          # 4 个 skill 文档（agent 调度的工具）
│   ├── financial-query/             # 金融数据查询（query2data）
│   ├── news-search/                 # 新闻检索
│   ├── announcement-search/         # 公告检索
│   └── report-search/               # 研报检索
├── backend/                         # FastAPI + LangGraph 后端
│   ├── app/                         # 应用代码
│   ├── tests/                       # 单元 + 端到端测试
│   ├── requirements.txt
│   ├── Dockerfile                   # HF Space 部署
│   └── start.sh
└── frontend/                        # React + Vite 前端
```

## 🚀 快速开始

### 1. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，至少配置：
#   LLM_API_KEY        (MiniMax / OpenAI / 其他 OpenAI 兼容)
#   IWENCAI_API_KEY    (同花顺问财 - https://www.iwencai.com/skillhub)
```

### 2. 启动后端

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./start.sh
# → http://localhost:7860/api/health
```

### 3. 启动前端

```bash
cd frontend
pnpm install
pnpm dev
# → http://localhost:5173
```

浏览器首次访问会自动申请匿名身份；不要自行传递 `user_id`。

### 无登录用户如何区分

服务端为每个浏览器签发不可伪造、可续期的匿名 Bearer 身份，前端保存在该浏览器的 `localStorage`。所有会话和记忆都只按令牌中的服务端签名 `user_id` 读写，客户端不能自行指定用户。该方案不使用不透明的浏览器指纹：同一浏览器可连续使用；清理站点数据、无痕窗口或更换设备会获得新身份，旧记忆无法恢复。若需要跨设备同步或身份找回，应接入正式登录/OIDC。

生产环境必须设置稳定的 `AUTH_SECRET`，且必须为 Hugging Face Space 启用 `/data` Persistent Storage（或配置 Turso）；否则重启会丢失历史和记忆。

## 🛰️ 部署

- **后端**：HuggingFace Spaces（Docker SDK）— 推送 `backend/` 到 `git@hf.co:spaces/appQQQ/FinDataPilot`
- **前端**：GitHub Pages / Cloudflare Pages — 由 `.github/workflows/deploy.yml` 自动部署

## 🔧 配置

`.env` 关键项：

| 变量 | 说明 | 默认 |
|---|---|---|
| `LLM_PROVIDER` | `minimax` / `openai` / `anthropic` / `custom` | `minimax` |
| `LLM_BASE_URL` | OpenAI 兼容 API 地址 | `https://api.minimaxi.com/v1` |
| `LLM_API_KEY` | LLM API 密钥 | 必填 |
| `LLM_MODEL` | 模型名 | `MiniMax-M3` |
| `IWENCAI_API_KEY` | 同花顺问财 API 密钥 | 必填 |
| `CORS_ALLOW_ORIGINS` | 允许的前端 origin（逗号分隔） | `http://localhost:5173` |
| `DATA_PILOT_PORT` | 后端端口 | `7860` |
| `AUTH_SECRET` | 生产环境必须设置，用于签发浏览器身份令牌 | 必填（生产） |
| `AUTH_TOKEN_TTL_HOURS` | 匿名身份有效期；前端会在到期前续期 | `8760` |
| `ADMIN_API_KEY` | 保护诊断与 Skill 管理接口；绝不写入 `VITE_*` | 可选 |
| `ENABLE_SKILL_UPLOAD` | 是否允许管理员上传**仅 Prompt** Skill | `false` |
| `AGENT_MAX_SKILL_CALLS` | 单个问题的 Skill 调用硬上限 | `8` |
| `MEMORY_ENABLED` | 是否启用短期/长期记忆 | `true` |
| `MEMORY_SHORT_TERM_TTL_DAYS` | 会话摘要有效期 | `30` |
| `MEMORY_LONG_TERM_MAX_ITEMS` | 每个匿名身份的长期记忆上限 | `100` |

## 管理与运行接口

- `POST /api/auth/anonymous`：初始化浏览器身份；前端已自动调用。
- `GET /api/memories`、`DELETE /api/memories/{id}`、`DELETE /api/memories`：查看、逐条删除或清空当前浏览器身份的记忆。
- `POST /api/agent/chat/stream`：携带 `Authorization: Bearer <token>` 发起 SSE。
- `POST /api/agent/chat/stop`：同一身份传 `{ "run_id": "..." }` 停止任务。
- `GET /api/agent/runs/{run_id}`：同一身份读取已持久化的执行事件与最终状态。
- `/api/diag`、Skill 调试/开关/上传/删除：携带 `X-API-Key: $ADMIN_API_KEY`。生产环境请保持 `ENABLE_SKILL_UPLOAD=false`；代码 Skill 始终拒绝。

## 📜 License

Internal project. All rights reserved.
