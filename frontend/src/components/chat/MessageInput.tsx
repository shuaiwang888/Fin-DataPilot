import { useEffect, useState } from "react";
import { Sender } from "@ant-design/x";
import { SafetyCertificateOutlined } from "@ant-design/icons";
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
      if (!prompt) return;
      setValue(prompt);
      window.requestAnimationFrame(() => {
        document.querySelector<HTMLTextAreaElement>(".fdp-sender textarea")?.focus();
      });
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
          <span>研究问题</span>
          <em className={streaming ? "is-streaming" : ""}>
            {streaming ? "正在取数并核对" : "可以提问"}
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
          placeholder="例如：对比宁德时代和阳光电源最近一个季度的营收与机构观点"
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
