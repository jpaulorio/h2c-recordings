import { existsSync } from "node:fs";
import { resolve } from "node:path";
export const root = resolve(import.meta.dir, "..");
export function pythonPath() {
  return (
    process.env.H2C_PYTHON ||
    (existsSync(resolve(root, ".venv/bin/python"))
      ? resolve(root, ".venv/bin/python")
      : "python3")
  );
}
export type WorkerEvent = { type: string; [key: string]: any };
export function worker(request: unknown, onEvent?: (e: WorkerEvent) => void) {
  const child = Bun.spawn(
    [pythonPath(), "-u", resolve(root, "python/worker.py")],
    {
      stdin: "pipe",
      stdout: "pipe",
      stderr: "ignore",
      env: process.env,
    },
  );
  child.stdin.write(JSON.stringify(request) + "\n");
  child.stdin.end();
  const result = (async () => {
    const reader = child.stdout.getReader();
    const decoder = new TextDecoder();
    let buffer = "",
      value: any,
      error = "";
    while (true) {
      const { done, value: bytes } = await reader.read();
      if (done) break;
      buffer += decoder.decode(bytes, { stream: true });
      let end: number;
      while ((end = buffer.indexOf("\n")) !== -1) {
        const line = buffer.slice(0, end);
        buffer = buffer.slice(end + 1);
        if (!line.trim()) continue;
        const event = JSON.parse(line) as WorkerEvent;
        if (event.type === "result") value = event.data;
        else if (event.type === "error") error = String(event.message);
        else onEvent?.(event);
      }
    }
    const code = await child.exited;
    if (error || code || value === undefined)
      throw new Error(
        error ||
          `Printer worker exited (${code}). Check Python and FFmpeg installation.`,
      );
    return value;
  })();
  return { result, cancel: () => child.kill("SIGTERM") };
}
