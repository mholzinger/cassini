<h1 align="center">🪐 cassini</h1>

<p align="center"><b>Rescue Sega Saturn save data from a real console — and relay it onto a MiSTer.</b></p>

<p align="center">
<img alt="CI" src="https://github.com/mholzinger/cassini/actions/workflows/ci.yml/badge.svg">
<img alt="Release" src="https://img.shields.io/github/v/release/mholzinger/cassini?sort=semver&display_name=tag">
<img alt="Platforms" src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-blue">
<img alt="Python" src="https://img.shields.io/badge/python-3.9%2B-blue">
<img alt="License" src="https://img.shields.io/badge/license-MIT-green">
</p>

Named for the probe that spent 13 years pulling data off Saturn and relaying it
home, **cassini** reads a real-hardware backup-RAM dump (a **Pseudo Saturn Kai**
/ **Save Game Copier** `.BUP`), lets you browse the saves inside it, and
**deploys them onto a MiSTer FPGA over SSH** — you just pick the game from a
searchable list of your installed library. GUI *or* CLI, no third-party
dependencies.

## Why it exists

Plenty of tools already *convert* Saturn save formats (see [Credits](#credits)).
cassini fills the missing last mile: **getting a rescued dump actually onto a
MiSTer, per game, safely** — automatic on-device backups, `md5` verification,
and a picker that reads your installed ROMs straight off the device so you never
have to hand-type a filename.

<details>
<summary><b>The origin story</b> (why this tool is named after a space probe)</summary>

A ~30 KB file called `SATBACKUP.BUP`, made with a Pseudo Saturn Kai cart and
forgotten on a MODE ODE's microSD for three years, held 28 saves from a real
Sega Saturn — including 27½ hours of *Grandia*. It wasn't on the NAS; it wasn't
anywhere obvious. Finding it (by content signature, not filename), decoding the
Saturn BIOS backup-RAM block format, converting it to the MiSTer's byte-spread
layout, and getting it to boot on real hardware became this tool. Like the
Cassini probe: pull the data off Saturn, relay it home.
</details>

## Download

Grab the latest build for your OS from **[Releases](../../releases/latest)**:

| OS | asset |
|----|-------|
| macOS (Apple Silicon) | `cassini-macos-arm64.tar.gz` |
| Linux (x64) | `cassini-linux-x64.tar.gz` |
| Windows (x64) | `cassini-windows-x64.zip` |
| Any (scripts) | `cassini-python.zip` |

The app is **dual-mode**: double-click for the GUI, or run it with arguments and
it behaves as the CLI. First run on macOS (unsigned build):

```bash
xattr -dr com.apple.quarantine cassini.app
```

## Using the GUI

1. **Open dump…** → your `.BUP`. The **Saves** tab lists everything inside it.
2. **Deploy to MiSTer** tab → enter your MiSTer's SSH host → **Load games ↺**.
3. **Search** to fuzzy-filter your library → pick the game → **Deploy** (or
   double-click it). No list? Type the exact ROM name in Search and Deploy uses
   that.
4. On the MiSTer: **exit the core to the main menu, then mount the game fresh**
   (see [gotchas](#️-mister-gotchas-learned-the-hard-way)).

cassini writes the full backup memory to that game's `.sav` (backing up the old
one first), so the game finds its save — and every other rescued save rides
along harmlessly.

## Using the CLI

```bash
cassini list      SATBACKUP.BUP                 # what's inside
cassini extract   SATBACKUP.BUP --save GRANDIA_001 --outdir out/
cassini convert   SATBACKUP.BUP mem.sav --to mister   # -> ready-to-flash MiSTer image
cassini deploy-mister SATBACKUP.BUP --host root@mister.local \
        --game "Grandia English (J) (Disc 1) T-Eng v0.8.6 TrekkiesUnite118"
cassini selftest  SATBACKUP.BUP                 # round-trip validation
```

| command | what it does |
|---|---|
| `list` | list the saves inside an image |
| `extract` | export saves as `.raw` + `.BUP` (libslinga `Vmem` header) |
| `convert` | convert a whole image between `packed` and `mister` formats |
| `build` | build a new image containing only selected saves |
| `selftest` | round-trip validation (extract → rebuild → re-extract) |
| `deploy-mister` | deploy the memory to one MiSTer game (full-memory by default) |
| `restore-mister` | deploy a whole `game → saves` map to a MiSTer (batch) |

Runtime is **pure standard library** — Python 3.9+, no third-party packages.
(PyInstaller is only needed to *build* the standalone apps.)

## ⚠️ MiSTer gotchas (learned the hard way)

1. **Deploy full-memory, not per-game.** By default cassini deploys the
   **original BIOS-written memory image** (byte-spread) to a game's `.sav`. Every
   game finds its own save and the real Saturn BIOS trusts the bytes. The
   `--per-game` flag synthesizes a minimal per-game image — it round-trips through
   cassini's own parser but **the real Saturn BIOS rejects that layout on
   hardware** (saves show up empty). Treat `--per-game` as experimental.

2. **The core only reads a `.sav` at game MOUNT.** It does *not* hot-reload a file
   you swap under a running game, and it can write its stale in-memory copy *back
   over* your file on exit. To apply a deployed save: **exit the Saturn core to
   the MiSTer main menu, then mount the game fresh.**

## Save formats

cassini auto-detects two on-disk layouts:

* **packed** — raw 32 KiB internal backup RAM; every byte is data. What Pseudo
  Saturn Kai / Save Game Copier write as a full `.BUP` dump. Opens with the ASCII
  signature `BackUpRam Format`.
* **mister** — the 64 KiB byte-spread image the MiSTer Saturn core uses: each
  data byte is the **odd** byte of a 16-bit word; the **even** byte is don't-care
  padding (the core writes `0xFF`). Same bytes as mednafen, 8→16-bit expanded.

<details>
<summary><b>Backup-RAM block format</b> (reverse-engineered)</summary>

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

* **Content stream** = `[uint16 block list][0x0000 terminator][save data]`, laid
  out as the header block's `[0x22:0x40]` followed by each listed block's
  `[0x04:0x40]` (each data block begins with a 4-byte `00 00 00 00` tag). The
  block list enumerates every data block and can itself span several blocks.

`selftest` and `tests/test_roundtrip.py` prove the writer is **self-consistent**
(extract → rebuild → re-extract is byte-identical). That means cassini agrees
with itself — it does **not** mean the real Saturn BIOS accepts a synthesized
layout (it doesn't; full-memory deploys sidestep the writer entirely).
</details>

## Build from source

Needs a Python whose **Tk is ≥ 8.6** — Apple's *system* Tk 8.5 renders the GUI
blank. On macOS: `brew install python-tk@3.12`.

```bash
./build.sh          # auto-picks a modern-Tk Python -> dist/cassini(.app/.exe)
```

Releases for all three OSes are cut automatically by GitHub Actions on a version
tag (`.github/workflows/release.yml`):

```bash
git tag v1.0.0 && git push origin v1.0.0
```

## Credits

cassini's format work stands on prior reverse-engineering. For pure format
conversion, these are mature and recommended:

* [slinga-homebrew/Save-Game-Copier](https://github.com/slinga-homebrew/Save-Game-Copier)
  & [libslinga](https://github.com/slinga-homebrew/libslinga) — the `.BUP` format
  and the on-Saturn copier.
* [savefileconverter.com](https://github.com/euan-forrester/save-file-converter)
  and the SegaXtreme online converter — MiSTer ↔ Pseudo Saturn Kai ↔ emulator ↔ Saroo.
* [hitomi2500/ss-save-parser](https://github.com/hitomi2500/ss-save-parser) and
  the Saturn BRAM Parser — single-save extract/insert.

## License

MIT © Mike Holzinger — see [LICENSE](LICENSE).
