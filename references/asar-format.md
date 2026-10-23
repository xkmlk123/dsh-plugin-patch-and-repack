# The asar format, and why a naive edit crashes the app

`app.asar` on this machine:
`C:/Users/xkmlk/AppData/Local/Programs/DeepSeek Harness/resources/app.asar`

It is **not** a zip. It is one binary blob: a JSON header describing every file,
followed by the concatenated file bodies with no gaps.

## Header layout

Read the first 16 bytes as four little-endian `uint32`:

| Offset | Field | Meaning |
| --- | --- | --- |
| 0 | `u0` | always `4` (size of the next field) |
| 4 | `u1` | `u2 + 4` |
| 8 | `u2` | `align4(u3) + 4` |
| 12 | `u3` | **JSON byte length** |

- The JSON starts at byte **16** and runs for `u3` bytes.
- The file bodies start at **`data_start = 16 + align4(u3)`**.

Worked example from the real file: `u3 = 4779780` is already 4-aligned, so
`data_start = 4779796`.

**Trap:** if you take `u2` as the JSON length instead of `u3`, the parse fails with
`JSONDecodeError: Extra data: line 1 column 4779781`. That is the padding, not your bug.

## Header contents

The top level has exactly one key, `files`. Walk it recursively; a node is either a
directory (`{"files": {...}}`) or a file (`{"size", "offset", "integrity", "unpacked"}`).

- **`offset` is a string**, not an integer. Re-emitting it as an integer makes the
  header 2 bytes shorter per entry — 32149 bytes short across 16726 entries — and the
  JSON then no longer describes the body layout. Write `str(cursor)`.
- Bodies are packed contiguously: `gaps = 0`. The last entry's
  `offset + size` equals `len(raw) - data_start`. Assert both after any repack.
- Some entries are `"unpacked": true` (no offset, body sits outside the asar). Leave
  them alone.

## Integrity: the part that crashes the app

**Every file entry carries an `integrity` field**, and Electron verifies it:

```json
"integrity": {
  "algorithm": "SHA256",
  "hash": "<sha256 of the whole file body>",
  "blockSize": 4194304,
  "blocks": ["<sha256 of block 0>", "<sha256 of block 1>", "..."]
}
```

- `blocks` are the sha256 of `body[i*blockSize : (i+1)*blockSize]`; the last block is
  short. Verified empirically on an 8-block file.
- `hash` is the sha256 of the entire body.

If you change a body and leave `integrity` stale, the app does not warn and does not
degrade: **it fails to start**, showing Electron's default page
("To run a local app, execute the following on the command line: ..."). That is what
happened on the first attempt in the session behind this skill.

So: replace the body, then recompute **both** `hash` and `blocks` for that entry.
`scripts/asar_repack.py` does this and then proves it.

## Repacking safely

1. Copy the live asar to `app.asar.bak` **before** the first edit, and keep every
   intermediate good state (`app.asar.v1`, `v2`, …). You will want to go back.
2. Build the new blob in a scratch directory, never in place.
3. Verify before swapping (the script does this):
   - every non-target entry's bytes are byte-identical to the original;
   - every entry's `integrity.hash` equals the sha256 of its body;
   - every `blocks` entry matches its slice;
   - `gaps == 0` and the body region ends exactly at `len(raw) - data_start`.
4. Swap it from **outside** the app process (see `restart-dsh.md`), and have the swap
   script roll back automatically if the app does not come back.

## Restoring

`app.asar.bak` is the original. Overwrite the live file with it and restart. Note that
a plugin patch lives elsewhere (`node_modules/`), so restoring the asar does **not**
undo a plugin patch, and vice versa. Say which one you are reverting.

## When the asar is the wrong tier

Re-read rule zero in `SKILL.md`. In the session behind this skill, four asar edits
were made to fix one behaviour and all four failed — because the missing piece was a
value the host code never received, which a plugin could supply instead. The fifth
attempt, a plugin-only patch, worked on the first try. The asar is where the code is,
not automatically where the fix goes.
