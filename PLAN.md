# Fin-DataPilot — 实施计划

## Context

构建一个通过自然语言进行金融取数的 **Agent 平台**，命名为 **Fin-DataPilot**。该平台以 **Agent 架构** 为核心，以 **Skill 为工具**，用户通过对话触发 Agent 自动选择并执行相应的 Skill，再将结果整合后返回给用户。平台对标"豆包"等对话式 AI 产品，核心特点：

- **多轮对话 + 历史查询**：左侧会话列表，支持新对话、切换、删除。
- **执行轨迹可见**：在回答中展示 Agent 的"计划 → 调用工具 → 检查结果 → 补查 → 总结"全过程，不暴露模型私有推理。
- **Skill 即插即用**：用户可在前端可视化地启用/禁用/配置 Skill；后端以统一接口注册 Skill（用户后续会提供 Skill 文档，按接口实现）。
- **多步反思循环**：Agent 可在单次回答中多次调用 Skill，每次都根据已有结果决定下一步。
- **可观测、可调试**：每次 Agent 运行的每一步都有 trace id，可在前端和日志中追溯。

### 业务目标

5 个核心 Skill（用户后续提供具体文档，按统一接口开发）：

1. **金融取数**（如行情、财务、估值、指数、行业、概念等结构化字段）
2. **新闻搜索**
3. **公告搜索**（业绩预告/解禁/增发/调研等事件）
4. **研报搜索**

### 部署目标

- **后端**：HuggingFace Spaces（git@hf.co:spaces/appQQQ/FinDataPilot），Docker SDK，端口 7860
- **前端**：GitHub（仓库待定，仓库名建议 `appQQQ/Fin-DataPilot-web` 或类似），推荐 GitHub Pages 或 Cloudflare Pages

---

## 架构选型（已确认 + 推荐）

### 已确认

| 维度 | 选型 | 理由 |
|---|---|---|
| LLM | **MiniMax-M3 起步，baseurl + apikey 可配置** | 用户要求；OpenAI 兼容协议 |
| Agent 框架 | **LangGraph + LangChain** | 用户要求；适合长任务、状态机、checkpoint、streaming 与 human-in-the-loop |
| 前端 | **React + Vite + TypeScript + Ant Design X** | 用户要求；Ant Design X 是面向 AI 对话场景的官方组件库，开箱即用 |

### 推荐

| 维度 | 选型 | 理由 |
|---|---|---|
| 后端框架 | **FastAPI + Uvicorn**（Docker 部署） | 异步、SSE、Pydantic、自动 OpenAPI 文档 |
| 后端 LLM 客户端 | **OpenAI 兼容协议 + LangChain ChatModel 适配层** | MiniMax-M3、OpenAI 兼容模型、自定义 baseurl/apikey 可共用一套工具调用协议 |
| 流式协议 | **SSE (Server-Sent Events)** | 单向、简单、CDN 友好、与 HF Spaces 兼容 |
| 状态管理（前端） | **Zustand**（本地）+ **TanStack Query**（远端缓存） | 轻量、清晰 |
| 流式 Hook | 自研 `useChatStream`（基于 `fetch` + `ReadableStream`，处理 POST + SSE） | 跨域 POST + SSE 标准做法 |
| 数据库 | **SQLite**（开发/单机） / **Turso libSQL 或 Postgres**（生产） | HF Spaces 文件持久化不稳定，生产状态建议外置 |
| 认证 | MVP：**无认证 + 简单 API Key 头**（`X-API-Key`）；后期可加 GitHub OAuth | 简化部署 |
| 限流 | `slowapi` 或 FastAPI middleware，按 IP + API Key 双维度 | 防止滥用，并避免单个前端用户拖垮外部 API |
| 可观测 | **LangFuse**（自托管/云）+ 结构化 JSON 日志 + trace_id | 开源、零依赖可选云服务 |

### 架构审查结论（基于当前仓库）

当前 `PLAN.md` 的方向正确，但有几处需要升级：

- **Skill 已经具备初始资产**：当前仓库已有 `Skills/financial-query`、`news-search`、`announcement-search`、`report-search` 的文档与脚本，阶段 3 应先做 `SkillAdapter`，把这些现有 Skill 包装成统一工具。
- **金融数据查询边界更细**：`financial-query` 文档明确它只适合"某标的某字段/数字"，不负责选股、排名、TopN；计划中的"金融取数包含选股"需要拆成独立 selector 类 Skill 扩展点。
- **Data Agent 架构应从固定工具注册升级为 Skill Manifest + Tool Catalog**：运行时由 manifest 生成 LLM tool schema、前端表单 schema、权限声明和审计字段，避免每加一个 Skill 就改 Agent 核心。
- **不要暴露完整 chain-of-thought**：前端可展示"可解释执行轨迹"，包括计划、工具选择、参数、结果摘要、反思结论；不展示模型私有推理原文。
- **需要 Guardrails 层**：金融场景必须在工具调用前后做参数校验、权限检查、数据来源标注、风险提示和模拟交易确认。
- **LangGraph 应作为编排运行时，不应把所有能力写死为节点**：推荐将 Router / Executor / Reflector / Synthesizer 固定为主图，具体 Skill 通过 registry/catalog 动态注入。

