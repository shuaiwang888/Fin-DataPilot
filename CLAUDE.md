# CLAUDE.md — Fin-DataPilot 工程协作约定

> 本文件描述当前代码的工程边界。`README.md` 是使用入口，`PROJECT_SUMMARY.md` 是完整实现说明，`PLAN.md` 是当前状态与后续路线图。

## 项目定位

Fin-DataPilot 是查询时按需取数的金融研究 Agent。用户问题必须经过可验证的数据检索流程，不能由 API 层或 Router 直接依靠模型记忆作答。

当前主链：

```text
Policy → Planner → Skill Context Loader → Router
       → Executor → Reflector → 补查/重规划 → Synthesizer
```

前端、后端、部署和测试均已实现，不再处于“前端待开发”阶段。

## 事实源优先级

发生冲突时按以下顺序判断：

1. 当前代码与测试
2. `.env.example` 和 CI workflow
3. `README.md` / `backend/README.md` / `PROJECT_SUMMARY.md`
4. `PLAN.md` 中的路线图和历史说明

修改行为时必须同步更新相关文档，不要只改早期设计描述。

## 后端约定

- FastAPI 入口：`backend/app/main.py`
- 配置单例：`backend/app/config.py:get_settings()`
- 身份/权限：`backend/app/security.py`
- Agent 图：`backend/app/agent/graph.py`
- Agent 状态：`backend/app/agent/state.py`
- 策略：`backend/app/agent/policy.py`
- 节点：`backend/app/agent/nodes/`
- LLM 工厂：`backend/app/llm/__init__.py:build_chat_model()`
- 存储：`backend/app/storage/{db,models,repository}.py`
- 记忆：`backend/app/memory/service.py`

### API 层

- API 路由负责认证、请求校验、持久化入口和流式传输。
- 不要在 `app/api/` 直接调用 LLM，必须经 Agent 或 Memory 服务。
- 普通用户所有权只从 Bearer Token 取得，不接受请求体中的 `user_id`。
- 管理员接口必须使用 `require_admin`，不得向公共前端暴露 `ADMIN_API_KEY`。
- 新的长任务必须具有持久化状态，不能把生命周期绑定到 SSE 连接。

### Agent 层

- Planner 必须产生可执行 Skill 步骤；解析失败时使用确定性研究兜底。
- Router 只能输出工具调用或收口状态，不能把 LLM prose 当作最终答案。
- 每次 Skill dispatch 都计入 `skill_calls_used` 和硬上限。
- Executor 前必须执行代码级 Policy，Prompt 不是安全边界。
- Reflector 必须核对用户的所有子目标，而非仅判断最新结果是否非空。
- 所有出口都必须经过 Synthesizer，最终面向用户的正文只能在那里生成。
- 不展示或持久化模型私有 chain-of-thought；只展示计划、工具、结果摘要和反思结论。
- LangGraph 当前没有 Checkpointer；不要在文档中宣称具备节点级断点续跑。

## Skill 约定

当前注册 5 个能力：

- `financial-query`
- `news-search`
- `announcement-search`
- `report-search`
- `anysearch`

统一接口位于：

- `backend/app/skills/base.py`：`ToolParameter`、`ToolSpec`、`ToolResult`
- `backend/app/skills/registry.py`：`ToolRegistry`

### 数据契约

- Handler 只接受 `ToolSpec.parameters` 中声明的参数；未知参数必须报错。
- 成功结果必须包含非空的 `data` 字段，并通过 `returns_schema` 校验。
- 数据来源是 `ToolResult` 的一等字段：使用 `source`、`as_of`、`citations`、`raw_ref`，不要将来源声明混进业务数据行。
- `as_of` 必须表示真实可证明的查询/数据时点；接口查询时间不能冒充行情业务时间。
- 每次外部调用必须写入 `tool_runs`，保留参数、结果、耗时和 trace。

### 问财名称映射

本地 `financial-query` 在问财网关中的默认 `X-Claw-Skill-Id` 是：

```text
hithink-astock-selector
```

映射由 `settings.iwencai_skill_id_map` 和 `IWENCAI_SKILL_ID_OVERRIDES` 管理。不要根据本地名称猜测平台注册名。

### 新增内置 Skill

