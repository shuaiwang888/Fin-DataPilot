import { Collapse } from "antd";
import { CheckOutlined, LoadingOutlined } from "@ant-design/icons";
import { useEffect, useState } from "react";
import type { ThinkingStep } from "../../stores/chatStore";
import { useChatStore } from "../../stores/chatStore";

interface Props {
  steps: ThinkingStep[];
}

const stepLabel: Record<string, string> = {
  entry: "理解问题",
  plan: "制定计划",
  load_skills: "准备能力",
  finalize: "开始汇总",
  router_final: "数据完成",
  reflect: "检查证据",
  synth_reason: "组织结论",
  recover: "恢复连接",
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

  useEffect(() => {
    if (userOverride === null) {
      setActiveKeys(isOpen ? ["thinking"] : []);
    }
  }, [isOpen, userOverride]);

  if (!steps || steps.length === 0) return null;
  const isThinking = steps.some((s) => s.text === "💭 思考中…");
  return (
    <Collapse
      className="fdp-process-panel"
      bordered={false}
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
            <span className="fdp-process-heading">
              <span className={`fdp-process-symbol ${isThinking ? "is-live" : "is-complete"}`}>
                {isThinking ? <LoadingOutlined spin /> : <CheckOutlined />}
              </span>
              <span>
                <strong>{isThinking ? "正在收集与核验证据" : "分析过程"}</strong>
                <small>{steps.length} 个状态更新</small>
              </span>
            </span>
          ),
          children: (
            <div className="fdp-process-timeline">
              {steps.map((s, index) => (
                <div
                  key={s.id}
                  className={`fdp-process-step ${s.text === "💭 思考中…" ? "is-live" : ""}`}
                >
                  <span className="fdp-process-index">{String(index + 1).padStart(2, "0")}</span>
                  <div>
                    <span className="fdp-process-step-name">{stepLabel[s.step] ?? s.step}</span>
                    <p>{s.text === "💭 思考中…" ? "正在等待下一个可验证结果…" : s.text}</p>
                  </div>
                </div>
              ))}
            </div>
          ),
        },
      ]}
    />
  );
}
