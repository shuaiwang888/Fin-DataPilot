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

/** Multipart upload — does NOT set Content-Type, so the browser fills in
 *  the boundary. Used for skill zip uploads. */
async function uploadForm<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return res.json() as Promise<T>;
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { method: "DELETE" });
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
  /** Upload a skill zip. Returns the new SkillItem on success. */
  uploadSkill: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return uploadForm<SkillItem>("/api/skills/upload", form);
  },
  /** Delete an uploaded skill. Throws on built-in skills (400). */
  deleteSkill: (name: string) => del<{ deleted: string }>(`/api/skills/${name}`),
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
  deleteAllSessions: () =>
    http<{ deleted: number; user_id: string }>("/api/sessions", { method: "DELETE" }),
};

export type { ToolSpec, SkillItem, Session, Message };

