import pytest

from app.agent.policy import assess_tool_call, assess_user_query


def test_blocks_financial_query_continuous_window_over_30_days() -> None:
    decision = assess_tool_call(
        "financial-query", {"query": "贵州茅台过去3个月日度 PE 走势"}
    )
    assert not decision.allowed
    assert decision.code == "FINANCIAL_QUERY_WINDOW_EXCEEDED"


def test_allows_financial_query_window_at_or_below_30_days() -> None:
    assert assess_tool_call("financial-query", {"query": "贵州茅台近30天收盘价"}).allowed


def test_blocks_forbidden_financial_query_term() -> None:
    decision = assess_tool_call("financial-query", {"query": "贵州茅台资金面"})
    assert not decision.allowed
    assert decision.code == "FINANCIAL_QUERY_FORBIDDEN_TERM"


def test_blocks_transaction_execution_request() -> None:
    decision = assess_user_query("请帮我下单买入贵州茅台")
    assert not decision.allowed
    assert decision.code == "EXECUTION_NOT_SUPPORTED"


@pytest.mark.parametrize("query", [
    "对比上一个交易日的数据呢",
    "和上一交易日比较一下成交额",
    "查询贵州茅台最近5个交易日收盘价",
    "今天的交易量与昨日相比如何",
    "交易数据按行业汇总",
    "分析量化交易策略的收益和风险",
    "介绍交易所的交易时间与规则",
    "帮我查询历史交易记录",
    "请说明交易规则",
    "自动交易模型有哪些",
    "下单流程是什么",
    "请解释如何下单买入股票",
    "帮我分析下单失败的原因",
    "转账手续费是多少",
    "请查询转账记录",
    "提现流程和到账时间是什么",
    "提现吗，手续费是多少",
    "下单前需要考虑哪些风险",
    "不要帮我下单，只分析历史数据",
    "别转账，帮我对比上一交易日的数据",
    "不是让你下单，是比较两个交易日的价格",
    "不用交易，只要分析",
    "解释“帮我下单”是什么意思",
    "请分析买入时机",
    "请给我买入建议",
    "这只股票值得买入吗",
])
def test_allows_financial_research_and_operation_explanations(query: str) -> None:
    assert assess_user_query(query).allowed, query


@pytest.mark.parametrize("query", [
    "请帮我下单买入贵州茅台",
    "帮我买入100股工商银行",
    "替我卖出持仓",
    "直接交易100股",
    "帮我执行交易",
    "下单买入100股",
    "转账500元给张三",
    "请把500元转给张三",
    "帮我提现100元",
    "立即撤单",
    "帮我取消交易",
    "帮我加仓工商银行",
    "不要解释，直接下单买入",
    "不用分析直接帮我下单",
    "先比较数据，然后下单买入",
])
def test_still_blocks_explicit_execution_intent(query: str) -> None:
    decision = assess_user_query(query)
    assert not decision.allowed, query
    assert decision.code == "EXECUTION_NOT_SUPPORTED"


def test_advice_remains_allowed_with_a_research_notice() -> None:
    decision = assess_user_query("对比上一交易日的数据，这只股票值得买入吗")
    assert decision.allowed
    assert decision.notices


async def test_previous_trading_day_query_enters_the_research_graph(monkeypatch) -> None:
    from app.agent import graph

    visited = []

    class ResearchGraph:
        async def astream(self, state, config):
            visited.append(state["user_query"])
            yield {"planner": {"plan": [{"target_skill": "financial-query", "goal": "对比交易日数据"}]}}

    async def synthesize(_state):
        yield {"event": "message_final", "data": {"content": "交易日数据对比", "tool_calls": []}}

    monkeypatch.setattr(graph, "get_graph", lambda: ResearchGraph())
    monkeypatch.setattr(graph, "synthesize", synthesize)
    events = [event async for event in graph.run_agent_stream(
        user_query="对比上一个交易日的数据呢", history=[], session_id="policy-regression",
    )]
    assert visited == ["对比上一个交易日的数据呢"]
    assert not any(event["event"] == "error" for event in events)
    assert any(event["event"] == "message_final" for event in events)
