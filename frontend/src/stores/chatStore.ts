import { create } from "zustand";
import type { Message } from "../lib/types";

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
}

interface ChatState {
  sessionId: string | null;
  messages: ChatMessage[];
  streaming: boolean;
  pendingText: string; // current streamed tokens (not yet flushed into last message)

  /** Update the active session id. By default also clears messages; pass
   *  `{ clearMessages: false }` to keep the optimistic UI bubbles (used
   *  when the server hands us a freshly-created session id mid-stream). */
  setSession: (id: string | null, opts?: { clearMessages?: boolean }) => void;
  appendUser: (text: string) => string;
  appendAssistant: () => string;
  appendThinking: (step: ThinkingStep) => void;
  appendToolCall: (tc: ToolCallRecord) => void;
  appendToken: (text: string) => void;
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

  setSession: (id, opts) => {
    const clearMessages = opts?.clearMessages !== false;
    if (clearMessages) {
      set({ sessionId: id, messages: [], pendingText: "", streaming: false });
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
      return { messages: msgs, pendingText: pending };
    });
  },

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
    });
  },

  reset: () => set({ sessionId: null, messages: [], pendingText: "", streaming: false }),
}));
