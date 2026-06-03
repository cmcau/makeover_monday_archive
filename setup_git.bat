@echo off
REM ============================================================
REM  One-time git setup for the Makeover Monday archive.
REM  Usage:
REM      setup_git.bat https://github.com/<you>/<repo>.git
REM  (You can also run it with no argument and add the remote later.)
REM ============================================================
setlocal
cd /d "%~dp0"

echo Initializing git repository in:
echo   %cd%
echo.

git init
if errorlevel 1 (
  echo [ERROR] git not found. Install Git for Windows from https://git-scm.com/download/win
  pause
  exit /b 1
)

REM Identity (safe to re-run; only sets it for this repo)
git config user.name  "Chris"
git config user.email "chris@visualisedata.com.au"

REM Stage everything (.gitignore keeps .env and __pycache__ out)
git add -A

echo.
echo === Files that will be committed (confirming .env is NOT listed) ===
git status --short
echo.

REM Hard stop if .env somehow got staged
git diff --cached --name-only | findstr /x ".env" >nul
if not errorlevel 1 (
  echo [ABORT] .env is staged! Removing it from the commit.
  git reset .env
)

git commit -m "Archive Makeover Monday datasets + tooling"

if not "%~1"=="" (
  echo.
  echo Adding remote: %~1
  git remote remove origin 2>nul
  git remote add origin %~1
  git branch -M main
  echo.
  echo Pushing to GitHub...
  git push -u origin main
  if errorlevel 1 (
    echo.
    echo [!] Push was rejected. The remote already has a commit ^(GitHub adds one
    echo     if you created the repo with a README, license, or .gitignore^), so the
    echo     histories diverged. Pick one:
    echo.
    echo       Your local files are the real archive - overwrite the starter commit:
    echo           git push --force-with-lease origin main
    echo.
    echo       Or keep what's on GitHub and merge it under your commits first:
    echo           git pull --rebase origin main
    echo           git push -u origin main
  )
) else (
  echo.
  echo No remote URL supplied. When your GitHub repo exists, run:
  echo     git remote add origin https://github.com/^<you^>/^<repo^>.git
  echo     git branch -M main
  echo     git push -u origin main
)

echo.
echo Done.
pause
endlocal
