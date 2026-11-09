# H2C Recordings

A local recording manager for Bambu H2-series printers. Browse camera recordings as **estimated print-job sessions**, select several jobs, and download each as a single merged MP4.

Use the **React web app with a Bun backend**, or the **keyboard-driven Python terminal program**. Both share the same FTPS, grouping, merging, and private settings code. There is no cloud service or Bambu account login.

## What it does

- Connects directly to your printer over implicit FTPS (port 990).
- Lists MP4 video recordings only, excluding thumbnails and camera index files.
- Groups consecutive chunks into sessions. A gap longer than 20 minutes between chunk starts creates a new entry; the threshold is configurable.
- Downloads multiple selected sessions sequentially, then merges each session’s chunks chronologically using FFmpeg stream copying, without re-encoding.
- Shows overall download percentage, downloaded bytes, transfer speed, and estimated time remaining. Merging is a separate stage with an indeterminate indicator; download ETA does not include merging.
- Retains successfully downloaded chunks after a failure or cancellation for retry. Removes temporary chunks after successful merging. Original printer files and existing local videos are preserved.
- Defaults to Eastern Time (`America/New_York`), with automatic EST/EDT changes.
- Includes a video library and an in-app player with seeking and a browser-download option.

**Sessions are estimates, not verified print jobs.** The camera directory does not provide job names or IDs. Back-to-back prints can share a session; long interruptions can split one. Times shown are the first and last **chunk starts**, not the exact end of a print. An ongoing recording may change while downloading; refresh and retry after it stops.

## Requirements

- macOS or Linux. The installation script targets these systems; Windows installation is not included.
- Python **3.9+**, with `venv` and pip available. The installer checks Python but does not install it or system packages. On Debian/Ubuntu, install `python3-venv` if needed.
- A Mac or PC on the same local network as the printer, or a VPN that routes to that LAN. A cloud connection in Bambu Studio alone is insufficient.
- The printer’s IP/hostname and LAN access code, found on the touchscreen under **Settings → Network / LAN Only** (labels vary by firmware).
- Enough free disk space: roughly **twice the selected session’s size** during downloading and merging.

Bun is installed if missing. Python dependencies provide a local FFmpeg binary and timezone data. An existing system FFmpeg can also be used.

## Install

```bash
git clone https://github.com/jpaulorio/h2c-recordings.git
cd h2c-recordings
bash scripts/install.sh
```

The installer:

1. Checks Python 3.9+ and installs Bun from its official installer if needed.
2. Installs locked JavaScript dependencies with Bun.
3. Creates `.venv` and installs the pinned FFmpeg runtime plus timezone data.
4. Checks TypeScript and builds the web app.
5. Installs `h2c` and `h2c-app` launchers in `~/.local/bin`.

Keep the cloned directory in place: the launchers reference it. Rerunning the installer refreshes both launchers. An existing `h2c` launcher is replaced.

If your shell cannot find the commands, add this line to your shell profile, then open a new terminal:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Use the web app

```bash
h2c-app
```

Open **http://127.0.0.1:3210**. Keep that terminal process running while downloading. Closing the browser is fine; stopping the backend interrupts the queue.

### Settings

Enter the printer IP, LAN access code, and an **absolute download-folder path on the computer running the backend**. Save, then use **Test saved connection**. You can also set the FTPS port, session gap, display timezone, remote camera directory, and filename timezone offset.

A blank access-code field preserves the existing code. Changing printers requires supplying that printer’s code. Settings changes are blocked while downloads are queued/running, so one queue always uses one printer configuration.

### Recordings

Refresh the printer’s video list, search by date/time, and check the sessions you want. Select all is supported. Click **Download & merge** to queue all selected sessions.

### Downloads

Each job shows its state: queued, downloading, merging, complete, failed, cancelled, or interrupted. Downloading shows job-wide percentage, bytes, speed, and ETA, refreshed about once per second. Completed cached chunks count toward progress but are excluded from speed calculations.

Jobs run sequentially to limit printer connections and disk usage. Cancel a waiting or running job without cancelling other jobs. Retry refreshes the printer snapshot and reuses completed cached chunks when the recording files are unchanged. After a server restart, unfinished entries are marked interrupted rather than silently restarted.

The final file is named `job_DATE_TIME_TIMEZONE_ID.mp4`. If that name already exists, identical merged content is skipped; different content gets a numbered filename.

### Videos

Lists MP4 files in the configured download folder (non-recursive; symlinks are ignored). New merged videos appear when jobs complete; **Refresh** also picks up existing MP4s. Click **Play video** to open the built-in player. Standard controls support play/pause, volume, seeking, and fullscreen. **Download video** saves a copy through the browser.

The backend supports HTTP byte ranges so large videos can be sought without downloading the entire file. Playback uses the original codec: browser/OS codec support varies, particularly for HEVC/H.265. If a browser cannot play a file, download it and use a compatible player such as VLC. The app does not transcode it.

## Use the terminal program

For first-time setup without the browser:

```bash
h2c --configure
```

This saves the printer IP, access code, and download folder in the same private settings file used by the web app.

Then run:

```bash
h2c
```

On an interactive terminal:

