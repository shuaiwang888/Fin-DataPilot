import { lazy, Suspense, useEffect, useRef } from "react";
import { ArrowRightOutlined, BankOutlined, RadarChartOutlined, SearchOutlined, FundOutlined, LineChartOutlined, SafetyCertificateOutlined, DotChartOutlined } from "@ant-design/icons";
import { useChatStore } from "../../stores/chatStore";

const MessageBubble = lazy(() =>
  import("./MessageBubble").then((module) => ({ default: module.MessageBubble })),
);

const promptGroups = [
  {
    index: "01",
    icon: <BankOutlined />,
    eyebrow: "公司研究",
    title: "读懂一家公司的价值",
    prompt: "分析阳光电源的资金面、消息面和近期走势",
    detail: "行情、资金、新闻与公告",
  },
  {
    index: "02",
    icon: <SearchOutlined />,
    eyebrow: "行业筛选",
    title: "发现符合条件的公司",
    prompt: "筛选银行股中股息率最高的 10 家公司",
    detail: "筛选、排名与横向对比",
  },
  {
    index: "03",
    icon: <RadarChartOutlined />,
    eyebrow: "事件追踪",
    title: "看清事件背后的影响",
    prompt: "整理宁德时代最近的重要新闻和潜在影响",
    detail: "新闻、公告与市场催化",
  },
  {
    index: "04",
    icon: <FundOutlined />,
    eyebrow: "市场观察",
    title: "把握市场的变化",
    prompt: "今日涨停的股票有哪些，按行业和市值整理",
    detail: "行情、板块与交易特征",
  },
];

function selectPrompt(prompt: string) {
  window.dispatchEvent(new CustomEvent("fdp:select-prompt", { detail: prompt }));
}

export function ChatWindow() {
  const messages = useChatStore((s) => s.messages);
  const sessionId = useChatStore((s) => s.sessionId);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const followBottom = useRef(true);

  useEffect(() => { followBottom.current = true; }, [sessionId]);

  useEffect(() => {
    if (containerRef.current && followBottom.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [messages]);

  useEffect(() => {
    const container = containerRef.current;
    const thread = container?.firstElementChild;
    if (!container || !thread) return;
    const observer = new ResizeObserver(() => {
      if (followBottom.current) container.scrollTop = container.scrollHeight;
    });
    observer.observe(thread);
    return () => observer.disconnect();
  }, [messages.length]);

  if (messages.length === 0) {
    return (
      <div className="fdp-welcome">
        <div className="fdp-welcome-inner">
          <section className="fdp-desk-brief">
            <p className="fdp-welcome-kicker"><span /> FIN-DATAPILOT · 金融研究助手</p>
            <h1>从一个问题，<br />到一份<span>有据可循的洞察。</span></h1>
            <p className="fdp-welcome-lead">
              查数据、读公告、看新闻。把分散的金融信息，<br className="fdp-desktop-break" />整理成带来源的分析与直观的图表。
            </p>
            <div className="fdp-feature-chips" aria-label="研究能力">
              <span><DotChartOutlined /> 多来源数据</span>
              <span><LineChartOutlined /> 可视化对比</span>
              <span><SafetyCertificateOutlined /> 结论可追溯</span>
            </div>
          </section>

          <div className="fdp-issue-list" aria-label="示例研究议题">
            <div className="fdp-issue-label"><strong>开始你的下一份研究</strong><span>选择一个方向，或直接提问</span></div>
            <div className="fdp-prompt-grid">
            {promptGroups.map((item) => (
              <button
                type="button"
                className="fdp-prompt-card"
                key={item.index}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => selectPrompt(item.prompt)}
              >
                <span className="fdp-prompt-index">{item.icon}</span>
                <span className="fdp-prompt-copy">
                  <span className="fdp-prompt-eyebrow">{item.eyebrow}</span>
                  <strong>{item.title}</strong>
                  <span className="fdp-prompt-detail">{item.detail}</span>
                </span>
                <span className="fdp-prompt-action"><ArrowRightOutlined /></span>
              </button>
            ))}
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="fdp-chat-area" onScroll={(event) => {
      const element = event.currentTarget;
      followBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 100;
    }}>
      <div className="fdp-chat-thread">
        <div className="fdp-thread-date"><span>本轮备忘</span></div>
        <Suspense fallback={<div className="fdp-thread-loading">正在加载分析记录…</div>}>
          {messages.map((m) => (
            <MessageBubble key={m.id} msg={m} />
          ))}
        </Suspense>
      </div>
    </div>
  );
}
