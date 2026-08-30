import { lazy, Suspense, useEffect, useRef } from "react";
import {
  ArrowRightOutlined,
  FundOutlined,
  GlobalOutlined,
  LineChartOutlined,
  RadarChartOutlined,
  RiseOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { useChatStore } from "../../stores/chatStore";

const MessageBubble = lazy(() =>
  import("./MessageBubble").then((module) => ({ default: module.MessageBubble })),
);

const promptGroups = [
  {
    icon: <LineChartOutlined />,
    tone: "emerald",
    eyebrow: "公司研究",
    title: "给出一家公司的完整画像",
    prompt: "分析阳光电源的资金面、消息面和近期走势",
    detail: "行情、资金、新闻与公告",
  },
  {
    icon: <FundOutlined />,
    tone: "gold",
    eyebrow: "行业筛选",
    title: "用多个条件筛选标的",
    prompt: "筛选银行股中股息率最高的 10 家公司",
    detail: "筛选、排名与横向对比",
  },
  {
    icon: <GlobalOutlined />,
    tone: "blue",
    eyebrow: "事件追踪",
    title: "追踪新闻与公司事件",
    prompt: "整理宁德时代最近的重要新闻和潜在影响",
    detail: "新闻、公告与市场催化",
  },
  {
    icon: <RadarChartOutlined />,
    tone: "violet",
    eyebrow: "市场观察",
    title: "观察当日市场结构",
    prompt: "今日涨停的股票有哪些，按行业和市值整理",
    detail: "行情、板块与交易特征",
  },
];

function selectPrompt(prompt: string) {
  window.dispatchEvent(new CustomEvent("fdp:select-prompt", { detail: prompt }));
}

export function ChatWindow() {
  const messages = useChatStore((s) => s.messages);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="fdp-welcome">
        <div className="fdp-welcome-inner">
          <div className="fdp-welcome-kicker"><ThunderboltOutlined /> 多源金融数据智能体</div>
          <h1>从问题到证据，<br /><span>再到清晰判断。</span></h1>
          <p className="fdp-welcome-lead">
            用自然语言连接行情、资金、财务、新闻、公告与研报。
            DataPilot 会拆解问题、核验证据，然后给出可追溯的结论。
          </p>

          <div className="fdp-capability-strip" aria-label="研究流程">
            <span><b>01</b> 拆解问题</span>
            <i />
            <span><b>02</b> 调用数据</span>
            <i />
            <span><b>03</b> 核验证据</span>
            <i />
            <span><b>04</b> 综合分析</span>
          </div>

          <div className="fdp-prompt-grid">
            {promptGroups.map((item) => (
              <button
                type="button"
                className={`fdp-prompt-card ${item.tone}`}
                key={item.eyebrow}
                onClick={() => selectPrompt(item.prompt)}
              >
                <span className="fdp-prompt-icon">{item.icon}</span>
                <span className="fdp-prompt-eyebrow">{item.eyebrow}</span>
                <strong>{item.title}</strong>
                <span className="fdp-prompt-detail">{item.detail}</span>
                <span className="fdp-prompt-action">开始这项研究 <ArrowRightOutlined /></span>
              </button>
            ))}
          </div>

          <div className="fdp-welcome-footnote">
            <span><RiseOutlined /> 最多 8 次必要取数</span>
            <i />
            <span><SafetyCertificateOutlined /> 证据与结论分层呈现</span>
            <i />
            <span>分析仅供研究参考</span>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="fdp-chat-area">
      <div className="fdp-chat-thread">
        <div className="fdp-thread-date"><span>当前分析</span></div>
        <Suspense fallback={<div className="fdp-thread-loading">正在加载分析记录…</div>}>
          {messages.map((m) => (
            <MessageBubble key={m.id} msg={m} />
          ))}
        </Suspense>
      </div>
    </div>
  );
}
