#!/usr/bin/env python3
"""Read a DSH app.asar: list entries, extract a file, or search decoded text.

The asar is not a zip. It is a JSON header followed by contiguous file bodies.
See references/asar-format.md for the layout and the integrity rules.

Usage:
    python asar.py ls   <asar> [substring]
    python asar.py cat  <asar> <entry-path> [out-file]
    python asar.py grep <asar> <regex> [context-chars] [limit]
    python asar.py stat <asar> <entry-path>
"""
from __future__ import annotations

import json
import re
import struct
import sys
from pathlib import Path

DEFAULT_ASAR = r"C:/Users/xkmlk/AppData/Local/Programs/DeepSeek Harness/resources/app.asar"


def read_header(raw: bytes) -> tuple[dict, int]:
    """Return (header, data_start). The JSON length is the 4th uint32, not the 3rd."""
    if len(raw) < 16:
        raise SystemExit("asar is shorter than its 16-byte prefix")
    u0, u1, u2, u3 = struct.unpack("<IIII", raw[:16])
    if u0 != 4:
        raise SystemExit(f"unexpected prefix (u0={u0}); this is not a DSH asar")
    json_len = u3
    data_start = 16 + ((json_len + 3) & ~3)
    if 16 + json_len > len(raw):
        raise SystemExit(f"declared JSON length {json_len} exceeds file size {len(raw)}")
    header = json.loads(raw[16 : 16 + json_len].decode("utf-8"))
    return header, data_start


def walk(header: dict):
    """Yield (entry_path, meta) for every packed file."""
    stack = [("", header.get("files", {}))]
    while stack:
        prefix, node = stack.pop()
        for name, meta in node.items():
            path = f"{prefix}/{name}" if prefix else f"/{name}"
            if "files" in meta:
                stack.append((path, meta["files"]))
            elif meta.get("unpacked"):
                continue
            else:
                yield path, meta


def lookup(header: dict, want: str) -> dict:
    want = "/" + want.strip("/")
    for path, meta in walk(header):
        if path == want:
            return meta
    raise SystemExit(f"no such entry: {want}")


def body_of(raw: bytes, data_start: int, meta: dict) -> bytes:
    off = int(meta["offset"])
    return raw[data_start + off : data_start + off + int(meta["size"])]


def cmd_ls(args: list[str]) -> None:
    asar = args[0] if args else DEFAULT_ASAR
    needle = args[1] if len(args) > 1 else ""
    raw = Path(asar).read_bytes()
    header, data_start = read_header(raw)
    rows = [(p, m) for p, m in walk(header) if needle in p]
    print(f"asar={asar} bytes={len(raw)} data_start={data_start} matches={len(rows)}")
    for path, meta in sorted(rows):
        print(f"  {int(meta['size']):>9}  off={str(meta['offset']):>10}  {path}")


def cmd_stat(args: list[str]) -> None:
    asar, want = (args[0], args[1]) if len(args) >= 2 else (DEFAULT_ASAR, args[0])
    raw = Path(asar).read_bytes()
    header, data_start = read_header(raw)
    meta = lookup(header, want)
    body = body_of(raw, data_start, meta)
    import hashlib

    print(f"entry   : {want}")
    print(f"size    : {len(body)}")
    print(f"offset  : {meta['offset']}  absolute={data_start + int(meta['offset'])}")
    print(f"sha256  : {hashlib.sha256(body).hexdigest()}")
    integ = meta.get("integrity") or {}
    if integ:
        print(f"declared: {integ.get('hash')}")
        print(f"match   : {hashlib.sha256(body).hexdigest() == integ.get('hash')}")


def cmd_cat(args: list[str]) -> None:
    asar, want = (args[0], args[1]) if len(args) >= 2 else (DEFAULT_ASAR, args[0])
    out = args[2] if len(args) > 2 else None
    raw = Path(asar).read_bytes()
    header, data_start = read_header(raw)
    body = body_of(raw, data_start, lookup(header, want))
    if out:
        Path(out).write_bytes(body)
        print(f"wrote {len(body)} bytes -> {out}")
    else:
        sys.stdout.write(body.decode("utf-8", "replace"))


def split_asar_arg(args: list[str]) -> tuple[str, list[str]]:
    """Accept the asar path optionally.

    `grep <regex>` and `grep <asar> <regex>` are both valid: a first argument that
    ends in `.asar` is taken as the archive, anything else as the pattern.
    """
    if args and args[0].lower().endswith(".asar"):
        return args[0], args[1:]
    return DEFAULT_ASAR, args


def cmd_grep(args: list[str]) -> None:
    """Search decoded bodies, reporting the entry each hit belongs to.

    Searching the whole blob as one string is the trap: hits then get attributed to
    whichever file happens to contain that byte offset, which is often a neighbour.
    """
    asar, args = split_asar_arg(args)
    pattern = args[0] if args else "."
    args = args[1:]
    ctx = int(args[2]) if len(args) > 2 else 120
    limit = int(args[3]) if len(args) > 3 else 20
    rx = re.compile(pattern)
    raw = Path(asar).read_bytes()
    header, data_start = read_header(raw)
    hits = 0
    for path, meta in sorted(walk(header)):
        if int(meta["size"]) > 8_000_000:
            continue
        try:
            text = body_of(raw, data_start, meta).decode("utf-8")
        except UnicodeDecodeError:
            continue
        for m in rx.finditer(text):
            hits += 1
            lo, hi = max(0, m.start() - ctx), min(len(text), m.end() + ctx)
            snippet = text[lo:hi].replace("\n", " ")
            print(f"[{path}] @{m.start()}")
            print(f"   ...{snippet}...")
            if hits >= limit:
                print(f"(stopped at {limit} hits)")
                return


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "ls":
        cmd_ls(args)
    elif cmd == "cat":
        cmd_cat(args)
    elif cmd == "grep":
        cmd_grep(args)
    elif cmd == "stat":
        cmd_stat(args)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
