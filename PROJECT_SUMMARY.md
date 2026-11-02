# Fin-DataPilot 项目全景总结

## 1. 项目定位

Fin-DataPilot 是一个“按需取数型”金融研究 Agent 平台，而不是传统 ETL、数据仓库或量化交易系统。

用户通过自然语言提问，系统自动规划检索步骤，实时调用金融数据或搜索 Skill，多轮检查证据是否充分，最后生成带数据来源的中文分析结果。

当前项目已经形成完整闭环，包含：

- 匿名身份与用户数据隔离
- 多轮会话与历史记录
- LangGraph 多步骤 Agent
- 金融数据、新闻、公告、研报和联网搜索 Skill
- SSE 实时执行轨迹与流式回答
- 后台任务、断线恢复和主动停止
- 短期、长期和单次运行三层记忆
- Skill 发布、调试和上传管理
- SQLite/HF 持久化与自动部署

---

## 2. 总体架构

```text
React + Ant Design X
        │
        │ Bearer 身份 / REST / POST-SSE
        ▼
FastAPI API 层
        ├── 匿名身份与权限
        ├── 会话、消息、记忆
        ├── Skill 管理
        └── Agent Run 管理
                │
                ▼
LangGraph Agent
Policy → Planner → Skill Context Loader
       → Router → Executor → Reflector
                     ▲          │
                     └── 补查/重规划
                            │
                            ▼
                       Synthesizer
                            │
             ┌──────────────┴──────────────┐
             ▼                             ▼
       同花顺问财 OpenAPI             AnySearch CLI
       行情/新闻/公告/研报           联网搜索/正文提取
             │                             │
             └──────── ToolResult ─────────┘
                            │
                            ▼
             SQLite / HF / 外置数据库扩展位
      sessions / messages / runs / events /
      tool_runs / memories / skill_prefs
```

### 关键入口

- 后端入口：`backend/app/main.py`
- Agent 图：`backend/app/agent/graph.py`
- Agent 状态：`backend/app/agent/state.py`
- Agent 节点：`backend/app/agent/nodes/`
- Skill 注册中心：`backend/app/skills/registry.py`
- 数据模型：`backend/app/storage/models.py`
- 前端入口：`frontend/src/App.tsx`
- SSE Hook：`frontend/src/hooks/useChatStream.ts`

---

## 3. 一次提问的完整数据流转

### 3.1 浏览器身份初始化

前端第一次访问时调用：

```http
POST /api/auth/anonymous
```

服务端签发如下结构的匿名 Bearer Token：

```text
v1.anon_<随机ID>.<过期时间>.<HMAC-SHA256签名>
```

令牌保存在浏览器 `localStorage`。以后所有会话、消息、记忆和运行记录都从令牌中取得 `user_id`，客户端不能自行指定用户。

默认令牌一年有效，前端会在过期前七天自动续期。清除浏览器数据、使用无痕窗口或更换设备后会获得新的匿名身份，旧身份下的数据无法自动找回。

相关代码：

- `frontend/src/lib/auth.ts`
- `backend/app/security.py`

### 3.2 前端建立乐观消息

用户发送问题后，前端立即：

1. 插入用户消息气泡。
2. 插入空的 Assistant 流式气泡。
3. 创建 `AbortController`。
4. 通过 `fetch` 发起 POST SSE 请求。

```http
POST /api/agent/chat/stream
Authorization: Bearer <token>
Content-Type: application/json

{
  "query": "用户问题",
  "session_id": "可选"
}
```

如果没有 `session_id`，后端会使用问题前 30 个字符作为标题创建会话，并推送 `session` 事件。

### 3.3 保存消息并加载上下文

后端收到请求后依次执行：

1. 校验会话是否属于当前匿名身份。
2. 将用户消息写入 `messages`。
3. 加载该会话之前的历史消息。
4. 加载会话级短期摘要。
5. 从长期记忆中选出与当前问题相关的项目。
6. 创建状态为 `running` 的 `agent_runs` 记录。
7. 向前端发送 `run_id`。
8. 将 Agent 放入独立的后台 Task 中执行。

因此，即使 Hugging Face 代理中断 SSE，后台任务仍可继续执行。

### 3.4 前置策略检查

进入 LangGraph 前先执行非 LLM 策略检查：

