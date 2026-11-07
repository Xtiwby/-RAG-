@echo off
setlocal
cd /d "%~dp0frontend"
set "PATH=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin;%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback;%PATH%"
where node >nul 2>&1
if errorlevel 1 (
  echo Node.js was not found. Install Node.js LTS, then run this script again.
  pause
  exit /b 1
)
if not exist "node_modules\vite\bin\vite.js" goto install
if not exist "node_modules\pdfjs-dist\build\pdf.mjs" goto install
if not exist "node_modules\marked\lib\marked.esm.js" goto install
goto build
:install
  call pnpm install --node-linker=hoisted --package-import-method=copy
  if errorlevel 1 (
    echo Dependency installation failed. Check Node.js, pnpm and network access.
    pause
    exit /b 1
  )
:build
node node_modules\vite\bin\vite.js build --configLoader runner
if errorlevel 1 (
  pause
  exit /b 1
)
echo Frontend: http://127.0.0.1:5173
echo Keep this window open. Press Ctrl+C to stop.
node node_modules\vite\bin\vite.js preview --configLoader runner --host 127.0.0.1 --port 5173 --strictPort
pause
