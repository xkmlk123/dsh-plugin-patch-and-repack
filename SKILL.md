---
name: dsh-plugin-patch-and-repack
description: Diagnose and fix a DeepSeek Harness (DSH) behaviour by patching a plugin or the app.asar bundle, then repack, swap, restart, and verify it. Use when a DSH feature misbehaves and the cause is inside an installed plugin's lib/*.js or inside the Electron asar, when a plugin patch must survive an app restart, when an asar edit crashed the app, or when you need a safe backup/rollback path for either. Covers locating code inside the asar, computing asar integrity hashes, restarting DSH from outside its own process tree, and proving the patch works before claiming success.
---

# Patching DSH: plugins and the asar

DSH ships as an Electron app whose own code lives in `app.asar`, and whose features
come from plugins under `~/.dsh/profiles/<profile>/node_modules/`. Both are editable
on disk. Both have traps that make a "successful" edit silently do nothing, or crash
the app. This skill is the order of operations that avoids those traps.

Read `references/asar-format.md` before touching `app.asar`. Read
`references/restart-dsh.md` before any restart. Both are short and both exist
because guessing at them costs an app crash or a silent no-op.

## Rule zero: prefer the plugin over the asar

Try in this order, and only move down when the one above is genuinely impossible:

1. **A plugin patch.** Plugins are plain ESM under
   `~/.dsh/profiles/<profile>/node_modules/<plugin>/lib/*.js`, they are not covered
   by asar integrity, and a bad edit is a one-file rollback.
2. **A plugin patch that supplies what the asar-side code is missing.** The most
   common real fix is not "change the host code" but "the host code never received
   a value it needs". Look for whether a listener can add it before forwarding.
3. **An asar patch.** Last resort. Every asar edit must recompute integrity hashes
   or the app will refuse to boot, and one wrong byte produced a full crash in the
   session that produced this skill.

Write down which tier you are in, and why the tier above failed, before you edit
anything. If you cannot name the failed attempt, you have not diagnosed yet.

## Step 1: locate the code

For a plugin, the file is already on disk and readable — start there.

For the asar, you cannot grep it normally: it is a single binary blob with a JSON
header. Use `scripts/asar.py`:

```sh
python scripts/asar.py grep "functionName" 100 5     # which ENTRY each hit is in
python scripts/asar.py ls   the-package              # list matching entries
python scripts/asar.py cat  /dsh/node_modules/<pkg>/lib/index.js orig.js
python scripts/asar.py stat /dsh/node_modules/<pkg>/lib/index.js
```

`grep` reports the entry path per hit on purpose: a raw byte-offset search over the
whole blob attributes a hit to whichever file happens to contain that offset, and
that mistake cost a full round of wrong conclusions. `stat` prints the body sha256
next to the declared `integrity.hash`, which is how you check a patch is live.

## Step 2: prove the cause, not the symptom

Do not patch on a hunch. Establish the exact mechanism and write it down as a
falsifiable sentence: "X is keyed by A, but the producer emits B, so the consumer
can never match them." Then check that sentence against the real runtime state —
the live file, the live log, the live extracted code — not against your memory of
the code you read earlier.

In particular, verify that the code you are about to edit is the code that actually
runs. A function can exist twice in the asar (a bundle copy and a types copy), and
patching only one of them changes nothing. This exact duplication silently defeated
a fix once.

## Step 3: patch, then check syntax before anything else

1. Copy the original file aside first: `<name>.bak` next to it, plus a copy inside
   the session folder.
2. Apply the smallest edit that implements the mechanism from step 2.
3. Check syntax with the runtime's own node, not the system one:

   ```sh
   "$NODE" --check path/to/file.js
   # for ESM-only files:
   "$NODE" --input-type=module --check < path/to/file.js
   ```

   On this machine `$NODE` is
   `~/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/node/bin/node.exe`.
4. Record the byte size and every marker string you added. You will need them to
   prove the patch is live later.

## Step 4: test the mechanism in isolation before restarting

Write a harness that imports the real patched module and drives it with fake
collaborators. This is the step that separates "I edited a file" from "the behaviour
changed".

- Assert the *new* behaviour and the *old* behaviour (regression).
- Assert the edge cases: empty input, no queue entry, a request that already
  carries the value, two calls in a row, a call from an unrelated session.
- If the patch is timing-related, drive the clock instead of sleeping.

Do not skip to a real-device test because the unit tests pass. Do not skip the unit
tests because a real-device test is available. Both caught bugs the other missed.

## Step 5: get it loaded

A plugin edit does nothing until the app restarts. `references/restart-dsh.md` has
the only method that works from inside DSH (a one-shot scheduled task), plus the
two methods that fail and why.

After restart, **prove** the new code is live before telling the user it is:

- the file size and marker strings are present in the live path;
- the boot log shows a fresh `apply: entered / mounted / channel started`;
- for an asar change, the entry's `integrity.hash` matches the sha256 of its body.

## Step 6: verify on the real device, then document

Have the user exercise the actual path and report what they see. "The frame
disappeared" is evidence; "the tests pass" is not.

Then update the session's `说明.md`: what was wrong, what changed, the adjustable
parameters, the verification table with real timestamps, the rollback command, and
the known limits. A patch nobody can roll back is a liability.

## Reporting to the user

Say what was broken, what you changed, what you verified, and how to undo it. If a
previous attempt failed, say that too and say why — the user has been watching the
attempts and needs the failure explained to trust the fix.

Never claim a fix works on the strength of a syntax check, a diff, or a passing unit
test alone.
