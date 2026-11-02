import type { AgentEvent, AnswerPresentation, Message } from "./types";
import type { ChatMessage, ToolCallRecord } from "../stores/chatStore";

export function toolRecord(raw: Record<string, unknown>, index = 0): ToolCallRecord {
  return {
    name: String(raw.name ?? "数据查询"),
    args: (raw.args ?? {}) as Record<string, unknown>,
    trace_id: String(raw.trace_id ?? `legacy-${index}`),
    ok: typeof raw.ok === "boolean" ? raw.ok : undefined,
    duration_ms: typeof raw.duration_ms === "number" ? raw.duration_ms : undefined,
    result: raw.result,
    error: typeof raw.error === "string" ? raw.error : undefined,
    ts: 0,
  };
}

/** Pure event reducer: the same contract powers live, recovered and saved turns. */
export function reduceChatEvent(message: ChatMessage, event: AgentEvent): ChatMessage {
  switch (event.event) {
    case "phase": return { ...message, phase: event.data.phase };
    case "plan": return { ...message, plan: event.data.sub_tasks };
    case "think":
    case "reflection": {
      const step = event.event === "think" ? event.data.step : "reflect";
      const text = event.event === "think" ? event.data.text : event.data.reason;
      if (!text) return message;
      const thinking = message.thinking ?? [];
      return { ...message, thinking: [...thinking, { id: `step-${thinking.length}`, step, text, ts: 0 }] };
    }
    case "tool_call":
    case "tool_result": {
      const records = [...(message.toolCalls ?? [])];
      const index = records.findIndex((record) => record.trace_id === event.data.trace_id);
      const raw = event.data as unknown as Record<string, unknown>;
      const record = toolRecord(raw, records.length);
      if (index < 0) records.push(record);
      else records[index] = { ...records[index], ...record, args: event.data.args ?? records[index].args };
      return { ...message, toolCalls: records, phase: "researching" };
    }
    case "preamble": return { ...message, preamble: event.data };
    case "summary_start": return { ...message, phase: "summarizing" };
    case "token_delta": return { ...message, content: message.content + event.data.text };
    case "message_final": return {
      ...message,
      content: event.data.content,
      toolCalls: event.data.tool_calls?.map(toolRecord) ?? message.toolCalls,
      preamble: event.data.preamble ?? message.preamble,
      presentation: event.data.presentation,
      error: undefined,
      phase: "summarizing",
    };
    // Public diagnostics are recoverable; a subsequent final answer can clear them.
    case "error": return { ...message, error: event.data.message };
    case "run_status": {
      if (event.data.status === "running" || event.data.status === "cancelling") return message;
      const phase = event.data.status === "completed" ? "completed" : event.data.status === "cancelled" ? "cancelled" : "failed";
      return {
        ...message, phase, streaming: false,
        content: message.content || (phase === "cancelled" ? "本次研究已停止。" : ""),
        error: phase === "cancelled" ? undefined : event.data.error || message.error,
      };
    }
    // Legacy reasoning tokens are intentionally ignored, including during replay.
    default: return message;
  }
}

export function hydrateMessage(raw: Message): ChatMessage {
  const metadata = raw.thinking ?? {};
  let message: ChatMessage = {
    ...raw,
    thinking: [],
    toolCalls: (raw.tool_calls ?? (metadata.trace as Record<string, unknown>[] | undefined) ?? []).map(toolRecord),
    phase: raw.role === "assistant" ? "completed" : undefined,
    presentation: metadata.presentation as AnswerPresentation | undefined,
    preamble: metadata.preamble as ChatMessage["preamble"],
  };
  for (const event of (metadata.events ?? []) as AgentEvent[]) message = reduceChatEvent(message, event);
  const status = metadata.status;
  const phase = status === "failed" || status === "cancelled" ? status : "completed";
  return { ...message, phase: raw.role === "assistant" ? phase : undefined, streaming: false };
}
