import type { AgentEvent } from "./types";
import { authHeaders } from "./auth";

/**
 * Parse a Server-Sent Events stream and yield structured events.
 * Designed to work with `fetch` + ReadableStream (works across origins and supports POST).
 *
 * `signal` is an AbortSignal — when aborted, we stop reading and let
 * the caller's `catch` block handle it (the user pressed "stop").
 */
export async function* parseSSE(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal
): AsyncGenerator<AgentEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  try {
    while (true) {
      if (signal?.aborted) throw new DOMException("aborted", "AbortError");
      const { value, done } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      buffer = buffer.replace(/\r\n/g, "\n");
      let index: number;
      while ((index = buffer.indexOf("\n\n")) !== -1) {
        const event = parseBlock(buffer.slice(0, index));
        buffer = buffer.slice(index + 2);
        if (event) yield event;
      }
      if (done) break;
    }
    if (buffer.trim()) {
      const event = parseBlock(buffer);
      if (event) yield event;
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

function parseBlock(block: string): AgentEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
  }
  if (!dataLines.length) return null;
  const dataStr = dataLines.join("\n");
  try {
    const data = JSON.parse(dataStr);
    if (!data || typeof data !== "object" || Array.isArray(data)) return null;
    return { event, data } as AgentEvent;
  } catch {
    return null;
  }
}

/** Open an SSE stream via POST + fetch. */
export async function* streamChat(
  url: string,
  body: Record<string, unknown>,
  signal?: AbortSignal
): AsyncGenerator<AgentEvent> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    throw new Error(`stream open failed: ${res.status} ${res.statusText}`);
  }
  yield* parseSSE(res.body, signal);
}
