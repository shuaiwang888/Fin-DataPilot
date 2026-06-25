---
name: financial-query
displayName: 金融数据查询
description: 金融结构化数据统一查询入口。通过同花顺问财 OpenAPI `query2data` 端点，用自然语言查询A股、指数、港股、美股、基金、ETF、期货、宏观、可转债等全市场金融结构化数据，支持行情指标、技术形态、财务指标、行业概念等多条件组合筛选标的，同时支持事件数据、经营数据、财务数据等查询。新闻/公告/研报全文等纯文本类查询走 `news-search` / `announcement-search` / `report-search`这几个SKill。
license: Complete terms in LICENSE.txt
---

# 金融数据查询 使用指南

## 版本

`2.0.0`（与 `X-Claw-Skill-Version` 保持一致）。本地 `name` 为 `financial-query`；同花顺问财平台上的注册名（`X-Claw-Skill-Id`）仍为 `hithink-financial-query`，API 调用必须沿用注册名。

## 技能概述

本 skill 是**金融结构化数据查询的统一入口**，通过调用同花顺问财 OpenAPI 的 `query2data` 端点，用一句自然语言就能拿到 A 股 / 港股 / 美股 / 基金 / 期货 / ETF / 板块 / 概念 / 指数 等全市场的结构化数据。

**覆盖标的（标的种类，不限于此）：**

- **股票**：A 股、港股、美股
- **基金**：公募基金、ETF、LOF、指数基金
- **指数**：宽基指数、行业指数、概念指数
- **期货 / 债券 / 可转债**
- **板块 / 概念 / 行业 / 地域**：成分、资金、涨跌
- **宏观经济**：GDP、CPI、PMI、社融、LPR

**支持查询的指标维度（不限于此）：**

- **行情**：最新价、涨跌幅、成交量、成交额、换手率、市值、振幅
- **估值**：PE、PB、PS、PEG、股息率、估值分位
- **财务**：营收、净利润、ROE、毛利率、负债率、现金流、EPS、研发投入
- **事件**：业绩预告、分红、回购、增持、解禁、龙虎榜、大宗交易、调研
- **资金**：主力资金净流入、北向资金、融资融券
- **指数 / 板块数据**：指数点位、成分股、板块涨跌


## 使用前

> **首次使用 - 获取 API Key**
> 所有技能都需要 `IWENCAI_API_KEY` 环境变量。如果用户尚未配置，按以下步骤引导：
>
> **步骤 1**：打开同花顺 i 问财 SkillHub → `https://www.iwencai.com/skillhub`
>
> **步骤 2**：登录
>
> **步骤 3**：点击具体的 Skill，在弹窗的"安装方式 → Agent 用户"中复制 `IWENCAI_API_KEY`
>
> **步骤 4**：配置环境变量
>
> ```bash
> # macOS / Linux
> export IWENCAI_API_KEY="your-api-key"
>
> # PowerShell
> $env:IWENCAI_API_KEY = "your-api-key"
> ```

## 核心处理流程

### 步骤 1: 接收用户 Query

接收自然语言请求，识别**标的（股票/基金/指数/板块/期货/债券）**、**指标（行情/财务/估值/事件）**、**时间范围**。

> **注意：本 skill 不做"筛选/选股/TopN/排名"**——若用户问的是"前 N 名 / 满足条件的所有标的 / 帮我选股"等，请改交给 `hithink-astock-selector` 等专用选股 skill。

### 步骤 2: Query 改写

将口语化问句改写为标准问财查询问句，**保持原意不变**：

- `"贵州茅台现在多少钱"` → `"贵州茅台 最新价"`
- `"贵州茅台的 PE 是多少"` → `"贵州茅台 PE(TTM)"`
- `"中证 500 指数当前点位"` → `"中证 500 指数 最新点位"`
- `"比亚迪最近的销量"` → `"比亚迪 月度销量"`
- `"宁德时代去年营收"` → `"宁德时代 2024 营业收入"`

**思维链拆解（按需）：**
- 单次查询：能一句话答的，直接发
- 多次查询：需要多角度数据的，拆成 2-4 个独立 query 并行调用

### 步骤 3: API 调用

调用同花顺问财 OpenAPI 网关的 `query2data` 端点，使用 `scripts/cli.py` CLI 或直接构造 HTTP 请求。**所有请求必须严格携带 8 个 Header**：

| Header | 取值说明 |
|--------|----------|
| `Authorization` | `Bearer <IWENCAI_API_KEY>` |
| `Content-Type` | `application/json` |
| `X-Claw-Call-Type` | `normal`（正常请求）/ `retry`（失败重试） |
| `X-Claw-Skill-Id` | **`hithink-financial-query`** |
| `X-Claw-Skill-Version` | **`2.0.0`** |
| `X-Claw-Plugin-Id` | `none` |
| `X-Claw-Plugin-Version` | `none` |
| `X-Claw-Trace-Id` | 64 字符 hex（`secrets.token_hex(32)`） |