> **升级决策**：采用"LangGraph 编排层 + Skill Manifest 工具层 + Data Contract 数据层 + SSE 事件层"。Agent 只负责规划、路由、执行、校验、总结；具体金融能力全部通过 Skill Manifest 接入。

---

## 整体架构图

```
┌────────────────────────────────────────────────────────────────────┐
│  Frontend (GitHub Pages / Cloudflare Pages)                        │
│  ┌──────────────┐ ┌──────────────────────┐ ┌──────────────────┐    │
│  │  Sidebar     │ │  ChatWindow          │ │  SkillManager    │    │
│  │  - 会话列表  │ │  - MessageBubble     │ │  - 启用/禁用     │    │
│  │  - 新对话    │ │  - TracePanel        │ │  - 配置          │    │
│  │  - 搜索历史  │ │  - ToolCallCard      │ │  - 测试调用      │    │
│  └──────────────┘ └──────────────────────┘ └──────────────────┘    │
│       Zustand stores: chatStore / skillStore / uiStore            │
│       useChatStream hook → POST /api/agent/chat/stream (SSE)      │
└────────────────────────────────────────────────────────────────────┘
                                │
                                │  HTTPS / SSE
                                ▼
┌────────────────────────────────────────────────────────────────────┐
│  Backend (HF Space, FastAPI :7860)                                 │
│  ┌──────────────┐ ┌────────────────┐ ┌────────────────────────┐    │
│  │  API Layer   │ │  Agent Runtime │ │  Skill Layer           │    │
│  │  /api/agent/ │ │  LangGraph     │ │  Manifest + Adapter    │    │
│  │  /api/sess/  │ │  Plan→Route    │ │  ┌──────────────────┐  │    │
│  │  /api/skill/ │ │  →Execute      │ │  │ financial-query   │  │    │
│  │  /api/health │ │  →Verify→Answer│ │  │ news-search       │  │    │
│  └──────────────┘ └────────────────┘ │  │ 公告搜索          │  │    │
│       │                  │          │  │ 研报搜索          │  │    │
│       ▼                  ▼          │  │ 模拟炒股          │  │    │
│  ┌──────────────┐ ┌────────────────┐ │  └──────────────────┘  │    │
│  │  Storage     │ │  LLM Factory   │ │  + 用户后续提供的新Skill│    │
│  │  SQLite/Turso│ │  MiniMax-M3    │ └────────────────────────┘    │
│  │  sessions +  │ │  OpenAI compat │                               │
│  │  messages +  │ │  + 可扩展      │                               │
│  │  runs/events │ │               │                               │
│  └──────────────┘ └────────────────┘                               │
└────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
                  ┌──────────────────────────────┐
                  │  External                    │
                  │  - MiniMax / OpenAI / Claude │
                  │  - 各金融数据 API（同花顺等）│
                  │  - 新闻/研报数据源           │
                  └──────────────────────────────┘
```

---

## Data Agent / LangGraph 状态机设计

采用现代 Data Agent 的分层运行时：**规划、工具路由、受控执行、结果评估、答案合成、审计回放**。LangGraph 负责状态机和 checkpoint，Skill 通过 manifest 动态注入，不把具体金融能力写死在节点里。

```
                    ┌──────────┐
   user message ──▶ │  Entry   │
                    └─────┬────┘
                          │ 写入 message + run
                          ▼
                    ┌──────────────┐
                    │  Intent      │  ← 标的/指标/时间/任务类型/风险等级
                    │  Classifier  │
                    └─────┬────────┘
                          │ intent + constraints
                          ▼
                    ┌──────────────┐
                    │   Planner    │  ← 生成可执行子任务，不暴露私有推理
                    └─────┬────────┘
                          │ plan.steps[]
                          ▼
        ┌─────────── Tool Router + Policy Gate ────────────────────────────┐
        │ 从 Tool Catalog 选择 skill；校验启用态、权限、参数、风险确认        │
        └────────────────────┬──────────────────────────────────────────────┘
                             │ tool_calls[]
                             ▼
                    ┌──────────────┐
                    │   Executor   │  ← SkillAdapter.dispatch；可并行/限流/超时
                    └─────┬────────┘
                          │ ToolResult[]
                          ▼
                    ┌──────────────┐
                    │  Verifier    │  ← 结果完整性、空数据、来源、字段可信度
                    │  Reflector   │     不足则补查；重复/越界则终止
                    └─────┬────────┘
                          │ verified_context
                          ▼
                    ┌──────────────┐
                    │ Synthesizer  │  ← 引用工具结果，生成答案/表格/图表建议
                    └─────┬────────┘
                          │ SSE: status/tool/result/answer/done
                          ▼
                       user
```

