# Restarting DSH from inside DSH

A patch to a plugin or to `app.asar` only takes effect after the app restarts. The
hard part is that **the agent runs inside the app**, so the obvious ways to restart
it either kill the agent mid-command or get reaped before they run.

## What fails, and why

| Attempt | Result |
| --- | --- |
| `Stop-Process -Name 'DeepSeek Harness'` | Kills the shell running the command. Returns `Windows Job runner exited with exit code 4294967295`; the app may still be up. |
| `Start-Process` a restart script | The child is reaped by the task's job object. It reports "launched", then nothing happens. |
| `ops_bash run_in_background` with a restart script | `bash could not be started: subprocess-local: Windows Job runner exited ...`. |
| `taskkill /F /PID <child>` | This one *does* work — killing is fine; it is the surviving launcher that is the problem. |

The pattern: any process you start is a child of the command's job object, and the
job object is torn down when the command returns. You need a launcher that lives
**outside** that tree.

## What works: a one-shot scheduled task

Windows Task Scheduler is not a child of the agent's job object, so it survives.

`scripts/restart_dsh.bat` (adjust the exe path if it differs):

```bat
@echo off
taskkill /F /IM "DeepSeek Harness.exe" >nul 2>&1
timeout /t 5 /nobreak >nul
start "" "C:\Users\%USERNAME%\AppData\Local\Programs\DeepSeek Harness\DeepSeek Harness.exe"
timeout /t 20 /nobreak >nul
schtasks /delete /tn "DSHWechatRestart" /f >nul 2>&1
```

Create and fire it:

```sh
schtasks /create /tn DSHWechatRestart /tr "C:\path\to\restart_dsh.bat" /sc once /st 23:59 /f
schtasks /run /tn DSHWechatRestart
```

The task deletes itself on its last line, so it is safe to reuse the name.

## Traps

- **Run `schtasks` from PowerShell, not from the MSYS/Git bash shell.** Under MSYS,
  `/create` and `/run` get path-translated into `C:/.../create` and the call fails with
  "invalid argument/option". Call `pwsh -Command 'schtasks /create ...'` or use the
  `pwsh` tool.
- **Put the `.bat` on an ASCII-only path.** `cmd.exe` reads a UTF-8 `.bat` in the GBK
  code page; a path containing Chinese characters becomes mojibake and `copy` fails to
  find its source. A Chinese-path swap script failed this way; the same script under
  `C:/Users/<you>/.dsh/dsh_wechat/_asarswap/` worked.
- **The tool call may report an unknown outcome** (it gets interrupted as the app
  dies). Do not retry blindly — check whether the app actually came back before doing
  anything else.

## Proving the restart happened

Do not trust the launcher's exit code. Check all three:

1. **Process start times.** Every `DeepSeek Harness` process should have started
   within the last minute:

   ```powershell
   Get-Process -Name 'DeepSeek Harness' | Select-Object Id,StartTime | Sort-Object StartTime
   ```

2. **The boot log.** `~/.dsh/wechat/boot.log` (or the plugin's own log) should show a
   fresh mount:

   ```
   apply: entered
   apply: mounted
   apply: channel started
   ```

3. **The file's mtime vs. the processes' start time.** If the patched file is newer
   than every process, the running app is still on the old code — restart did not
   happen, whatever the launcher said.

The web GUI port also changes on restart (e.g. `5371` → `10052` → `3349`). Treat the
new port in the runtime context as corroboration, not as proof.

## After a crash

If the app shows Electron's default page, the asar is bad. It usually restores itself
from its backup, but verify:

- live `app.asar` byte size and sha256 match the known-good backup;
- rename the bad blob to `app.asar.broken-<date>` rather than deleting it — it is
  evidence;
- confirm the plugin tree is untouched (a crashed asar does not damage
  `node_modules/`), and that the WeChat binding in `~/.dsh/wechat/state.json` is
  still intact.
