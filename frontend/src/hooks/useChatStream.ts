import { useCallback, useEffect, useRef } from "react";
import { useChatStore } from "../stores/chatStore";
import type { ChatMessage } from "../stores/chatStore";
import { useSessionStore } from "../stores/sessionStore";
import { streamChat } from "../lib/sse";
import { api } from "../lib/api";
import type { AgentRun } from "../lib/api";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

/** Text the synthesizer placeholders carry (matched in ThinkingPanel.tsx). */
const THINKING_PLACEHOLDER_TEXT = "💭 思考中…";
const RECOVERY_POLL_INTERVAL_MS = 2_000;
const RECOVERY_MAX_POLLS = 70;

function delay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
      return;
    }
    const onAbort = () => {
      window.clearTimeout(timer);
      reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
    };
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

async function recoverDurableRun(runId: string, signal: AbortSignal): Promise<AgentRun | null> {
  for (let attempt = 0; attempt < RECOVERY_MAX_POLLS; attempt += 1) {
    if (signal.aborted) {
      throw Object.assign(new Error("aborted"), { name: "AbortError" });
    }
    try {
      const run = await api.getRun(runId, signal);
      if (run.status !== "running") return run;
    } catch (error) {
      if ((error as Error).name === "AbortError" || signal.aborted) throw error;
      // A short polling request may also hit a transient proxy reset. Keep
      // retrying; the server-side task and durable run record are independent.
    }
    await delay(RECOVERY_POLL_INTERVAL_MS, signal);
  }
  return null;
}

function applyRecoveredAnswer(content: string): void {
  useChatStore.setState((state) => {
    const cleaned = stripThinkingPlaceholders(state.messages) ?? state.messages;
    const messages = [...cleaned];
    const last = messages[messages.length - 1];
    if (last?.role === "assistant") {
      messages[messages.length - 1] = { ...last, content, streaming: false };
    }
    return {
      messages,
      pendingText: "",
      streaming: false,
      answerStarted: false,
    };
  });
}

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
  const runIdRef = useRef<string | null>(null);

  const send = useCallback(
    async (query: string) => {
      if (!query.trim() || chat.streaming) return;
      chat.appendUser(query);
      chat.appendAssistant();

      const controller = new AbortController();
      abortRef.current = controller;
      runIdRef.current = null;

      const url = `${API_BASE}/api/agent/chat/stream`;
      let newSessionId: string | null = null;
      // The id we expect to see in the store while this stream is the
      // active one. Starts as the id at request time, then gets
      // updated when the server's `session` event mints a fresh id
      // (the new-conversation case). When the user clicks another
      // session mid-stream, the store's sessionId changes for a
      // DIFFERENT reason and no longer matches — that's when we bail.
      let requestSessionId = useChatStore.getState().sessionId;
      const recoverActiveRun = async (runId: string) => {
        chat.appendThinking({
          id: `t_recover_${Date.now()}`,
          step: "recover",
          text: "连接已切换为后台恢复，正在等待任务完成…",
          ts: Date.now(),
        });
        try {
          const recovered = await recoverDurableRun(runId, controller.signal);
          if (recovered?.status === "completed" && recovered.final_text) {
            applyRecoveredAnswer(recovered.final_text);
          } else if (recovered?.status === "cancelled") {
            applyRecoveredAnswer("⏹ 任务已停止。");
          } else if (recovered?.status === "failed") {
            applyRecoveredAnswer(`⚠️ ${recovered.error ?? "任务执行失败"}`);
          } else {
            applyRecoveredAnswer(
              "网络连接中断，后台任务暂时仍未完成。可稍后从当前会话历史查看结果。",
            );
          }
        } catch (recoveryError) {
          if ((recoveryError as Error).name === "AbortError" || controller.signal.aborted) {
            applyRecoveredAnswer("⏹ 已停止生成。");
          } else {
            applyRecoveredAnswer("网络连接中断，且暂时无法恢复后台结果。请稍后重试。");
          }
        }
      };
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
          if (ev.event === "run") {
            runIdRef.current = ev.data.run_id;
          } else if (ev.event === "run_status") {
            runIdRef.current = null;
          } else if (ev.event === "session") {
            newSessionId = ev.data.session_id;
            // Server just minted a new session — keep the optimistic UI
            // bubbles (user + assistant) we already rendered. Only update
            // the sessionId reference, don't wipe messages.
            // IMPORTANT: cause: "server" tells chatStore NOT to abort
            // the in-flight SSE. This event IS from the current stream
            // telling us its id; aborting here would kill the very
            // stream we're listening to (the bug: new-conversation's
            // first query was immediately aborted because the server
            // minted a fresh id and setSession treated it as a "user
            // switched sessions" signal).
            chat.setSession(newSessionId, {
              clearMessages: false,
              cause: "server",
            });
            // Keep the guard's reference in sync — once we've seen
            // the server's id, subsequent events (token_delta etc.)
            // legitimately belong to it. Without this sync, every
            // event after `session` would see current = newId vs.
            // requestSessionId = null and bail out — killing the
            // stream right after it announces its id.
            requestSessionId = newSessionId;
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
                  // Always trust the backend's final payload. Streaming
                  // token_delta can briefly contain leaked think text if a
                  // model emits malformed tags; message_final is the
                  // cleaned source of truth.
                  m[m.length - 1] = { ...last, content: finalContent };
                  if (preamble) {
                    m[m.length - 1] = { ...m[m.length - 1], preamble };
                  }
                }
                return { messages: m, pendingText: finalContent };
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
        // Some proxies terminate a chunked response as a clean EOF instead
        // of surfacing a fetch error. A remaining run id means no terminal
        // run_status arrived, so recover exactly as we do for a thrown reset.
        const unfinishedRunId = runIdRef.current;
        if (unfinishedRunId && !controller.signal.aborted) {
          await recoverActiveRun(unfinishedRunId);
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
          const recoverRunId = runIdRef.current;
          if (recoverRunId) {
            await recoverActiveRun(recoverRunId);
          } else {
            const msg = e?.message ?? "出错了";
            const friendly = /network|fetch|aborted|timeout/i.test(msg)
              ? "网络连接中断（HF Space 代理超时）。请稍后重试。"
              : `⚠️ ${msg}`;
            chat.appendToken(`\n\n${friendly}`);
            chat.finalizeAssistant();
          }
        }
      } finally {
        abortRef.current = null;
        runIdRef.current = null;
      }
    },
    [chat, sessions]
  );

  const stop = useCallback(() => {
    if (abortRef.current) {
      const runId = runIdRef.current;
      // Tell the server first so paid tool work is cancelled even if the
      // browser immediately tears down its fetch stream.
      if (runId) void api.stopRun(runId).catch(() => {});
      abortRef.current.abort();
      // Synchronously finalize any in-flight assistant message so the
      // UI doesn't show a "loading" state for the now-dead stream.
      // finalizeAssistant is idempotent — if message_final already
      // ran (no longer streaming), this is a no-op.
      useChatStore.getState().finalizeAssistant();
      abortRef.current = null;
      runIdRef.current = null;
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
