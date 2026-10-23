@echo off
REM Restart DeepSeek Harness from outside its own process tree.
REM Run this via Task Scheduler, never directly from a DSH tool call:
REM   schtasks /create /tn DSHWechatRestart /tr "C:\path\to\restart_dsh.bat" /sc once /st 23:59 /f
REM   schtasks /run /tn DSHWechatRestart
REM
REM The last line deletes the task, so the name is reusable.

taskkill /F /IM "DeepSeek Harness.exe" >nul 2>&1
timeout /t 5 /nobreak >nul
start "" "%LOCALAPPDATA%\Programs\DeepSeek Harness\DeepSeek Harness.exe"
timeout /t 20 /nobreak >nul
schtasks /delete /tn "DSHWechatRestart" /f >nul 2>&1
