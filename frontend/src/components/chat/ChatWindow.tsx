import { lazy, Suspense, useEffect, useRef } from "react";
import { ArrowRightOutlined } from "@ant-design/icons";
import { useChatStore } from "../../stores/chatStore";

const MessageBubble = lazy(() =>
  import("./MessageBubble").then((module) => ({ default: module.MessageBubble })),
);

const promptGroups = [
  {
    index: "01",
    eyebrow: "公司研究",
    title: "给出一家公司的完整画像",
    prompt: "分析阳光电源的资金面、消息面和近期走势",
    detail: "行情、资金、新闻与公告",
  },
  {
    index: "02",
    eyebrow: "行业筛选",
    title: "用多个条件筛选标的",
    prompt: "筛选银行股中股息率最高的 10 家公司",
    detail: "筛选、排名与横向对比",
  },
  {
    index: "03",
    eyebrow: "事件追踪",
    title: "追踪新闻与公司事件",
    prompt: "整理宁德时代最近的重要新闻和潜在影响",
    detail: "新闻、公告与市场催化",
  },
  {
    index: "04",
    eyebrow: "市场观察",
    title: "观察当日市场结构",
    prompt: "今日涨停的股票有哪些，按行业和市值整理",
    detail: "行情、板块与交易特征",
  },
];

const methodSteps = ["拆解问题", "按需取数", "核验证据", "写成备忘"];

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
          <section className="fdp-desk-brief">
            <p className="fdp-welcome-kicker">研究台</p>
            <h1>先取证，<br />再下判断。</h1>
            <p className="fdp-welcome-lead">
              把公司、行业或市场问题交给研究台。系统会拆成可执行的取数步骤，核对覆盖情况，再写成带来源的备忘。
            </p>
            <ol className="fdp-method" aria-label="研究流程">
              {methodSteps.map((step, index) => (
                <li key={step}>
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  {step}
                </li>
              ))}
            </ol>
            <p className="fdp-welcome-footnote">单次研究最多 8 次取数。结论只依据已核验证据，供研究参考。</p>
          </section>

          <div className="fdp-issue-list" aria-label="示例研究议题">
            <p className="fdp-issue-label">选一个议题，或在下方直接提问</p>
            {promptGroups.map((item) => (
              <button
                type="button"
                className="fdp-prompt-card"
                key={item.index}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => selectPrompt(item.prompt)}
              >
                <span className="fdp-prompt-index">{item.index}</span>
                <span className="fdp-prompt-copy">
                  <span className="fdp-prompt-eyebrow">{item.eyebrow}</span>
                  <strong>{item.title}</strong>
                  <span className="fdp-prompt-detail">{item.detail}</span>
                </span>
                <span className="fdp-prompt-action">填入 <ArrowRightOutlined /></span>
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="fdp-chat-area">
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
