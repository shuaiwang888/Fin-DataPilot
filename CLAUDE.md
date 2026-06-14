# CLAUDE.md — Fin-DataPilot 项目指引

> 给 Claude Code 阅读的工程规范。`PLAN.md` 是设计文档，本文件是协作约定。

## 项目定位

自然语言金融数据 Agent 平台。用户通过对话触发 Agent 自动选择并执行 Skill（金融数据查询 / 新闻 / 公告 / 研报），将结果整合后回答给用户。

## 关键约定

### Skills（位于 `Skills/`）

- **4 个 Skill**：financial-query / news-search / announcement-search / report-search
- **统一接口**：后端 `app/skills/{base,registry}.py` 定义 `ToolSpec` / `ToolResult` / `ToolRegistry`。
- **本地 name 与 iWencai 注册名不同**：`financial-query`（本地） ↔ `hithink-financial-query`（iWencai），由 `IWENCAI_SKILL_ID_OVERRIDES` 或 `settings.iwencai_skill_id_map` 维护。
- **不输出数据源**：Skill 只返回裸数据，由 Agent 层负责数据源标注。

### 后端（`backend/`）

- **入口**：`app/main.py`（FastAPI），`lifespan` 钩子里跑 `init_db()` 初始化表。
- **配置**：`app/config.py` 用 `pydantic-settings` 读 `.env`，单例 `get_settings()`。
- **LLM**：`app/llm/__init__.py:build_chat_model()` 工厂，默认 MiniMax-M3。
- **Agent**：`app/agent/graph.py` 装配 LangGraph StateGraph；节点 `app/agent/nodes/{skill_router,executor,reflector,synthesizer}.py`。
- **状态**：`app/agent/state.py` 定义 `AgentState` 与 SSE 事件常量。
- **存储**：`app/storage/{db,models,repository}.py`，默认 SQLite（生产可换 Turso libSQL）。

### 前端（`frontend/` - 阶段 6+ 待实现）

- React + Vite + TypeScript + Ant Design X
- Zustand 状态管理
- 自研 `useChatStream` 处理 POST + SSE

## 常见操作

### 安装依赖

```bash
cd backend && pip install -r requirements.txt
```

### 启动 dev server

```bash
cd backend && ./start.sh
```

### 添加新 Skill

1. 在 `backend/app/skills/<name>.py` 写一个 `ToolSpec` + async handler。
2. 末尾 `REGISTRY.register(SPEC, handler)`。
3. 在 `backend/app/main.py` 的 lifespan 之前 `from app.skills import <name>`（或通过 `app/skills/__init__.py` 自动收集）。
4. 重启服务，`GET /api/skills` 应能看到。

### 修改 LLM 配置

改 `.env` 中 `LLM_*` 系列变量，重启即可。`LLM_PROVIDER=custom` 走 `ChatOpenAI(base_url=...)`，可对接任何 OpenAI 兼容服务。

## 不要做的事

- ❌ 在 Skill 层输出"数据来源：xxx"声明（由 Agent 统一处理）。
- ❌ 在 `app/api/` 直接调用 LLM，**必须**经 Agent 层。
- ❌ 把 LLM API Key 提交到 git。
- ❌ 改 `Skills/` 下原始 skill 文档的 8 个 X-Claw-* Header 规则。

## 进一步阅读

- [PLAN.md](PLAN.md) — 完整实施计划
- [Skills/](Skills/) — 各 skill 详细文档
- [backend/README.md](backend/README.md) — 后端开发文档（待写）