| Key                     | Action                          |
| ----------------------- | ------------------------------- |
| ↑ / ↓                   | Move between sessions           |
| Space                   | Select / deselect               |
| ← / → or Page Up / Down | Move between pages              |
| Home / End              | First / last entry              |
| a                       | Select all                      |
| c                       | Clear selections                |
| Enter                   | Continue with selected sessions |
| q or Escape             | Cancel                          |

Choose the download folder and confirm. Every selected session downloads and merges into its own MP4. The terminal shows job-wide percentage, bytes, speed, and remaining download time, followed by the merging stage. A numbered selector is used when curses or an interactive terminal is unavailable.

```bash
h2c --test-connection       # TLS, login, encrypted listing; no downloads
h2c --all                   # Queue all sessions; still asks for folder and confirmation
h2c --session-gap 10         # Start a new estimated session after a 10-minute gap
h2c --host 192.168.1.50      # Override printer; prompts for its code if host differs
h2c --remote-dir /ipcam      # Camera folder (default)
h2c --active                # Try active FTP data connections if passive fails
h2c --help
```

The terminal program can run directly without installing Bun or the web app:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python python/h2c_recordings.py --configure
.venv/bin/python python/h2c_recordings.py
```

## How it works

```text
React UI → Bun HTTP API → Python JSON-line worker → implicit FTPS printer
               │                    │
        sequential queue       cached MP4 chunks
               │                    ↓
        progress polling       FFmpeg concat → merged MP4
               │                    ↓
               └────────── video library + ranged playback
```

The Bun backend serves React, validates local API requests, manages/persists the multi-job queue, streams completed videos, and launches the Python worker. The Python code handles TLS session reuse on FTPS data connections, MLSD or Unix LIST fallback, video filtering, session grouping, downloads, and FFmpeg merging. Credentials are passed to no command-line arguments; the worker reads them from private local settings.

FTP `modify` timestamps are UTC. The tested H2C writes recording filename timestamps in UTC+8; this app converts filename times from that offset and FTP times from UTC into the selected display timezone. Change the filename offset under Advanced settings if your printer behaves differently.

Printer certificates are self-signed, so TLS encrypts traffic but certificate identity verification is disabled. Use this app on a trusted local network. The backend binds **127.0.0.1 only**, rejects unrelated Host/Origin headers, and requires JSON for mutating API requests. It is a single-user local application, not an internet-facing or multi-user service. Do not expose its port publicly.

The access code is stored as plaintext in a settings file with owner-only permissions (`0600`), not in an encrypted vault. API responses only report whether a code exists; they never return it. No credentials, printer addresses, user recordings, queue history, or local settings are included in this repository.

## Local files and configuration

| Location                                                                                            | Purpose                                                               |
| --------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| `~/Library/Application Support/H2C Recordings/settings.json` (macOS)                                | Shared printer settings and credentials                               |
| `$XDG_CONFIG_HOME/h2c-recordings/settings.json` or `~/.config/h2c-recordings/settings.json` (Linux) | Shared printer settings and credentials                               |
| `.data/queue.json`                                                                                  | Local download queue/history; ignored by Git                          |
| Selected download folder                                                                            | Finished MP4 videos                                                   |
| `<download folder>/.h2c-parts/`                                                                     | Temporary chunks retained for retries; cleaned per successful session |

Existing macOS settings from the terminal program are reused. The web app reads your library from the shared download folder. Downloads started independently in the terminal do not appear in the web queue; their finished MP4s appear in Videos after refreshing the folder.

Environment overrides:

```bash
PORT=3211 h2c-app
H2C_CONFIG_FILE=/absolute/private/settings.json h2c-app
H2C_DATA_DIR=/absolute/private/app-state h2c-app
H2C_PYTHON=/absolute/path/to/python3 h2c-app
```

`H2C_CONFIG_FILE` also applies to `h2c`. Do not put credentials in environment example files or commit private settings.

## Development and tests

```bash
bun install --frozen-lockfile
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
bun run dev                 # local server with hot reload
bun run check               # TypeScript
bun test                    # queue sequencing/cancellation and HTTP ranges
bun run test:python         # FTPS parsing, grouping, download safety, merge integration, settings
bun run build               # builds the Bun server and React assets in dist/
```

The standard tests are offline: they do not require a printer or use its credentials. Merge tests generate small real MP4 files and verify FFmpeg output. Browser/API verification can use the optional synthetic FTPS fixture described in `tests/README.md`.

## Troubleshooting

- **Cannot connect:** check IP, LAN access code, port 990, local network/VPN route, and printer firmware restrictions. Test the saved connection in Settings or run `h2c --test-connection`.
- **Recording changed or disappeared:** the camera may still be writing, or older recordings may have rotated off the drive. Refresh and select the updated session after recording stops.
- **Not enough disk space:** free approximately twice that session’s listed size. Retry chunks are under `.h2c-parts`; remove that hidden folder manually only when no downloads are running if you want to discard cached progress.
- **Merge failed:** completed chunks are retained. Check FFmpeg installation and available space, then retry. Inputs need compatible codecs/parameters for lossless stream copying.
- **Video will not play:** try another browser or download it and use VLC. Browser codec support is independent of whether a merged file is valid.
- **Port already in use:** stop the other app instance or choose another `PORT`.
- **Installer cannot create venv:** install Python’s venv/pip components for your OS and rerun the installer.

This is an independent utility, not an official Bambu Lab application. Compatibility was checked against one H2C printer; other models and firmware may differ.
