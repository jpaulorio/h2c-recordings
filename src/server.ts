import { mkdirSync, readFileSync, writeFileSync, renameSync } from "node:fs";
import { resolve, basename } from "node:path";
import html from "./index.html";
import { root, worker } from "./bridge";
import { DownloadQueue, type Snapshot } from "./queue";
import { fileResponse } from "./http";
import type { QueueItem } from "./types";
const port = Number(process.env.PORT || 3210);
const dataDir = resolve(process.env.H2C_DATA_DIR || resolve(root, ".data"));
mkdirSync(dataDir, { recursive: true, mode: 0o700 });
const queuePath = resolve(dataDir, "queue.json");
const queue = new DownloadQueue(
  (job, event) => worker({ action: "download", job }, event),
  () => {
    writeFileSync(queuePath + ".tmp", JSON.stringify(queue.items), {
      mode: 0o600,
    });
    renameSync(queuePath + ".tmp", queuePath);
  },
);
try {
  queue.restore(JSON.parse(readFileSync(queuePath, "utf8")) as QueueItem[]);
} catch {
  /* first launch */
}
let jobs = new Map<string, Snapshot>();
let library = new Map<
  string,
  { id: string; name: string; path: string; size: number; modified: number }
>();
const json = (value: unknown, status = 200) =>
  Response.json(value, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
const busy = () =>
  queue.items.some((item) =>
    ["queued", "downloading", "merging"].includes(item.status),
  );
const server = Bun.serve({
  hostname: "127.0.0.1",
  port,
  idleTimeout: 120,
  routes: {
    "/": html,
    "/settings": html,
    "/downloads": html,
    "/library": html,
  },
  async fetch(request) {
    const url = new URL(request.url);
    const path = url.pathname;
    const expected = new Set([`127.0.0.1:${port}`, `localhost:${port}`]);
    if (!expected.has(request.headers.get("host") || ""))
      return json({ error: "Invalid host" }, 403);
    const origin = request.headers.get("origin");
    if (
      origin &&
      ![`http://127.0.0.1:${port}`, `http://localhost:${port}`].includes(origin)
    )
      return json({ error: "Cross-origin access is not allowed" }, 403);
    if (
      !["GET", "HEAD"].includes(request.method) &&
      !request.headers.get("content-type")?.startsWith("application/json")
    )
      return json({ error: "Expected JSON" }, 415);
    try {
      if (path === "/api/settings" && request.method === "GET")
        return json(await worker({ action: "settings" }).result);
      if (path === "/api/settings" && request.method === "PUT") {
        if (busy())
          return json(
            {
              error:
                "Wait for the queue to finish, or cancel jobs before changing settings.",
            },
            409,
          );
        const value = await worker({
          action: "save-settings",
          data: await request.json(),
        }).result;
        jobs.clear();
        return json(value);
      }
      if (path === "/api/connection" && request.method === "POST")
        return json(await worker({ action: "test" }).result);
      if (path === "/api/jobs" && request.method === "POST") {
        const scan = (await worker({ action: "list" }).result) as Snapshot[];
        jobs = new Map(scan.map((job) => [job.id, job]));
        return json(scan.map(({ chunks, ...summary }) => summary));
      }
      if (path === "/api/queue" && request.method === "GET")
        return json(queue.items);
      if (path === "/api/queue" && request.method === "POST") {
        const body = (await request.json()) as { ids?: unknown };
        if (
          !Array.isArray(body.ids) ||
          !body.ids.length ||
          body.ids.length > 500 ||
          body.ids.some((id) => typeof id !== "string" || !jobs.has(id))
        )
          return json(
            { error: "Refresh recordings and select valid jobs." },
            400,
          );
        return json(queue.add(body.ids.map((id) => jobs.get(id)!)), 202);
      }
      const cancel = /^\/api\/queue\/([^/]+)\/cancel$/.exec(path);
      if (cancel && request.method === "POST")
        return queue.cancel(cancel[1])
          ? json({ ok: true })
          : json({ error: "Job is no longer running." }, 409);
      if (path === "/api/library" && request.method === "GET") {
        const videos = (await worker({ action: "library" }).result) as {
          id: string;
          name: string;
          path: string;
          size: number;
          modified: number;
        }[];
        library = new Map(videos.map((video) => [video.id, video]));
        return json(videos.map(({ path, ...video }) => video));
      }
      const video = /^\/api\/videos\/([a-f0-9]+)$/.exec(path);
      if (video && ["GET", "HEAD"].includes(request.method)) {
        const item = library.get(video[1]);
        if (!item)
          return json(
            { error: "Refresh the Videos page to locate this video." },
            404,
          );
        const file = Bun.file(item.path);
        if (!(await file.exists()))
          return json({ error: "Video was moved or deleted." }, 404);
        return fileResponse(file, request, basename(item.path));
      }
      return json({ error: "Not found" }, 404);
    } catch (error) {
      return json(
        { error: error instanceof Error ? error.message : "Request failed" },
        400,
      );
    }
  },
});
console.log(`H2C Recordings → http://127.0.0.1:${server.port}`);
for (const signal of ["SIGINT", "SIGTERM"] as const)
  process.on(signal, () => {
    queue.shutdown();
    server.stop();
    setTimeout(() => process.exit(0), 500);
  });
