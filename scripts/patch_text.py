#!/usr/bin/env python3
"""Apply an exact-text replacement to a JS file, asserting the match count.

The discipline this encodes: every patch must state how many times it expects to
match, and must fail loudly when the count differs. A silent zero-match "patch" is
the most expensive failure mode — the file looks edited, the behaviour does not
change, and you spend hours re-diagnosing.

Usage:
    python patch_text.py --file lib/host.js --count 1 \
        --old 'if (a) return b;' --new 'if (a) { return b; }'

    # or from a JSON list of edits, applied in order:
    python patch_text.py --file lib/host.js --edits edits.json

edits.json:
    [
      {"count": 1, "old": "...", "new": "...", "why": "carry the call id"},
      {"count": 2, "old": "...", "new": "..."}
    ]

Options:
    --check      only report match counts, change nothing
    --backup     copy <file>.bak first (refuses to overwrite an existing .bak)
    --dry-run    show a diff, write nothing
"""
from __future__ import annotations

import argparse
import difflib
import json
import shutil
import sys
from pathlib import Path


def load_edits(args) -> list[dict]:
    if args.edits:
        return json.loads(Path(args.edits).read_text(encoding="utf-8"))
    if not args.old:
        raise SystemExit("provide --old/--new or --edits")
    return [{"count": args.count, "old": args.old, "new": args.new}]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--old")
    ap.add_argument("--new")
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--edits")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--backup", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    path = Path(args.file)
    original = path.read_text(encoding="utf-8")
    text = original
    edits = load_edits(args)

    for i, edit in enumerate(edits, 1):
        old, new = edit["old"], edit["new"]
        want = int(edit.get("count", 1))
        found = text.count(old)
        why = edit.get("why")
        label = f"edit {i}" + (f" ({why})" if why else "")
        print(f"{label}: expected {want}, found {found}")
        if found != want:
            print(f"ABORT: {label} match count {found} != {want}; nothing written")
            sys.exit(1)
        if args.check:
            continue
        text = text.replace(old, new)

    if args.check:
        print("CHECK_OK (no changes written)")
        return

    if text == original:
        print("no change")
        return

    delta = len(text.encode("utf-8")) - len(original.encode("utf-8"))
    print(f"size: {len(original.encode('utf-8'))} -> {len(text.encode('utf-8'))} ({delta:+d} bytes)")

    if args.dry_run:
        diff = difflib.unified_diff(
            original.splitlines(), text.splitlines(), "before", "after", n=2, lineterm=""
        )
        print("\n".join(list(diff)[:200]))
        print("dry run: nothing written")
        return

    if args.backup:
        bak = path.with_suffix(path.suffix + ".bak")
        if bak.exists():
            print(f"refusing to overwrite existing backup {bak}")
            sys.exit(1)
        shutil.copy2(path, bak)
        print(f"backup: {bak}")

    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
