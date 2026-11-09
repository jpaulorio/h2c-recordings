import { describe, expect, test } from "bun:test";
import { DownloadQueue, type Snapshot } from "../src/queue";
const job = (id: string): Snapshot => ({
  id,
  start: null,
  lastChunk: null,
  count: 1,
  size: 3,
  label: "test",
  chunks: [{ path: `/ipcam/${id}.mp4`, size: 3, modified: null }],
});
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
function harness() {
  const tasks: {
    job: Snapshot;
    resolve: (x: unknown) => void;
    reject: (e: Error) => void;
    cancelled: boolean;
    event: (e: any) => void;
  }[] = [];
  const queue = new DownloadQueue((job, event) => {
    let resolve!: (x: unknown) => void, reject!: (e: Error) => void;
    const result = new Promise((a, b) => {
      resolve = a;
      reject = b;
    });
    const task = { job, resolve, reject, cancelled: false, event };
    tasks.push(task);
    return {
      result,
      cancel: () => {
        task.cancelled = true;
        reject(new Error("cancelled"));
      },
    };
  });
  return { queue, tasks };
}
describe("multi-job queue", () => {
  test("downloads sequentially and exposes per-job progress and merged output", async () => {
    const { queue, tasks } = harness();
    queue.add([job("a"), job("b")]);
    expect(tasks.length).toBe(1);
    expect(queue.items[1].status).toBe("queued");
    tasks[0].event({
      type: "progress",
      done: 1,
      total: 3,
      percent: 33.3,
      rate: 1,
      eta: 2,
    });
    expect(queue.items[0].progress?.percent).toBe(33.3);
    tasks[0].event({ type: "stage", stage: "merging" });
    expect(queue.items[0].status).toBe("merging");
    tasks[0].event({ type: "output", path: "/tmp/merged.mp4" });
    tasks[0].resolve({});
    await tick();
    expect(queue.items[0].status).toBe("complete");
    expect(queue.items[0].file).toBe("/tmp/merged.mp4");
    expect(tasks.length).toBe(2);
    tasks[1].resolve({});
    await tick();
    expect(queue.items[1].status).toBe("complete");
  });
  test("does not duplicate active jobs", () => {
    const { queue } = harness();
    queue.add([job("a"), job("a")]);
    queue.add([job("a")]);
    expect(queue.items.length).toBe(1);
    queue.shutdown();
  });
  test("cancels queued jobs without running them", async () => {
    const { queue, tasks } = harness();
    const items = queue.add([job("a"), job("b")]);
    queue.cancel(items[1].id);
    tasks[0].resolve({});
    await tick();
    expect(tasks.length).toBe(1);
    expect(items[1].status).toBe("cancelled");
  });
  test("cancels running worker and continues next job", async () => {
    const { queue, tasks } = harness();
    queue.add([job("a"), job("b")]);
    queue.cancel(queue.items[0].id);
    await tick();
    expect(tasks[0].cancelled).toBe(true);
    expect(queue.items[0].status).toBe("cancelled");
    expect(tasks.length).toBe(2);
    tasks[1].resolve({});
    await tick();
  });
  test("failed job does not stop the queue", async () => {
    const { queue, tasks } = harness();
    queue.add([job("a"), job("b")]);
    tasks[0].reject(new Error("printer unavailable"));
    await tick();
    expect(queue.items[0].status).toBe("failed");
    expect(queue.items[0].error).toBe("printer unavailable");
    expect(tasks.length).toBe(2);
    tasks[1].resolve({});
    await tick();
  });
  test("marks unfinished jobs interrupted after a restart", () => {
    const { queue } = harness();
    queue.restore([
      { id: "old", job: job("a"), status: "merging", created: "now" },
    ]);
    expect(queue.items[0].status).toBe("interrupted");
  });
});
