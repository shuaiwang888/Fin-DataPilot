import { Bubble } from "@ant-design/x";
import { lazy, memo, Suspense } from "react";
import type { ChatMessage } from "../../stores/chatStore";
import { ThinkingPanel } from "./ThinkingPanel";
import { ToolCallCard } from "./ToolCallCard";
import { PreambleCard } from "./PreambleCard";
import { AnswerVisuals } from "./AnswerVisuals";
import { ResearchProgress } from "./ResearchProgress";
import { FileTextOutlined, FormOutlined } from "@ant-design/icons";

interface Props {
  msg: ChatMessage;
}

const MarkdownContent = lazy(() => import("./MarkdownContent"));

export const MessageBubble = memo(function MessageBubble({ msg }: Props) {
  const isUser = msg.role === "user";
  const content = isUser
    ? msg.content
    : (msg.content || (msg.streaming ? "▍" : ""));

  return (
    <div className={`fdp-message-row ${isUser ? "is-user" : "is-assistant"}`}>
      <div className="fdp-message-author">
        <span className="fdp-message-avatar">{isUser ? <FormOutlined /> : <FileTextOutlined />}</span>
        <div><strong>{isUser ? "你" : "Fin-DataPilot"}</strong><span>{isUser ? "研究问题" : "研究分析 · 来源可追溯"}</span></div>
      </div>
      <div className="fdp-message-body">
        {!isUser && msg.phase && <ResearchProgress message={msg} />}
        {!isUser && msg.thinking && msg.thinking.length > 0 && (
          <ThinkingPanel steps={msg.thinking} />
        )}
        {!isUser && msg.toolCalls && msg.toolCalls.length > 0 && (
          <ToolCallCard records={msg.toolCalls} />
        )}
        {!isUser && msg.preamble && <PreambleCard preamble={msg.preamble} />}
        {!isUser && msg.presentation && <AnswerVisuals presentation={msg.presentation} />}
        <Bubble
          content={content}
          placement={isUser ? "end" : "start"}
          loading={msg.streaming && !msg.content}
          messageRender={!isUser ? (msg.streaming ? renderStreamingText : renderMarkdown) : undefined}
          className="fdp-message-bubble"
        />
        {!isUser && msg.error && <p className="fdp-answer-error" role="alert">{msg.error}</p>}
        {!isUser && !msg.streaming && msg.content && <div className="fdp-answer-actions">
          <button type="button" onClick={() => window.dispatchEvent(new CustomEvent("fdp:select-prompt", { detail: "把上一轮回答整理成三点关键结论，并保留来源和不确定性" }))}>提炼关键结论 ↗</button>
          <button type="button" onClick={() => {
            const blob = new Blob([msg.content], { type: "text/markdown;charset=utf-8" });
            const url = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = url;
            link.download = "Fin-DataPilot-研究备忘.md";
            link.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
          }}>导出备忘 ↓</button>
        </div>}
      </div>
    </div>
  );
});

const renderMarkdown = (content: string) => (
  <Suspense fallback={<div className="fdp-streaming-text">{content}</div>}>
    <MarkdownContent content={content} />
  </Suspense>
);

const renderStreamingText = (content: string) => (
  <div className="fdp-streaming-text">{content}</div>
);
