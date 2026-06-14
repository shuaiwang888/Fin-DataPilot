import type { Message, Session, SkillItem, ToolSpec } from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => http<{ ok: boolean; tools: { count: number; names: string[] } }>("/api/health"),
  listSkills: () => http<{ skills: SkillItem[] }>("/api/skills"),
  toggleSkill: (name: string, enabled: boolean) =>
    http<{ name: string; enabled: boolean }>(`/api/skills/${name}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled }),
    }),
  debugSkill: (name: string, args: Record<string, unknown>) =>
    http<{ ok: boolean; data: unknown; error?: string }>(`/api/skills/${name}/debug`, {
      method: "POST",
      body: JSON.stringify({ args }),
    }),
  listSessions: () => http<{ sessions: Session[] }>("/api/sessions"),
  createSession: (title: string) =>
    http<{ id: string; title: string; created_at: string }>("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ title }),
    }),
  getSession: (id: string) => http<{ session: Session; messages: Message[] }>(`/api/sessions/${id}`),
  patchSession: (id: string, title: string) =>
    http<{ id: string; title: string }>(`/api/sessions/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
  deleteSession: (id: string) =>
    http<{ id: string; deleted: boolean }>(`/api/sessions/${id}`, { method: "DELETE" }),
};

export type { ToolSpec, SkillItem, Session, Message };
