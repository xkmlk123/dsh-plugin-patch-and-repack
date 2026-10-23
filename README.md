# dsh-plugin-patch-and-repack

A skill for fixing DeepSeek Harness behaviour by patching an installed plugin or the
Electron `app.asar` — safely, verifiably, and reversibly.

## When to use it

- A DSH feature misbehaves and the cause is inside an installed plugin's `lib/*.js`.
- The cause is inside `app.asar` (Electron bundle) and you need to edit it.
- An asar edit crashed the app and you need to know why.
- You need to restart DSH from inside DSH (the naive ways all fail).
- You need a patch that survives an app restart and can be rolled back.

## What it encodes

The expensive lessons from one real session: four asar edits that all failed, one app
crash caused by stale integrity hashes, and a fifth attempt (plugin-only) that worked.

1. **Tier order.** Plugin patch → plugin patch that supplies a missing value → asar
   patch. Move down only when the tier above is genuinely impossible, and name why.
2. **Locate, don't guess.** `asar.py` reports which *entry* a grep hit belongs to —
   the same function can exist twice, and patching one changes nothing.
3. **Prove the mechanism.** State it as a falsifiable sentence and check it against
   live artifacts, not against your memory of code you read earlier.
4. **Test in isolation, then on the device.** Both catch bugs the other misses.
5. **Prove it is loaded.** File size, marker strings, a fresh boot-log mount. A
   launcher's exit code proves nothing.
6. **Document and make it reversible.** A patch nobody can roll back is a liability.

## Contents

| Path | Purpose |
| --- | --- |
| `SKILL.md` | The procedure. |
| `references/asar-format.md` | Header layout, the four-uint32 prefix, integrity hashes, why a stale hash crashes the app. |
| `references/restart-dsh.md` | The scheduled-task method that works; the three that fail and why. |
| `references/case-question-card.md` | The full worked example, including all four failures. |
| `scripts/asar.py` | `ls` / `cat` / `grep` / `stat` against a DSH asar. |
| `scripts/asar_repack.py` | Repack with integrity recomputed, plus a full `--verify`. |
| `scripts/patch_text.py` | Exact-text patching that aborts on a wrong match count. |
| `scripts/restart_dsh.bat` | One-shot scheduled-task restart. |

## Requirements

- Python 3.8+ (standard library only — no dependencies).
- The DSH node binary for syntax checks:
  `~/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/node/bin/node.exe`
- Windows, for the restart path. The asar scripts are platform-independent.

## Quick start

```sh
# 1. find the code
python scripts/asar.py grep "theFunctionName" 100 5
python scripts/asar.py cat  /dsh/node_modules/<pkg>/lib/index.js  orig.js

# 2. patch it (fails loudly if the anchor is not unique)
python scripts/patch_text.py --file orig.js --count 1 --old '...' --new '...' --backup

# 3. check syntax with the DSH node
"$NODE" --input-type=module --check < orig.js

# 4. repack and verify
python scripts/asar_repack.py --targets targets.json --out app.asar.new --apply
python scripts/asar_repack.py --verify app.asar.bak app.asar.new --targets targets.json

# 5. swap and restart from outside the app
#    (see references/restart-dsh.md)
```

## Note on the paths

Defaults point at the machine the skill was written on
(`C:/Users/xkmlk/AppData/Local/Programs/DeepSeek Harness/resources/app.asar`). Pass
`--src` / the archive path explicitly on another machine; the layout is the same.
