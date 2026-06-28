import { create } from "zustand";
import type { AnswerPreamble, Message } from "../lib/types";

export interface ThinkingStep {
  id: string;
  step: string;
  text: string;
  ts: number;
}

export interface ToolCallRecord {
  name: string;
  args: Record<string, unknown>;
  trace_id: string;
  result?: unknown;
  ok?: boolean;
  duration_ms?: number;
  error?: string;
  ts: number;
}

export interface ChatMessage extends Omit<Message, "thinking" | "tool_calls"> {
  // For UI: each assistant message can have associated thinking + tool calls
  thinking?: ThinkingStep[];
  toolCalls?: ToolCallRecord[];
  streaming?: boolean;
  preamble?: AnswerPreamble;
}

interface ChatState {
  sessionId: string | null;
  messages: ChatMessage[];
  streaming: boolean;
  pendingText: string; // current streamed tokens (not yet flushed into last message)

  /** Has the answer body started streaming for the current assistant turn?
   *  Reset on every new assistant message; set on the first token_delta.
   *  Used by ThinkingPanel to auto-collapse once the answer is flowing. */
  answerStarted: boolean;

  /** Internal: set by useChatStream when the hook mounts, so the store
   *  can abort the in-flight SSE whenever the active session changes.
   *  UI code MUST NOT call this directly — it's a back-channel from
   *  the stream hook to the store, used by setSession/reset to make
   *  "switch to another session" tear down the old stream.
   *  No-op until useChatStream registers. */
  _abortInflight: () => void;

  /** Update the active session id. By default also clears messages; pass
   *  `{ clearMessages: false }` to keep the optimistic UI bubbles (used
   *  when the server hands us a freshly-created session id mid-stream).
   *
   *  `cause`:
   *    - "user"  (default): caller is the UI switching to a different
   *        session. When `id` actually changes, the in-flight SSE is
   *        aborted first so late events from the old stream can't
   *        pollute the new session.
   *    - "server": caller is useChatStream propagating the session id
   *        that the server just minted. The in-flight SSE MUST NOT be
   *        aborted here — this IS that stream, telling us its id. */
  setSession: (
    id: string | null,
    opts?: { clearMessages?: boolean; cause?: "user" | "server" }
  ) => void;
  appendUser: (text: string) => string;
  appendAssistant: () => string;
  appendThinking: (step: ThinkingStep) => void;
  appendToolCall: (tc: ToolCallRecord) => void;
  appendToken: (text: string) => void;
  setAnswerStarted: (v: boolean) => void;
  finalizeAssistant: () => void;
  setMessages: (msgs: Message[]) => void;
  reset: () => void;
}

let _id = 0;
const newId = () => `m_${Date.now()}_${++_id}`;

export const useChatStore = create<ChatState>((set, get) => ({
  sessionId: null,
  messages: [],
  streaming: false,
  pendingText: "",
  answerStarted: false,
  // Registered by useChatStream; no-op until then.
  _abortInflight: () => {},

  setSession: (id, opts) => {
    const clearMessages = opts?.clearMessages !== false;
    const cause = opts?.cause ?? "user";
    const { sessionId: prevId, _abortInflight } = get();
    // Tear down the in-flight stream when the active session actually
    // changes — but ONLY when the change is user-driven. The server's
    // `session` event is the stream itself telling us its id, so
    // aborting it would kill the very stream we're trying to listen
    // to (this was the bug: new-conversation's first query got
    // immediately aborted because the server minted a fresh id).
    if (cause === "user" && prevId !== id) {
      _abortInflight();
    }
    if (clearMessages) {
      set({
        sessionId: id,
        messages: [],
        pendingText: "",
        streaming: false,
        answerStarted: false,
      });
    } else {
      // Server just minted a new session id mid-stream — keep the
      // optimistic UI bubbles (user + assistant) we already rendered.
      set({ sessionId: id });
    }
  },

  appendUser: (text) => {
    const id = newId();
    set((s) => ({
      messages: [
        ...s.messages,
        { id, role: "user", content: text, created_at: new Date().toISOString() },
      ],
    }));
    return id;
  },

  appendAssistant: () => {
    const id = newId();
    set((s) => ({
      messages: [
        ...s.messages,
        {
          id,
          role: "assistant",
          content: "",
          created_at: new Date().toISOString(),
          thinking: [],
          toolCalls: [],
          streaming: true,
        },
      ],
      pendingText: "",
      streaming: true,
      answerStarted: false,
    }));
    return id;
  },

  appendThinking: (step) => {
    set((s) => {
      const msgs = [...s.messages];
      const last = msgs[msgs.length - 1];
      if (last && last.role === "assistant" && last.streaming) {
        msgs[msgs.length - 1] = {
          ...last,
          thinking: [...(last.thinking ?? []), step],
        };
      }
      return { messages: msgs };
    });
  },

  appendToolCall: (tc) => {
    set((s) => {
      const msgs = [...s.messages];
      const last = msgs[msgs.length - 1];
      if (last && last.role === "assistant" && last.streaming) {
        msgs[msgs.length - 1] = {
          ...last,
          toolCalls: [...(last.toolCalls ?? []), tc],
        };
      }
      return { messages: msgs };
    });
  },

  appendToken: (text) => {
    set((s) => {
      const pending = s.pendingText + text;
      const msgs = [...s.messages];
      const last = msgs[msgs.length - 1];
      if (last && last.role === "assistant" && last.streaming) {
        msgs[msgs.length - 1] = { ...last, content: pending };
      }
      return {
        messages: msgs,
        pendingText: pending,
        answerStarted: true, // first token_delta = answer is flowing
      };
    });
  },

  setAnswerStarted: (v) => set({ answerStarted: v }),

  finalizeAssistant: () => {
    set((s) => {
      const msgs = [...s.messages];
      const last = msgs[msgs.length - 1];
      if (last && last.role === "assistant") {
        msgs[msgs.length - 1] = { ...last, streaming: false };
      }
      return { messages: msgs, pendingText: "", streaming: false };
    });
  },

  setMessages: (msgs) => {
    set({
      messages: msgs.map((m) => ({ ...m, thinking: [], toolCalls: [] })),
      pendingText: "",
      streaming: false,
      answerStarted: false,
    });
  },

  reset: () => {
    // New-conversation button path: also tear down any in-flight stream.
    get()._abortInflight();
    set({
      sessionId: null,
      messages: [],
      pendingText: "",
      streaming: false,
      answerStarted: false,
    });
  },
}));
