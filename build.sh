#!/usr/bin/env bash
# Build the standalone cassini app for the current OS with PyInstaller.
# Output lands in dist/ (cassini, cassini.exe, or cassini.app).
#
# IMPORTANT: needs a Python whose Tk is >= 8.6. Apple's *system* Tk 8.5
# (what /usr/bin/python3 links against) renders nested Tk frames blank, so
# the GUI comes up empty. On macOS install a modern Tk:  brew install python-tk@3.12
set -euo pipefail
cd "$(dirname "$0")"

pick_python() {
  for p in python3.12 python3.13 python3.11 python3.10 \
           /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12 python3; do
    command -v "$p" >/dev/null 2>&1 || continue
    tk=$("$p" -c 'import tkinter;print(tkinter.TkVersion)' 2>/dev/null) || continue
    awk "BEGIN{exit !($tk>=8.6)}" && { echo "$p"; return 0; }
  done
  return 1
}

PY=$(pick_python) || {
  echo "ERROR: no Python with Tk >= 8.6 found." >&2
  echo "       Apple's system Tk 8.5 renders the GUI blank." >&2
  echo "       Fix on macOS:  brew install python-tk@3.12" >&2
  exit 1
}
echo "using $PY (Tk $("$PY" -c 'import tkinter;print(tkinter.TkVersion)'))"

case "$(uname -s)" in
  Darwin|Linux) SEP=":" ;;
  *) SEP=";" ;;   # Windows (Git Bash / MSYS)
esac

VENV=".build-venv"
"$PY" -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip pyinstaller >/dev/null
rm -rf build dist
"$VENV/bin/python" -m PyInstaller --noconfirm --onefile --name cassini --windowed \
  --add-data "examples/mister-restore.tsv${SEP}examples" cassini_gui.py

echo "built -> dist/"
ls -la dist/
