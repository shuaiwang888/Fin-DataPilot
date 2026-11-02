# Fin-DataPilot 当前状态与路线图

> 本文以当前代码为基线，记录已经落地的能力、尚未完成的扩展位和后续优先级。完整数据流和实现细节见 `PROJECT_SUMMARY.md`。

## 1. 产品目标

Fin-DataPilot 面向自然语言金融研究场景：

```text
用户问题
  → 拆解研究步骤
  → 调用结构化金融数据、新闻、公告、研报或联网搜索
  → 核对证据完整性
  → 必要时补查或重规划
  → 输出带来源、条件和风险说明的回答
```

项目不执行真实交易、转账或账户操作。当前也不是批量 ETL、指标仓库或量化回测系统。

## 2. 当前技术栈

| 层级 | 当前实现 | 状态 |
|---|---|---|
| 前端 | React 18、Vite、TypeScript、Ant Design X、Zustand | 已落地 |
| API | FastAPI、ORJSON、SlowAPI、CORS | 已落地 |
| Agent | LangGraph、LangChain | 已落地 |
| LLM | `ChatOpenAI` 兼容层，默认 MiniMax-M3 | 已落地 |
| 数据源 | 同花顺问财 OpenAPI、AnySearch CLI | 已落地 |
| 流式协议 | `fetch + ReadableStream` 的 POST SSE | 已落地 |
| 持久化 | SQLAlchemy Async + SQLite | 已落地 |
| 身份 | HMAC 签名匿名 Bearer Token | 已落地 |
| 记忆 | 工作记忆、会话摘要、长期用户记忆 | 已落地 |
| 后端部署 | Hugging Face Docker Space | 已落地 |
| 前端部署 | GitHub Pages | 已落地 |
| 外置数据库 | Turso/libSQL 配置占位 | 未验证 |
| Agent checkpoint | LangGraph Checkpointer | 未接入 |
| 可观测平台 | LangFuse 配置占位 | 未接入 |
| 数据库迁移 | Alembic | 未接入 |

## 3. 当前架构

```text
┌─────────────────────────────────────────────────────────────┐
│ Frontend                                                    │
│ Sidebar / Chat / Trace / Tool Result / Skill / Memory       │
│ Zustand + useChatStream + API client                        │
└──────────────────────────┬──────────────────────────────────┘
                           │ Bearer / REST / POST-SSE
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ FastAPI                                                     │
│ Auth / Sessions / Memories / Skills / Agent Runs            │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ LangGraph                                                   │
│ Policy → Planner → Skill Context Loader → Router            │
│       → Executor → Reflector ──┐                             │
│                     ▲          └→ 补查或重规划               │
│                     └──────────── Synthesizer → END          │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ ToolRegistry                                                │
│ financial / news / announcement / report / anysearch        │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
              问财 OpenAPI / AnySearch CLI
                           │
                           ▼
      SQLite: sessions / messages / tool_runs / agent_runs /
              skill_prefs / session_memories / long_term_memories
```

## 4. 已落地能力

### 4.1 身份、会话和权限

- 服务端签发、校验和续期匿名 Bearer Token。
- 所有会话、记忆和运行都按令牌用户隔离。
- 客户端不能通过请求体选择 `user_id`。
- 支持会话新建、列表、详情、重命名、删除和清空。
- 每个身份默认最多保留 50 个会话。
- 普通用户与管理员接口分离。
- Skill 诊断和变更使用独立 `ADMIN_API_KEY`。

### 4.2 Agent 编排

- Planner 预先生成 1～8 个可执行子任务。
- 规划失败时不会直接回答，而是生成确定性研究兜底步骤。
- Skill Context Loader 按计划加载仓库内 `SKILL.md` 和模板。
- Router 优先消费 Reflector Hint，其次消费计划步骤。
- 支持 `<step_N_top_stock>` 等前序结果占位符。
- Executor 在 dispatch 前检查取消和代码级 Policy。
- Reflector 结合确定性规则和 LLM 判断证据完整性。
- 计划耗尽且证据不足时回到 Planner 重规划。
- Synthesizer 是唯一最终正文出口。

### 4.3 调用限制和循环保护

- 每个问题最多 8 次 Skill dispatch；环境变量高于 8 时会被钳回。
- 最多 5 个规划周期。
- 默认后台超时 600 秒。
- 完全相同的连续调用会终止。
- 同一 Skill 连续返回空结果会终止或切换 AnySearch。
- `financial-query` 连续指标窗口超过 30 天时拒绝执行。
- 真实下单、转账和账户操作在入口直接拒绝。

