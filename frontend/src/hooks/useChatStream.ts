import { useCallback } from "react";
import { useChatStore } from "../stores/chatStore";
import { useSessionStore } from "../stores/sessionStore";
import { streamChat } from "../lib/sse";
import { api } from "../lib/api";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

export function useChatStream() {
  const chat = useChatStore();
  const sessions = useSessionStore();

  const send = useCallback(
    async (query: string) => {
      if (!query.trim() || chat.streaming) return;
      chat.appendUser(query);
      chat.appendAssistant();

      const url = `${API_BASE}/api/agent/chat/stream`;
      let newSessionId: string | null = null;
      try {
        for await (const ev of streamChat(url, {
          query,
          session_id: chat.sessionId,
        })) {
          if (ev.event === "session") {
            newSessionId = ev.data.session_id;
            // Server just minted a new session — keep the optimistic UI
            // bubbles we already rendered (user + assistant). Only update
            // the sessionId reference, don't wipe messages.
            chat.setSession(newSessionId, { clearMessages: false });
            sessions.setActive(newSessionId);
            // Refresh sidebar session list (best effort)
            api.listSessions().then((r) => sessions.setSessions(r.sessions)).catch(() => {});
          } else if (ev.event === "ping") {
            // no-op
          } else if (ev.event === "think") {
            chat.appendThinking({
              id: `t_${Date.now()}_${Math.random()}`,
              step: ev.data.step ?? "",
              text: ev.data.text ?? "",
              ts: Date.now(),
            });
          } else if (ev.event === "tool_call") {
            chat.appendToolCall({
              name: ev.data.name,
              args: ev.data.args ?? {},
              trace_id: ev.data.trace_id ?? "",
              ts: Date.now(),
            });
          } else if (ev.event === "tool_result") {
            // Attach result to the most recent tool call with the same trace_id
            const msgs = useChatStore.getState().messages;
            const last = msgs[msgs.length - 1];
            if (last && last.toolCalls) {
              const tcs = [...last.toolCalls];
              for (let i = tcs.length - 1; i >= 0; i--) {
                if (tcs[i].trace_id === ev.data.trace_id) {
                  tcs[i] = {
                    ...tcs[i],
                    result: ev.data.result,
                    ok: ev.data.ok,
                    duration_ms: ev.data.duration_ms,
                    error: ev.data.error,
                  };
                  break;
                }
              }
              useChatStore.setState((s) => {
                const m2 = [...s.messages];
                m2[m2.length - 1] = { ...last, toolCalls: tcs };
                return { messages: m2 };
              });
            }
          } else if (ev.event === "reflection") {
            chat.appendThinking({
              id: `t_${Date.now()}_${Math.random()}`,
              step: "reflect",
              text: `[${ev.data.verdict}] ${ev.data.reason ?? ""}`,
              ts: Date.now(),
            });
          } else if (ev.event === "preamble") {
            // Stash structured query/condition info on the assistant message
            useChatStore.setState((s) => {
              const m = [...s.messages];
              const last = m[m.length - 1];
              if (last && last.role === "assistant") {
                m[m.length - 1] = { ...last, preamble: ev.data };
              }
              return { messages: m };
            });
          } else if (ev.event === "token_delta") {
            chat.appendToken(ev.data.text ?? "");
          } else if (ev.event === "think_chunk") {
            // Stream the thinking into a single live-updated step so the
            // user sees the reasoning appear in real time. We tag the step
            // id with a constant prefix so we can find & update it on
            // every chunk instead of appending a new step each time.
            useChatStore.setState((s) => {
              const w = s as unknown as {
                _pendingThink?: string;
                _liveThinkId?: string;
              };
              w._pendingThink = (w._pendingThink ?? "") + (ev.data.text ?? "");
              const m = [...s.messages];
              const last = m[m.length - 1];
              if (last && last.role === "assistant") {
                const existingId = w._liveThinkId;
                const live = (last.thinking ?? []).map((t) =>
                  t.id === existingId
                    ? { ...t, text: w._pendingThink ?? "" }
                    : t
                );
                if (existingId && live.some((t) => t.id === existingId)) {
                  m[m.length - 1] = { ...last, thinking: live };
                } else {
                  const id = `t_live_${Date.now()}`;
                  w._liveThinkId = id;
                  m[m.length - 1] = {
                    ...last,
                    thinking: [
                      ...(last.thinking ?? []),
                      { id, step: "synth_reason", text: w._pendingThink ?? "", ts: Date.now() },
                    ],
                  };
                }
              }
              return { messages: m };
            });
          } else if (ev.event === "think_done") {
            useChatStore.setState((s) => {
              const w = s as unknown as {
                _pendingThink?: string;
                _liveThinkId?: string;
              };
              w._pendingThink = "";
              w._liveThinkId = undefined;
              return s;
            });
          } else if (ev.event === "heartbeat") {
            // Server is still generating — keep the live thinking step
            // visible (or add a placeholder so the UI shows a spinner).
            useChatStore.setState((s) => {
              const w = s as unknown as {
                _pendingThink?: string;
                _liveThinkId?: string;
                _lastHeartbeatAt?: number;
              };
              w._lastHeartbeatAt = Date.now();
              if (w._pendingThink) return s; // already showing live text
              const m = [...s.messages];
              const last = m[m.length - 1];
              if (last && last.role === "assistant") {
                const liveId = w._liveThinkId;
                if (liveId && (last.thinking ?? []).some((t) => t.id === liveId)) {
                  return s; // already a placeholder
                }
                const id = `t_live_${Date.now()}`;
                w._liveThinkId = id;
                m[m.length - 1] = {
                  ...last,
                  thinking: [
                    ...(last.thinking ?? []),
                    { id, step: "synth_reason", text: "💭 思考中…", ts: Date.now() },
                  ],
                };
              }
              return { messages: m };
            });
          } else if (ev.event === "summary_start") {
            // Reset thinking buffer for the new run
            useChatStore.setState((s) => {
              const w = s as unknown as { _pendingThink?: string; _liveThinkId?: string };
              w._pendingThink = "";
              w._liveThinkId = undefined;
              return s;
            });
          } else if (ev.event === "message_final") {
            // The synthesizer's payload carries the final answer text.
            // We use it as a SAFETY NET: if the LLM dumped everything
            // into the think block and never produced token_delta events,
            // the answer bubble is otherwise empty. Fall back to the
            // payload's content whenever it's non-empty and the
            // accumulated content is empty.
            const finalContent = (ev.data?.content ?? "").trim();
            const preamble = ev.data?.preamble;
            if (finalContent) {
              useChatStore.setState((s) => {
                const m = [...s.messages];
                const last = m[m.length - 1];
                if (last && last.role === "assistant") {
                  const currentContent = (last.content ?? "").trim();
                  if (!currentContent) {
                    m[m.length - 1] = { ...last, content: finalContent };
                  }
                  if (preamble) {
                    m[m.length - 1] = { ...m[m.length - 1], preamble };
                  }
                }
                return { messages: m };
              });
            } else if (preamble) {
              useChatStore.setState((s) => {
                const m = [...s.messages];
                const last = m[m.length - 1];
                if (last && last.role === "assistant") {
                  m[m.length - 1] = { ...last, preamble };
                }
                return { messages: m };
              });
            }
            chat.finalizeAssistant();
          } else if (ev.event === "error") {
            chat.appendToken(`\n\n⚠️ ${ev.data.message ?? "出错了"}`);
            chat.finalizeAssistant();
          } else if (ev.event === "done") {
            chat.finalizeAssistant();
          }
        }
      } catch (err) {
        // Network errors (connection drop, HF proxy timeout) are often
        // transient. Show a friendly message + offer a one-click retry
        // by re-sending the same query.
        const msg = (err as Error)?.message ?? "出错了";
        const friendly = /network|fetch|aborted|timeout/i.test(msg)
          ? "网络连接中断（HF Space 代理超时）。请重试，或检查后端是否还在运行。"
          : `⚠️ ${msg}`;
        chat.appendToken(`\n\n${friendly}\n\n如需重试，请直接重新发送上一条问题。`);
        chat.finalizeAssistant();
      }
    },
    [chat, sessions]
  );

  return { send, streaming: chat.streaming };
}
