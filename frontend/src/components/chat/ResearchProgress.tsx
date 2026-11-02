import type { ChatMessage } from "../../stores/chatStore";

const stages = [
  { phase: "planning", label: "拆解问题" },
  { phase: "researching", label: "收集证据" },
  { phase: "checking", label: "核对覆盖" },
  { phase: "summarizing", label: "形成结论" },
];

export function ResearchProgress({ message }: { message: ChatMessage }) {
  const current = stages.findIndex((stage) => stage.phase === message.phase);
  const terminal = ["completed", "cancelled", "failed"].includes(message.phase ?? "");
  return (
    <div className="fdp-research-progress" aria-label="研究进度">
      <ol>
        {stages.map((stage, index) => (
          <li key={stage.phase} className={message.phase === "completed" || index < current ? "is-done" : index === current ? "is-current" : ""} aria-current={index === current ? "step" : undefined}>
            <span>{message.phase === "completed" || index < current ? "✓" : index + 1}</span>{stage.label}
          </li>
        ))}
      </ol>
      <p role="status">{message.phase === "cancelled" ? "本次研究已停止" : message.phase === "failed" ? "本次研究未完成" : message.phase === "completed" ? "研究记录已保存，可继续追问" : stages[current]?.label || "正在同步研究记录"}{!terminal && "…"}</p>
      {!!message.plan?.length && <details className="fdp-research-plan"><summary>查看本轮研究计划 · {message.plan.length} 项</summary><ul>{message.plan.map((goal, index) => <li key={index}>{goal}</li>)}</ul></details>}
    </div>
  );
}