- 拒绝下单、代买、代卖、转账、提现等真实账户操作。
- 对推荐、买入、卖出、目标价等问句添加研究参考提示。
- `financial-query` 连续行情窗口超过 30 天时拒绝调用，要求拆分区间。

策略不仅存在于 Prompt 中，还会在 Skill 执行前再次通过代码检查。

### 3.5 Planner 生成执行计划

Planner 根据以下内容生成严格 JSON 计划：

- 用户最新问题
- 最近 6 条对话记录
- 回忆出的用户上下文
- 当前可用 Skill 与参数 Schema
- 已经执行的工具和结果（重新规划时）

计划步骤结构：

```json
{
  "goal": "取得近期行情和资金变化",
  "target_skill": "financial-query",
  "args": {
    "query": "宁德时代近期涨跌幅、成交额、资金净流入",
    "limit": "10"
  },
  "success_criteria": "取得可引用的结构化行情数据"
}
```

简单问题通常是一条计划；复合问题会拆成 2～8 步。如果规划 LLM 失败或返回非法格式，系统会根据金融关键词生成确定性的兜底计划，避免直接依靠模型已有知识回答。

### 3.6 Skill 上下文加载

系统会根据计划，从仓库的 `Skills/<name>/SKILL.md` 加载对应说明和轻量模板：

- 总注入预算默认 12,000 字符。
- 单份 Skill 说明最多约 6,000 字符。
- 单个 Skill 最多加载 4 个模板。
- 只允许读取项目固定的 `Skills/` 目录。
- 拒绝路径穿越和用户提供的任意文件路径。

加载内容属于单次运行临时状态，不写入长期记忆。

### 3.7 Router 选择下一步

路由优先级为：

1. Reflector 生成的补查 Hint。
2. Planner 中下一个待执行步骤。
3. 计划异常时，由 LLM 生成新的工具调用。

后续步骤可以引用前一步结果：

```text
<step_0_top_stock>
<step_0_top_name>
<step_0_top_code>
<step_0_first>
```

Router 会从前一步数据中选择市值最大、涨幅最高或第一条数据，替换占位符后再调用公告、新闻或研报 Skill。

Router 具有循环和成本保护：

- 连续两次 Skill 和参数完全相同则停止。
- 连续三次调用同一 Skill 且多次为空则停止。
- 每个问题最多执行 8 次 Skill 调用；配置高于 8 时会被钳回。
- 最多进行 5 次重新规划。

当前 Agent 是严格串行执行，后一步可以依赖前一步结果。`AGENT_MAX_PARALLEL_SKILLS` 目前只是兼容配置，没有并发执行多个 Skill。

### 3.8 Executor 调用数据源

所有 Skill 都通过统一调用链：

```text
ToolSpec
   → 参数校验
   → Policy 检查
   → Handler 执行
   → 返回值校验
   → ToolResult
```

统一结果结构：

```json
{
  "tool": "financial-query",
  "ok": true,
  "data": {},
  "error": null,
  "trace_id": "...",
  "duration_ms": 1234,
  "meta": {},
  "source": "同花顺问财 OpenAPI",
  "as_of": "接口查询时间",
  "citations": [],
  "raw_ref": "iwencai://..."
}
```

参数层会拒绝：

- 未声明参数
- 参数类型错误
- 必填参数为空
- 数值越界
- 字符串长度越界
- 不符合正则规则的值

成功结果还会根据简化 JSON Schema 校验。每次调用都会写入 `tool_runs`，保存参数、完整结果、耗时、成功状态和 trace ID。

### 3.9 Reflector 判断证据是否充分

Reflector 不只判断“是否拿到数据”，还会核对用户问题的全部子目标。

主要规则包括：

- 空结果：使用用户原始说法重试一次。
- 金融 Skill 仍为空：转 AnySearch 联网兜底。
- 返回内容与用户指定实体不匹配：转 AnySearch 做实体消歧。
- 先筛选股票再查公告、新闻或研报：自动提取目标股票并继续调用。
- 用户要求“公告和研报”：要求两种数据都完成。
- 原因、风险、影响分析：通常继续补充公告、新闻和研报。
- 计划仍有步骤：继续推进计划。
- 计划耗尽但证据不足：回到 Planner 重新规划。

Reflector 可能返回：

```text
sufficient → 进入总结
need_more  → 继续调用 Skill 或重新规划
failed     → 带错误证据进入总结
```

