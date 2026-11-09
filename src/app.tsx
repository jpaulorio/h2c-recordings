import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowDownToLine,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleHelp,
  Clock3,
  Film,
  FolderOpen,
  HardDrive,
  Layers3,
  LoaderCircle,
  Play,
  Radio,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Video as VideoIcon,
  X,
} from "lucide-react";
import type { Job, QueueItem, Settings, Video } from "./types";
import "./style.css";
type Page = "recordings" | "downloads" | "library" | "settings";
const paths: Record<Page, string> = {
  recordings: "/",
  downloads: "/downloads",
  library: "/library",
  settings: "/settings",
};
const initialPage = () =>
  (Object.entries(paths).find(
    ([, path]) => path === location.pathname,
  )?.[0] as Page) || "recordings";
async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch("/api" + path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed");
  return data;
}
const bytes = (value: number | null | undefined) => {
  if (value == null) return "Unknown size";
  let amount = value;
  let unit = 0;
  while (amount >= 1024 && unit < 4) {
    amount /= 1024;
    unit++;
  }
  return `${amount.toFixed(unit ? 1 : 0)} ${["B", "KB", "MB", "GB", "TB"][unit]}`;
};
const eta = (seconds: number | null | undefined) =>
  seconds == null
    ? "Calculating…"
    : seconds < 60
      ? `${Math.ceil(seconds)} sec`
      : seconds < 3600
        ? `${Math.ceil(seconds / 60)} min`
        : `${Math.floor(seconds / 3600)}h ${Math.ceil((seconds % 3600) / 60)}m`;
function date(
  value: string | number | null,
  zone = "America/New_York",
  style: "day" | "time" | "full" = "full",
) {
  if (value == null) return "Unknown date";
  const d = new Date(typeof value === "number" ? value * 1000 : value);
  return new Intl.DateTimeFormat(
    "en-US",
    style === "day"
      ? { month: "short", day: "numeric", year: "numeric", timeZone: zone }
      : style === "time"
        ? {
            hour: "numeric",
            minute: "2-digit",
            timeZoneName: "short",
            timeZone: zone,
          }
        : {
            month: "short",
            day: "numeric",
            year: "numeric",
            hour: "numeric",
            minute: "2-digit",
            timeZoneName: "short",
            timeZone: zone,
          },
  ).format(d);
}
const busy = (q: QueueItem) =>
  ["queued", "downloading", "merging"].includes(q.status);
