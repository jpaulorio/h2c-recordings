#!/usr/bin/env python3
"""Browse and download Bambu H2-series camera recordings over local-network FTPS.

Requires Python 3.9+ and FFmpeg for merging recording sessions.

Run: python3 h2c_recordings.py
     python3 h2c_recordings.py --test-connection
     python3 h2c_recordings.py --remote-dir /   # browse the whole USB drive
"""

import argparse
import calendar
import errno
import filecmp
import ftplib
import getpass
import hashlib
import json
import math
import os
import posixpath
import re
import shutil
import socket
import ssl
import sys
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple


APP_DIR = (Path.home() / "Library" / "Application Support" / "H2C Recordings" if sys.platform == "darwin"
           else Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "h2c-recordings")
APP_DIR = Path(os.environ.get("H2C_CONFIG_FILE", str(APP_DIR / "settings.json"))).expanduser().parent
CONFIG_FILE = Path(os.environ.get("H2C_CONFIG_FILE", str(APP_DIR / "settings.json"))).expanduser()
VIDEO_DATE_RE = re.compile(
    r"(20\d{2})[-_](\d{2})[-_](\d{2})[T_ -](\d{2})[-_:](\d{2})[-_:](\d{2})"
)
# This printer writes recording filenames in UTC+08:00; FTP metadata is UTC.
RECORDING_TIMEZONE = timezone(timedelta(hours=8))
DISPLAY_TIMEZONE = ZoneInfo("America/New_York")
MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_abbr) if name}
FTP_EXCEPTIONS = ftplib.all_errors + (OSError, ValueError)


@dataclass
class RemoteFile:
    path: str
    size: Optional[int]
    modified: Optional[datetime]

    @property
    def name(self) -> str:
        return posixpath.basename(self.path)


@dataclass
class RecordingJob:
    chunks: List[RemoteFile]

    @property
    def modified(self):
        return self.chunks[0].modified

    @property
    def size(self):
        sizes = [chunk.size for chunk in self.chunks]
        return sum(sizes) if all(size is not None for size in sizes) else None

    @property
    def name(self):
        end = display_timestamp(self.chunks[-1].modified)
        return f"Estimated job | {len(self.chunks)} chunks | last chunk {end}"

    @property
    def path(self):
        return self.name


def group_recordings(files, gap_minutes=20):
    if gap_minutes <= 0:
        raise ValueError("Session gap must be greater than zero")
    # Only MP4 camera recordings participate; thumbnails and index files are ignored.
    videos = sorted((file for file in files if file.name.lower().endswith(".mp4")),
                    key=lambda file: (file.modified or datetime.min.replace(tzinfo=timezone.utc), file.path))
    jobs = []
    for file in videos:
        previous = jobs[-1].chunks[-1] if jobs else None
        continuous = (previous is not None and file.modified is not None
                      and previous.modified is not None
                      and posixpath.dirname(file.path) == posixpath.dirname(previous.path)
                      and file.modified - previous.modified <= timedelta(minutes=gap_minutes))
        if continuous:
            jobs[-1].chunks.append(file)
        else:
            jobs.append(RecordingJob([file]))
    return list(reversed(jobs))


def find_ffmpeg():
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    saved = read_config().get("ffmpeg_path")
    if isinstance(saved, str) and os.path.isfile(saved) and os.access(saved, os.X_OK):
        return saved
    bundled = Path(__file__).resolve().parent / "h2c-runtime"
    if bundled.is_dir() and str(bundled) not in sys.path:
        sys.path.insert(0, str(bundled))
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        raise RuntimeError("FFmpeg is required to merge videos. Install FFmpeg and retry.")


class ImplicitFTPS(ftplib.FTP_TLS):
    """Implicit FTPS (:990), with TLS session reuse on file/data connections."""

    def __init__(self, timeout: int = 20):
        # Bambu printers use a self-signed certificate. The connection is TLS-
        # encrypted, but cannot authenticate the device via a public CA.
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        super().__init__(context=context, timeout=timeout)

    def connect(self, host="", port=0, timeout=-999, source_address=None):
        if host:
            self.host = host
        if port:
            self.port = port
        if timeout != -999:
            self.timeout = timeout
        if source_address is not None:
            self.source_address = source_address
        self.sock = socket.create_connection(
            (self.host, self.port), self.timeout, source_address=self.source_address
        )
        self.af = self.sock.family
        try:
            self.sock = self.context.wrap_socket(self.sock, server_hostname=self.host)
            self.file = self.sock.makefile("r", encoding=self.encoding)
            self.welcome = self.getresp()
            return self.welcome
        except Exception:
            self.close()
            raise

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            try:
                conn = self.context.wrap_socket(
                    conn, server_hostname=self.host, session=self.sock.session
                )
            except Exception:
                conn.close()
                raise
        return conn, size


