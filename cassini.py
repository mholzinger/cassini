#!/usr/bin/env python3
"""cassini - relay Sega Saturn save data home, onto a MiSTer.

Named for the probe that spent 13 years pulling data off Saturn and
relaying it back to Earth. cassini reads a real-hardware backup-RAM dump
(e.g. a Pseudo Saturn Kai / Save Game Copier .BUP), splits it into clean
per-game saves, and deploys each one to a MiSTer FPGA over SSH - named to
match your ROM library, with on-device backups.

Conversion/extraction is a means to an end here; plenty of mature tools
already convert Saturn save formats (savefileconverter.com, the Saturn
BRAM Parser, slinga's Save-Game-* projects). What cassini adds is the
automated per-game split + MiSTer deploy/sync pipeline.

Supported on-disk formats (auto-detected):
  * packed   - raw 32 KiB internal backup RAM (every byte is data).
               What Pseudo Saturn Kai / Save Game Copier write as a full
               .BUP memory dump (opens with "BackUpRam Format").
  * mister   - 64 KiB byte-spread image used by the MiSTer Saturn core:
               each data byte lives in the ODD byte of a 16-bit word, the
               EVEN byte is don't-care padding (the core writes 0xFF).

See README.md for the reverse-engineered block-format specification.
"""
import argparse
import os
import shlex
import struct
import subprocess
import sys

__version__ = "1.0.0"

BLOCK = 0x40                       # block size for internal memory
INTERNAL_SIZE = 0x8000             # 32 KiB
FORMAT_TAG = b"BackUpRam Format"   # 16 bytes, repeated through block 0
START_TAG = b"\x80\x00\x00\x00"    # first 4 bytes of a save's header block
CONT_TAG = b"\x00\x00\x00\x00"     # first 4 bytes of a continuation/data block

# Metadata region copied verbatim per save: absolute offsets 0x04..0x21.
META_OFF, META_END = 0x04, 0x22    # 30 bytes
# Field offsets *within* a header block:
#   0x00 start tag (4)   0x04 filename (11) 0x0F nul
#   0x10 comment (10)    0x1A language (1)  0x1B date (3)
#   0x1E datasize (4, big-endian)           0x22 content stream begins
HDR_CONTENT = 0x22                  # header block contributes [0x22:0x40] = 30 bytes
DAT_CONTENT = 0x04                  # data block contributes  [0x04:0x40] = 60 bytes
HDR_CONTENT_LEN = BLOCK - HDR_CONTENT   # 30
DAT_CONTENT_LEN = BLOCK - DAT_CONTENT   # 60


# --------------------------------------------------------------------------
# format detection / normalization
# --------------------------------------------------------------------------
def to_packed(raw: bytes) -> bytes:
    """Return the packed 32 KiB internal-RAM bytes from any supported image."""
    if len(raw) >= INTERNAL_SIZE and raw[:16] == FORMAT_TAG:
        return raw[:INTERNAL_SIZE]
    if len(raw) == 2 * INTERNAL_SIZE:
        odd = bytes(raw[1::2])
        if odd[:16] == FORMAT_TAG:
            return odd
        even = bytes(raw[0::2])
        if even[:16] == FORMAT_TAG:
            return even
        raise ValueError("64 KiB image but no 'BackUpRam Format' in either byte lane")
    raise ValueError("unrecognized backup image (size=%d, head=%r)" % (len(raw), raw[:16]))


def to_mister(packed: bytes, pad: int = 0xFF) -> bytes:
    """Spread packed bytes into the 64 KiB MiSTer layout (data in odd bytes)."""
    out = bytearray(len(packed) * 2)
    for i, b in enumerate(packed):
        out[2 * i] = pad
        out[2 * i + 1] = b
    return bytes(out)


def load_packed(path: str) -> bytes:
    with open(path, "rb") as f:
        return to_packed(f.read())


