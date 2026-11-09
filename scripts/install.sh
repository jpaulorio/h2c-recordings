#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
case "$(uname -s)" in Darwin|Linux) ;; *) echo 'This installer supports macOS and Linux.'; exit 1;; esac
if ! command -v python3 >/dev/null; then
  echo 'Python 3.9+ is required. Install Python from https://www.python.org/downloads/ and rerun this script.'; exit 1
fi
python3 -c 'import sys; assert sys.version_info >= (3,9), "Python 3.9+ is required"'
if ! command -v bun >/dev/null; then
  if [[ -x "$HOME/.bun/bin/bun" ]]; then export PATH="$HOME/.bun/bin:$PATH";
  else
    echo 'Installing Bun from its official installer...'
    INSTALLER="$(mktemp)"
    trap 'rm -f "$INSTALLER"' EXIT
    curl --fail --silent --show-error --location https://bun.com/install -o "$INSTALLER"
    bash "$INSTALLER"
    export PATH="$HOME/.bun/bin:$PATH"
  fi
fi
bun install --frozen-lockfile
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
.venv/bin/python -c 'import imageio_ffmpeg; print("FFmpeg:", imageio_ffmpeg.get_ffmpeg_exe())'
bun run check
bun run build
mkdir -p "$HOME/.local/bin"
ROOT="$ROOT" .venv/bin/python - <<'PY'
from pathlib import Path
import os,shlex
root=Path(os.environ['ROOT'])
bun=__import__('shutil').which('bun')
launchers={
 'h2c':f'#!/bin/sh\nexec {shlex.quote(str(root/".venv/bin/python"))} {shlex.quote(str(root/"python/h2c_recordings.py"))} "$@"\n',
 'h2c-app':f'#!/bin/sh\ncd {shlex.quote(str(root))}\nexec {shlex.quote(bun)} run start "$@"\n',
}
for name,script in launchers.items():
 path=Path.home()/'.local/bin'/name
 path.write_text(script);path.chmod(0o755)
print('Installed h2c and h2c-app in ~/.local/bin.')
PY
printf '\nReady. Run: h2c-app\nThen open http://127.0.0.1:3210\nTerminal version: h2c\n'
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) printf '\nAdd this to your shell profile:\nexport PATH="$HOME/.local/bin:$PATH"\n';; esac