### 3.10 Synthesizer 生成最终回答

总结器只允许基于已经取得的 evidence 生成回答：

- 不得编造缺失数据。
- 多行数据优先输出 Markdown 表格。
- 投资判断必须说明条件、风险和不确定性。
- 引用只能使用工具实际返回的 Citation ID。
- 禁止输出 `<think>`、内部推理或工作步骤。

为避免超出模型上下文：

- 单个工具结果默认最多 30,000 字符。
- 文章正文类字段每项最多 600 字符。
- 总长度超预算时从尾部移除结果条目。
- AnySearch 原始输出最多保留 50 KB。

最终答案通过 `token_delta` 流式返回，结束时发送 `message_final`。

### 3.11 持久化、断线恢复和停止

后台任务完成后：

1. 保存 Assistant 消息。
2. 将 `agent_runs` 更新为 `completed`、`failed` 或 `cancelled`。
3. 保存最终文本。
4. 保存最近 128 条运行事件。
5. 异步更新短期摘要和长期记忆。

如果 SSE 中断，前端每 2 秒查询一次：

```http
GET /api/agent/runs/{run_id}
```

最多轮询 70 次。恢复过程直接读取持久化结果，不会重新执行付费 Skill。

用户点击停止时，前端先请求：

```http
POST /api/agent/chat/stop
```

随后中断浏览器 SSE。后端同时记录持久化取消状态，并取消当前进程中的后台 Task。

---

## 4. Skill 体系

当前代码实际注册 5 个能力，而不是早期 README 所写的 4 个。

| Skill | 用途 | 数据源 |
|---|---|---|
| `financial-query` | 行情、财务、估值、资金、筛选、排名、TopN | 问财 `query2data` |
| `news-search` | 财经新闻全文检索 | 问财综合搜索 `news` |
| `announcement-search` | 公告、业绩、回购、解禁、监管事件等 | 问财综合搜索 `announcement` |
| `report-search` | 研报、机构、分析师、评级、目标价、摘要 | 问财综合搜索 `report` |
| `anysearch` | 联网搜索、垂直搜索、URL 提取、批量搜索 | 本地 AnySearch CLI |

### 4.1 问财 Skill

四个问财 Skill 都携带统一的 `X-Claw-*` 请求头，并在结果中补充：

- 接口查询时间
- 数据源名称
- 查询条件
- Vendor 入口 URL
- 数据条目自身的 URL（如有）
- 内部 `raw_ref`

其中 `as_of` 表示接口查询时间，并不冒充实际行情或财务数据的业务时点；具体数据时点仍以返回字段为准。

### 4.2 AnySearch

AnySearch 不直接实现搜索 HTTP 协议，而是由后端异步启动仓库内的 CLI 子进程。

支持动作：

- `search`：通用或垂直搜索
- `extract`：URL 正文提取为 Markdown
- `batch_search`：批量搜索
- `get_sub_domains`：查询垂直领域的子域能力

后端优先探测 Python，其次 Node.js；检测结果可写入 `runtime.conf`。子进程默认超时 30 秒。

### 4.3 Skill 注册与发布

所有 Skill 通过 `ToolRegistry` 注册，Registry 负责：

- 保存 ToolSpec 与 Handler
- 维护启用状态
- 生成 OpenAI Function Schema
- 向 Prompt 渲染可用能力
- 校验参数
- 调用 Handler
- 校验返回结果

Skill 的启用状态是管理员发布决策，存放在数据库的系统级 `skill_prefs` 中，不是普通用户的个人偏好。

公共前端只展示已经发布的 Skill。切换、调试、上传和删除接口必须使用 `X-API-Key: ADMIN_API_KEY`。

上传功能默认关闭；开启后仅支持 Prompt-only `SKILL.md`，新上传的 Python 代码 Skill 会被拒绝。

---

## 5. 数据库存储结构

项目目前没有金融数据仓库和批量 ETL。外部数据在用户提问时实时获取，原始结果主要作为审计 JSON 保存。

### 5.1 `sessions`

保存会话：

- `id`
- `user_id`
- `title`
- `created_at`
- `updated_at`

默认每个匿名身份最多保存 50 个会话。新建会话后，会按创建时间清理最旧的超额会话。

### 5.2 `messages`

保存消息：

