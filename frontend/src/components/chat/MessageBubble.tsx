import { Bubble } from "@ant-design/x";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage } from "../../stores/chatStore";
import { ThinkingPanel } from "./ThinkingPanel";
import { ToolCallCard } from "./ToolCallCard";
import { PreambleCard } from "./PreambleCard";

interface Props {
  msg: ChatMessage;
}

export function MessageBubble({ msg }: Props) {
  const isUser = msg.role === "user";
  const content = isUser
    ? msg.content
    : (msg.content || (msg.streaming ? "▍" : ""));

  return (
    <div style={{ margin: "12px 0" }}>
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
        avatar={isUser ? <span>🙂</span> : <span>📊</span>}
        loading={msg.streaming && !msg.content}
        messageRender={!isUser ? renderMarkdown : undefined}
        style={{
          maxWidth: "85%",
        }}
      />
    </div>
  );
}

const renderMarkdown = (content: string) => (
  <ReactMarkdown
    remarkPlugins={[remarkGfm]}
    components={{
      table: ({ children }) => (
        <div style={{ overflowX: "auto" }}>
          <table className="fdp-tool-result-table">{children}</table>
        </div>
      ),
    }}
  >
    {content}
  </ReactMarkdown>
);