function App() {
  const [page, setPage] = useState<Page>(initialPage);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [videos, setVideos] = useState<Video[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [libraryLoading, setLibraryLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [scanned, setScanned] = useState(false);
  const [adding, setAdding] = useState(false);
  const [playing, setPlaying] = useState<Video | null>(null);
  const zone = settings?.timezone || "America/New_York";
  function navigate(next: Page) {
    history.pushState({}, "", paths[next]);
    setPage(next);
    setError("");
    setNotice("");
  }
  useEffect(() => {
    const pop = () => setPage(initialPage());
    window.addEventListener("popstate", pop);
    return () => window.removeEventListener("popstate", pop);
  }, []);
  useEffect(() => {
    api<Settings>("/settings")
      .then(setSettings)
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    let alive = true;
    const update = () =>
      api<QueueItem[]>("/queue")
        .then((items) => {
          if (alive) setQueue(items);
        })
        .catch(() => {});
    void update();
    const timer = setInterval(update, 1000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);
  async function scan() {
    setLoading(true);
    setError("");
    try {
      const result = await api<Job[]>("/jobs", "POST", {});
      setJobs(result);
      setSelected(new Set());
      setScanned(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    if (settings?.host && settings.hasAccessCode && !scanned) void scan();
  }, [
    settings?.host,
    settings?.hasAccessCode,
    settings?.sessionGap,
    settings?.timezone,
    settings?.filenameUtcOffset,
    scanned,
  ]);
  async function loadVideos() {
    setLibraryLoading(true);
    try {
      setVideos(await api<Video[]>("/library"));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLibraryLoading(false);
    }
  }
  const completedKey = queue
    .filter((q) => q.status === "complete")
    .map((q) => q.id)
    .join(",");
  useEffect(() => {
    if (page === "library") void loadVideos();
  }, [page, completedKey]);
  const active = queue.filter(busy);
  const totalSize = jobs.reduce((sum, j) => sum + (j.size || 0), 0);
  const filtered = useMemo(
    () =>
      jobs.filter((j) =>
        `${date(j.start, zone)} ${j.count} ${j.label}`
          .toLowerCase()
          .includes(search.toLowerCase()),
      ),
    [jobs, search, zone],
  );
  const chosen = jobs.filter((j) => selected.has(j.id));
  function toggle(id: string) {
    setSelected((current) => {
      const next = new Set(current);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }
  async function enqueue(ids: string[]) {
    setAdding(true);
    setError("");
    try {
      await api("/queue", "POST", { ids });
      setSelected(new Set());
      navigate("downloads");
      setQueue(await api<QueueItem[]>("/queue"));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setAdding(false);
    }
  }
  async function cancel(id: string) {
    try {
      await api(`/queue/${id}/cancel`, "POST", {});
      setQueue(await api<QueueItem[]>("/queue"));
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function retry(item: QueueItem) {
    setError("");
    try {
      const fresh = await api<Job[]>("/jobs", "POST", {});
      setJobs(fresh);
      if (!fresh.some((j) => j.id === item.job.id))
        throw new Error(
          "This session changed. Choose its current entry on the Recordings page.",
        );
      await enqueue([item.job.id]);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const labels = {
    recordings: [
      "YOUR PRINTER, ON REPLAY",
      "A home for every recording.",
      "Browse your camera sessions. Keep the moments that matter.",
    ],
    downloads: [
      "FROM PRINTER TO PLAYBACK",
      "Good things take a little time.",
      "Your selected sessions, downloaded and merged into complete videos.",
    ],
    library: [
      "SAVED & READY TO PLAY",
      "Your prints, in motion.",
      "All the merged videos in your download folder, ready for a replay.",
    ],
    settings: [
      "MAKE YOURSELF AT HOME",
      "A few settings. Then you’re set.",
      "Connect your printer and choose where your recordings belong.",
    ],
  };
  return (
    <div className="shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="/"
          onClick={(e) => {
            e.preventDefault();
            navigate("recordings");
          }}
        >
          <span className="brand-mark">
            <Layers3 size={23} />
          </span>
          <span>
            H2C<span className="brand-light"> recordings</span>
          </span>
        </a>
        <div className="side-caption">WORKSPACE</div>
        <nav>
          {(
            [
              ["recordings", Film, "Recordings"],
              ["downloads", ArrowDownToLine, "Downloads"],
              ["library", Play, "Videos"],
              ["settings", Settings2, "Settings"],
            ] as const
          ).map(([id, Icon, label]) => (
            <a
              key={id}
              href={paths[id]}
              className={`nav-item ${page === id ? "active" : ""}`}
              onClick={(e) => {
                e.preventDefault();
                navigate(id);
              }}
            >
              <Icon size={18} />
              {label}
              {id === "downloads" && active.length > 0 && (
                <span className="nav-count">{active.length}</span>
              )}
            </a>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="local-note">
            <ShieldCheck size={19} />
            <div>
              <strong>Stays on your network</strong>
              <span>Your printer. Your files.</span>
            </div>
          </div>
          <div className="sidebar-footer">
            Made for the things you make.<span>H2C Recordings · v1.0</span>
          </div>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <span className="breadcrumb">
            Workspace <ChevronRight size={13} />
            <strong>
              {page === "library"
                ? "Videos"
                : page[0].toUpperCase() + page.slice(1)}
            </strong>
          </span>
          <button className="printer-pill" onClick={() => navigate("settings")}>
            <span
              className={`status-dot ${settings?.hasAccessCode ? "configured" : ""}`}
            />
            {settings?.host ? "Printer configured" : "Printer setup"}
            <Settings2 size={13} />
          </button>
        </header>
        <div className="content">
          <div className="page-heading">
            <div>
              <div className="eyebrow">{labels[page][0]}</div>
              <h1>{labels[page][1]}</h1>
              <p className="subtitle">{labels[page][2]}</p>
            </div>
            <div className="heading-icon">
              {page === "recordings" ? (
                <Film size={40} strokeWidth={1} />
              ) : page === "library" ? (
                <VideoIcon size={40} strokeWidth={1} />
              ) : page === "downloads" ? (
                <ArrowDownToLine size={40} strokeWidth={1} />
              ) : (
                <Settings2 size={40} strokeWidth={1} />
              )}
            </div>
          </div>
          {error && (
            <div className="alert error" role="alert">
              <CircleHelp size={18} />
              <span>{error}</span>
              <button aria-label="Dismiss error" onClick={() => setError("")}>
                <X size={16} />
              </button>
            </div>
          )}
          {notice && (
            <div className="alert success" role="status">
              <CheckCircle2 size={18} />
              {notice}
            </div>
          )}
          {page === "recordings" && (
            <>
              <section className="stats">
                <Stat
                  icon={<Layers3 size={20} />}
                  label="RECORDING SESSIONS"
                  value={scanned ? String(jobs.length) : "—"}
                  detail="One entry. Every related chunk."
                />
                <Stat
                  icon={<HardDrive size={20} />}
                  label="ON YOUR PRINTER"
                  value={scanned ? bytes(totalSize) : "—"}
                  detail="Video files only"
                />
                <Stat
                  icon={<Clock3 size={20} />}
                  label="DISPLAY TIMEZONE"
                  value={
                    zone === "America/New_York"
                      ? "Eastern Time"
                      : zone.split("/").pop() || zone
                  }
                  detail={
                    zone === "America/New_York"
                      ? "EST / EDT, adjusted automatically"
                      : zone
                  }
                />
              </section>
              <section className="panel">
                <div className="panel-heading">
                  <div>
                    <h2>
                      Recording sessions{" "}
                      <span className="count">{jobs.length}</span>
                    </h2>
                    <p>Estimated jobs, grouped by continuous recording.</p>
                  </div>
                  <button
                    className="button secondary"
                    disabled={loading || !settings?.hasAccessCode}
                    onClick={scan}
                  >
                    <RefreshCw
                      size={15}
                      className={loading ? "spinning" : ""}
                    />
                    {loading ? "Reading printer…" : "Refresh"}
                  </button>
                </div>
                {!settings?.hasAccessCode ? (
                  <Empty
                    icon={<Radio size={30} />}
                    title="Let’s connect your printer"
                    text="Add its local IP address and LAN access code to see your recordings."
                    action={
                      <button
                        className="button primary"
                        onClick={() => navigate("settings")}
                      >
                        Set up printer <ArrowRight size={16} />
                      </button>
                    }
                  />
                ) : loading ? (
                  <Empty
                    icon={<LoaderCircle size={28} className="spinning" />}
                    title="A quick look at your printer…"
                    text="Reading recording sessions over an encrypted connection."
                  />
                ) : !jobs.length ? (
                  <Empty
                    icon={<Film size={30} />}
                    title={
                      scanned
                        ? "No video recordings found"
                        : "Ready when you are"
                    }
                    text="Refresh to read the camera’s video recordings."
                  />
                ) : (
                  <>
                    <div className="table-toolbar">
                      <label className="search-label">
                        <span>Find a session</span>
                        <input
                          type="search"
                          aria-label="Find a session"
                          placeholder="Search by date or time…"
                          value={search}
                          onChange={(e) => setSearch(e.target.value)}
                        />
                      </label>
                      <span>
                        {filtered.length} sessions · {settings.sessionGap} min
                        gap
                      </span>
                    </div>
                    <div className="table-scroll">
                      <table>
                        <thead>
                          <tr>
                            <th className="checkbox-cell">
                              <input
                                type="checkbox"
                                aria-label="Select all visible sessions"
                                checked={
                                  filtered.length > 0 &&
                                  filtered.every((j) => selected.has(j.id))
                                }
                                onChange={(e) =>
                                  setSelected((current) => {
                                    const next = new Set(current);
                                    filtered.forEach((j) =>
                                      e.target.checked
                                        ? next.add(j.id)
                                        : next.delete(j.id),
                                    );
                                    return next;
                                  })
                                }
                              />
                            </th>
                            <th>RECORDING SESSION</th>
                            <th>CHUNKS</th>
                            <th>SIZE</th>
                            <th>STATUS</th>
                          </tr>
                        </thead>
                        <tbody>
                          {filtered.map((job) => {
                            const current = queue.find(
                              (q) => q.job.id === job.id && busy(q),
                            );
                            return (
                              <tr
                                key={job.id}
                                className={
                                  selected.has(job.id) ? "selected" : ""
                                }
                              >
                                <td className="checkbox-cell">
                                  <input
                                    type="checkbox"
                                    aria-label={`Select session ${date(job.start, zone)}`}
                                    checked={selected.has(job.id)}
                                    onChange={() => toggle(job.id)}
                                  />
                                </td>
                                <td>
                                  <div className="session-cell">
                                    <span className="film-icon">
                                      <Film size={19} />
                                    </span>
                                    <span>
                                      <strong>
                                        {date(job.start, zone, "day")}
                                      </strong>
                                      <small>
                                        {date(job.start, zone, "time")}{" "}
                                        <span className="muted">→</span>{" "}
                                        {date(
                                          job.lastChunk,
                                          zone,
                                          date(job.start, zone, "day") ===
                                            date(job.lastChunk, zone, "day")
                                            ? "time"
                                            : "full",
                                        )}
                                      </small>
                                    </span>
                                  </div>
                                </td>
                                <td>
                                  <span className="chunk-label">
                                    {job.count} <span>clips</span>
                                  </span>
                                </td>
                                <td className="size-cell">{bytes(job.size)}</td>
                                <td>
                                  <span
                                    className={`badge ${current ? "green" : ""}`}
                                  >
                                    {current
                                      ? current.status === "queued"
                                        ? "Queued"
                                        : "In progress"
                                      : "On printer"}
                                  </span>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                    <div className="table-foot">
                      <CircleHelp size={14} />
                      <span>
                        Sessions are estimates. A gap over {settings.sessionGap}{" "}
                        minutes starts a new entry. End time is the last chunk’s
                        start.
                      </span>
                    </div>
                  </>
                )}
              </section>
              <div className="selection-bar">
                <div>
                  <span className="selection-icon">
                    <Check size={19} />
                  </span>
                  <strong>
                    {chosen.length}{" "}
                    {chosen.length === 1 ? "session" : "sessions"} selected
                  </strong>
                  <span className="muted">
                    {chosen.length
                      ? `${bytes(chosen.reduce((s, j) => s + (j.size || 0), 0))} total`
                      : "Choose the sessions you’d like to keep."}
                  </span>
                </div>
                <button
                  className="button primary"
                  disabled={!chosen.length || adding}
                  onClick={() => enqueue(chosen.map((j) => j.id))}
                >
                  <ArrowDownToLine size={17} />
                  {adding ? "Adding to queue…" : "Download & merge"}
                </button>
              </div>
              <p className="under-note">
                <ShieldCheck size={13} /> Downloads preserve your printer’s
                original files. Videos merge without re-encoding.
              </p>
            </>
          )}
          {page === "downloads" && (
            <>
              <div className="section-intro">
                <div>
                  <h2>
                    Download queue{" "}
                    <span className="count">{active.length} active</span>
                  </h2>
                  <p>
                    Jobs run one at a time. You can leave this page while the
                    app stays running.
                  </p>
                </div>
                <button
                  className="button secondary"
                  onClick={() => navigate("recordings")}
                >
                  Add recordings <ArrowRight size={15} />
                </button>
              </div>
              {!queue.length ? (
                <section className="panel">
                  <Empty
                    icon={<ArrowDownToLine size={32} />}
                    title="A little space for what’s next"
                    text="Select one or more sessions to start your download queue."
                    action={
                      <button
                        className="button primary"
                        onClick={() => navigate("recordings")}
                      >
                        Browse recordings <ArrowRight size={16} />
                      </button>
                    }
                  />
                </section>
              ) : (
                <div className="queue-list">
                  {[...queue].reverse().map((item) => (
                    <QueueCard
                      key={item.id}
                      item={item}
                      zone={zone}
                      cancel={() => cancel(item.id)}
                      retry={() => retry(item)}
                      openLibrary={() => navigate("library")}
                    />
                  ))}
                </div>
              )}
              <div className="tip">
                <FolderOpen size={19} />
                <div>
                  <strong>One session, one finished video.</strong>
                  <p>
                    Files are saved to{" "}
                    <code>
                      {settings?.downloadFolder || "your selected folder"}
                    </code>
                    . Completed chunks are kept for retry after an interruption.
                  </p>
                </div>
              </div>
            </>
          )}
          {page === "library" && (
            <>
              <div className="section-intro">
                <div>
                  <h2>
                    Your video library{" "}
                    <span className="count">{videos.length}</span>
                  </h2>
                  <p>Merged MP4s from your configured download folder.</p>
                </div>
                <button
                  className="button secondary"
                  disabled={libraryLoading}
                  onClick={loadVideos}
                >
                  <RefreshCw
                    size={15}
                    className={libraryLoading ? "spinning" : ""}
                  />
                  Refresh
                </button>
              </div>
              {!videos.length ? (
                <section className="panel">
                  <Empty
                    icon={<Play size={32} />}
                    title={
                      libraryLoading
                        ? "Looking for your videos…"
                        : "Your next replay starts here"
                    }
                    text="Finished videos appear here automatically after merging."
                    action={
                      <button
                        className="button primary"
                        onClick={() => navigate("recordings")}
                      >
                        Browse recordings <ArrowRight size={16} />
                      </button>
                    }
                  />
                </section>
              ) : (
                <div className="video-grid">
                  {videos.map((video) => (
                    <article className="video-card" key={video.id}>
                      <button
                        className="video-cover"
                        aria-label={`Play ${video.name}`}
                        onClick={() => setPlaying(video)}
                      >
                        <span className="cover-lines" />
                        <span className="video-tag">MERGED MP4</span>
                        <span className="play-button">
                          <Play size={25} fill="currentColor" />
                        </span>
                        <span className="cover-date">
                          {date(video.modified, zone, "day")}
                        </span>
                      </button>
                      <div className="video-body">
                        <h3 title={video.name}>{video.name}</h3>
                        <p>
                          {bytes(video.size)} <span>·</span>{" "}
                          {date(video.modified, zone, "time")}
                        </p>
                        <div className="video-actions">
                          <button
                            className="button small secondary"
                            onClick={() => setPlaying(video)}
                          >
                            <Play size={14} />
                            Play video
                          </button>
                          <a
                            className="icon-button"
                            aria-label={`Download ${video.name}`}
                            href={`/api/videos/${video.id}?download=1`}
                          >
                            <ArrowDownToLine size={17} />
                          </a>
                        </div>
                      </div>
                    </article>
                  ))}
                </div>
              )}
              <p className="under-note">
                <FolderOpen size={14} />
                {settings?.downloadFolder}
              </p>
            </>
          )}
          {page === "settings" && settings && (
            <SettingsForm
              settings={settings}
              locked={active.length > 0}
              saved={(value) => {
                setSettings(value);
                setScanned(false);
                setNotice(
                  "Settings saved. Your printer is ready for a connection test.",
                );
              }}
              error={setError}
              notice={setNotice}
            />
          )}
          <footer className="content-footer">
            <span>
              <span className="tiny-dot" />
              LOCAL FIRST. ALWAYS YOURS.
            </span>
            <span>Built for Bambu H2-series recordings.</span>
          </footer>
        </div>
      </main>
      {playing && <Player video={playing} close={() => setPlaying(null)} />}
    </div>
  );
}
function Stat({
  icon,
  label,
  value,
  detail,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="stat">
      <div className="stat-label">
        {icon}
        <span>{label}</span>
      </div>
      <div className="stat-value">{value}</div>
      <p>{detail}</p>
    </div>
  );
}
function Empty({
  icon,
  title,
  text,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  text: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">{icon}</div>
      <h3>{title}</h3>
      <p>{text}</p>
      {action}
    </div>
  );
}
function QueueCard({
  item,
  zone,
  cancel,
  retry,
  openLibrary,
}: {
  item: QueueItem;
  zone: string;
  cancel: () => void;
  retry: () => void;
  openLibrary: () => void;
}) {
  const progress = item.progress;
  const running = item.status === "downloading";
  return (
    <article className="queue-card">
      <div className="queue-top">
        <div className="session-cell">
          <span
            className={`film-icon ${item.status === "complete" ? "done-icon" : ""}`}
          >
            {item.status === "complete" ? (
              <CheckCircle2 size={22} />
            ) : (
              <Film size={22} />
            )}
          </span>
          <span>
            <strong>{date(item.job.start, zone, "day")}</strong>
            <small>
              {date(item.job.start, zone, "time")} · {item.job.count} clips ·{" "}
              {bytes(item.job.size)}
            </small>
          </span>
        </div>
        <span
          className={`badge ${item.status === "complete" ? "green" : item.status === "failed" ? "red" : ""}`}
        >
          {item.status === "downloading"
            ? "Downloading"
            : item.status === "merging"
              ? "Merging video"
              : item.status[0].toUpperCase() + item.status.slice(1)}
        </span>
      </div>
      {(running || item.status === "merging") && (
        <>
          <div className="progress-label">
            <strong>
              {item.status === "merging"
                ? "Download complete. Merging into one MP4…"
                : `${progress?.percent == null ? "Starting…" : progress.percent.toFixed(1) + "% downloaded"}`}
            </strong>
            <span>
              {item.status === "merging"
                ? "Finishing your video"
                : `${eta(progress?.eta)} remaining`}
            </span>
          </div>
          <div
            className={`progress-track ${item.status === "merging" ? "indeterminate" : ""}`}
            role="progressbar"
            aria-label="Download progress"
            aria-valuenow={progress?.percent ?? undefined}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <span
              style={{
                width: `${item.status === "merging" ? 35 : progress?.percent || 0}%`,
              }}
            />
          </div>
          <div className="progress-meta">
            <span>
              {bytes(progress?.done || 0)} of {bytes(item.job.size)}
            </span>
            <span>
              {item.status === "merging"
                ? "Merging time varies by video size"
                : `${bytes(progress?.rate || 0)}/s · updates every second`}
            </span>
          </div>
        </>
      )}
      {item.error && <p className="inline-error">{item.error}</p>}
      <div className="queue-bottom">
        <span>
          {item.status === "queued"
            ? "Waiting for the previous job to finish."
            : item.status === "complete"
              ? "Saved as one merged MP4."
              : item.status === "cancelled" ||
                  item.status === "interrupted" ||
                  item.status === "failed"
                ? "Completed chunks can be reused on retry."
                : "Encrypted transfer · original files stay on the printer"}
        </span>
        {busy(item) ? (
          <button className="text-button" onClick={cancel}>
            Cancel
          </button>
        ) : item.status === "complete" ? (
          <button className="text-button" onClick={openLibrary}>
            View in Videos <ArrowRight size={14} />
          </button>
        ) : (
          <button className="text-button" onClick={retry}>
            Retry <RefreshCw size={13} />
          </button>
        )}
      </div>
    </article>
  );
}
function SettingsForm({
  settings,
  locked,
  saved,
  error,
  notice,
}: {
  settings: Settings;
  locked: boolean;
  saved: (s: Settings) => void;
  error: (s: string) => void;
  notice: (s: string) => void;
}) {
  const [form, setForm] = useState({ ...settings, accessCode: "" });
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  useEffect(() => setForm({ ...settings, accessCode: "" }), [settings]);
  const field = (key: string, value: unknown) =>
    setForm((current) => ({ ...current, [key]: value }));
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    error("");
    notice("");
    try {
      saved(await api<Settings>("/settings", "PUT", form));
    } catch (e) {
      error((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  async function test() {
    setTesting(true);
    error("");
    notice("");
    try {
      const result = await api<{ videos: number; jobs: number }>(
        "/connection",
        "POST",
        {},
      );
      notice(
        `Connection successful. ${result.videos} video chunks across ${result.jobs} estimated sessions.`,
      );
    } catch (e) {
      error((e as Error).message);
    } finally {
      setTesting(false);
    }
  }
  return (
    <form className="settings-layout" onSubmit={save}>
      <div>
        <section className="panel settings-panel">
          <div className="settings-section-title">
            <span className="film-icon">
              <Radio size={20} />
            </span>
            <div>
              <h2>Printer connection</h2>
              <p>Find these on your printer’s Network / LAN Only screen.</p>
            </div>
          </div>
          <div className="form-grid">
            <label>
              Printer IP or hostname
              <input
                required
                placeholder="192.168.1.50"
                value={form.host}
                onChange={(e) => field("host", e.target.value)}
              />
            </label>
            <label>
              FTPS port
              <input
                required
                type="number"
                min={1}
                max={65535}
                value={form.port}
                onChange={(e) => field("port", Number(e.target.value))}
              />
            </label>
            <label className="full-width">
              LAN access code
              <input
                type="password"
                autoComplete="new-password"
                placeholder={
                  form.hasAccessCode
                    ? "Saved locally — leave blank to keep"
                    : "Printer LAN access code"
                }
                value={form.accessCode}
                onChange={(e) => field("accessCode", e.target.value)}
              />
              <small>
                {form.hasAccessCode
                  ? "An access code is saved. Its value is never sent back to the browser."
                  : "Required to log in to the printer."}
              </small>
            </label>
          </div>
        </section>
        <section className="panel settings-panel">
          <div className="settings-section-title">
            <span className="film-icon">
              <FolderOpen size={20} />
            </span>
            <div>
              <h2>Downloads & grouping</h2>
              <p>
                A home for your videos, and a little order for your sessions.
              </p>
            </div>
          </div>
          <div className="form-grid">
            <label className="full-width">
              Download folder
              <input
                required
                placeholder="/Users/you/Downloads/H2C Recordings"
                value={form.downloadFolder}
                onChange={(e) => field("downloadFolder", e.target.value)}
              />
              <small>
                Absolute path on the computer running the app. Created when
                downloading.
              </small>
            </label>
            <label>
              Session gap (minutes)
              <input
                required
                type="number"
                min="0.1"
                step="0.1"
                value={form.sessionGap}
                onChange={(e) => field("sessionGap", Number(e.target.value))}
              />
            </label>
            <label>
              Display timezone
              <input
                required
                value={form.timezone}
                onChange={(e) => field("timezone", e.target.value)}
              />
            </label>
          </div>
          <details>
            <summary>Advanced recording settings</summary>
            <div className="form-grid">
              <label>
                Remote camera folder
                <input
                  required
                  value={form.remoteDir}
                  onChange={(e) => field("remoteDir", e.target.value)}
                />
              </label>
              <label>
                Filename UTC offset (hours)
                <input
                  type="number"
                  min="-14"
                  max="14"
                  step="0.5"
                  value={form.filenameUtcOffset}
                  onChange={(e) =>
                    field("filenameUtcOffset", Number(e.target.value))
                  }
                />
                <small>
                  Observed H2C filenames use UTC+8. FTP metadata uses UTC.
                </small>
              </label>
            </div>
          </details>
        </section>
        <div className="settings-actions">
          <button
            className="button secondary"
            type="button"
            disabled={testing || !settings.hasAccessCode}
            onClick={test}
          >
            {testing ? (
              <LoaderCircle size={15} className="spinning" />
            ) : (
              <Radio size={15} />
            )}
            Test saved connection
          </button>
          <button
            className="button primary"
            disabled={saving || locked}
            type="submit"
          >
            {saving ? "Saving…" : "Save settings"}
            <Check size={16} />
          </button>
        </div>
        {locked && (
          <p className="muted">
            Cancel or finish active jobs before changing settings.
          </p>
        )}
      </div>
      <aside className="settings-aside">
        <ShieldCheck size={28} strokeWidth={1.4} />
        <h3>A direct connection.</h3>
        <p>
          Your app talks to the printer over encrypted FTPS on your local
          network. No cloud account is needed.
        </p>
        <hr />
        <h4>A note about sessions</h4>
        <p>
          The camera records in chunks. We group continuous chunks into
          estimated jobs, then merge each selection into a single video.
        </p>
        <p>
          Back-to-back prints may share a session. Adjust the gap if you’d
          prefer smaller groups.
        </p>
        <hr />
        <span className="local-label">
          <span className="tiny-dot" />
          LOCAL SETTINGS · OWNER-ONLY FILE
        </span>
      </aside>
    </form>
  );
}
function Player({ video, close }: { video: Video; close: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    dialog.current?.showModal();
    return () => dialog.current?.close();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="player-dialog"
      onCancel={close}
      onClick={(e) => {
        if (e.target === dialog.current) close();
      }}
    >
      <div className="player-heading">
        <div>
          <div className="eyebrow">YOUR VIDEO</div>
          <h2>{video.name}</h2>
        </div>
        <button
          className="icon-button"
          aria-label="Close player"
          onClick={close}
        >
          <X size={22} />
        </button>
      </div>
      <video
        key={video.id}
        controls
        autoPlay
        playsInline
        preload="metadata"
        src={`/api/videos/${video.id}`}
        onError={() => setFailed(true)}
      />
      {failed && (
        <p className="inline-error">
          This browser couldn’t play the video codec. Download the MP4 and open
          it in a compatible player such as VLC.
        </p>
      )}
      <div className="player-footer">
        <span>{bytes(video.size)} · Merged MP4</span>
        <a
          className="button secondary"
          href={`/api/videos/${video.id}?download=1`}
        >
          <ArrowDownToLine size={16} />
          Download video
        </a>
      </div>
    </dialog>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
