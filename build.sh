#!/usr/bin/env bash
# Build the standalone cassini app for the current OS with PyInstaller.
# Output lands in dist/ (cassini, cassini.exe, or cassini.app).
set -euo pipefail
cd "$(dirname "$0")"

python3 -m pip install --upgrade pyinstaller >/dev/null

case "$(uname -s)" in
  Darwin) SEP=":" ;;
  Linux)  SEP=":" ;;
  *)      SEP=";" ;;   # Windows (Git Bash / MSYS)
esac

pyinstaller --onefile --name cassini --windowed \
  --add-data "examples/mister-restore.tsv${SEP}examples" \
  cassini_gui.py

echo "built -> dist/"
ls -la dist/