**请求体：**
```json
{
  "query": "改写后的查询语句",
  "page": "1",
  "limit": "100",
  "is_cache": "1",
  "expand_index": "true"
}
```

**Python 调用示例：**
```python
import os, json, secrets, urllib.request

url = "https://openapi.iwencai.com/v1/query2data"
api_key = os.environ["IWENCAI_API_KEY"]
trace_id = secrets.token_hex(32)

payload = {
    "query": "贵州茅台 最新价",
    "page": "1", "limit": "100",
    "is_cache": "1", "expand_index": "true",
}
headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json",
    "X-Claw-Call-Type": "normal",
    "X-Claw-Skill-Id": "hithink-financial-query",
    "X-Claw-Skill-Version": "2.0.0",
    "X-Claw-Plugin-Id": "none",
    "X-Claw-Plugin-Version": "none",
    "X-Claw-Trace-Id": trace_id,
}
req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                              headers=headers, method="POST")
resp = urllib.request.urlopen(req, timeout=30)
result = json.loads(resp.read().decode("utf-8"))

datas = result.get("datas", [])
code_count = result.get("code_count", 0)
```

### 步骤 4: 空数据处理

`datas` 为空时**最多重试 2 次**逐步放宽：

- 首次重试：去掉最苛刻的次要条件
- 二次重试：用更通用表述（如"贵州茅台"代替"600519.SH 贵州茅台"）

每次重试把 `X-Claw-Call-Type` 改为 `retry`。

### 步骤 5: 数据解析

`datas` 是对象数组，列名随查询变化（中文 key，如"股票代码 / 股票简称 / 最新价 / 涨跌幅 / 市值 / PE / 主营行业"等）。`code_count` 是符合条件的总条数（> `len(datas)` 时需翻页）。

## 请求参数

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `query` | string | 是 | 用户/改写后的查询问句（中文自然语言） |
| `page` | string | 否 | 分页页码，默认 `1` |
| `limit` | string | 否 | 每页条数，默认 `100`（最高 500） |
| `is_cache` | string | 否 | 是否走缓存，默认 `1` |
| `expand_index` | string | 否 | 是否展开指数，默认 `true` |

## 响应参数

| 参数名 | 类型 | 说明 |
|--------|------|------|
| `datas` | array | 金融数据对象数组，列名随 query 变化 |
| `code_count` | int | 符合查询条件的总条数（可能 > `len(datas)`） |
| `chunks_info` | object | 查询字句信息（解析后的条件） |
| `status_code` | int | `0` = 成功；非 0 = 错误（见错误码） |

## CLI 使用

`scripts/cli.py` 提供跨平台命令行入口。

```bash
python3 scripts/cli.py --query "贵州茅台 最新价"
python3 scripts/cli.py --query "今日涨停 行业=科技" --page 1 --limit 50
python3 scripts/cli.py --query "银行 股息率前10" --timeout 60
```

**参数：**
- `--query` 必填
- `--page` 默认 1
- `--limit` 默认 10
- `--api-key` 可选（默认从 env 读）
- `--call-type` 默认 `normal`
- `--timeout` 默认 30

## 错误码

| 状态码 | 含义 | 建议 |
|---|---|---|
| `-2326 / -2126 / -1325` | 统计量过大或超时 | 缩小时间范围 / 减少指标 / 收窄标的 |
| `-2331 / -1330 / -2309` | 数据库查询超时 | 稍后重试 |
| `-2322 / -2321 / -1321` | 指标不存在 | 调整指标表达 |
| `-225` | 当前周期表无此指标 | 调整周期或口径 |

## 与其他 skill 的边界

| 问句 | 用 `financial-query` (本) | 用专门 skill |
|---|---|---|
| "贵州茅台最新价" | ✅ | — |
| "贵州茅台的 PE / 营收 / ROE" | ✅ | — |
| "中证 500 指数当前点位" | ✅ | — |
| "比亚迪上个月销量" | ✅ | — |
| "宁德时代 最近新闻全文" | ❌ | `news-search` |
| "宁德时代 最近公告" | ❌（除非只要摘要数字） | `announcement-search` |
| "宁德时代 最新研报" | ❌（除非只要研报评级汇总） | `report-search` |


**简单规则：**
- 问的是**"某个标的的某个数字/字段是多少"** → 本 skill
- 问的是**"符合某条件的所有标的 / 排名 / TopN / 选股"** → 专用选股 skill
- 问的是**"全文/原文/URL"** → 专门检索 skill
- 问的是**"执行操作"（模拟交易）** → `simulated-trading`

## 代码结构

```
financial-query/
├── SKILL.md          # 本文件
├── LICENSE.txt       # 许可证
└── scripts/
    └── cli.py        # CLI 入口（封装 8 个 Header + 重试 + 解析）
```
