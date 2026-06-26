import { useCallback, useEffect, useRef } from "react";
import { useChatStore } from "../stores/chatStore";
import type { ChatMessage, ThinkingStep } from "../stores/chatStore";
import { useSessionStore } from "../stores/sessionStore";
import { streamChat } from "../lib/sse";
import { api } from "../lib/api";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

/** Text the synthesizer placeholders carry (matched in ThinkingPanel.tsx). */
const THINKING_PLACEHOLDER_TEXT = "💭 思考中…";

/** Remove every "💭 思考中…" placeholder step from the last assistant
 *  message's thinking array. Called when:
 *   - summary_start arrives (synthesizer about to start; old heartbeat
 *     placeholders from before streaming are stale)
 *   - token_delta first arrives (real answer is flowing, placeholders
 *     should vanish)
 *   - message_final / done arrives (defense in depth — if any slipped
 *     through, scrub them so the panel label flips from "思考中…" to
 *     "思考过程")
 *
 *  Returns the new messages array if anything was removed, else null.
 *  Caller can decide whether to setState with the returned array.
 */
function stripThinkingPlaceholders(messages: ChatMessage[]): ChatMessage[] | null {
  const last = messages[messages.length - 1];
  if (!last || last.role !== "assistant") return null;
  const thinking = last.thinking ?? [];
  const cleaned = thinking.filter((t) => t.text !== THINKING_PLACEHOLDER_TEXT);
  if (cleaned.length === thinking.length) return null;
  return [
    ...messages.slice(0, -1),
    { ...last, thinking: cleaned },
  ];
}