# --------------------------------------------------------------------------
# read path
# --------------------------------------------------------------------------
def _extract(packed: bytes, H: int) -> dict:
    """Extract the save whose header block is H, following its block chain."""
    B = BLOCK
    meta = packed[H * B + META_OFF: H * B + META_END]      # 30 bytes, copied verbatim
    name = meta[0:11].split(b"\0")[0]
    comment = meta[0x0C:0x16]                              # absolute 0x10..0x19
    datasize = struct.unpack(">I", meta[0x1A:0x1E])[0]     # absolute 0x1E..0x21
    lang = meta[0x16]                                      # absolute 0x1A

    # The content stream = [u16 block list][0x0000][data]. It is laid out as
    # header[0x22:0x40] followed by each listed block's [0x04:0x40].
    content = bytearray(packed[H * B + HDR_CONTENT: H * B + B])
    blocklist, appended, pos, term = [], 0, 0, None
    while True:
        while pos + 2 > len(content) and appended < len(blocklist):
            b = blocklist[appended]; appended += 1
            content += packed[b * B + DAT_CONTENT: b * B + B]
        if pos + 2 > len(content):
            break
        v = struct.unpack(">H", content[pos:pos + 2])[0]
        if v == 0:
            term = pos + 2
            break
        blocklist.append(v); pos += 2
    while term is not None and len(content) - term < datasize and appended < len(blocklist):
        b = blocklist[appended]; appended += 1
        content += packed[b * B + DAT_CONTENT: b * B + B]
    data = bytes(content[term:term + datasize]) if term is not None else b""

    return dict(name=name.decode("latin1"), meta=bytes(meta), comment=comment,
                lang=lang, datasize=datasize, data=data,
                header_block=H, blocks=[H] + blocklist)


