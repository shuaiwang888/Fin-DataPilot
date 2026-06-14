import { Collapse } from "antd";
import { useState } from "react";
import type { ThinkingStep } from "../../stores/chatStore";
import { useChatStore } from "../../stores/chatStore";

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
  // Default state:
  //   - reasoning still streaming (live placeholder present) → open
  //   - answer already started streaming → collapsed
  //   - either side can override via click
  const answerStarted = useChatStore((s) => s.answerStarted);
  const defaultOpen =
    !answerStarted && steps.some((s) => s.text === "💭 思考中…");
  const [userOverride, setUserOverride] = useState<"open" | "closed" | null>(null);
  const isOpen = userOverride ? userOverride === "open" : defaultOpen;
  const [activeKeys, setActiveKeys] = useState<string[]>(isOpen ? ["thinking"] : []);

  if (!steps || steps.length === 0) return null;
  const isThinking = steps.some((s) => s.text === "💭 思考中…");
  return (
    <Collapse
      ghost
      size="small"
      activeKey={activeKeys}
      onChange={(k) => {
        const arr = Array.isArray(k) ? k : [k];
        setActiveKeys(arr);
        // Record user override so the auto-toggle stops overriding them.
        if (arr.length > 0) setUserOverride("open");
        else setUserOverride("closed");
      }}
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