**关键点**：
- `AgentState` 传递 `messages / intent / plan / tool_calls / tool_results / verification / answer / events`。
- `Tool Catalog` 每轮从启用的 Skill Manifest 生成，避免模型看到未启用或无权限工具。
- `Verifier` 是独立节点：检查空数据、字段缺失、数据时效、来源声明、是否需要补查。
- **最大补查轮数 3**，超过后带着现有证据回答，并明确缺口；相同 `(skill, args)` 重复出现直接终止。
- 能真流式的模型使用 `astream()` 输出 `answer_delta`；不能真流式时也必须用统一 SSE 事件模拟进度，但在事件 meta 中标明 `stream_mode`。
- 只展示"执行轨迹"，不展示模型私有 chain-of-thought。

### AgentState 建议

```python
class AgentState(TypedDict, total=False):
    session_id: str
    run_id: str
    user_id: str
    messages: list[dict]
    intent: dict
    plan: dict
    enabled_tools: list[dict]
    tool_calls: list[dict]
    tool_results: list[dict]
    verification: dict
    answer: str
    citations: list[dict]
    events: list[dict]
    loop_count: int
    errors: list[dict]
```

---

## Skill 框架设计（核心可扩展点）

当前仓库已经包含多个 Skill 文档和脚本，后端不应重新发明每个 Skill，而应提供统一适配层：

- `SkillManifest`：描述名称、版本、描述、参数、返回、权限、来源、路由边界。
- `SkillAdapter`：包装 CLI、Python 函数或 HTTP API，统一成 async `dispatch()`。
- `ToolCatalog`：按用户启用态、权限、环境变量完整性生成 LLM tools schema 和前端表单 schema。
- `ToolResult`：统一封装数据、错误、trace、耗时、来源、原始响应摘要。

```python
# app/skills/base.py
from typing import Any, Awaitable, Callable, Literal
from pydantic import BaseModel, Field

class ToolParameter(BaseModel):
    name: str
    type: Literal["string", "number", "integer", "boolean", "object", "array"]
    description: str
    required: bool = True
    enum: list[Any] | None = None
    default: Any = None
    items: dict | None = None  # for array type
    properties: dict | None = None  # for object type

class SkillManifest(BaseModel):
    name: str                     # 唯一标识，如 "financial-query"
    display_name: str             # 前端展示名
    description: str              # 给 LLM 和前端看的工具描述
    category: str                 # "structured_data" | "news" | "announcement" | "report" | "trading"
    version: str
    parameters: list[ToolParameter]
    returns_schema: dict
    requires: list[str] = []      # 所需环境变量
    source: str = "iwencai"
    route_rules: list[str] = []   # 何时应该/不应该调用
    examples: list[dict] = []
    enabled_by_default: bool = True
    risk_level: Literal["low", "medium", "high"] = "low"

class ToolResult(BaseModel):
    tool: str
    ok: bool
    data: Any = None
    error: str | None = None
    trace_id: str | None = None
    duration_ms: int
    source: str | None = None
    raw_ref: str | None = None     # 原始响应落盘路径或对象存储 key
    meta: dict[str, Any] = {}

Handler = Callable[..., Awaitable[ToolResult]]

class SkillAdapter:
    manifest: SkillManifest
    async def dispatch(self, args: dict, context: dict) -> ToolResult: ...

class ToolCatalog:
    def register(self, adapter: SkillAdapter): ...
    def unregister(self, name: str): ...
    def set_enabled(self, user_id: str, name: str, enabled: bool): ...
    def list_enabled(self, user_id: str) -> list[SkillManifest]: ...
    def to_llm_tools(self, user_id: str) -> list[dict]: ...
    def to_form_schema(self, user_id: str) -> list[dict]: ...
    async def dispatch(self, user_id: str, name: str, args: dict, context: dict) -> ToolResult: ...
```

**注册示例**：

```python
# app/skills/adapters/financial_query.py
from app.skills.base import SkillManifest, ToolParameter, ToolResult, SkillAdapter

class FinancialQueryAdapter(SkillAdapter):
    manifest = SkillManifest(
        name="financial-query",
        display_name="金融数据查询",
        description="通过同花顺问财 query2data 查询某个标的的行情、财务、估值、事件等结构化字段。",
        category="structured_data",
        version="2.0.0",
        parameters=[
            ToolParameter(name="query", type="string", description="改写后的问财自然语言查询"),
            ToolParameter(name="page", type="integer", description="页码", required=False, default=1),
            ToolParameter(name="limit", type="integer", description="返回条数，最高 500", required=False, default=100),
        ],
        returns_schema={"type": "object", "properties": {"datas": {"type": "array"}}},
        requires=["IWENCAI_API_KEY"],
        source="iwencai",
        route_rules=[
            "适合：某标的某字段/数字是多少",
            "不适合：选股、筛选、排名、TopN",
        ],
    )

    async def dispatch(self, args: dict, context: dict) -> ToolResult:
        # 调用 Skills/financial-query/scripts/cli.py 或等价 Python API
        ...
```

