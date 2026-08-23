import { Bubble } from "@ant-design/x";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage } from "../../stores/chatStore";
import { ThinkingPanel } from "./ThinkingPanel";
import { ToolCallCard } from "./ToolCallCard";
import { PreambleCard } from "./PreambleCard";
import { RobotOutlined, UserOutlined } from "@ant-design/icons";

interface Props {
  msg: ChatMessage;
}

export function MessageBubble({ msg }: Props) {
  const isUser = msg.role === "user";
  const content = isUser
    ? msg.content
    : (msg.content || (msg.streaming ? "▍" : ""));

  return (
    <div className={`fdp-message-row ${isUser ? "is-user" : "is-assistant"}`}>
      <div className="fdp-message-author">
        <span className="fdp-message-avatar">{isUser ? <UserOutlined /> : <RobotOutlined />}</span>
        <div><strong>{isUser ? "你" : "DataPilot"}</strong><span>{isUser ? "研究问题" : "金融分析助手"}</span></div>
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
          messageRender={!isUser ? renderMarkdown : undefined}
          className="fdp-message-bubble"
        />
      </div>
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
