import { useState } from "react";
import { Sender } from "@ant-design/x";
import { useChatStream } from "../../hooks/useChatStream";

interface Props {
  disabled?: boolean;
}

export function MessageInput({ disabled }: Props) {
  const [value, setValue] = useState("");
  const { send, stop, streaming } = useChatStream();

  const handleSubmit = (next: string) => {
    const q = next.trim();
    if (!q) return;
    setValue("");
    send(q);
  };

  return (
    <div className="fdp-input-area">
      <Sender
        value={value}
        onChange={setValue}
        onSubmit={handleSubmit}
        // While streaming, AntD X's <Sender> shows a built-in
        // "stop" button. Hook it to the AbortController-backed stop()
        // from the chat hook.
        onCancel={streaming ? stop : undefined}
        placeholder="输入金融问题，例如：贵州茅台 PE、银行 股息率前 10..."
        loading={streaming}
        disabled={disabled}
        autoSize={{ minRows: 1, maxRows: 6 }}
        style={{ width: "100%" }}
      />
    </div>
  );
}
