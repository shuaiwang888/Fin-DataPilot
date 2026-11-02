import { describe, expect, it } from "vitest";
import { hydrateMessage, reduceChatEvent } from "./chatEvents";
import type { ChatMessage } from "../stores/chatStore";
import type { AgentEvent, AnswerPresentation } from "./types";

const blank: ChatMessage = { id: "m1", role: "assistant", content: "", created_at: "", streaming: true };
const presentation: AnswerPresentation = { version: 1, status: "ready", successful_tools: 1, total_tools: 1, charts: [], warnings: [] };
const call: AgentEvent = { event: "tool_call", data: { name: "financial-query", args: { query: "营收" }, trace_id: "t1" } };
const result: AgentEvent = { event: "tool_result", data: { name: "financial-query", ok: true, duration_ms: 100, trace_id: "t1", result: { data: { datas: [] } } } };

describe("public chat event contract", () => {
  it("pairs tool results by trace while preserving query arguments", () => {
    const message = reduceChatEvent(reduceChatEvent(blank, call), result);
    expect(message.toolCalls).toHaveLength(1);
    expect(message.toolCalls?.[0]).toMatchObject({ args: { query: "营收" }, ok: true, duration_ms: 100 });
    expect(reduceChatEvent(blank, result).toolCalls).toHaveLength(1);
  });
  it("final payload replaces temporary text and clears a recovered diagnostic", () => {
    let message = reduceChatEvent(blank, { event: "token_delta", data: { text: "临时文本" } });
    message = reduceChatEvent(message, { event: "error", data: { message: "临时故障" } });
    message = reduceChatEvent(message, { event: "message_final", data: { content: "正式结论", tool_calls: [], presentation } });
    expect(message.content).toBe("正式结论");
    expect(message.error).toBeUndefined();
    expect(message.presentation).toEqual(presentation);
    expect(message.streaming).toBe(true); // Wait for durable run_status before allowing the next turn.
    message = reduceChatEvent(message, { event: "run_status", data: { run_id: "r1", status: "completed" } });
    expect(message.streaming).toBe(false);
  });
  it("hydrates saved tools, progress, plan and visuals", () => {
    const message = hydrateMessage({ ...blank, tool_calls: [{ ...result.data, args: { query: "营收" } }], thinking: {
      events: [{ event: "plan", data: { sub_tasks: ["对比营收"] } }], presentation,
    } });
    expect(message.toolCalls?.[0].args.query).toBe("营收");
    expect(message.presentation).toEqual(presentation);
    expect(message.plan).toEqual(["对比营收"]);
    expect(message.phase).toBe("completed");
  });
  it("ignores private legacy reasoning in live and saved streams", () => {
    expect(reduceChatEvent(blank, { event: "think_chunk", data: { text: "private" } })).toBe(blank);
    expect(reduceChatEvent(blank, { event: "think_done", data: { text: "private" } })).toBe(blank);
  });
  it("keeps cancelling active until the server confirms a terminal status", () => {
    expect(reduceChatEvent(blank, { event: "run_status", data: { run_id: "r1", status: "cancelling" } }).streaming).toBe(true);
    expect(reduceChatEvent(blank, { event: "run_status", data: { run_id: "r1", status: "cancelled" } }).phase).toBe("cancelled");
    expect(hydrateMessage({ ...blank, thinking: { status: "cancelled" } }).phase).toBe("cancelled");
  });
});