### 4.4 Skill

当前注册 5 个能力：

1. `financial-query`：结构化金融数据、筛选、排名和 TopN。
2. `news-search`：财经新闻。
3. `announcement-search`：公告和公司事件。
4. `report-search`：研报。
5. `anysearch`：联网搜索、正文提取和垂直搜索兜底。

统一 `ToolResult` 已支持：

- `data`
- `error`
- `trace_id`
- `duration_ms`
- `source`
- `as_of`
- `citations`
- `raw_ref`

### 4.5 SSE 和后台运行

- POST SSE 支持 Bearer Header 和请求体。
- 15 秒 keep-alive 和 heartbeat。
- 计划、工具调用、结果、反思和回答 token 实时展示。
- 后台 Task 与 HTTP 连接解耦。
- `AgentRun` 持久化状态、事件和最终文本。
- 前端断线后每 2 秒轮询运行状态，最多 70 次。
- 用户可以停止运行；同进程任务直接取消，数据库同时记录取消请求。

### 4.6 记忆

- 单次运行工作记忆不写入长期画像。
- 会话摘要默认 30 天有效。
- 长期记忆默认每个身份最多 100 条。
- 回忆时综合词项重合、重要性、类别和更新时间。
- 只保存用户明确表达的稳定事实。
- 明确拒绝秘密和高敏感信息。
- 用户可以逐条删除或清空全部记忆。

### 4.7 前端

- 完整会话侧栏和欢迎页。
- 流式消息、计划/反思折叠面板、工具结果卡片。
- Markdown 和 GFM 表格渲染。
- 新会话服务端 ID 更新不会误杀当前 SSE。
- 切换会话会中断旧订阅并阻止旧事件污染新会话。
- token 通过 rAF 合并，降低渲染频率。
- Skill 管理在公共 SPA 中保持只读。
- 记忆查看和删除界面已完成。

### 4.8 部署与测试

- 后端 Docker Space 使用 Python 3.11、UID 1000、单 Uvicorn worker。
- `/data` 持久卷检测和管理员诊断接口已完成。
- 前后端独立 GitHub Actions 部署。
- CI 执行 Ruff、Mypy、Pytest、ESLint 和 Vite Build。
- 当前后端测试基线：194 个用例。

## 5. 当前未完成或仅占位的能力

### 5.1 LangGraph Checkpoint

图没有配置 Checkpointer。当前断线恢复只能读取自建运行事件和最终文本，不能从某个 LangGraph 节点继续执行。

### 5.2 Turso/libSQL

配置类和诊断接口认识 Turso 变量，但生产依赖没有安装，URL 构造和集成测试也没有验证。正式支持前需要：

- 选择并锁定驱动。
- 明确 SQLAlchemy URL 格式。
- 增加 CI 集成测试。
- 验证事务、并发和 JSON 事件更新。
- 制定 SQLite 到远程库迁移方案。

### 5.3 Anthropic

LLM 工厂有 `anthropic` 分支，但 requirements 没有 `langchain-anthropic`。启用前要补依赖、流式行为测试和模型输出兼容测试。

### 5.4 LangFuse

存在环境变量但没有 trace 集成。当前可观测能力来自 JSON 日志、trace ID、`tool_runs` 和 `agent_runs`。

### 5.5 历史执行轨迹回放

后端消息保存 Tool/Thinking JSON，但前端加载历史时会清空这些字段，只恢复消息正文。

### 5.6 数据库迁移

当前只有 `Base.metadata.create_all()`，没有版本化 schema 迁移。

### 5.7 应用层数据缓存

问财请求会声明上游可缓存，但项目自身没有查询结果缓存、缓存键、TTL 或失效策略。

### 5.8 并行子任务

`AGENT_MAX_PARALLEL_SKILLS` 保留在配置中，但图当前有意串行执行，以支持后一步依赖前一步证据。

## 6. 已知风险

### 6.1 SSE 写放大

已修复：`token_delta`、heartbeat 不进入持久化日志，只保存公开执行事件与最终答案。

### 6.2 订阅断开后的内存 Queue

已修复：客户端断开后停止投递实时事件，后台继续执行与持久化。

### 6.3 旧代码 Skill 恢复

新上传代码 Skill 已拒绝，但启动恢复仍可能导入持久卷中遗留的 `.py` Skill，与当前安全声明不一致。

### 6.4 AnySearch 删除保护