- 用户、Assistant、System 或 Tool 角色
- 消息正文
- Tool Call JSON
- Thinking JSON
- 创建时间

删除会话时消息级联删除。

### 5.3 `tool_runs`

保存每次工具调用的审计信息：

- Skill 名称
- 参数 JSON
- 返回结果 JSON
- 成功状态
- 耗时
- trace ID
- 所属会话

### 5.4 `agent_runs`

保存一次 Agent 执行：

- `run_id`
- 会话和用户归属
- 原始问题
- 运行状态
- 最近 128 条事件
- 最终文本
- 错误信息

### 5.5 `skill_prefs`

保存 Skill 全局发布和启用状态。当前使用特殊用户 `__system__` 表示全局配置。

### 5.6 `session_memories`

保存会话级短期摘要：

- 摘要正文
- 已处理消息数
- 过期时间
- 用户和会话归属

默认有效期为 30 天。

### 5.7 `long_term_memories`

保存跨会话稳定事实：

- 分类：preference、profile、goal、portfolio、context
- 内容
- 去重键
- 重要性 1～5
- 来源会话
- 最近访问时间

默认每个匿名身份最多 100 条。

---

## 6. 三层记忆体系

### 6.1 工作记忆

只在单次 Agent Run 中存在，记录：

- 当前问题
- 已完成步骤
- 已取得的工具证据

不会持久化为长期用户画像。

### 6.2 会话级短期记忆

每轮回答后生成滚动摘要，保留：

- 用户当前目标
- 已确认条件
- 重要结论
- 尚未完成的问题

### 6.3 跨会话长期记忆

只保存用户明确表达、未来仍有价值的稳定事实，如：

- 投资偏好
- 风险偏好
- 长期关注对象
- 投资目标
- 组合背景

记忆整理会拒绝密码、API Key、验证码、银行卡、身份证、私钥和精确敏感信息。LLM 不可用时，规则仍可捕获“请记住……”等明确请求。

用户可以查看、逐条删除长期记忆，也可以同时清空长期记忆和所有短期摘要。

---

## 7. SSE 事件协议

后端向前端推送的主要事件包括：

| 事件 | 含义 |
|---|---|
| `session` | 服务端创建了新会话 |
| `run` | 后台 Agent Run 已创建 |
| `ping` | 初始连接确认 |
| `heartbeat` | 长连接保活 |
| `think` | 计划、能力加载或收口说明 |
| `tool_call` | 即将调用 Skill |
| `tool_result` | Skill 调用结果 |
| `reflection` | 证据是否充分的判断 |
| `preamble` | 查询条件和结果数量摘要 |
| `summary_start` | 开始生成最终回答 |
| `token_delta` | 最终回答增量文本 |
| `message_final` | 清洗后的最终消息 |
| `done` | Agent 图执行结束 |
| `run_status` | 后台任务最终状态 |
| `error` | 执行或生成错误 |

前端使用 `fetch + ReadableStream` 解析 POST SSE，不使用原生 `EventSource`，因为原生 `EventSource` 不支持自定义 POST Body 和 Bearer Header。

流式 token 会通过 `requestAnimationFrame` 合并，每个动画帧最多更新一次 Zustand 和 React，避免一个 token 触发一次全页面渲染。

---

## 8. 前端架构

前端技术栈：

- React 18
- Vite
- TypeScript
- Ant Design
- Ant Design X
- Zustand
- React Markdown
- Remark GFM

### 8.1 状态管理

#### `chatStore`

负责：

- 当前会话消息
- 流式回答状态
- 公开阶段、计划、展示契约与错误状态
- 思考步骤
- 工具调用记录
- 当前回答是否已经开始
- 会话切换时中断旧 SSE

#### `sessionStore`

负责：

- 会话列表
- 当前选中的会话
- 新增、删除、重命名
- 加载状态

#### `skillStore`

负责：

- Skill 列表
- 启用状态展示
- Skill 管理抽屉状态
- 上传或删除后的本地更新

### 8.2 页面组件

- `Sidebar`：会话列表、新建、重命名、删除、清空和工作区入口
- `ChatWindow`：欢迎页和对话消息区
- `MessageInput`：提问输入和停止生成
- `MessageBubble`：用户或 Assistant 消息
- `ThinkingPanel`：计划和反思过程
- `ToolCallCard`：工具参数、状态、耗时和结果
- `PreambleCard`：查询条件和返回数量
- `SkillManagerDrawer`：已发布能力查看
- `MemoryManagerModal`：长期记忆查看和删除

