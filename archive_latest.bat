@echo off
REM ============================================================
REM  Weekly Makeover Monday auto-archive.
REM  - Downloads any new weeks for the current year (resumable;
REM    already-downloaded weeks/files are skipped).
REM  - Regenerates the root README index.
REM  - Optionally commits and pushes to GitHub.
REM
REM  Reads your API token from the .env file in this folder.
REM  Run manually to test, or schedule it (see README "Automate").
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM ---- Set to 1 to auto-commit & push after each run, 0 to skip ----
set AUTO_PUSH=0

REM ---- Current year (yyyy) via PowerShell ----
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy"') do set YEAR=%%i

set LOG=archive_log.txt
echo. >> "%LOG%"
echo ======== Run %DATE% %TIME%  (year %YEAR%) ======== >> "%LOG%"

echo Archiving Makeover Monday %YEAR% from the master list (skipping existing)...
python download_makeovermonday.py --from-site --year %YEAR% >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [ERROR] Archiver failed - see %LOG%
  echo [ERROR] Archiver failed >> "%LOG%"
  exit /b 1
)

echo Regenerating index...
python generate_index.py >> "%LOG%" 2>&1

if "%AUTO_PUSH%"=="1" (
  echo Committing and pushing changes...
  git add -A >> "%LOG%" 2>&1
  git diff --cached --quiet
  if errorlevel 1 (
    git commit -m "Auto-archive update %DATE%" >> "%LOG%" 2>&1
    git push >> "%LOG%" 2>&1
    echo Pushed. >> "%LOG%"
  ) else (
    echo No changes to commit. >> "%LOG%"
  )
)

echo Done. Log: %LOG%
endlocal