**当前仓库 Skill 接入优先级**：

| Skill | 状态 | 路由边界 |
|---|---|---|
| `financial-query` | 已有 `SKILL.md` + CLI | 结构化字段/数字查询；不做选股、筛选、TopN |
| `news-search` | 已有 README/SKILL/scripts | 财经新闻、政策、行业动态 |
| `announcement-search` | 已有 README/SKILL/scripts | 上市公司公告、分红、回购、业绩预告等 |
| `report-search` | 已有 README/SKILL/scripts | 研报、评级、目标价、投研观点 |
| `simulated-trading` | 待补 | 高风险 Skill，必须加入确认步骤和交易审计 |
| selector 类 Skill | 待补 | 选股、筛选、排名、TopN，不应混在 `financial-query` 中 |

**前端使用**：`GET /api/skills` 返回所有 Skill Manifest；`PATCH /api/skills/{name}` 启用/禁用；`POST /api/skills/{name}/test` 直接调试 Skill。Agent 每轮只把用户启用且环境变量满足的 Skill 放入 LLM 工具列表。

---

## 项目结构

### 仓库布局（建议两个独立仓库）

```
# 仓库 1: appQQQ/FinDataPilot  (后端, 部署到 HF Space)
FinDataPilot/
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI 入口
│   │   ├── config.py                # pydantic-settings
│   │   ├── api/
│   │   │   ├── agent.py             # /api/agent/chat, /chat/stream
│   │   │   ├── sessions.py          # /api/sessions, /api/sessions/{id}/messages
│   │   │   ├── skills.py            # /api/skills (list/enable/disable)
│   │   │   └── health.py
│   │   ├── agent/
│   │   │   ├── graph.py             # LangGraph StateGraph
│   │   │   ├── state.py             # AgentState TypedDict
│   │   │   ├── nodes/
│   │   │   │   ├── intent_classifier.py
│   │   │   │   ├── planner.py
│   │   │   │   ├── tool_router_policy_gate.py
│   │   │   │   ├── executor.py
│   │   │   │   ├── verifier_reflector.py
│   │   │   │   └── synthesizer.py
│   │   │   ├── prompts/             # system prompts
│   │   │   ├── events.py            # SSE 事件常量
│   │   │   └── streaming.py         # SSE 事件编排
│   │   ├── skills/
│   │   │   ├── base.py              # SkillManifest / ToolResult / SkillAdapter
│   │   │   ├── catalog.py           # ToolCatalog
│   │   │   ├── manifests/
│   │   │   │   ├── financial-query.yaml
│   │   │   │   ├── news-search.yaml
│   │   │   │   ├── announcement-search.yaml
│   │   │   │   └── report-search.yaml
│   │   │   ├── adapters/
│   │   │   │   ├── financial_query.py
│   │   │   │   ├── news_search.py
│   │   │   │   ├── announcement_search.py
│   │   │   │   └── report_search.py
│   │   │   └── __init__.py
│   │   ├── llm/
│   │   │   ├── factory.py           # LLM 工厂
│   │   │   ├── base.py
│   │   │   └── providers/
│   │   │       ├── minimax.py
│   │   │       ├── openai.py
│   │   │       └── anthropic.py
│   │   ├── storage/
│   │   │   ├── db.py                # SQLAlchemy / 原始 sqlite3
│   │   │   ├── models.py
│   │   │   ├── repository.py
│   │   │   └── schema.sql
│   │   ├── utils/
│   │   │   ├── trace.py
│   │   │   ├── streaming.py         # SSE 工具
│   │   │   └── errors.py
│   │   └── web/index.html           # vanilla fallback
│   ├── tests/
│   │   ├── unit/
│   │   └── eval/                    # agent 评估样例
│   ├── pyproject.toml               # ruff + mypy strict
│   ├── requirements.txt
│   ├── .env.example
│   ├── Dockerfile
│   ├── start.sh
│   ├── README.md
│   └── CLAUDE.md
└── (HF Space 直接根目录 = backend 内容；可选方案)

# 仓库 2: appQQQ/Fin-DataPilot-web  (前端, 部署到 GitHub Pages / Cloudflare)
Fin-DataPilot-web/
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── components/
│   │   ├── layout/
│   │   │   ├── Sidebar.tsx
│   │   │   ├── TopBar.tsx
│   │   │   └── SkillManagerDrawer.tsx
│   │   ├── chat/
│   │   │   ├── ChatWindow.tsx
│   │   │   ├── MessageBubble.tsx
│   │   │   ├── UserMessage.tsx
│   │   │   ├── AssistantMessage.tsx
│   │   │   ├── TracePanel.tsx
│   │   │   ├── ToolCallCard.tsx
│   │   │   ├── ToolResultView.tsx     # 表格、图表
│   │   │   ├── WelcomeScreen.tsx
│   │   │   └── MessageInput.tsx
│   │   └── common/
│   │       ├── Empty.tsx
│   │       └── ErrorBoundary.tsx
│   ├── hooks/
│   │   ├── useChatStream.ts            # POST + SSE 核心 hook
│   │   ├── useSessions.ts
│   │   └── useSkills.ts
│   ├── stores/
│   │   ├── chatStore.ts                # Zustand: 当前会话、消息列表
│   │   ├── sessionStore.ts             # Zustand: 会话列表
│   │   ├── skillStore.ts               # Zustand: 启用/禁用状态
│   │   └── uiStore.ts                  # Zustand: 抽屉、主题
│   ├── lib/
│   │   ├── api.ts                      # fetch 封装
│   │   ├── sse.ts                      # SSE 解析
│   │   ├── markdown.tsx                # 渲染 markdown + 代码高亮
│   │   └── types.ts
│   ├── styles/global.css
│   └── assets/
├── public/
├── index.html
├── package.json
├── tsconfig.json
├── vite.config.ts
├── .github/workflows/deploy.yml        # 自动部署到 GitHub Pages
├── README.md
└── CLAUDE.md
```

