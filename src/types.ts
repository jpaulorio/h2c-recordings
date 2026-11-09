export type Settings = {
  host: string;
  port: number;
  remoteDir: string;
  downloadFolder: string;
  sessionGap: number;
  timezone: string;
  filenameUtcOffset: number;
  hasAccessCode: boolean;
};
export type Job = {
  id: string;
  start: string | null;
  lastChunk: string | null;
  count: number;
  size: number | null;
  label: string;
};
export type Progress = {
  done: number;
  total: number | null;
  percent: number | null;
  rate: number;
  eta: number | null;
};
export type QueueItem = {
  id: string;
  job: Job;
  status:
    | "queued"
    | "downloading"
    | "merging"
    | "complete"
    | "failed"
    | "cancelled"
    | "interrupted";
  progress?: Progress;
  error?: string;
  file?: string;
  created: string;
};

export type Video = {
  id: string;
  name: string;
  size: number;
  modified: number;
};
