import { useCallback, useEffect, useRef } from "react";
import { useChatStore } from "../stores/chatStore";
import { useSessionStore } from "../stores/sessionStore";
import { streamChat } from "../lib/sse";
import { api } from "../lib/api";
import type { AgentEvent } from "../lib/types";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

function pause(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      clearTimeout(timer);
      reject(new DOMException("aborted", "AbortError"));
    };
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, 2_000);
    signal.addEventListener("abort", onAbort, { once: true });
    if (signal.aborted) onAbort();
  });
}

export function useChatStream() {
  const streaming = useChatStore((state) => state.streaming);
  const active = useRef<{ controller: AbortController; runId: string | null } | null>(null);

  // Detaching on navigation preserves the server task; only the Stop action cancels it.
  const detach = useCallback(() => {
    active.current?.controller.abort();
    active.current = null;
  }, []);

  const execute = useCallback(async (query?: string, resumeId?: string) => {
    const chat = useChatStore.getState();
    if (chat.streaming || (!resumeId && !query?.trim())) return;
    if (query) chat.appendUser(query);
    chat.appendAssistant();
    const context = { controller: new AbortController(), runId: resumeId ?? null };
    active.current = context;
    const signal = context.controller.signal;
    let sessionId = chat.sessionId;
    let sawFinal = false;
    let terminal = false;
    let tokenBuffer = "";
    let frame: number | null = null;
    const isCurrent = () => active.current === context && !signal.aborted && useChatStore.getState().sessionId === sessionId;
    const flush = () => {
      if (frame !== null) cancelAnimationFrame(frame);
      frame = null;
      if (tokenBuffer && isCurrent()) chat.applyEvent({ event: "token_delta", data: { text: tokenBuffer } });
      tokenBuffer = "";
    };
    const apply = (event: AgentEvent) => {
      if (!isCurrent()) return;
      if (event.event === "token_delta") {
        tokenBuffer += event.data.text;
        frame ??= requestAnimationFrame(flush);
        return;
      }
      flush();
      if (event.event === "message_final") sawFinal = true;
      if (event.event === "run_status" && !["running", "cancelling"].includes(event.data.status)) terminal = true;
      chat.applyEvent(event);
    };
    const recover = async (runId: string) => {
      apply({ event: "think", data: { step: "recover", text: "正在同步后台执行记录…" } });
      for (let attempt = 0; attempt < 310 && isCurrent(); attempt++) {
        try {
          const run = await api.getRun(runId, signal);
          if (!isCurrent()) return;
          sawFinal = false;
          // Rebuild instead of appending: repeated polls must never duplicate tools or steps.
          useChatStore.setState((state) => {
            const last = state.messages[state.messages.length - 1];
            return { messages: [...state.messages.slice(0, -1), {
              ...last, content: "", thinking: [], toolCalls: [], plan: [], error: undefined,
              streaming: true, phase: "planning",
            }] };
          });
          for (const event of run.events) apply(event as AgentEvent);
          flush();
          if (run.final_text && !sawFinal) {
            const messages = useChatStore.getState().messages;
            const calls = messages[messages.length - 1]?.toolCalls?.map((call) => ({ ...call })) ?? [];
            apply({ event: "message_final", data: { content: run.final_text, tool_calls: calls } });
          }
          if (!["running", "cancelling"].includes(run.status)) {
            apply({ event: "run_status", data: { run_id: runId, status: run.status, error: run.error } });
            return;
          }
        } catch (error) {
          if (signal.aborted) throw error;
          // A proxy reset during a poll does not affect the durable server task.
        }
        await pause(signal);
      }
      if (isCurrent()) {
        apply({ event: "error", data: { message: "后台任务暂未完成，请稍后打开此会话继续同步。" } });
        chat.finalizeAssistant();
      }
    };
    try {
      if (resumeId) {
        await recover(resumeId);
      } else {
        for await (const event of streamChat(`${API_BASE}/api/agent/chat/stream`, { query, session_id: sessionId }, signal)) {
          if (!isCurrent()) break;
          if (event.event === "session") {
            sessionId = event.data.session_id;
            chat.setSession(sessionId, { cause: "server", clearMessages: false });
            useSessionStore.getState().setActive(sessionId);
            void api.listSessions().then((result) => useSessionStore.getState().setSessions(result.sessions)).catch(() => {});
          } else if (event.event === "run") {
            context.runId = event.data.run_id;
          } else {
            apply(event);
          }
        }
        flush();
        if (isCurrent() && !terminal && context.runId) await recover(context.runId);
        else if (isCurrent() && !terminal) {
          if (!sawFinal) apply({ event: "error", data: { message: "任务未返回完整答案，请重试。" } });
          chat.finalizeAssistant();
        }
      }
    } catch (error) {
      flush();
      if (isCurrent()) {
        try {
          if (context.runId) await recover(context.runId);
          else throw error;
        } catch {
          if (isCurrent()) {
            apply({ event: "error", data: { message: "连接中断，暂时无法同步结果，请稍后打开此会话。" } });
            chat.finalizeAssistant();
          }
        }
      }
    } finally {
      flush();
      if (active.current === context) active.current = null;
    }
  }, []);

  const send = useCallback((query: string) => execute(query), [execute]);
  const resume = useCallback((runId: string) => { void execute(undefined, runId); }, [execute]);
  const stop = useCallback(() => {
    const context = active.current;
    if (!context) return;
    if (context.runId) void api.stopRun(context.runId).catch(() => {});
    useChatStore.getState().applyEvent({ event: "run_status", data: { run_id: context.runId ?? "", status: "cancelled" } });
    detach();
  }, [detach]);

  useEffect(() => {
    useChatStore.setState({ _abortInflight: detach, _resumeRun: resume });
    return () => {
      detach();
      useChatStore.setState({ _abortInflight: () => {}, _resumeRun: () => {} });
    };
  }, [detach, resume]);

  return { send, stop, streaming };
}