> **简化方案（备选）**：把前端构建产物输出到后端 `backend/app/web_dist/`，HF Space 同时托管前端与后端，**省去第二个仓库**。优点：单仓、CORS 简单；缺点：HF 重新构建慢、前端代码混入后端仓库。

---

## 关键设计细节

### 1. SSE 事件协议（前端与后端的契约）

事件名常量定义在 `app/agent/events.py`，所有事件都带 `run_id`、`event_id`、`ts`：

| event | data 字段 | 触发时机 |
|---|---|---|
| `ping` | `{}` | 连接建立/保活 |
| `run_started` | `{session_id, run_id, user_message_id}` | 本轮运行创建 |
| `status` | `{stage, label, progress}` | 可见执行状态，如理解问题、选择工具、汇总结果 |
| `plan` | `{steps: [{id, goal, expected_tool}], visible: true}` | 可见计划，不包含私有推理 |
| `tool_call` | `{call_id, name, args, trace_id, progress}` | Skill 开始执行 |
| `tool_result` | `{call_id, name, ok, row_count, duration_ms, summary, trace_id, source}` | Skill 返回 |
| `verification` | `{verdict, missing, next_action}` | 结果完整性评估 |
| `answer_start` | `{stream_mode}` | 开始生成最终回答 |
| `answer_delta` | `{text}` | 最终回答增量 |
| `answer_final` | `{content, citations, tables, charts, tool_calls, duration_ms}` | 最终回答落库 |
| `error` | `{code, message, retryable, trace_id}` | 出错 |
| `done` | `{run_id}` | SSE 终止 |

前端 `useChatStream` 收到事件后 dispatch 到 `chatStore`，UI 各组件订阅对应字段实时渲染。

### 2. 执行轨迹（TracePanel）

- 折叠面板，默认展开；展示"执行轨迹"：计划 → 工具选择 → 参数 → 结果摘要 → 完整性评估 → 汇总。
- 每一步带时间戳 + trace_id，点击可展开原始 JSON。
- 不展示模型私有 chain-of-thought；`status` 和 `verification` 用业务语言解释当前动作。
- 结果不足时显示缺口和下一步，例如"公告结果为空，改用更宽时间范围重试"。

### 3. 多轮会话持久化

SQLite 表结构（生产用 Turso 兼容 libSQL）：

```sql
CREATE TABLE sessions (
  id TEXT PRIMARY KEY,            -- UUID
  user_id TEXT,                    -- 后期接入
  title TEXT,
  created_at TIMESTAMP,
  updated_at TIMESTAMP
);

CREATE TABLE messages (
  id TEXT PRIMARY KEY,
  session_id TEXT REFERENCES sessions(id),
  role TEXT CHECK (role IN ('user','assistant','system','tool')),
  content TEXT,
  tool_calls_json TEXT,            -- assistant 的 tool_calls
  tool_call_id TEXT,               -- tool 消息归属
  execution_json TEXT,             -- 该轮可见执行轨迹
  created_at TIMESTAMP
);
CREATE INDEX idx_messages_session ON messages(session_id, created_at);

CREATE TABLE tool_runs (
  id TEXT PRIMARY KEY,
  run_id TEXT,
  message_id TEXT REFERENCES messages(id),
  skill_name TEXT,
  args_json TEXT,
  result_json TEXT,
  ok INTEGER,
  duration_ms INTEGER,
  trace_id TEXT,
  created_at TIMESTAMP
);

CREATE TABLE agent_runs (
  id TEXT PRIMARY KEY,
  session_id TEXT REFERENCES sessions(id),
  user_message_id TEXT,
  status TEXT,
  intent_json TEXT,
  plan_json TEXT,
  verification_json TEXT,
  started_at TIMESTAMP,
  finished_at TIMESTAMP,
  duration_ms INTEGER
);

CREATE TABLE agent_events (
  id TEXT PRIMARY KEY,
  run_id TEXT REFERENCES agent_runs(id),
  event_type TEXT,
  payload_json TEXT,
  created_at TIMESTAMP
);

CREATE TABLE user_skill_prefs (
  user_id TEXT,                    -- 初期固定 'default'
  skill_name TEXT,
  enabled INTEGER,
  config_json TEXT,
  PRIMARY KEY (user_id, skill_name)
);
```

