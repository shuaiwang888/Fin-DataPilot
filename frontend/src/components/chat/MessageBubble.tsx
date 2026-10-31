import { Bubble } from "@ant-design/x";
import { lazy, memo, Suspense } from "react";
import type { ChatMessage } from "../../stores/chatStore";
import { ThinkingPanel } from "./ThinkingPanel";
import { ToolCallCard } from "./ToolCallCard";
import { PreambleCard } from "./PreambleCard";
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
        <div><strong>{isUser ? "研究问题" : "研究备忘"}</strong><span>{isUser ? "你的提问" : "只依据已取到的证据"}</span></div>
      </div>
      <div className="fdp-message-body">
        {!isUser && msg.thinking && msg.thinking.length > 0 && (
          <ThinkingPanel steps={msg.thinking} />
        )}
        {!isUser && msg.toolCalls && msg.toolCalls.length > 0 && (
          <ToolCallCard records={msg.toolCalls} />
        )}
        {!isUser && msg.preamble && <PreambleCard preamble={msg.preamble} />}
        <Bubble
          content={content}
          placement={isUser ? "end" : "start"}
          loading={msg.streaming && !msg.content}
          messageRender={!isUser ? (msg.streaming ? renderStreamingText : renderMarkdown) : undefined}
          className="fdp-message-bubble"
        />
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