公共 SPA 中 `CAN_MANAGE_SKILLS=false`，因此不会把管理员密钥暴露到前端构建中。

---

## 9. API 概览

### 9.1 公共接口

- `GET /api/health`
- `GET /api/echo`
- `POST /api/auth/anonymous`

### 9.2 Bearer 身份接口

- `GET /api/auth/me`
- `GET /api/sessions`
- `POST /api/sessions`
- `GET /api/sessions/{id}`
- `PATCH /api/sessions/{id}`
- `DELETE /api/sessions/{id}`
- `DELETE /api/sessions`
- `GET /api/memories`
- `DELETE /api/memories/{id}`
- `DELETE /api/memories`
- `GET /api/skills`
- `POST /api/agent/chat/stream`
- `POST /api/agent/chat/stop`
- `GET /api/agent/runs/{run_id}`

### 9.3 管理员接口

- `GET /api/diag`
- `GET /api/diag/anysearch`
- `PATCH /api/skills/{name}`
- `POST /api/skills/{name}/debug`
- `POST /api/skills/upload`
- `DELETE /api/skills/{name}`

管理员接口使用独立的 `X-API-Key`，不会复用浏览器匿名身份。

---

## 10. 部署架构

### 10.1 后端

后端部署到 Hugging Face Docker Space：

- Python 3.11 Slim
- FastAPI + Uvicorn
- 单 Worker
- 监听端口 7860
- `/api/health` Docker 健康检查
- 非 root 用户 UID 1000
- `/data` 用于持久化数据库和上传的 Skill

部署脚本会把以下内容复制到临时 Git 仓库，再推送到 HF Space：

- `backend/app`
- `backend/requirements.txt`
- `backend/Dockerfile`
- `backend/start.sh`
- `Skills/`
- `.env.example`

### 10.2 前端

前端通过 GitHub Actions 发布到 GitHub Pages：

- Node.js 20
- pnpm 9.15.9
- `pnpm install --frozen-lockfile`
- TypeScript 编译
- Vite 生产构建
- GitHub Pages Artifact 发布

`VITE_API_BASE` 指向 HF Space 后端；默认资源基础路径为 `/Fin-DataPilot/`。

### 10.3 CI

后端质量门禁：

- Ruff
- Mypy strict
- Pytest

前端质量门禁：

- ESLint
- TypeScript
- Vite Build

Dependabot 每周检查 Python、npm 和 GitHub Actions 依赖。

---

## 11. 本次验证结果

本次基于当前工作区实际执行：

- 后端测试：`237 passed`
- Mypy：48 个后端源文件无问题
- Ruff：通过
- 前端 ESLint：通过
- 前端生产构建：通过

这说明项目主链、Agent 循环、策略、记忆、持久化、Skill Registry、AnySearch、上传安全和断线恢复等关键逻辑目前具备较完整的自动测试保障。

---

## 12. 当前边界与工程风险

### 12.1 文档同步要求

项目入口、后端手册、协作约定和路线图现已按当前实现统一为 5 个能力、每题最多 8 次 Skill 调用，并确认前端已经完整落地。后续修改 Skill、SSE 事件、API、配置或部署流程时，应同步维护这些文档，避免历史设计再次覆盖当前事实。

### 12.2 当前不是 ETL 或金融数据仓库

系统在用户提问时实时请求外部 API，没有：

- 定时批量采集
- 指标宽表
- 数据清洗流水线
- 数据版本管理
- 应用层查询缓存
- 向量数据库

相同问题可能再次产生外部 API 请求和成本。

### 12.3 SSE 事件持久化

已修复逐 token 写放大：token 与 heartbeat 只在实时订阅中传输，阶段事件和 `message_final` 持久化。最终正文单独保存，恢复时以最终答案为准。

### 12.4 断开连接后的 Queue

订阅断开后关闭事件投递，只继续后台执行与持久化。结束时仅发送终态与哨兵，不累积无人消费的 token。

### 12.5 LangGraph 没有 Checkpointer

当前恢复依赖自建的 `agent_runs/events_json`，可以恢复最终结果，但不能从 LangGraph 某个节点的中断位置继续执行。

