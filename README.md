# cassini

**Relay Sega Saturn save data home — onto a MiSTer.**

Named for the probe that spent 13 years pulling data off Saturn and relaying
it back to Earth. `cassini` reads a real-hardware backup-RAM dump (e.g. a
**Pseudo Saturn Kai** / **Save Game Copier** `.BUP`), splits it into clean
per-game saves, and deploys each one to a **MiSTer FPGA** over SSH — named to
match your ROM library, with on-device backups.

There are already excellent tools for *converting* Saturn save formats (see
[Credits](#credits)). What cassini adds is the missing last mile: an automated
**per-game split + MiSTer deploy/sync** pipeline, as both a CLI and a simple
cross-platform GUI.

---

## Quick start

```bash
# GUI (no arguments)
python3 cassini_gui.py

# CLI
python3 cassini.py list  SATBACKUP.BUP
python3 cassini.py selftest SATBACKUP.BUP
python3 cassini.py extract SATBACKUP.BUP --save GRANDIA_001 --outdir out/
python3 cassini.py restore-mister SATBACKUP.BUP \
    --map examples/mister-restore.tsv --host root@mister.local --dry-run
```

Runtime is **pure standard library** — Python 3.8+, no third-party packages.
(PyInstaller is only needed to *build* the standalone binaries.)

## Commands

| command | what it does |
|---|---|
| `list` | list the saves inside an image |
| `extract` | export saves as `.raw` + `.BUP` (libslinga `Vmem` header) |
| `convert` | convert a whole image between `packed` and `mister` formats |
| `build` | build a new image containing only selected saves |
| `selftest` | round-trip validation (extract → rebuild → re-extract) |
| `deploy-mister` | build one clean per-game save and scp it to a MiSTer |
| `restore-mister` | deploy a whole `game → saves` map to a MiSTer |

### The MiSTer restore map

A TSV of `MiSTer ROM name` (without `.sav`) → comma-separated internal save IDs:

```
Resident Evil (USA)	BIOUDATA_SS,BIOUDATA_01
Quake (USA)	LOBOQUAKE__
Grandia English (J) (Disc 1) T-Eng v0.8.6 TrekkiesUnite118	GRANDIA_001,GRANDIA_002
```

`restore-mister` builds a clean per-game image for each row, **backs up** the
existing `.sav` on the device (once, as `<name>.sav.bak-<date>`), pushes the new
file, and verifies it with `md5sum`. See `examples/mister-restore.tsv`.

## Save formats

cassini auto-detects two on-disk layouts:

* **packed** — raw 32 KiB internal backup RAM; every byte is data. This is what
  Pseudo Saturn Kai / Save Game Copier write as a full `.BUP` memory dump. Opens
  with the ASCII signature `BackUpRam Format`.
* **mister** — the 64 KiB byte-spread image the MiSTer Saturn core uses: each
  data byte is the **odd** byte of a 16-bit word; the **even** byte is
  don't-care padding (the core writes `0xFF`). Same bytes as mednafen, just
  8→16-bit expanded.

### Backup-RAM block format (reverse-engineered)

Internal memory is 512 blocks of `0x40` bytes.

* **Block 0** — the signature `BackUpRam Format` repeated four times.
* **Header block** (one per save), first byte `0x80`:
  | offset | size | field |
  |--------|------|-------|
  | `0x00` | 4 | start tag `80 00 00 00` |
  | `0x04` | 11 | filename (internal save ID) |
  | `0x10` | 10 | comment |
  | `0x1A` | 1 | language |
  | `0x1B` | 3 | date |
  | `0x1E` | 4 | data size (big-endian) |
  | `0x22` | 30 | start of the *content stream* |
* **Content stream** = `[uint16 block list][0x0000 terminator][save data]`,
  laid out as the header block's `[0x22:0x40]` followed by each listed block's
  `[0x04:0x40]` (each data block begins with a 4-byte `00 00 00 00` tag). The
  block list enumerates every data block and can itself span several blocks.

The writer is validated by `selftest` and `tests/test_roundtrip.py`: extracting
every save, rebuilding a fresh image, and re-extracting must be byte-identical.

## Build the standalone apps

One-file binaries are produced by [PyInstaller](https://pyinstaller.org). The
bundled binary is **dual-mode** (like the intv2convert binary): launched with no
arguments it opens the GUI; with arguments it behaves as the CLI.

```bash
pip install pyinstaller
./build.sh           # -> dist/cassini  (or cassini.exe / cassini.app)
```

Pushing a version tag builds and publishes all three OS downloads via GitHub
Actions (`.github/workflows/release.yml`):

```bash
git tag v1.0.0 && git push origin v1.0.0
# -> cassini-windows-x64.zip, cassini-linux-x64.tar.gz,
#    cassini-macos-arm64.tar.gz, cassini-python.zip
```

**First run on macOS** (unsigned build): clear the Gatekeeper quarantine flag —

```bash
xattr -dr com.apple.quarantine cassini.app
```

## Credits

cassini's format work stands on prior reverse-engineering. For pure format
conversion these are mature and recommended:

* [slinga-homebrew/Save-Game-Copier](https://github.com/slinga-homebrew/Save-Game-Copier)
  and [libslinga](https://github.com/slinga-homebrew/libslinga) — the `.BUP`
  format and the on-Saturn copier.
* [savefileconverter.com](https://github.com/euan-forrester/save-file-converter)
  and the SegaXtreme online converter — MiSTer ↔ Pseudo Saturn Kai ↔ emulator ↔ Saroo.
* [hitomi2500/ss-save-parser](https://github.com/hitomi2500/ss-save-parser) and
  the Saturn BRAM Parser — single-save extract/insert.