LangGraph 的 `AsyncSqliteSaver` 与 `messages` 表可共存；用 LangGraph 的 checkpointer 存状态机中间态，messages 表存"用户可见的对话"。

### 4. LLM 工厂

```python
# app/llm/factory.py
class LLMFactory:
    def create(self, settings: Settings) -> BaseChatModel:
        provider = settings.llm_provider  # "minimax" | "openai" | "anthropic" | "custom"
        if provider == "minimax":
            return ChatOpenAI(
                base_url=settings.llm_base_url,    # 默认 https://api.minimaxi.com/v1
                api_key=settings.llm_api_key,
                model=settings.llm_model,          # 默认 MiniMax-M3
                streaming=True,
            )
        elif provider == "openai":
            return ChatOpenAI(api_key=..., model=...)
        elif provider == "anthropic":
            return ChatAnthropic(...)
        elif provider == "custom":
            # 用户自定义 baseurl + apikey 的 OpenAI 兼容服务
            return ChatOpenAI(base_url=..., api_key=..., model=...)
```

`.env` 关键项：

```bash
LLM_PROVIDER=custom
LLM_BASE_URL=https://api.minimaxi.com/v1
LLM_API_KEY=...
LLM_MODEL=MiniMax-M3
LLM_TEMPERATURE=0.2
LLM_MAX_TOKENS=4096

CORS_ALLOW_ORIGINS=https://appqqq.github.io,http://localhost:5173
API_KEY=                         # 可选：客户端需要带的 X-API-Key

HF_SPACE_URL=https://appQQQ-FinDataPilot.hf.space
```

### 5. Skill 启用态与权限

- 后端 `user_skill_prefs` 是 source of truth；前端 LocalStorage 只做首屏缓存。
- `GET /api/skills` 返回 manifest、启用态、环境变量是否就绪、风险等级。
- Agent 每次运行前按 `user_id` 生成 Tool Catalog，只用启用且可执行的 Skill。
- 高风险 Skill（如模拟交易）需要 `requires_confirmation=true`，Agent 先返回确认事件，用户确认后再执行。

### 6. 反思与重试策略

- **Skill 调用失败**：自动 retry 1 次（间隔 1s）；仍失败 → 错误信息进入 Reflector，Reflector 决定：
  - 切换 Skill（同一意图不同 Skill）
  - 用更宽松的参数重试
  - 直接 Synthesizer，告诉用户"未取到数据"
- **反思轮数 > 5**：强制 Synthesizer，附注"本轮未能完整回答，建议换个问法"。
- **死循环检测**：相同 (skill, args) 在 3 轮内重复出现 → 强制结束。

### 7. 流式输出

Synthesizer 节点优先用 `llm.astream()`，每收到 chunk 立即 emit `answer_delta`。如果某个模型或兼容服务无法真流式，则后端仍按统一事件输出 `answer_delta`，并在 `answer_start.stream_mode` 标记为 `buffered`，便于前端和日志区分真实 token 流与后处理流。

### 8. 部署相关

- **HF Space Dockerfile**（端口 7860，HEALTHCHECK，依赖装好后 `uvicorn` 启动）。
- **GitHub Pages / Cloudflare Pages** 部署前端（GitHub Actions 推送 dist/）。
- **CORS**：后端白名单前端域名。
- **冷启动**：前端在用户输入时显示"Agent 唤醒中..."骨架屏。
- **保活**：GitHub Actions 每 6h 调一次 `/api/health`（可选，HF 免费版 48h 才睡）。
- **前端构建产物回写后端**（如果采用单仓简化方案）：前端 `pnpm build` → 输出到 `backend/app/web_dist/`，FastAPI 同时托管 `/assets/*` 与 `/`。

---

## 实施阶段

### 阶段 0：环境与约定

- 初始化 `FinDataPilot` 仓库（git init）；写 `README.md` + `CLAUDE.md`。
- `.gitignore` + `.env.example`。
- 选 Linter/Formatter：`ruff` + `mypy --strict` + `prettier` + `eslint`。
- 决定仓库策略：双仓 vs 单仓（推荐双仓，更清晰）。

### 阶段 1：后端骨架（可运行的最小 FastAPI）