export function useChatStream() {
  const chat = useChatStore();
  const sessions = useSessionStore();

  // Holds the in-flight AbortController so the user can cancel the
  // current stream (the AntD X <Sender> "stop" button calls
  // stop(), which aborts the fetch, which the parser then surfaces
  // as a thrown DOMException we catch and treat as "user stopped").
  const abortRef = useRef<AbortController | null>(null);

  const send = useCallback(
    async (query: string) => {
      if (!query.trim() || chat.streaming) return;
      chat.appendUser(query);
      chat.appendAssistant();

      const controller = new AbortController();
      abortRef.current = controller;

      const url = `${API_BASE}/api/agent/chat/stream`;
      let newSessionId: string | null = null;
      // Snapshot the session id at the moment the request was sent.
      // The server may mint a new id (then a `session` event arrives
      // and updates the store); until then, every event we receive
      // belongs to this snapshot. If the store's sessionId changes
      // for any OTHER reason — user clicked another session, hit
      // "新对话", etc. — we break out so late events from this stream
      // can't pollute the new session's messages.
      const requestSessionId = useChatStore.getState().sessionId;
      try {
        for await (const ev of streamChat(
          url,
          { query, session_id: chat.sessionId },
          controller.signal,
        )) {
          // Guard: did the active session change under us? If so, the
          // user (or _abortInflight) has moved on — drop the event.
          // The `session` event is special: it's the server telling
          // us the id it minted, so that's the ONLY event allowed to
          // change sessionId from null to a real value.
          if (ev.event !== "session") {
            const current = useChatStore.getState().sessionId;
            if (current !== requestSessionId) {
              // session was switched under us; bail out cleanly
              controller.abort();
              break;
            }
          }
          if (ev.event === "session") {
            newSessionId = ev.data.session_id;
            // Server just minted a new session — keep the optimistic UI
            // bubbles (user + assistant) we already rendered. Only update
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
            // First real answer token → scrub any leftover "💭 思考中…"
            // placeholders so the panel label flips from "思考中…" to
            // "思考过程" the moment the answer starts streaming.
            useChatStore.setState((s) => {
              const cleaned = stripThinkingPlaceholders(s.messages);
              return cleaned ? { messages: cleaned } : s;
            });
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
            // Guards (avoid spamming new placeholders):
            //   1. _pendingThink already set → live text streaming, no-op
            //   2. _liveThinkId already points at a step in the array → no-op
            //   3. answerStarted → real answer already flowing, no-op
            //   4. any "💭 思考中…" step already exists → no-op
            useChatStore.setState((s) => {
              const w = s as unknown as {
                _pendingThink?: string;
                _liveThinkId?: string;
                _lastHeartbeatAt?: number;
              };
              w._lastHeartbeatAt = Date.now();
              if (w._pendingThink) return s;
              if (s.answerStarted) return s;
              const m = [...s.messages];
              const last = m[m.length - 1];
              if (!last || last.role !== "assistant") return s;
              const liveId = w._liveThinkId;
              if (liveId && (last.thinking ?? []).some((t) => t.id === liveId)) return s;
              // Guard 4: if ANY placeholder is already in the array,
              // don't add another. (Handles the case where summary_start
              // cleared _liveThinkId but the old placeholder is still in
              // the array — otherwise this branch would create duplicates.)
              const hasPlaceholder = (last.thinking ?? []).some(
                (t) => t.text === "💭 思考中…"
              );
              if (hasPlaceholder) {
                // Re-anchor _liveThinkId so the next heartbeat doesn't
                // try to add another one.
                const existing = (last.thinking ?? []).find(
                  (t) => t.text === "💭 思考中…"
                );
                if (existing) w._liveThinkId = existing.id;
                return s;
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
              return { messages: m };
            });
          } else if (ev.event === "summary_start") {
            // Reset thinking buffer for the new run AND scrub any
            // leftover "💭 思考中…" placeholders from before the
            // synthesizer started. Otherwise the ThinkingPanel's label
            // keeps saying "思考中…" even after the answer is streaming.
            useChatStore.setState((s) => {
              const w = s as unknown as { _pendingThink?: string; _liveThinkId?: string };
              w._pendingThink = "";
              w._liveThinkId = undefined;
              const cleaned = stripThinkingPlaceholders(s.messages);
              return cleaned ? { messages: cleaned } : s;
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
            // Defense in depth: scrub any "💭 思考中…" placeholders
            // that survived the synthesizer's normal flow. Without
            // this, if a heartbeat added a placeholder AFTER the last
            // summary_start (race) or AFTER the answer started
            // streaming, the panel would still say "思考中…" forever.
            useChatStore.setState((s) => {
              const cleaned = stripThinkingPlaceholders(s.messages);
              return cleaned ? { messages: cleaned } : s;
            });
            chat.finalizeAssistant();
          }
        }
      } catch (err) {
        const e = err as Error & { name?: string };
        if (e.name === "AbortError" || controller.signal.aborted) {
          // User pressed stop. Don't show the "network error" message;
          // just finalize the partial answer with a "已停止" marker.
          useChatStore.setState((s) => {
            const m = [...s.messages];
            const last = m[m.length - 1];
            if (last && last.role === "assistant" && last.streaming) {
              const sep = last.content ? "\n\n" : "";
              m[m.length - 1] = {
                ...last,
                content: (last.content ?? "") + `${sep}⏹ 已停止生成。`,
                streaming: false,
              };
            }
            return {
              messages: m,
              pendingText: "",
              streaming: false,
              answerStarted: false,
            };
          });
        } else {
          // Network errors (connection drop, HF proxy timeout) are often
          // transient. Show a friendly message + offer a one-click retry
          // by re-sending the same query.
          const msg = e?.message ?? "出错了";
          const friendly = /network|fetch|aborted|timeout/i.test(msg)
            ? "网络连接中断（HF Space 代理超时）。请重试，或检查后端是否还在运行。"
            : `⚠️ ${msg}`;
          chat.appendToken(`\n\n${friendly}\n\n如需重试，请直接重新发送上一条问题。`);
          chat.finalizeAssistant();
        }
      } finally {
        abortRef.current = null;
      }
    },
    [chat, sessions]
  );

  const stop = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      // Synchronously finalize any in-flight assistant message so the
      // UI doesn't show a "loading" state for the now-dead stream.
      // finalizeAssistant is idempotent — if message_final already
      // ran (no longer streaming), this is a no-op.
      useChatStore.getState().finalizeAssistant();
      abortRef.current = null;
    }
  }, []);

  // Register/unregister this hook's stop() as the store's abort hook.
  // Any setSession/reset call with a different id will trigger it,
  // which is what makes "click a different session" actually tear
  // down the old stream.
  useEffect(() => {
    useChatStore.setState({ _abortInflight: stop });
    return () => {
      // Unregister on unmount to avoid a stale stop() being called
      // by a later session switch.
      const current = useChatStore.getState()._abortInflight;
      if (current === stop) {
        useChatStore.setState({ _abortInflight: () => {} });
      }
    };
  }, [stop]);

  return { send, stop, streaming: chat.streaming };
}
