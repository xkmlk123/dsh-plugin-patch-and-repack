#!/usr/bin/env python3
"""Repack a DSH app.asar with one or more replaced file bodies, recomputing integrity.

The rule that matters: Electron verifies every entry's `integrity.hash` and
`integrity.blocks`. Replace a body without recomputing them and the app does not
boot at all. This script recomputes both, then verifies the whole archive.

Usage:
    # dry run: show what would change, write nothing
    python asar_repack.py --targets targets.json --out app.asar.new

    # write, then verify
    python asar_repack.py --targets targets.json --out app.asar.new --apply
    python asar_repack.py --verify app.asar.bak app.asar.new --targets targets.json

`targets.json` maps an entry path to a local replacement file:
    {
      "/dsh/node_modules/@deepseek-ai/dsh-user-questions/lib/index.js":
          "patched/index.js"
    }

Relative replacement paths resolve against the targets.json directory.

Traps this script exists to avoid:
  * `meta["offset"]` is a STRING in the header. Writing it back as an int shortens
    the JSON by 2 bytes per entry and silently breaks the body layout.
  * The JSON length is the 4th uint32 of the prefix, not the 3rd.
  * Bodies are contiguous; `gaps` must stay 0.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import sys
from pathlib import Path

DEFAULT_ASAR = r"C:/Users/xkmlk/AppData/Local/Programs/DeepSeek Harness/resources/app.asar"
BLOCK_SIZE = 4_194_304


def read_header(raw: bytes) -> tuple[dict, int]:
    if len(raw) < 16:
        raise SystemExit("asar is shorter than its 16-byte prefix")
    u0, _u1, _u2, u3 = struct.unpack("<IIII", raw[:16])
    if u0 != 4:
        raise SystemExit(f"unexpected prefix (u0={u0}); this is not a DSH asar")
    json_len = u3
    data_start = 16 + ((json_len + 3) & ~3)
    header = json.loads(raw[16 : 16 + json_len].decode("utf-8"))
    return header, data_start


def integrity_of(body: bytes, block_size: int = BLOCK_SIZE) -> dict:
    blocks = [
        hashlib.sha256(body[i : i + block_size]).hexdigest()
        for i in range(0, len(body), block_size)
    ] or [hashlib.sha256(b"").hexdigest()]
    return {
        "algorithm": "SHA256",
        "hash": hashlib.sha256(body).hexdigest(),
        "blockSize": block_size,
        "blocks": blocks,
    }


def walk(header: dict):
    """Yield (entry_path, meta) for every packed file, in a stable order."""
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


def body_of(raw: bytes, data_start: int, meta: dict) -> bytes:
    off = int(meta["offset"])
    return raw[data_start + off : data_start + off + int(meta["size"])]


def load_targets(path: Path) -> dict[str, Path]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for entry, local in data.items():
        local_path = Path(local)
        if not local_path.is_absolute():
            local_path = (path.parent / local_path).resolve()
        if not local_path.is_file():
            raise SystemExit(f"replacement file missing: {local_path} (for {entry})")
        out["/" + entry.strip("/")] = local_path
    return out


def repack(src: Path, targets: dict[str, Path], out: Path, apply: bool) -> None:
    raw = src.read_bytes()
    header, data_start = read_header(raw)
    print(f"source    : {src}  bytes={len(raw)}  data_start={data_start}")

    known = {p: m for p, m in walk(header)}
    missing = [e for e in targets if e not in known]
    if missing:
        raise SystemExit(f"target lookup failed, entries not in asar: {missing}")

    # Collect bodies in header order so the new blob stays contiguous.
    bodies: list[tuple[str, bytes]] = []
    for path, meta in walk(header):
        if path in targets:
            bodies.append((path, targets[path].read_bytes()))
        else:
            bodies.append((path, body_of(raw, data_start, meta)))

    cursor = 0
    for path, body in bodies:
        meta = known[path]
        meta["size"] = len(body)
        meta["offset"] = str(cursor)  # STRING, not int — see module docstring
        meta["integrity"] = integrity_of(body, int(meta.get("integrity", {}).get("blockSize", BLOCK_SIZE)))
        cursor += len(body)

    blob = b"".join(body for _p, body in bodies)
    encoded = json.dumps(header, separators=(",", ":")).encode("utf-8")
    pad = (-len(encoded)) % 4
    prefix = struct.pack("<IIII", 4, len(encoded) + pad + 4, len(encoded) + pad + 4, len(encoded))
    new_raw = prefix + encoded + (b"\x00" * pad) + blob

    print(f"repacked  : {out}  bytes={len(new_raw)}  delta={len(new_raw) - len(raw):+d}")
    for entry in targets:
        print(f"  replaced {entry}  <- {targets[entry]}  ({known[entry]['size']} bytes)")
    if not apply:
        print("dry run: nothing written (pass --apply)")
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(new_raw)
    print(f"wrote {out}")


def verify(orig: Path, new: Path, targets: dict[str, Path]) -> int:
    """Prove the new archive differs only where intended, and is internally consistent."""
    raw_a, raw_b = orig.read_bytes(), new.read_bytes()
    head_a, start_a = read_header(raw_a)
    head_b, start_b = read_header(raw_b)
    a = dict(walk(head_a))
    b = dict(walk(head_b))
    problems: list[str] = []

    if set(a) != set(b):
        problems.append(f"entry set changed: +{len(set(b) - set(a))} -{len(set(a) - set(b))}")

    changed = 0
    for path, meta_b in b.items():
        body_b = body_of(raw_b, start_b, meta_b)
        integ = meta_b.get("integrity") or {}
        if integ.get("hash") != hashlib.sha256(body_b).hexdigest():
            problems.append(f"integrity.hash mismatch: {path}")
        blocks = integ.get("blocks") or []
        bs = int(integ.get("blockSize", BLOCK_SIZE))
        expect = [
            hashlib.sha256(body_b[i : i + bs]).hexdigest() for i in range(0, len(body_b), bs)
        ] or [hashlib.sha256(b"").hexdigest()]
        if blocks != expect:
            problems.append(f"integrity.blocks mismatch: {path}")
        if path in targets:
            if body_b != targets[path].read_bytes():
                problems.append(f"target body is not the patched file: {path}")
            changed += 1
        else:
            meta_a = a.get(path)
            if meta_a is None:
                continue
            if body_of(raw_a, start_a, meta_a) != body_b:
                problems.append(f"unintended change to non-target: {path}")

    # Contiguity: bodies must start at 0 and tile the tail exactly.
    cursor = 0
    for path, meta in walk(head_b):
        if int(meta["offset"]) != cursor:
            problems.append(f"gap before {path}: expected offset {cursor}, got {meta['offset']}")
            break
        cursor += int(meta["size"])
    if cursor != len(raw_b) - start_b:
        problems.append(f"body region {cursor} != tail {len(raw_b) - start_b}")

    print(f"orig bytes={len(raw_a)}  new bytes={len(raw_b)}  entries={len(b)}  replaced={changed}")
    if problems:
        print("VERIFY_FAILED")
        for p in problems[:20]:
            print("  -", p)
        return 1
    print("VERIFY_OK")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=DEFAULT_ASAR, help="archive to repack")
    ap.add_argument("--targets", required=True, help="JSON map of entry path -> replacement file")
    ap.add_argument("--out", help="where to write the new archive")
    ap.add_argument("--apply", action="store_true", help="actually write (default is dry run)")
    ap.add_argument("--verify", nargs=2, metavar=("ORIG", "NEW"), help="verify instead of repack")
    args = ap.parse_args()

    targets = load_targets(Path(args.targets))
    if args.verify:
        sys.exit(verify(Path(args.verify[0]), Path(args.verify[1]), targets))
    if not args.out:
        raise SystemExit("--out is required unless --verify is used")
    repack(Path(args.src), targets, Path(args.out), args.apply)


if __name__ == "__main__":
    main()
