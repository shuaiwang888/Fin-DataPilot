import { expect, it } from "vitest";
import { parseSSE } from "./sse";

async function collect(chunks: Uint8Array[]) {
  const stream = new ReadableStream<Uint8Array>({ start(controller) {
    for (const chunk of chunks) controller.enqueue(chunk);
    controller.close();
  } });
  const events = [];
  for await (const event of parseSSE(stream)) events.push(event);
  expect(stream.locked).toBe(false);
  return events;
}

it("decodes UTF-8 characters and CRLF delimiters split at every byte", async () => {
  const bytes = new TextEncoder().encode(': keep-alive\r\n\r\nevent: token_delta\r\ndata: {"text":"金融对比"}\r\n\r\nevent: done\r\ndata: {}\r\n\r\n');
  const events = await collect(Array.from(bytes, (byte) => new Uint8Array([byte])));
  expect(events).toEqual([{ event: "token_delta", data: { text: "金融对比" } }, { event: "done", data: {} }]);
});

it("ignores malformed data and flushes a final unterminated block", async () => {
  const bytes = new TextEncoder().encode('event: token_delta\ndata: bad-json\n\nevent: done\ndata: {}');
  expect(await collect([bytes])).toEqual([{ event: "done", data: {} }]);
});

it("releases the reader when the consumer stops early", async () => {
  let cancelled = false;
  const stream = new ReadableStream<Uint8Array>({
    start(controller) { controller.enqueue(new TextEncoder().encode('event: done\ndata: {}\n\n')); },
    cancel() { cancelled = true; },
  });
  for await (const event of parseSSE(stream)) { expect(event.event).toBe("done"); break; }
  expect(cancelled).toBe(true);
  expect(stream.locked).toBe(false);
});
