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
