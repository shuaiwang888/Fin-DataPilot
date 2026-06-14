import { Collapse } from "antd";
import type { ThinkingStep } from "../../stores/chatStore";

interface Props {
  steps: ThinkingStep[];
}

const stepLabel: Record<string, string> = {
  entry: "入口",
  router_final: "直接回答",
  reflect: "反思",
};

export function ThinkingPanel({ steps }: Props) {
  if (!steps || steps.length === 0) return null;
  return (
    <Collapse
      ghost
      size="small"
      items={[
        {
          key: "thinking",
          label: <span style={{ fontSize: 12, color: "#888" }}>🧠 思考过程 ({steps.length} 步)</span>,
          children: (
            <div>
              {steps.map((s) => (
                <div key={s.id} className="fdp-thinking">
                  <span className="fdp-step-tag">{stepLabel[s.step] ?? s.step}</span>
                  {s.text}
                </div>
              ))}
            </div>
          ),
        },
      ]}
    />
  );
}
