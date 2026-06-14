import { Collapse } from "antd";
import { useState } from "react";
import type { ThinkingStep } from "../../stores/chatStore";

interface Props {
  steps: ThinkingStep[];
}

const stepLabel: Record<string, string> = {
  entry: "入口",
  router_final: "直接回答",
  reflect: "反思",
  synth_reason: "思考",
};

export function ThinkingPanel({ steps }: Props) {
  // Default to open when there's content (so the user immediately sees
  // reasoning — they can collapse it themselves).
  const [activeKeys, setActiveKeys] = useState<string[]>(["thinking"]);
  if (!steps || steps.length === 0) return null;
  const isThinking = steps.some((s) => s.text === "💭 思考中…");
  return (
    <Collapse
      ghost
      size="small"
      activeKey={activeKeys}
      onChange={(k) => setActiveKeys(Array.isArray(k) ? k : [k])}
      items={[
        {
          key: "thinking",
          label: (
            <span style={{ fontSize: 12, color: "#888" }}>
              {isThinking ? "🧠 思考中…" : "🧠 思考过程"} ({steps.length} 步)
              {isThinking && (
                <span style={{ marginLeft: 8, color: "#1677ff" }}>
                  <span className="fdp-thinking-pulse" />
                </span>
              )}
            </span>
          ),
          children: (
            <div>
              {steps.map((s) => (
                <div
                  key={s.id}
                  className="fdp-thinking"
                  style={{
                    whiteSpace: s.text === "💭 思考中…" ? "normal" : "pre-wrap",
                  }}
                >
                  <span className="fdp-step-tag">{stepLabel[s.step] ?? s.step}</span>
                  {s.text === "💭 思考中…" ? (
                    <span style={{ color: "#1677ff" }}>思考中…</span>
                  ) : (
                    s.text
                  )}
                </div>
              ))}
            </div>
          ),
        },
      ]}
    />
  );
}