`anysearch` 是仓库内置能力，但上传管理模块的不可删除集合当前只列出四个问财 Skill。管理员删除端点需要阻止误卸载 AnySearch。

### 6.5 多实例停止语义

活动 Task Registry 位于进程内。数据库可以传播 `cancelling` 状态，但只有持有任务的进程能立即 `task.cancel()`；多副本场景需要共享队列或 Worker 控制平面。

### 6.6 本地密钥

本地 `.env` 可能包含真实问财密钥。虽然被 `.gitignore` 排除，仍需确认未进入日志或历史并定期轮换。

## 7. 路线图

### P0：安全与生产可靠性

- [ ] 删除或禁用旧 `.py` Skill 启动导入路径。
- [ ] 将 `anysearch` 加入不可删除的内置能力集合。
- [ ] 引入 Alembic 并创建当前 schema 基线迁移。
- [ ] 为生产配置增加启动自检：`AUTH_SECRET`、LLM、问财、管理员密钥和持久卷。
- [ ] 轮换并审计本地/CI/HF Secret。

验收标准：

- API 进程不会加载租户 Python。
- 所有内置 Skill 都不能通过删除 API 卸载。
- 新旧数据库可以通过版本化迁移升级。
- 生产缺少关键安全配置时拒绝启动或明确降级。

### P1：事件与运行系统

- [x] token 仅实时传输，以最终正文恢复，避免逐 token 写入。
- [x] 持久化阶段事件和最终文本，不逐 token 重写完整 JSON 数组。
- [x] 订阅断开后停止向 Queue 投递，只保留持久化。
- [x] 明确 `running/cancelling/cancelled/failed/completed` 前后端类型。
- [ ] 评估 LangGraph SQLite/Postgres Checkpointer。

验收标准：

- 长回答的数据库提交次数与 token 数解耦。
- 断开连接不会造成无消费者 Queue 增长。
- 运行状态机在单进程和多副本语义一致。

### P2：历史与可观测性

- [x] 从持久化消息恢复工具调用、公开执行轨迹与确定性图表。
- [ ] 增加运行详情/trace 页面。
- [ ] 接入 LangFuse 或 OpenTelemetry，并确保不记录密钥和敏感记忆。
- [ ] 区分并展示“接口查询时间”和“数据业务时点”。
- [ ] 会话列表增加分页和搜索。

验收标准：

- 刷新页面后仍可查看与实时运行一致的审计轨迹。
- 可通过 `run_id/trace_id` 定位一次问题的 API、Agent、Skill 和存储记录。

### P3：数据与扩展平台

- [ ] 完成一种外置数据库的真实适配和 CI 集成测试。
- [ ] 增加应用层查询缓存及安全缓存键。
- [ ] 将可执行代码 Skill 放入独立沙箱 Worker。
- [ ] 为互不依赖的计划步骤增加有界并行执行。
- [ ] 增加正式登录/OIDC 和跨设备身份恢复。

验收标准：

- 多实例部署不依赖单机 SQLite 和进程内任务引用。
- 相同只读查询可以在明确 TTL 内复用，不混淆用户权限或数据时点。
- 第三方代码无法访问 API 进程密钥和持久卷。

## 8. 非目标

在单独立项前，以下内容不属于当前项目承诺：

- 真实证券交易和账户托管。
- 自动买卖建议或收益承诺。
- 高频行情系统。
- 完整量化回测和组合执行平台。
- 企业级身份提供商和跨设备账号同步。
- 通用文件/文档生成平台。

## 9. 变更完成标准

每项新功能应同时满足：

1. 代码路径有明确所有权和安全边界。
2. 参数和结果使用正式数据契约。
3. 涉及用户数据时按 Bearer 用户隔离。
4. 长任务可停止、可追踪、可恢复或明确说明不能恢复。
5. 不暴露模型私有思维链。
6. 不把密钥放入前端构建、日志或持久化事件。
7. 后端通过 Ruff、Mypy、Pytest。
8. 前端通过 ESLint、TypeScript 和 Vite Build。
9. 同步更新 README、后端文档、环境变量示例和完整项目总结。

## 10. 文档索引

- `README.md`：安装、使用、架构和 API 入口。
- `PROJECT_SUMMARY.md`：完整数据流、数据库、前端和风险分析。
- `backend/README.md`：后端运行、接口、持久化和测试手册。
- `CLAUDE.md`：工程协作和代码约定。
- `.env.example`：当前环境变量基线。
- `Skills/*/SKILL.md`：各能力原始执行说明。