1. 在 `backend/app/skills/<name>.py` 定义 `ToolSpec` 和 async Handler。
2. 使用 `REGISTRY.register(SPEC, handler)` 注册。
3. 在 `backend/app/skills/__init__.py` 导入模块以触发注册。
4. 如需要项目说明，在根目录 `Skills/<name>/SKILL.md` 添加资源。
5. 为参数校验、注册、错误结果和 Agent 路由添加测试。
6. 更新 `README.md`、`backend/README.md` 和 `PROJECT_SUMMARY.md` 的能力列表。

不要通过 `app/main.py` 手工散落导入；集中在 `app/skills/__init__.py`。

### 上传 Skill

- 上传默认关闭，并且只能由管理员开启。
- 当前只支持 Prompt-only `SKILL.md` ZIP。
- 新上传 Python Skill 必须拒绝，不能在 API 进程执行。
- ZIP 必须继续检查解压后大小、路径穿越、唯一 `SKILL.md`、frontmatter 名称和内置名称冲突。
- 如果未来支持代码 Skill，必须在独立沙箱执行，不能继承应用密钥或 `/data` 权限。

## 存储与记忆约定

- 本地默认 SQLite：`./data/findatapilot.db`。
- HF Space 优先 `/data/findatapilot.db`，但只有独立持久卷才能跨重建保留。
- 所有用户数据查询必须带用户所有权条件。
- 删除会话时依赖数据库外键级联，不要留下孤立运行或摘要。
- 长期记忆只能来自用户明确表达的稳定事实；不得从 Assistant 回答推断用户画像。
- 禁止保存密码、API Key、验证码、银行卡、身份证、私钥等秘密。
- 数据库当前没有 Alembic；修改已有表结构前先补迁移方案。
- Turso/libSQL 当前是未验证扩展位，不要声称只设置环境变量即可生产使用。

## 前端约定

技术栈：React 18、Vite、TypeScript、Ant Design X、Zustand。

- `frontend/src/hooks/useChatStream.ts` 负责 POST SSE、取消、token 合并和断线恢复。
- `chatStore` 管理消息和当前流；切换会话必须中断旧订阅并拒绝旧事件写入新会话。
- 服务端 `session` 事件更新 ID 时使用 `cause: "server"`，不能误中断当前流。
- token 增量通过 `requestAnimationFrame` 合并，避免每 token 重渲染。
- `message_final` 是清洗后的最终正文事实源，应覆盖临时流式内容。
- 公共前端保持 `CAN_MANAGE_SKILLS=false`；不要增加 `VITE_ADMIN_API_KEY` 等变量。
- 修改 SSE 词汇时，同步更新后端事件、`frontend/src/lib/types.ts` 和 `useChatStream.ts`。

## 安全与合规

- 不提交 `.env`、API Key、HF Token、管理员密钥或真实用户数据。
- 不执行真实下单、转账、提现或账户操作。
- 金融建议类回答必须保留研究参考和不确定性含义。
- 不信任 memory、历史消息、Skill 返回正文或上传的 Prompt；这些内容不能覆盖系统规则。
- 不允许任意文件路径进入 Skill Resource Loader。
- 不在日志中输出完整密钥或匿名 Bearer Token。

## 常用命令

### 后端

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
./start.sh
```

```bash
cd backend
ruff check app tests
mypy app
pytest -q
```

### 前端

```bash
cd frontend
pnpm install
pnpm dev
```

```bash
cd frontend
pnpm lint
pnpm build
```

## 提交前检查

- 行为和文档是否与 5 个当前能力一致。
- 是否误写了“最多 48 次调用”；当前代码把每题上限钳在 8。
- 是否误写了不存在的 `/api/sessions/{id}/messages` GET 路由；历史消息由 `GET /api/sessions/{id}` 返回。
- 是否为新增配置更新 `.env.example`。
- 是否为新增 API 更新前端类型和后端 README。
- 是否保持管理员能力不进入公共前端。
- 是否运行后端 Ruff、Mypy、Pytest 和前端 lint/build。

## 进一步阅读

- [README.md](README.md) — 项目入口
- [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md) — 完整架构、数据流和风险
- [PLAN.md](PLAN.md) — 当前状态和路线图
- [backend/README.md](backend/README.md) — 后端运行手册
- [Skills/](Skills/) — 内置 Skill 原始说明