如果未来需要真正的节点级断点续跑，应接入 LangGraph Checkpointer。

### 12.6 历史与可视化恢复

前端通过 `chatEvents.ts` 同一 reducer 处理实时事件、后台恢复和历史水合。新版消息保留公开执行事件、完整工具参数与结果、图表和查询前置信息；旧版只能恢复原先保存的字段。会话详情返回 `active_run_id`，打开运行中会话时继续同步。


### 12.7 Turso、Anthropic、LangFuse 尚未真正落地

- Turso/libSQL 配置存在，但生产依赖仍被注释。
- Anthropic 分支存在，但 `langchain-anthropic` 没有加入 requirements。
- LangFuse 只有环境变量，没有实际 trace 接入代码。

目前稳定可用的 LLM 路径主要是 MiniMax、OpenAI 和其他 OpenAI 兼容服务。

### 12.8 旧代码 Skill 恢复路径

新上传接口会拒绝 Python Skill，但启动恢复逻辑仍可能导入持久目录里已经存在的旧 `.py` Skill。

这与“代码 Skill 不在 API 进程执行”的安全声明不完全一致。生产环境应删除旧代码导入路径，或者将代码 Skill 移入没有应用凭据和持久卷权限的独立沙箱。

### 12.9 数据库没有迁移机制

当前启动时只调用 SQLAlchemy `create_all()`，没有 Alembic 等版本化迁移工具。新增表可以自动创建，但修改已有字段或约束时无法平滑升级旧数据库。

### 12.10 本地密钥管理

本地 `.env` 中存在非占位格式的问财密钥。该文件当前被 `.gitignore` 排除且没有被 Git 跟踪，但仍应：

- 确认未进入日志、截图或历史提交
- 定期轮换
- 生产环境只通过 Secret 管理器注入

---

## 13. 建议的后续优先级

### P0：安全与生产可靠性

1. 清理旧代码 Skill 自动导入路径。
2. 引入 Alembic 数据库迁移。
3. 轮换并检查本地真实密钥。
4. 验证 HF `/data` 是真实持久卷。

### P1：性能与可恢复性

1. 已完成：阶段事件和最终文本持久化，token 仅实时传输。
2. 断开连接后停止向内存 Queue 投递。
3. 只保存关键轨迹和最终答案，减少 SQLite 写入。
4. 评估接入 LangGraph Checkpointer。

### P2：用户体验

1. 已完成：恢复历史消息的工具调用、公开进度与图表。
2. 增加会话搜索和分页。
3. 增加运行历史和 trace 详情页。
4. 展示数据业务时点与接口查询时点的区别。

### P3：平台扩展

1. 完成真正的 Turso/Postgres 适配。
2. 接入 LangFuse 或 OpenTelemetry。
3. 增加应用层查询缓存。
4. 将独立 Skill 执行迁移到沙箱 Worker。
5. 为复杂问题实现安全的并行子任务执行。

---

## 14. 总结

Fin-DataPilot 已经不是简单的“LLM 套 API”原型，而是一个具备以下能力的完整金融 Agent 平台：

- 可靠的匿名身份隔离
- 持久化会话与记忆
- 可解释的多步骤规划
- 受控 Skill 调度
- 多轮证据反思和补查
- 数据来源与查询时点追踪
- SSE 流式回答
- 后台执行和断线恢复
- 停止、审计和运行状态管理
- 前后端自动化测试与部署

其当前核心优势是“自然语言问题 → 多源数据检索 → 证据充分性判断 → 可追溯回答”的完整闭环。下一阶段最值得投入的方向是事件存储性能、历史轨迹回放、数据库迁移、外部存储适配和 Skill 沙箱化。

## 2026-11-02 对话重构

新增 `agent/events.py` 与 `agent/presentation.py`，把公开事件元数据和确定性图表构造从传输/总结逻辑中抽离。`message_final.presentation` 统一描述取得的证据、缺口与图表；图表不是模型生成的数值。前端新增 ResearchProgress 与 AnswerVisuals，支持指标切换、负值对比、来源时点、继续提炼与 Markdown 导出。切换会话只断开订阅，停止按钮才请求取消；会话 HTTP 响应增加当前会话检查，防止过期响应覆盖其他会话。前端测试通过 Vitest 加入 CI。
