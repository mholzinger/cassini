#!/usr/bin/env python3
"""Self-contained correctness tests for the cassini engine.

Fabricates synthetic Saturn saves (no private dumps needed), builds a
backup-RAM image, and proves extract->build->extract is byte-identical,
across save sizes that exercise every code path:
  * tiny    - fits entirely in the header block (no data blocks)
  * medium  - a few data blocks
  * large   - block list itself spans multiple blocks
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cassini as eng


def make_save(name, comment, data, lang=0x01, date=b"\x4e\xe1\x37"):
    """Build a synthetic save dict with a valid 30-byte metadata region."""
    meta = bytearray(30)
    meta[0:len(name)] = name.encode("latin1")[:11]
    c = comment.encode("latin1")[:10]
    meta[0x0C:0x0C + len(c)] = c                 # comment at absolute 0x10
    meta[0x16] = lang                            # language at absolute 0x1A
    meta[0x17:0x1A] = date                       # date at absolute 0x1B
    struct.pack_into(">I", meta, 0x1A, len(data))  # datasize at absolute 0x1E
    return dict(name=name, meta=bytes(meta), comment=bytes(meta[0x0C:0x16]),
                lang=lang, datasize=len(data), data=data)


def test_roundtrip():
    saves = [
        make_save("TINY_______", "tiny", b"\xAA" * 4),       # k=0, header only
        make_save("SMALL______", "small", bytes(range(256)) * 1),
        make_save("MEDIUM_____", "medium", bytes((i * 7) & 0xFF for i in range(1441))),
        make_save("LARGE______", "large", bytes((i * 13) & 0xFF for i in range(3040))),
        make_save("ODD________", "odd", b"\x5A" * 31),
    ]
    img = eng.build_image(saves)
    back = {s["name"]: s for s in eng.parse_saves(img)}
    assert set(back) == {s["name"] for s in saves}, "save set changed"
    for s in saves:
        b = back[s["name"]]
        assert b["data"] == s["data"], "DATA mismatch: %s" % s["name"]
        assert b["meta"] == s["meta"], "META mismatch: %s" % s["name"]
    # internal selftest helper agrees
    assert eng.selftest(img)
    print("round-trip: %d synthetic saves OK" % len(saves))


def test_format_conversion():
    saves = [make_save("CONV_______", "conv", bytes(range(200)))]
    packed = eng.build_image(saves)
    mister = eng.to_mister(packed, pad=0xFF)
    assert len(mister) == 2 * len(packed)
    assert mister[1::2] == packed, "odd byte lane must equal packed data"
    assert all(b == 0xFF for b in mister[0::2]), "even bytes must be pad"
    assert eng.to_packed(mister) == packed, "mister->packed must round-trip"
    print("format conversion: packed <-> mister OK")


if __name__ == "__main__":
    test_roundtrip()
    test_format_conversion()
    print("ALL TESTS PASSED")
