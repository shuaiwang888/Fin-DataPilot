// API types shared with the backend
export interface ToolSpec {
  name: string;
  display_name: string;
  description: string;
  category: string;
  parameters: Array<{
    name: string;
    type: "string" | "number" | "integer" | "boolean" | "object" | "array";
    description: string;
    required: boolean;
    enum?: unknown[] | null;
  }>;
  requires: string[];
  enabled_by_default: boolean;
  version: string;
  examples: Array<Record<string, unknown>>;
}

export interface SkillItem {
  spec: ToolSpec;
  enabled: boolean;
  requirements_met?: Record<string, boolean>;
}

export interface Session {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  tool_calls?: Array<Record<string, unknown>>;
  tool_call_id?: string;
  thinking?: Record<string, unknown>;
  created_at: string;
}

// SSE event types
export type AgentEvent =
  | { event: "ping"; data: { ts: number } }
  | { event: "session"; data: { session_id: string; title: string } }
  | { event: "think"; data: { step: string; text: string; trace_id?: string } }
  | { event: "plan"; data: { sub_tasks: string[] } }
  | { event: "tool_call"; data: { name: string; args: Record<string, unknown>; trace_id: string } }
  | {
      event: "tool_result";
      data: {
        name: string;
        ok: boolean;
        duration_ms: number;
        trace_id: string;
        result?: unknown;
        error?: string;
      };
    }
  | { event: "reflection"; data: { verdict: string; reason: string } }
  | { event: "summary_start"; data: Record<string, never> }
  | { event: "token_delta"; data: { text: string } }
  | { event: "message_final"; data: { content: string; tool_calls: unknown[] } }
  | { event: "error"; data: { message: string } }
  | { event: "done"; data: { trace_id?: string } };
