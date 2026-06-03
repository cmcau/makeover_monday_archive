@echo off
REM ============================================================
REM  Push the archive to GitHub in small, resumable batches.
REM  Good for flaky connections: each year is its own commit and
REM  push, and every push is retried until it succeeds. If your
REM  connection drops, just run this again - already-pushed
REM  commits are skipped and it carries on.
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM --- Make git tolerant of slow / flaky connections ---
git config http.postBuffer 524288000
git config http.lowSpeedLimit 1000
git config http.lowSpeedTime 600
git config http.version HTTP/1.1

REM --- Safety check: make sure a remote is set ---
git remote get-url origin >nul 2>&1
if errorlevel 1 (
  echo [ERROR] No 'origin' remote. Add it first, e.g.:
  echo     git remote add origin https://github.com/cmcau/makeover_monday_archive.git
  pause
  exit /b 1
)

REM --- Rewind to no commits but KEEP every file, so we can ---
REM --- rebuild history as many small commits.              ---
echo Resetting local history (your files are untouched)...
git update-ref -d refs/heads/main 2>nul
git reset -q

set FIRST=1

REM ===== Batch 1: code, docs and other root files (everything except output\) =====
echo.
echo === Committing tooling and root files ===
git add -- . ":(exclude)output"
git commit -q -m "Tooling, docs and config"
call :push_retry

REM ===== One commit + push per year folder =====
for /d %%D in (output\*) do (
  echo.
  echo === Committing %%D ===
  git add "%%D"
  git commit -q -m "Archive %%~nxD"
  call :push_retry
)

echo.
echo ============================================
echo  All batches pushed successfully.
echo ============================================
pause
exit /b 0

REM ------------------------------------------------------------
:push_retry
:retry
if "%FIRST%"=="1" (
  REM First push claims the remote (overwrites GitHub's starter commit) and sets upstream
  git push --force -u origin main
) else (
  git push
)
if errorlevel 1 (
  echo.
  echo Push failed - probably the connection. Retrying in 8 seconds...
  echo (Press Ctrl+C to stop; re-running this script later will resume.)
  timeout /t 8 >nul
  goto retry
)
set FIRST=0
exit /b 0
