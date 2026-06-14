import { useEffect, useRef } from "react";
import { Empty } from "antd";
import { useChatStore } from "../../stores/chatStore";
import { MessageBubble } from "./MessageBubble";

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
      <div
        style={{
          flex: 1,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "#999",
        }}
      >
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <div style={{ color: "#999" }}>
              <div style={{ fontSize: 20, marginBottom: 8 }}>👋 开始一段新的金融对话</div>
              <div style={{ fontSize: 13 }}>试试：<em>「贵州茅台的 PE」</em> / <em>「银行股股息率前 10」</em> / <em>「宁德时代最近新闻」</em></div>
            </div>
          }
        />
      </div>
    );
  }

  return (
    <div ref={containerRef} className="fdp-chat-area">
      {messages.map((m) => (
        <MessageBubble key={m.id} msg={m} />
      ))}
    </div>
  );
}