def read_config() -> dict:
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(data: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp_name = tempfile.mkstemp(prefix=".settings-", dir=str(APP_DIR))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, CONFIG_FILE)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def configure_path_completion() -> bool:
    try:
        import readline
    except ImportError:
        return False

    def completer(text: str, state: int) -> Optional[str]:
        if state == 0:
            raw = readline.get_line_buffer()
            expanded = os.path.expanduser(raw)
            parent_str, partial = os.path.split(expanded)
            parent = Path(parent_str or ".")
            matches = []
            try:
                for child in parent.iterdir():
                    if child.is_dir() and child.name.lower().startswith(partial.lower()):
                        suffix = child.name[len(partial):] + os.sep
                        matches.append(raw + suffix)
            except (OSError, PermissionError):
                pass
            completer.matches = sorted(matches, key=str.lower)
        matches = getattr(completer, "matches", [])
        return matches[state] if state < len(matches) else None

    readline.set_completer(completer)
    readline.set_completer_delims("\n")  # Spaces in directory names work.
    if "libedit" in (readline.__doc__ or ""):
        readline.parse_and_bind("bind ^I rl_complete")  # Default macOS Python.
    else:
        readline.parse_and_bind("tab: complete")
    return True


def choose_local_folder(config: dict) -> Path:
    default = config.get("last_folder") or str(Path.home() / "Downloads")
    has_completion = configure_path_completion()
    if has_completion:
        print("Folder path: press Tab to autocomplete directories (spaces supported).")
    while True:
        raw = input(f"Download folder [{default}]: ").strip()
        path = Path(os.path.expandvars(raw or default)).expanduser()
        if path.is_dir():
            return path.resolve()
        if path.exists():
            print("That path exists but is not a directory.")
        else:
            choice = input(f"Create folder '{path}'? [y/N]: ").strip().lower()
            if choice in ("y", "yes"):
                path.mkdir(parents=True, exist_ok=True)
                return path.resolve()


def timestamp_in_name(name: str) -> Optional[datetime]:
    match = VIDEO_DATE_RE.search(name)
    if not match:
        return None
    try:
        return datetime(*map(int, match.groups()), tzinfo=RECORDING_TIMEZONE)
    except ValueError:
        return None


