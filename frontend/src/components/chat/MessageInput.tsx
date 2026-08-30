import { useEffect, useState } from "react";
import { Sender } from "@ant-design/x";
import { SafetyCertificateOutlined, ThunderboltOutlined } from "@ant-design/icons";
import { useChatStream } from "../../hooks/useChatStream";

interface Props {
  disabled?: boolean;
}

export function MessageInput({ disabled }: Props) {
  const [value, setValue] = useState("");
  const { send, stop, streaming } = useChatStream();

  useEffect(() => {
    const handlePrompt = (event: Event) => {
      const prompt = (event as CustomEvent<string>).detail;
      if (prompt) setValue(prompt);
    };
    window.addEventListener("fdp:select-prompt", handlePrompt);
    return () => window.removeEventListener("fdp:select-prompt", handlePrompt);
  }, []);

  const handleSubmit = (next: string) => {
    const q = next.trim();
    if (!q) return;
    setValue("");
    send(q);
  };

  return (
    <div className="fdp-input-area">
      <div className="fdp-composer-shell">
        <div className="fdp-composer-label">
          <span><ThunderboltOutlined /> DataPilot</span>
          <em className={streaming ? "is-streaming" : ""}>
            {streaming ? "正在执行研究计划" : "就绪"}
          </em>
        </div>
        <Sender
          value={value}
          onChange={setValue}
          onSubmit={handleSubmit}
          // While streaming, AntD X's <Sender> shows a built-in
          // "stop" button. Hook it to the AbortController-backed stop()
          // from the chat hook.
          onCancel={streaming ? stop : undefined}
          placeholder="提问公司、行业、行情或市场事件…"
          loading={streaming}
          disabled={disabled}
          autoSize={{ minRows: 1, maxRows: 6 }}
          className="fdp-sender"
        />
        <div className="fdp-composer-meta">
          <span><SafetyCertificateOutlined /> 分析结果仅供研究参考，不构成投资建议</span>
          <span><kbd>Enter</kbd> 发送 · <kbd>Shift</kbd> + <kbd>Enter</kbd> 换行</span>
        </div>
      </div>
    </div>
  );
}