- FastAPI app、CORS、health endpoint、基础日志。
- `pydantic-settings` 配置层。
- LLM 工厂（支持 minimax / openai / custom，**MiniMax-M3 默认**）。
- `GET /api/health` 返回模型、数据库、Skill 环境变量就绪状态。
- `POST /api/agent/chat/stream` 先返回固定 SSE 事件，验证前后端协议。

### 阶段 2：Storage 层

- 选定 SQLite（dev）/ Turso（prod）。
- 创建 `sessions / messages / agent_runs / agent_events / tool_runs / user_skill_prefs` 表。
- `repository.py` 提供 CRUD。
- 所有 SSE 事件异步写入 `agent_events`，支持运行回放和调试。

### 阶段 3：Skill Manifest + Adapter

- `SkillManifest` / `ToolResult` / `ToolCatalog` / `SkillAdapter`。
- 从当前 `Skills/*/SKILL.md` 和 `README.md` 抽取 manifest 初稿，人工补齐参数 schema。
- 接入现有四个 Skill：`financial-query`、`news-search`、`announcement-search`、`report-search`。
- `GET /api/skills` 列出所有 Skill。
- `PATCH /api/skills/{name}` 启用/禁用。
- `POST /api/skills/{name}/test` 直接调用指定 Skill。
- 单元测试：manifest 校验、参数校验、环境变量缺失、超时、错误封装。

### 阶段 4：LangGraph Data Agent 核心

- `AgentState` TypedDict 设计。
- 6 个节点：IntentClassifier / Planner / ToolRouterPolicyGate / Executor / VerifierReflector / Synthesizer。
- `StateGraph` 装配；`AsyncSqliteSaver` 持久化。
- 最大补查轮数、重复调用检测、工具超时与并发限制。
- 单元测试 + 端到端样例：问"贵州茅台的 PE"必须路由到 `financial-query`；问"宁德时代最近公告"必须路由到 `announcement-search`。

### 阶段 5：SSE 流式输出

- 完整事件协议实现（以本计划的 `run_started/status/plan/tool_call/...` 为准）。
- 进度百分比贯穿。
- 客户端断连处理（agent 端检测 `request.is_disconnected()`）。
- `agent_events` 回放接口：`GET /api/agent/runs/{run_id}/events`。

### 阶段 6：前端骨架

- Vite + React + TS + AntD X 初始化。
- 路由（`/` → 工作台；`/skills` → Skill 管理；`/settings` → 设置）。
- Layout：左侧会话栏 + 主聊天区。
- AntD X 的 `<Bubble>`、`<Conversations>`、`<Welcome>`、`<Sender>` 组件。
- `chatStore` / `sessionStore` / `skillStore` / `uiStore` (Zustand)。
- 部署到 GitHub Pages（先静态壳子）。

### 阶段 7：前后端联调

- `useChatStream` hook 解析 SSE。
- 消息渲染：用户消息 / 助手消息 / 执行轨迹面板 / 工具调用卡片 / 表格 / 图表。
- 历史会话列表 + 切换 + 搜索。
- 错误重试按钮。

### 阶段 8：Skill 管理 UI

- SkillManager 抽屉：列表、启用/禁用、配置（参数 schema 自动生成表单，用 `react-jsonschema-form` 或手撸）。
- 调试面板：选中 Skill → 输入参数 → 直接调用 → 看结果。

### 阶段 9：接入用户提供的 Skill 文档

- 用户逐个 Skill 提供文档 → 生成/补齐 `SkillManifest` → 实现 `SkillAdapter` → 注册。
- 每个 Skill 上线：单元测试 + 端到端样例 + 评估集。
- 选股/筛选/TopN 类能力单独接 selector Skill，不放进 `financial-query`。

### 阶段 10：打磨

- 限流、CORS、错误兜底。
- LangFuse 接入（或结构化日志）。
- Dockerfile 优化、镜像大小控制。
- README + 用户指南。

### 阶段 11：正式部署

- 后端 push 到 HF Space：`git push` 或 `huggingface-cli upload`。
- 前端 push 到 GitHub，触发 Actions → Pages。
- 监控首日使用、修复发现的问题。

---

## 关键文件清单（待新建）

### 后端

- `backend/app/main.py`
- `backend/app/config.py`
- `backend/app/llm/factory.py` + `providers/*.py`
- `backend/app/skills/base.py` + `catalog.py` + `adapters/*.py` + `manifests/*.yaml`
- `backend/app/agent/graph.py` + `state.py` + `nodes/*.py` + `streaming.py`
- `backend/app/api/agent.py` + `sessions.py` + `skills.py` + `health.py`
- `backend/app/storage/{db,models,repository,schema.sql}.py`
- `backend/Dockerfile` + `start.sh` + `requirements.txt` + `pyproject.toml`
- `backend/.env.example`
- `backend/README.md` + `CLAUDE.md`

### 前端

