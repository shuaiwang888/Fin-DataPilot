import type { AgentEvent } from "./types";

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

  while (true) {
    if (signal?.aborted) {
      try { await reader.cancel(); } catch { /* ignore */ }
      throw Object.assign(new Error("aborted"), { name: "AbortError" });
    }
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const raw = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const ev = parseBlock(raw);
      if (ev) yield ev;
    }
  }
  // flush any remaining
  if (buffer.trim()) {
    const ev = parseBlock(buffer);
    if (ev) yield ev;
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
    return { event, data } as AgentEvent;
  } catch {
    return { event, data: { raw: dataStr } } as unknown as AgentEvent;
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
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    throw new Error(`stream open failed: ${res.status} ${res.statusText}`);
  }
  yield* parseSSE(res.body, signal);
}
