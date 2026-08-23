import { useEffect, useRef } from "react";
import {
  ArrowRightOutlined,
  FundOutlined,
  GlobalOutlined,
  LineChartOutlined,
  RiseOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { useChatStore } from "../../stores/chatStore";
import { MessageBubble } from "./MessageBubble";

const promptGroups = [
  {
    icon: <LineChartOutlined />,
    tone: "emerald",
    eyebrow: "公司研究",
    title: "快速了解一家上市公司",
    prompt: "分析贵州茅台最新估值水平和盈利能力",
    detail: "估值 · 财务 · 经营质量",
  },
  {
    icon: <FundOutlined />,
    tone: "gold",
    eyebrow: "行业筛选",
    title: "从全市场发现投资线索",
    prompt: "筛选银行股中股息率最高的 10 家公司",
    detail: "选股 · 排名 · 横向对比",
  },
  {
    icon: <GlobalOutlined />,
    tone: "blue",
    eyebrow: "事件追踪",
    title: "追踪新闻与市场催化",
    prompt: "整理宁德时代最近的重要新闻和潜在影响",
    detail: "新闻 · 公告 · 情绪信号",
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
        <div className="fdp-welcome-orb fdp-orb-one" />
        <div className="fdp-welcome-orb fdp-orb-two" />
        <div className="fdp-welcome-inner">
          <div className="fdp-welcome-kicker"><ThunderboltOutlined /> AI 驱动的金融研究工作台</div>
          <h1>把复杂数据，变成<br /><span>清晰的投资洞察。</span></h1>
          <p className="fdp-welcome-lead">
            用自然语言连接行情、财务、新闻与公告。告诉我你想研究什么，
            我会规划路径、调用数据并给出可追溯的分析。
          </p>

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
                <span className="fdp-prompt-action">试试这个问题 <ArrowRightOutlined /></span>
              </button>
            ))}
          </div>

          <div className="fdp-welcome-footnote">
            <span><RiseOutlined /> 多源数据联动</span>
            <i />
            <span><SafetyCertificateOutlined /> 结果过程可追溯</span>
            <i />
            <span>7×24 小时研究陪伴</span>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="fdp-chat-area">
      <div className="fdp-chat-thread">
        <div className="fdp-thread-date"><span>当前分析</span></div>
        {messages.map((m) => (
          <MessageBubble key={m.id} msg={m} />
        ))}
      </div>
    </div>
  );
}