- `src/App.tsx` + `main.tsx`
- `src/components/chat/*.tsx`（ChatWindow、MessageBubble、TracePanel、ToolCallCard、WelcomeScreen、MessageInput）
- `src/components/layout/*.tsx`（Sidebar、TopBar、SkillManagerDrawer）
- `src/hooks/useChatStream.ts` + `useSessions.ts` + `useSkills.ts`
- `src/stores/{chat,session,skill,ui}Store.ts`
- `src/lib/{api,sse,markdown,types}.ts(x)`
- `package.json` + `vite.config.ts` + `tsconfig.json`
- `.github/workflows/deploy.yml`

### 当前仓库可复用资产

- `Skills/financial-query/SKILL.md` + `scripts/cli.py` → `financial-query` adapter。
- `Skills/news-search/SKILL.md` + `scripts/news_search.py` → `news-search` adapter。
- `Skills/announcement-search/SKILL.md` + `scripts/announcement_search.py` → `announcement-search` adapter。
- `Skills/report-search/SKILL.md` + `scripts/api_client.py` → `report-search` adapter。
- `Skills/*/references/api.md` → manifest 参数、返回 schema、错误处理规则。

---

## 验证（Verification）

每个阶段都必须可运行 + 可验证：

1. **阶段 1 验证**：`uvicorn` 启动 → `curl /api/health` 返回 ok → `POST /api/agent/chat/stream` 返回 `run_started/status/done`。
2. **阶段 3 验证**：`GET /api/skills` 返回当前仓库 4 个已接入 Skill → 禁用 `financial-query` 后 Tool Catalog 不再包含它。
3. **阶段 4 验证**：`pytest tests/eval/test_routing.py` 通过：结构化字段问题走 `financial-query`，新闻走 `news-search`，公告走 `announcement-search`，研报走 `report-search`。
4. **阶段 5 验证**：`curl -N -X POST /api/agent/chat/stream` 能看到 `run_started` → `plan` → `tool_call` → `tool_result` → `verification` → `answer_delta` → `answer_final` → `done`。
5. **阶段 7 验证**：浏览器打开前端 → 发送"宁德时代最近一个月的新闻" → 看到执行轨迹 + 工具调用卡片 + 真实新闻结果。
6. **阶段 9 验证**：每接入一个真实 Skill，编写至少 3 个端到端问题（happy path / 参数不全 / 失败重试）。
7. **阶段 11 验证**：HF Space 部署成功 → 前端从 `appqqq.github.io` 调用 → 完整流程无 CORS / 鉴权错误。

### 测试栈

- 后端：`pytest` + `pytest-asyncio` + `httpx.AsyncClient`（E2E）。
- 前端：`vitest` + `@testing-library/react`（关键组件）。
- Agent 评估：`tests/eval/` 下放 JSON 格式的 (query, expected_skill_sequence, expected_keywords) 样例。

---

## 风险与决策点

| 风险 | 缓解 |
|---|---|
| HF Space 免费版冷启动 30-60s | 前端骨架屏 + 唤醒提示；考虑升级到 paid tier |
| MiniMax-M3 工具调用能力未知 | 阶段 4 早期做能力评估；准备降级到 Claude Haiku / DeepSeek V3 的备选 |
| 用户 Skill 文档接口差异大 | `SkillManifest + SkillAdapter` 隔离差异，Agent 只面对统一 Tool Catalog |
| LLM 流式输出延迟感 | `requestAnimationFrame` 合并 UI 更新；token 级 + 增量 markdown 渲染 |
| CORS / 跨域 SSE | `fetch` + `ReadableStream`（POST SSE）已验证可行 |
| 用户认证缺失被滥用 | `slowapi` 限流 + 可选 `X-API-Key`；后期可加 GitHub OAuth |

### 留给用户确认的开放项

1. **仓库策略**：双仓（前后端分离）vs 单仓（前端构建产物进后端）？**推荐双仓**。
2. **数据库**：SQLite（dev）+ Turso（prod）是否同意？
3. **认证**：MVP 是否接受"无认证 + 限流"？还是必须先做 GitHub OAuth？
4. **域名**：是否需要自定义域名（Cloudflare 在前）？还是先用 `appQQQ-FinDataPilot.hf.space` + `appqqq.github.io/Fin-DataPilot-web`？
5. **Skill 文档格式**：用户后续提供时希望是 Markdown + 自由描述，还是结构化 YAML / OpenAPI？影响 skill 实现的工作量。

---

## 立即可执行的下一步

确认本计划后，建议先做：

1. 初始化 `FinDataPilot`（backend）与 `Fin-DataPilot-web`（frontend）两个仓库（或单仓）。
2. 写 `README.md` + `CLAUDE.md`。
3. 进入**阶段 1**：最小 FastAPI + LLM 工厂 + 固定 SSE 协议端点；本地跑通后部署到 HF Space。
4. 部署成功后再进入阶段 2+。
5. 用户提供 Skill 文档时，**并行**进入阶段 9，按文档实现具体 Skill。
