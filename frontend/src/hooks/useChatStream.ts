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
            // Streaming sub-chunk inside a <think> block — buffer it client-side
            // until the matching think_done event arrives, so the thinking
            // panel renders a single coherent entry rather than a flood of
            // tiny fragments.
            useChatStore.setState((s) => {
              const w = s as unknown as { _pendingThink?: string };
              w._pendingThink = (w._pendingThink ?? "") + (ev.data.text ?? "");
              return s;
            });
          } else if (ev.event === "think_done") {
            useChatStore.setState((s) => {
              const w = s as unknown as { _pendingThink?: string };
              const buffered = w._pendingThink ?? ev.data.text ?? "";
              w._pendingThink = "";
              if (!buffered.trim()) return s;
              const m = [...s.messages];
              const last = m[m.length - 1];
              if (last && last.role === "assistant") {
                m[m.length - 1] = {
                  ...last,
                  thinking: [
                    ...(last.thinking ?? []),
                    {
                      id: `t_${Date.now()}_${Math.random()}`,
                      step: "synth_reason",
                      text: buffered,
                      ts: Date.now(),
                    },
                  ],
                };
              }
              return { messages: m };
            });
          } else if (ev.event === "message_final") {
            // content is already accumulated via token_delta; ensure finalized
            if (ev.data?.preamble) {
              useChatStore.setState((s) => {
                const m = [...s.messages];
                const last = m[m.length - 1];
                if (last && last.role === "assistant") {
                  m[m.length - 1] = { ...last, preamble: ev.data.preamble };
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
        chat.appendToken(`\n\n⚠️ ${(err as Error).message}`);
        chat.finalizeAssistant();
      }
    },
    [chat, sessions]
  );

  return { send, streaming: chat.streaming };
}
