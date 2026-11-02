import { create } from "zustand";
import type { AgentEvent, AnswerPreamble, AnswerPresentation, Message, ResearchPhase } from "../lib/types";
import { hydrateMessage, reduceChatEvent } from "../lib/chatEvents";

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
  thinking?: ThinkingStep[];
  toolCalls?: ToolCallRecord[];
  streaming?: boolean;
  preamble?: AnswerPreamble;
  presentation?: AnswerPresentation;
  phase?: ResearchPhase;
  plan?: string[];
  error?: string;
}

interface ChatState {
  sessionId: string | null;
  messages: ChatMessage[];
  streaming: boolean;
  answerStarted: boolean;
  _abortInflight: () => void;
  _resumeRun: (runId: string) => void;
  applyEvent: (event: AgentEvent) => void;
  setSession: (id: string | null, opts?: { clearMessages?: boolean; cause?: "user" | "server" }) => void;
  appendUser: (text: string) => string;
  appendAssistant: () => string;
  finalizeAssistant: () => void;
  setMessages: (messages: Message[]) => void;
  reset: () => void;
}

let sequence = 0;
const newId = () => `m_${Date.now()}_${++sequence}`;
const empty = { messages: [] as ChatMessage[], streaming: false, answerStarted: false };

export const useChatStore = create<ChatState>((set, get) => ({
  sessionId: null,
  ...empty,
  _abortInflight: () => {},
  _resumeRun: () => {},
  applyEvent: (event) => set((state) => {
    const last = state.messages[state.messages.length - 1];
    if (!last || last.role !== "assistant") return state;
    const next = reduceChatEvent(last, event);
    return {
      messages: [...state.messages.slice(0, -1), next],
      streaming: next.streaming === true,
      answerStarted: !!next.content,
    };
  }),
  setSession: (id, options) => {
    if (options?.cause !== "server" && get().sessionId !== id) get()._abortInflight();
    set(options?.clearMessages === false ? { sessionId: id } : { sessionId: id, ...empty });
  },
  appendUser: (content) => {
    const id = newId();
    set((state) => ({ messages: [...state.messages, { id, role: "user", content, created_at: new Date().toISOString() }] }));
    return id;
  },
  appendAssistant: () => {
    const id = newId();
    set((state) => ({
      messages: [...state.messages, {
        id, role: "assistant", content: "", created_at: new Date().toISOString(),
        thinking: [], toolCalls: [], streaming: true, phase: "planning",
      }],
      streaming: true,
      answerStarted: false,
    }));
    return id;
  },
  finalizeAssistant: () => set((state) => {
    const last = state.messages[state.messages.length - 1];
    if (!last || last.role !== "assistant") return { streaming: false };
    return {
      messages: [...state.messages.slice(0, -1), {
        ...last, streaming: false, phase: last.error ? "failed" : last.phase === "cancelled" ? "cancelled" : "completed",
      }],
      streaming: false,
    };
  }),
  setMessages: (messages) => set({ messages: messages.map(hydrateMessage), streaming: false, answerStarted: false }),
  reset: () => {
    get()._abortInflight();
    set({ sessionId: null, ...empty });
  },
}));
