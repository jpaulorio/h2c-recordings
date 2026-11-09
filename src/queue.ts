import { randomUUID } from "node:crypto";
import type { Job, Progress, QueueItem } from "./types";
import type { WorkerEvent } from "./bridge";
export type Snapshot = Job & {
  chunks: { path: string; size: number | null; modified: string | null }[];
};
export type Runner = (
  job: Snapshot,
  event: (e: WorkerEvent) => void,
) => { result: Promise<any>; cancel: () => void };
const isCancelled = (item: QueueItem) => item.status === "cancelled";
const active = new Set(["queued", "downloading", "merging"]);
export class DownloadQueue {
  items: QueueItem[] = [];
  private snapshots = new Map<string, Snapshot>();
  private current?: { item: QueueItem; cancel: () => void };
  private pumping = false;
  constructor(
    private run: Runner,
    private changed: () => void = () => {},
  ) {}
  restore(items: QueueItem[]) {
    this.items = items.map((item) =>
      active.has(item.status)
        ? {
            ...item,
            status: "interrupted",
            error:
              "Server stopped. Refresh recordings and queue this job to resume cached chunks.",
          }
        : item,
    );
  }
  add(jobs: Snapshot[]) {
    const added: QueueItem[] = [];
    for (const job of jobs) {
      if (
        this.items.some(
          (item) => item.job.id === job.id && active.has(item.status),
        )
      )
        continue;
      const { chunks, ...summary } = job;
      const item: QueueItem = {
        id: randomUUID(),
        job: summary,
        status: "queued",
        created: new Date().toISOString(),
      };
      this.snapshots.set(item.id, job);
      this.items.push(item);
      added.push(item);
    }
    this.changed();
    void this.pump();
    return added;
  }
  cancel(id: string) {
    const item = this.items.find((item) => item.id === id);
    if (!item || !active.has(item.status)) return false;
    item.status = "cancelled";
    if (this.current?.item.id === id) this.current.cancel();
    this.changed();
    return true;
  }
  private async pump() {
    if (this.pumping) return;
    this.pumping = true;
    try {
      let item: QueueItem | undefined;
      while ((item = this.items.find((item) => item.status === "queued"))) {
        const current = item;
        current.status = "downloading";
        this.changed();
        try {
          const task = this.run(this.snapshots.get(current.id)!, (event) => {
            if (current.status === "cancelled") return;
            if (event.type === "progress") {
              const { done, total, percent, rate, eta } = event;
              current.progress = {
                done,
                total,
                percent,
                rate,
                eta,
              } as Progress;
            }
            if (event.type === "stage" && event.stage === "merging")
              current.status = "merging";
            if (event.type === "output") current.file = String(event.path);
            this.changed();
          });
          this.current = { item: current, cancel: task.cancel };
          await task.result;
          if (!isCancelled(current)) current.status = "complete";
        } catch (error) {
          if (!isCancelled(current)) {
            current.status = "failed";
            current.error =
              error instanceof Error ? error.message : "Download failed";
          }
        } finally {
          this.current = undefined;
          this.snapshots.delete(current.id);
          this.changed();
        }
      }
    } finally {
      this.pumping = false;
    }
  }
  shutdown() {
    for (const item of this.items)
      if (active.has(item.status)) {
        item.status = "interrupted";
        item.error =
          "Server stopped. Refresh recordings and queue this job to resume.";
      }
    this.current?.cancel();
    this.changed();
  }
}