def parse_saves(packed: bytes) -> list:
    saves = []
    for blk in range(1, len(packed) // BLOCK):
        if packed[blk * BLOCK: blk * BLOCK + 4] == START_TAG:
            name = packed[blk * BLOCK + 4: blk * BLOCK + 0x0F].split(b"\0")[0]
            if name and all(32 <= c < 127 for c in name):
                saves.append(_extract(packed, blk))
    return saves


# --------------------------------------------------------------------------
# write path
# --------------------------------------------------------------------------
def _blocks_needed(datasize: int) -> int:
    """Number of data blocks k so the list+terminator+data fit (30 + 60k bytes)."""
    k = 0
    while not (HDR_CONTENT_LEN + DAT_CONTENT_LEN * k >= 2 * k + 2 + datasize):
        k += 1
    return k


def build_image(saves: list, size: int = INTERNAL_SIZE) -> bytes:
    """Build a valid packed internal-RAM image containing exactly `saves`."""
    B = BLOCK
    nblk = size // B
    img = bytearray(size)                       # zeroed => free blocks
    for off in range(0, B, len(FORMAT_TAG)):    # block 0: format signature
        img[off:off + len(FORMAT_TAG)] = FORMAT_TAG

    next_free = 1
    for s in saves:
        data, datasize = s["data"], s["datasize"]
        if len(data) != datasize:
            raise ValueError("save %s: data length %d != datasize %d"
                             % (s["name"], len(data), datasize))
        k = _blocks_needed(datasize)
        if next_free + 1 + k > nblk:
            raise ValueError("out of space writing %s" % s["name"])
        H = next_free
        dblocks = list(range(H + 1, H + 1 + k))
        next_free = H + 1 + k

        content = bytearray()
        for d in dblocks:
            content += struct.pack(">H", d)
        content += b"\x00\x00"                   # list terminator
        content += data
        content += b"\x00" * (HDR_CONTENT_LEN + DAT_CONTENT_LEN * k - len(content))

        base = H * B
        img[base:base + 4] = START_TAG
        img[base + META_OFF:base + META_END] = s["meta"]
        img[base + HDR_CONTENT:base + B] = content[:HDR_CONTENT_LEN]
        for i, d in enumerate(dblocks):
            db = d * B
            img[db:db + 4] = CONT_TAG
            chunk = content[HDR_CONTENT_LEN + i * DAT_CONTENT_LEN:
                            HDR_CONTENT_LEN + (i + 1) * DAT_CONTENT_LEN]
            img[db + 4:db + 4 + len(chunk)] = chunk
    return bytes(img)


# --------------------------------------------------------------------------
# .BUP export (libslinga-style 64-byte "Vmem" header + raw data)
# --------------------------------------------------------------------------
def make_bup(save: dict) -> bytes:
    h = bytearray(64)
    h[0:4] = b"Vmem"
    name = save["name"].encode("latin1")[:11]
    h[0x10:0x10 + len(name)] = name
    com = bytes(save["comment"])[:10]
    h[0x1C:0x1C + len(com)] = com
    h[0x27] = save["lang"]
    struct.pack_into(">I", h, 0x2C, save["datasize"])
    struct.pack_into(">H", h, 0x30, BLOCK)
    return bytes(h) + save["data"]


# --------------------------------------------------------------------------
# self-test: extract -> rebuild -> re-extract must be byte-identical
# --------------------------------------------------------------------------
def selftest(packed: bytes) -> bool:
    orig = parse_saves(packed)
    rebuilt = build_image(orig)
    back = parse_saves(rebuilt)
    a = {s["name"]: s for s in orig}
    b = {s["name"]: s for s in back}
    ok = True
    if set(a) != set(b):
        ok = False
        print("  name set differs: only-orig=%s only-rebuilt=%s"
              % (set(a) - set(b), set(b) - set(a)))
    for name in sorted(set(a) & set(b)):
        if a[name]["data"] != b[name]["data"]:
            ok = False; print("  DATA mismatch: %s" % name)
        if a[name]["meta"] != b[name]["meta"]:
            ok = False; print("  META mismatch: %s" % name)
    print("self-test: %d saves, round-trip %s"
          % (len(orig), "CLEAN" if ok else "FAILED"))
    return ok


# --------------------------------------------------------------------------
# MiSTer deploy
# --------------------------------------------------------------------------
def _ssh(host, cmd):
    return subprocess.run(["ssh", "-o", "ConnectTimeout=15", host, cmd],
                          capture_output=True, text=True)


SAT_ROM_EXTS = ("cue", "chd", "m3u", "ccd", "iso")
_GAME_ROOTS = ("/media/fat/games", "/media/usb0", "/media/usb1", "/media/usb2",
               "/media/usb3", "/media/network", "/media/cifs")


def list_mister_games(host, saves_dir="/media/fat/saves/Saturn", roots=_GAME_ROOTS):
    """Candidate .sav base-names on a MiSTer: existing (flat) saves PLUS every
    Saturn ROM found by recursively scanning the game roots. A game's save is
    named after the ROM file it was launched from, so ROM base-names are the
    valid deploy targets even for games never saved in yet."""
    exts = " -o ".join("-iname '*.%s'" % e for e in SAT_ROM_EXTS)
    rootlist = " ".join(shlex.quote(r) for r in roots)
    cmd = (
        "ls -1 %s/*.sav 2>/dev/null; "
        "for r in %s; do find \"$r\" -maxdepth 3 -type d -iname saturn 2>/dev/null; done "
        "| while read d; do find \"$d\" -type f \\( %s \\) 2>/dev/null; done"
        % (shlex.quote(saves_dir), rootlist, exts)
    )
    known = {"sav"} | set(SAT_ROM_EXTS)
    names = set()
    for line in _ssh(host, cmd).stdout.splitlines():
        base = line.strip().rsplit("/", 1)[-1]
        if "." in base:
            stem, ext = base.rsplit(".", 1)
            if ext.lower() in known:
                base = stem
        if base and base != "boot" and "(Track " not in base:
            names.add(base)
    return sorted(names)


def deploy_mister(packed, save_ids, host, game, remote_dir, pad, date_tag, dry, full=True):
    chosen = [s for s in parse_saves(packed) if s["name"] in save_ids]
    found = {s["name"] for s in chosen}
    missing = [i for i in save_ids if i not in found]
    if missing:
        print("  SKIP '%s': saves not in dump: %s" % (game, ", ".join(missing)))
        return
    if full:
        # PROVEN: deploy the original BIOS-written memory (all saves), byte-spread.
        # Every game finds its own save; the real Saturn BIOS trusts these bytes.
        img = to_mister(packed, pad=pad)
        mode = "full-mem"
    else:
        # EXPERIMENTAL: synthesized per-game image. Round-trips through cassini's
        # own parser but the real Saturn BIOS has rejected this layout on hardware.
        img = to_mister(build_image(chosen), pad=pad)
        mode = "per-game(EXPERIMENTAL - BIOS may reject)"
    remote = "%s/%s.sav" % (remote_dir.rstrip("/"), game)
    print("%-55s <- %s  [%s]" % (game, ",".join(save_ids), mode))
    if dry:
        return
    q = shlex.quote(remote)
    bak = shlex.quote(remote + ".bak-" + date_tag)
    # non-clobbering backup: only snapshot the very first time we touch a file.
    _ssh(host, "if [ -f %s ] && [ ! -f %s ]; then cp -p %s %s; fi" % (q, bak, q, bak))
    push = "/tmp/_cassini_push.sav"
    tmp = "/tmp/_cassini_remote.sav"
    with open(push, "wb") as f:
        f.write(img)
    subprocess.run(["scp", "-q", "-o", "ConnectTimeout=15", push,
                    "%s:%s" % (host, tmp)], check=True)
    r = _ssh(host, "cp %s %s && chmod 755 %s && md5sum %s" % (shlex.quote(tmp), q, q, q))
    sys.stdout.write("    " + r.stdout)
    _ssh(host, "rm -f %s" % shlex.quote(tmp))


def parse_map(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            game, ids = line.split("\t", 1)
            rows.append((game.strip(), [x.strip() for x in ids.split(",") if x.strip()]))
    return rows


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def cmd_list(a):
    saves = parse_saves(load_packed(a.image))
    print("%-12s %7s  %-6s  %s" % ("ID", "BYTES", "LANG", "COMMENT"))
    for s in saves:
        casc = "".join(chr(c) if 32 <= c < 127 else "." for c in s["comment"])
        print("%-12s %7d  0x%02x    %s" % (s["name"], s["datasize"], s["lang"], casc))
    used = sum(len(s["blocks"]) for s in saves) + 1
    print("(%d saves, %d/%d blocks free)"
          % (len(saves), INTERNAL_SIZE // BLOCK - used, INTERNAL_SIZE // BLOCK))


def cmd_extract(a):
    saves = parse_saves(load_packed(a.image))
    if a.save:
        saves = [s for s in saves if s["name"] in a.save]
    os.makedirs(a.outdir, exist_ok=True)
    for s in saves:
        with open(os.path.join(a.outdir, s["name"] + ".raw"), "wb") as f:
            f.write(s["data"])
        with open(os.path.join(a.outdir, s["name"] + ".BUP"), "wb") as f:
            f.write(make_bup(s))
        print("extracted %s (%d bytes)" % (s["name"], s["datasize"]))


def cmd_convert(a):
    packed = load_packed(a.infile)
    out = to_mister(packed, pad=int(a.pad, 16)) if a.to == "mister" else packed
    with open(a.outfile, "wb") as f:
        f.write(out)
    print("wrote %s (%d bytes, %s)" % (a.outfile, len(out), a.to))


def cmd_build(a):
    saves = [s for s in parse_saves(load_packed(a.image)) if s["name"] in a.saves]
    img = build_image(saves)
    if a.format == "mister":
        img = to_mister(img, pad=int(a.pad, 16))
    with open(a.out, "wb") as f:
        f.write(img)
    print("wrote %s (%d bytes, %s): %s"
          % (a.out, len(img), a.format, ", ".join(s["name"] for s in saves)))


def cmd_selftest(a):
    raise SystemExit(0 if selftest(load_packed(a.image)) else 1)


MOUNT_NOTE = ("NOTE: the MiSTer Saturn core loads a .sav only at game MOUNT and "
              "does not hot-reload it.\n      To see a deployed save: exit the core "
              "to the main menu, then mount the game FRESH.")


def cmd_deploy(a):
    deploy_mister(load_packed(a.image), a.saves, a.host, a.game,
                  a.remote_dir, int(a.pad, 16), a.date, a.dry_run, full=not a.per_game)
    if not a.dry_run:
        print(MOUNT_NOTE)


def cmd_restore(a):
    packed = load_packed(a.image)
    rows = parse_map(a.map)
    head = "DRY-RUN: would restore" if a.dry_run else "Restoring"
    print("%s %d games to %s:%s\n" % (head, len(rows), a.host, a.remote_dir))
    for game, ids in rows:
        deploy_mister(packed, ids, a.host, game, a.remote_dir,
                      int(a.pad, 16), a.date, a.dry_run, full=not a.per_game)
    if a.dry_run:
        print("\ndone (dry run).")
    else:
        print("\ndone.  Backups saved as <game>.sav.bak-%s\n%s" % (a.date, MOUNT_NOTE))


def main(argv=None):
    p = argparse.ArgumentParser(prog="cassini", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version="cassini " + __version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("list", help="list saves in an image")
    s.add_argument("image"); s.set_defaults(fn=cmd_list)

    s = sub.add_parser("extract", help="extract saves to .raw + .BUP")
    s.add_argument("image"); s.add_argument("--save", action="append",
                                            help="internal ID (repeatable)")
    s.add_argument("--outdir", required=True); s.set_defaults(fn=cmd_extract)

    s = sub.add_parser("convert", help="convert a whole image between formats")
    s.add_argument("infile"); s.add_argument("outfile")
    s.add_argument("--to", choices=["packed", "mister"], required=True)
    s.add_argument("--pad", default="ff"); s.set_defaults(fn=cmd_convert)

    s = sub.add_parser("build", help="build a new image from selected saves")
    s.add_argument("image"); s.add_argument("--saves", required=True,
                                            type=lambda v: v.split(","))
    s.add_argument("-o", "--out", required=True)
    s.add_argument("--format", choices=["packed", "mister"], default="packed")
    s.add_argument("--pad", default="ff"); s.set_defaults(fn=cmd_build)

    s = sub.add_parser("selftest", help="round-trip validation on an image")
    s.add_argument("image"); s.set_defaults(fn=cmd_selftest)

    s = sub.add_parser("deploy-mister", help="build one clean per-game save and scp it")
    s.add_argument("image"); s.add_argument("--saves", required=True,
                                            type=lambda v: v.split(","))
    s.add_argument("--host", required=True)
    s.add_argument("--game", required=True, help="MiSTer ROM name (no .sav)")
    s.add_argument("--remote-dir", default="/media/fat/saves/Saturn")
    s.add_argument("--pad", default="ff"); s.add_argument("--date", default="manual")
    s.add_argument("--per-game", action="store_true",
                   help="EXPERIMENTAL: synthesize a per-game image (BIOS may reject)")
    s.add_argument("--dry-run", action="store_true"); s.set_defaults(fn=cmd_deploy)

    s = sub.add_parser("restore-mister",
                       help="deploy a whole game->saves map to a MiSTer (full-memory)")
    s.add_argument("image"); s.add_argument("--map", required=True,
                                            help="TSV: '<ROM name>\\t<ID1,ID2>'")
    s.add_argument("--host", required=True)
    s.add_argument("--remote-dir", default="/media/fat/saves/Saturn")
    s.add_argument("--pad", default="ff"); s.add_argument("--date", default="manual")
    s.add_argument("--per-game", action="store_true",
                   help="EXPERIMENTAL: synthesize per-game images (BIOS may reject)")
    s.add_argument("--dry-run", action="store_true"); s.set_defaults(fn=cmd_restore)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