def parse_list_date(month: str, day: str, year_or_time: str) -> Optional[datetime]:
    month_num = MONTHS.get(month.lower()[:3])
    if month_num is None:
        return None
    try:
        if ":" in year_or_time:
            hours, minutes = (int(n) for n in year_or_time.split(":", 1))
            date = datetime(datetime.now(timezone.utc).year, month_num, int(day), hours, minutes,
                            tzinfo=timezone.utc)
            if date > datetime.now(timezone.utc) + timedelta(days=1):
                date = date.replace(year=date.year - 1)
            return date
        return datetime(int(year_or_time), month_num, int(day), tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_list_line(line: str) -> Optional[Tuple[str, bool, Optional[int], Optional[datetime]]]:
    # Unix-format LIST, as normally returned by Bambu's vsftpd server.
    parts = line.split(maxsplit=8)
    if len(parts) < 9 or not parts[0] or parts[0][0] not in ("-", "d"):
        return None
    name = parts[8]
    if name in (".", "..") or "/" in name or "\\" in name:
        return None
    try:
        size = int(parts[4]) if parts[0].startswith("-") else None
    except ValueError:
        size = None
    return (name, parts[0].startswith("d"), size, parse_list_date(*parts[5:8]))


def parse_mlsd_modify(value: str) -> Optional[datetime]:
    try:
        return datetime.strptime(value[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def list_directory(ftp: ImplicitFTPS, directory: str):
    ftp.cwd(directory)
    try:
        entries = []
        for name, facts in ftp.mlsd():
            if name in (".", "..") or "/" in name or "\\" in name:
                continue
            kind = facts.get("type", "").lower()
            if kind not in ("dir", "file"):
                continue
            size = int(facts["size"]) if facts.get("size", "").isdigit() else None
            entries.append((name, kind == "dir", size,
                            parse_mlsd_modify(facts.get("modify"))))
        return entries
    except (ftplib.error_perm, ftplib.error_reply):
        pass  # Older Bambu firmware has LIST but not MLSD.

    lines: List[str] = []
    ftp.retrlines("LIST", lines.append)
    entries = [parsed for line in lines if (parsed := parse_list_line(line))]
    if not entries and lines:
        raise RuntimeError("FTP listing format not recognized; unable to identify files safely.")
    return entries


def remote_files(ftp: ImplicitFTPS, base: str) -> List[RemoteFile]:
    files: List[RemoteFile] = []
    stack = [posixpath.normpath("/" + base.lstrip("/"))]
    visited = set()
    while stack:
        directory = stack.pop()
        if directory in visited:
            continue
        visited.add(directory)
        for name, is_dir, size, modified in list_directory(ftp, directory):
            path = posixpath.join(directory, name)
            if is_dir:
                stack.append(path)
            else:
                files.append(RemoteFile(path, size, timestamp_in_name(name) or modified))
    files.sort(key=lambda item: (item.modified or datetime.min.replace(tzinfo=timezone.utc), item.path), reverse=True)
    return files


def size_label(size: Optional[int]) -> str:
    if size is None:
        return "?"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return "?"


def parse_selection(raw: str, count: int) -> List[int]:
    result = set()
    for token in raw.replace(" ", "").split(","):
        if not token:
            raise ValueError("Empty item in selection")
        if "-" in token:
            start_str, end_str = token.split("-", 1)
            if not (start_str.isdigit() and end_str.isdigit()):
                raise ValueError("Use numbers or ranges such as 2-5")
            start, end = int(start_str), int(end_str)
            if start > end:
                raise ValueError("Range start must be before its end")
            if start < 1 or end > count:
                raise ValueError(f"Select numbers between 1 and {count}")
            result.update(range(start, end + 1))
        elif token.isdigit():
            result.add(int(token))
        else:
            raise ValueError(f"Invalid selection: {token}")
    if not result or min(result) < 1 or max(result) > count:
        raise ValueError(f"Select numbers between 1 and {count}")
    return sorted(index - 1 for index in result)


def display_timestamp(value: Optional[datetime]) -> str:
    if value is None:
        return "Unknown"
    return value.astimezone(DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S %Z")


def keyboard_file_selector(screen, files, curses):
    """Select files in a terminal while retaining selection across pages."""
    screen.keypad(True)
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    selected = set()
    cursor = 0
    message = ""
    while True:
        height, width = screen.getmaxyx()
        screen.erase()

        def write(row, text, highlight=False):
            if 0 <= row < height and width > 1:
                # Terminal width and Unicode may cause curses to report clipping.
                try:
                    screen.addnstr(row, 0, text, width - 1,
                                   curses.A_REVERSE if highlight else curses.A_NORMAL)
                except curses.error:
                    pass

        if height < 6 or width < 30:
            write(0, "Enlarge terminal; q to quit.")
            key = screen.getch()
            if key in (ord("q"), ord("Q"), 27):
                return None
            continue
        page_size = height - 5
        top = (cursor // page_size) * page_size
        write(0, f"Entries: {len(files)} | Selected: {len(selected)} | Newest first")
        write(1, "    #  Selected  Date/time (ET)                Size       Job / chunks")
        for row, idx in enumerate(range(top, min(top + page_size, len(files))), 2):
            entry = files[idx]
            date = display_timestamp(entry.modified)
            marker = "[x]" if idx in selected else "[ ]"
            label = f"{len(entry.chunks)} video chunks" if isinstance(entry, RecordingJob) else entry.path
            write(row, f"{idx+1:5}  {marker}  {date}  {size_label(entry.size):>10}  {label}",
                  idx == cursor)
        write(height - 3, "Up/Down: move | Space: toggle | Enter: download | q: cancel")
        write(height - 2, "Left/Right or PgUp/PgDn: page | a: select all | c: clear")
        detail = (f"Last chunk starts: {display_timestamp(files[cursor].chunks[-1].modified)}"
                  if isinstance(files[cursor], RecordingJob) else f"File {cursor+1}/{len(files)}")
        write(height - 1, message or detail)
        screen.refresh()
        key = screen.getch()
        message = ""
        if key in (ord("q"), ord("Q"), 27):
            return None
        if key in (10, 13, curses.KEY_ENTER):
            if selected:
                return [files[idx] for idx in sorted(selected)]
            message = "Select at least one file with Space, or q to cancel."
        elif key == ord(" "):
            if cursor in selected:
                selected.remove(cursor)
            else:
                selected.add(cursor)
        elif key == curses.KEY_UP:
            cursor = max(0, cursor - 1)
        elif key == curses.KEY_DOWN:
            cursor = min(len(files) - 1, cursor + 1)
        elif key in (curses.KEY_PPAGE, curses.KEY_LEFT):
            cursor = max(0, cursor - page_size)
        elif key in (curses.KEY_NPAGE, curses.KEY_RIGHT):
            cursor = min(len(files) - 1, cursor + page_size)
        elif key == curses.KEY_HOME:
            cursor = 0
        elif key == curses.KEY_END:
            cursor = len(files) - 1
        elif key in (ord("a"), ord("A")):
            selected = set(range(len(files)))
        elif key in (ord("c"), ord("C")):
            selected.clear()


def choose_files(files: List[RemoteFile]) -> Optional[List[RemoteFile]]:
    if files and sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("TERM") != "dumb":
        try:
            import curses
        except ImportError:
            pass
        else:
            return curses.wrapper(keyboard_file_selector, files, curses)
    return choose_files_by_number(files)


def choose_files_by_number(files: List[RemoteFile]) -> Optional[List[RemoteFile]]:
    if not files:
        print("No files found in the selected remote directory.")
        return None
    page_size = 25
    page = 0
    pages = (len(files) + page_size - 1) // page_size
    while True:
        print(f"\nEntries ({len(files)} total; newest first) — page {page+1}/{pages}")
        print(f"{'#':>5}  {'Recording date/time (ET)':23}  {'Size':>10}  Remote path")
        print("-" * 88)
        for idx in range(page * page_size, min((page + 1) * page_size, len(files))):
            entry = files[idx]
            date = display_timestamp(entry.modified)
            print(f"{idx+1:>5}  {date:23}  {size_label(entry.size):>10}  {entry.path}")
        choice = input("\nChoose 1,3-5 | a=ALL | n=next | p=previous | g=page | q=quit: ").strip().lower()
        if choice in ("q", "quit"):
            return None
        if choice in ("a", "all"):
            return files
        if choice == "n":
            page = min(pages - 1, page + 1)
        elif choice == "p":
            page = max(0, page - 1)
        elif choice == "g":
            raw_page = input(f"Page [1-{pages}]: ").strip()
            if raw_page.isdigit() and 1 <= int(raw_page) <= pages:
                page = int(raw_page) - 1
            else:
                print("Invalid page number.")
        else:
            try:
                return [files[index] for index in parse_selection(choice, len(files))]
            except ValueError as exc:
                print(exc)


def safe_relative_path(remote_path: str, remote_base: str) -> Path:
    relative = posixpath.relpath(remote_path, remote_base)
    components = PurePosixPath(relative).parts
    if not components or any(part in ("", ".", "..") for part in components):
        raise ValueError(f"Unsafe remote path: {remote_path}")
    return Path(*components)


def first_available_name(path: Path) -> Path:
    if not os.path.lexists(path):
        return path
    number = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({number}){path.suffix}")
        if not os.path.lexists(candidate):
            return candidate
        number += 1


def publish_file(temp_name, dest):
    """Publish without overwriting existing files, including on non-hardlink volumes."""
    while True:
        dest = first_available_name(dest)
        try:
            os.link(temp_name, dest)
            break
        except FileExistsError:
            continue
        except OSError as exc:
            if exc.errno not in (errno.ENOTSUP, errno.EOPNOTSUPP, errno.EPERM,
                                 errno.EXDEV, errno.ENOSYS):
                raise
        try:
            output = dest.open("xb")
        except FileExistsError:
            continue
        try:
            with output, open(temp_name, "rb") as source:
                shutil.copyfileobj(source, output, length=256 * 1024)
        except BaseException:
            dest.unlink()
            raise
        break
    return dest


def download_file(ftp: ImplicitFTPS, entry: RemoteFile, base: str, local_dir: Path) -> str:
    local_dir = local_dir.resolve(strict=True)
    relative = safe_relative_path(entry.path, base)
    parent = local_dir
    for component in relative.parts[:-1]:
        parent = parent / component
        if parent.is_symlink():
            raise ValueError(f"Refusing symlink download directory: {parent}")
        parent.mkdir(exist_ok=True)
    dest = parent / relative.name
    fd, temp_name = tempfile.mkstemp(prefix=".h2c-download-", dir=str(dest.parent))
    completed = 0
    try:
        with os.fdopen(fd, "wb") as output:
            def consume(block: bytes) -> None:
                nonlocal completed
                output.write(block)
                completed += len(block)
                if entry.size:
                    print(f"\r  {entry.name}: {completed * 100 / entry.size:5.1f}%", end="", flush=True)
                else:
                    print(f"\r  {entry.name}: {size_label(completed)}", end="", flush=True)

            ftp.retrbinary(f"RETR {entry.path}", consume, blocksize=256 * 1024)
        print()
        if entry.size is not None and completed != entry.size:
            raise IOError(f"Incomplete transfer: expected {entry.size}, got {completed} bytes")
        # Compare actual bytes before skipping; size alone cannot establish identity.
        if (not dest.is_symlink() and dest.is_file()
                and filecmp.cmp(temp_name, dest, shallow=False)):
            print(f"SKIP (identical content): {dest.name}")
            return "skipped"
        dest = publish_file(temp_name, dest)
        print(f"SAVED: {dest}")
        return "downloaded"
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def duration_label(seconds):
    if seconds is None:
        return "calculating..."
    seconds = max(0, math.ceil(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


class DownloadProgress:
    """Job-wide byte progress; cached bytes count as done, but not as transfer speed."""
    def __init__(self, total, cached=0, callback=None):
        self.callback = callback
        self.total = total
        self.cached = cached
        self.received = 0
        self.started = time.monotonic()
        self.last_update = None
        self.line_width = 0

    def snapshot(self):
        elapsed = time.monotonic() - self.started
        done = self.cached + self.received
        rate = self.received / elapsed if elapsed > 0 else 0
        remaining = max(0, self.total - done) if self.total is not None else None
        eta = (0 if remaining == 0 else remaining / rate if rate and remaining is not None else None)
        percent = (min(100, done * 100 / self.total) if self.total else
                   100 if self.total == 0 else None)
        return done, percent, rate, eta

    def update(self, block_size=0, force=False, finish=False):
        self.received += block_size
        now = time.monotonic()
        if not force and self.last_update is not None and now - self.last_update < 1:
            return
        self.last_update = now
        done, percent, rate, eta = self.snapshot()
        progress = f"{percent:5.1f}%" if percent is not None else "unknown total"
        if self.callback:
            self.callback({"type": "progress", "done": done, "total": self.total,
                           "percent": percent, "rate": rate, "eta": eta})
        total = size_label(self.total)
        line = (f"Download {progress} | {size_label(done)} / {total} | "
                f"{size_label(rate)}/s | remaining {duration_label(eta)}")
        if sys.stdout.isatty():
            print("\r" + line.ljust(self.line_width), end="\n" if finish else "", flush=True)
            self.line_width = len(line)
        else:
            print(line, flush=True)


def download_job(ftp, job, base, local_dir, ffmpeg, on_event=None):
    local_dir = local_dir.resolve(strict=True)
    identity = json.dumps([(c.path, c.size) for c in job.chunks]).encode("utf-8")
    key = hashlib.sha256(identity).hexdigest()[:16]
    cache = local_dir / ".h2c-parts"
    if cache.is_symlink():
        raise ValueError(f"Refusing symlink cache: {cache}")
    cache.mkdir(exist_ok=True)
    cache = cache / key
    if cache.is_symlink():
        raise ValueError(f"Refusing symlink cache: {cache}")
    cache.mkdir(exist_ok=True)
    cached = 0
    cached_indices = set()
    for index, entry in enumerate(job.chunks):
        target = cache / f"chunk-{index:06}.mp4"
        if target.is_symlink():
            raise ValueError(f"Refusing symlink chunk: {target}")
        if target.is_file() and entry.size is not None and target.stat().st_size == entry.size:
            cached += entry.size
            cached_indices.add(index)
    progress = DownloadProgress(job.size, cached, callback=on_event)
    progress.update(force=True)
    for index, entry in enumerate(job.chunks):
        target = cache / f"chunk-{index:06}.mp4"
        if target.is_symlink():
            raise ValueError(f"Refusing symlink chunk: {target}")
        if index in cached_indices:
            print(f"Cached [{index+1}/{len(job.chunks)}]: {entry.name}")
            continue
        if sys.stdout.isatty():
            print()
        print(f"Downloading chunk [{index+1}/{len(job.chunks)}]: {entry.name}", flush=True)
        fd, temp_name = tempfile.mkstemp(prefix=".partial-", dir=str(cache))
        completed = 0
        try:
            with os.fdopen(fd, "wb") as output:
                def consume(block):
                    nonlocal completed
                    output.write(block)
                    completed += len(block)
                    progress.update(len(block))
                ftp.retrbinary(f"RETR {entry.path}", consume, blocksize=256 * 1024)
            progress.update(force=True, finish=True)
            if entry.size is not None and completed != entry.size:
                raise OSError(f"Recording changed or transfer incomplete: {entry.name}; retry after recording stops")
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
    progress.update(force=True, finish=True)
    # Controlled relative names avoid quoting or remote-path injection into FFmpeg.
    listing = cache / "concat.txt"
    if listing.is_symlink():
        raise ValueError(f"Refusing symlink manifest: {listing}")
    listing.write_text("".join(f"file 'chunk-{i:06}.mp4'\n" for i in range(len(job.chunks))), encoding="utf-8")
    start = job.modified.astimezone(DISPLAY_TIMEZONE).strftime("%Y-%m-%d_%H-%M-%S_%Z") if job.modified else "unknown-time"
    dest = local_dir / f"job_{start}_{key[:8]}.mp4"
    try:
        # The temporary output stays on the destination volume for publication.
        with tempfile.TemporaryDirectory(prefix=".h2c-merge-", dir=str(local_dir)) as temp_dir:
            merged = Path(temp_dir) / "merged.mp4"
            if on_event:
                on_event({"type": "stage", "stage": "merging"})
            print(f"Download complete. Merging {len(job.chunks)} chunks into one MP4 ...", flush=True)
            result = subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                                     "-f", "concat", "-safe", "1", "-i", str(listing),
                                     "-map", "0:v:0", "-map", "0:a?", "-c", "copy",
                                     "-movflags", "+faststart", str(merged)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode or not merged.is_file() or not merged.stat().st_size:
                raise RuntimeError("FFmpeg merge failed: " + result.stderr[-2000:])
            if not dest.is_symlink() and dest.is_file() and filecmp.cmp(merged, dest, shallow=False):
                print(f"SKIP (identical merged video): {dest}")
                shutil.rmtree(cache)
                if on_event:
                    on_event({"type": "output", "path": str(dest)})
                return "skipped"
            dest = publish_file(merged, dest)
            print(f"SAVED: {dest}")
            shutil.rmtree(cache)
            if on_event:
                on_event({"type": "output", "path": str(dest)})
            return "downloaded"
    except (RuntimeError, OSError):
        print(f"Chunks retained for retry: {cache}", file=sys.stderr)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--configure", action="store_true", help="Save printer credentials and download folder interactively")
    parser.add_argument("--host", help="Printer IP; prompted if omitted")
    parser.add_argument("--port", type=int, default=None, help="FTPS port (default: 990)")
    parser.add_argument("--remote-dir", default=None, help="Remote folder to list recursively (default: /ipcam)")
    parser.add_argument("--active", action="store_true", help="Use active FTP data connections if passive mode fails")
    parser.add_argument("--session-gap", type=float, default=None,
                        help="Minutes between chunk starts that separate estimated jobs (default: 20)")
    parser.add_argument("--all", action="store_true", help="Download and merge all estimated jobs without selecting entries")
    parser.add_argument("--test-connection", action="store_true",
                        help="Test TLS, login, and remote file listing; download nothing")
    args = parser.parse_args()
    config = read_config()
    args.session_gap = args.session_gap if args.session_gap is not None else config.get("session_gap", 20)
    args.port = args.port if args.port is not None else config.get("port", 990)
    if args.session_gap <= 0 or not math.isfinite(args.session_gap):
        parser.error("--session-gap must be a finite positive number")
    global DISPLAY_TIMEZONE, RECORDING_TIMEZONE
    DISPLAY_TIMEZONE = ZoneInfo(config.get("timezone", "America/New_York"))
    RECORDING_TIMEZONE = timezone(timedelta(hours=config.get("filename_utc_offset", 8)))
    default_host = config.get("last_host", "")
    if args.configure and not args.host:
        host = (input(f"H2C IP address [{default_host}]: ").strip() or default_host).strip()
    else:
        host = (args.host or default_host or input("H2C IP address: ").strip()).strip()
    if not host:
        print("No printer address provided.", file=sys.stderr)
        return 2
    if args.configure:
        saved_code = config.get("access_code") if host == config.get("last_host") else None
        code = getpass.getpass("Printer LAN access code (blank keeps saved code): ").strip() or saved_code
        if not code:
            print("No access code provided.", file=sys.stderr)
            return 2
        folder = choose_local_folder(config)
        config.update(last_host=host, access_code=code, last_folder=str(folder))
        config["port"] = args.port
        save_config(config)
        print("Saved printer settings in an owner-only local file.")
        return 0
    access_code = config.get("access_code") if host == config.get("last_host") else None
    if host != config.get("last_host"):
        config.pop("access_code", None)
    if not isinstance(access_code, str) or not access_code:
        access_code = getpass.getpass("Printer LAN access code (not saved): ").strip()
    if not access_code:
        print("No access code provided.", file=sys.stderr)
        return 2
    remote_base = posixpath.normpath("/" + (args.remote_dir or config.get("remote_dir", "/ipcam")).lstrip("/"))
    print(f"\nConnecting to {host}:{args.port} via encrypted FTPS ...")
    ftp = ImplicitFTPS()
    try:
        ftp.connect(host, args.port)
        print("OK: TLS connection established and FTP greeting received.")
        ftp.login(user="bblp", passwd=access_code)
        print("OK: Printer accepted LAN access code.")
        ftp.prot_p()
        ftp.set_pasv(not args.active)
        print(f"Listing {remote_base} recursively ...")
        entries = remote_files(ftp, remote_base)
        if args.test_connection:
            print(f"OK: Encrypted data connection and file listing work ({len(entries)} files found).")
            print("Connection test passed. No files were downloaded.")
            config["last_host"] = host
            save_config(config)
            return 0
        jobs = group_recordings(entries, args.session_gap)
        print(f"{len(jobs)} estimated jobs from {sum(len(job.chunks) for job in jobs)} video chunks. "
              f"New session after a gap over {args.session_gap:g} minutes; exact print boundaries unavailable.")
        if not jobs:
            print("No video recordings found.")
            return 0
        ffmpeg = find_ffmpeg()
        selected = jobs if args.all else choose_files(jobs)
        if not selected:
            return 0
        print(f"\nSelected {len(selected)} estimated job(s); remote size: "
              f"{size_label(sum(item.size or 0 for item in selected))} (known sizes).")
        local_dir = choose_local_folder(config)
        config["last_host"] = host
        config["last_folder"] = str(local_dir)
        save_config(config)
        confirm = input(f"Download and merge {len(selected)} job(s) into '{local_dir}'? [Y/n]: ").strip().lower()
        if confirm not in ("", "y", "yes"):
            print("Canceled.")
            return 0
        results: Dict[str, int] = {"downloaded": 0, "skipped": 0, "failed": 0}
        for number, entry in enumerate(selected, 1):
            print(f"\n[{number}/{len(selected)}] {entry.path}")
            try:
                results[download_job(ftp, entry, remote_base, local_dir, ffmpeg)] += 1
            except FTP_EXCEPTIONS + (RuntimeError,) as exc:
                print(f"ERROR downloading {entry.name}: {exc}", file=sys.stderr)
                results["failed"] += 1
                # Some errors leave an FTP connection unsynchronized: stop safely.
                if isinstance(exc, (OSError, ftplib.error_proto, ftplib.error_reply)):
                    print("Stopping after a connection/transfer error; rerun to retry.")
                    break
        print(f"\nDone: {results['downloaded']} downloaded, "
              f"{results['skipped']} already present, {results['failed']} failed.")
        return 1 if results["failed"] else 0
    except FTP_EXCEPTIONS + (RuntimeError,) as exc:
        print(f"\nFTPS error: {exc}", file=sys.stderr)
        print("Check IP/access code, port 990, and that your Mac is on the same LAN.", file=sys.stderr)
        print("If listing fails, try --active; firmware may also limit USB/FTPS access.", file=sys.stderr)
        return 1
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nCanceled.")
        sys.exit(130)
