# Fin-DataPilot

> 自然语言金融数据 Agent 平台。基于 LangGraph + LangChain + FastAPI + React，以 Skill 为工具，4 个核心 skill 全部由本项目的 Agent 统一调度。

## ✨ 特性

- **多轮对话 + 历史查询**：左侧会话列表、新对话、搜索、删除。
- **思考过程可见**：SSE 实时推送 `think → tool_call → tool_result → reflection → summary` 事件链。
- **Skill 即插即用**：前端可视化启用/禁用；后端统一 `ToolSpec` / `ToolRegistry` 接口。
- **多步反思循环**：Agent 失败可自动重试 / 切换 Skill，最多 5 轮。
- **流式回答**：`token_delta` 事件，前端 rAF 合并渲染。

## 🏗️ 架构

```
[ React + Ant Design X ]              ← GitHub Pages / Cloudflare Pages
        │ HTTPS + SSE
        ▼
[ FastAPI + LangGraph :7860 ]         ← HuggingFace Spaces (Docker)
   ├─ /api/agent/chat/stream
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
    └── (TBD - 见阶段 6-7)
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

### 3. 启动前端（见阶段 6-7）

```bash
cd frontend
pnpm install
pnpm dev
# → http://localhost:5173
```

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

## 📜 License

Internal project. All rights reserved.
