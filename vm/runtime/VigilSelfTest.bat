@echo off
REM ============================================================
REM  Vigil self-test: talk in the small account before adding to a group.
REM  Requires the tunnel to be up (run VigilTunnel.bat first, in
REM  another window, and leave it open).
REM
REM  Keep the small-account WeChat logged in (the 4.1.8 one on D:).
REM  Ctrl+C to exit. Does not touch any real config.
REM
REM  NOTE: this file must stay pure ASCII. The Chinese chat name is
REM  built here with chr() instead of typed inline -- a non-ASCII
REM  byte in a .bat breaks the structure under GBK.
REM ============================================================
cd /d "F:\vigil"

REM ---- 1. is the tunnel up? ----
.venv-wx\Scripts\python.exe -c "import socket,sys; s=socket.socket(); s.settimeout(2); sys.exit(0 if s.connect_ex(('127.0.0.1',18787))==0 else 1)"
if errorlevel 1 (
  echo.
  echo   The tunnel is NOT up, so the brain is unreachable and she will
  echo   never reply. Open  VigilTunnel.bat  in another window first,
  echo   wait until it says "Tunnel is up", then come back here.
  echo.
  pause
  exit /b 1
)
echo   Tunnel is up.

REM ---- 2. File Transfer Assistant, built from code points (ASCII-safe) ----
set "TARGET="
.venv-wx\Scripts\python.exe -c "import sys; sys.stdout.write(chr(0x6587)+chr(0x4EF6)+chr(0x4F20)+chr(0x8F93)+chr(0x52A9)+chr(0x624B))" > "%TEMP%\_zx_t.txt"
set /p TARGET=<"%TEMP%\_zx_t.txt"
del "%TEMP%\_zx_t.txt"

echo.
echo   Log in the small-account WeChat first (the 4.1.8 one on D:).
echo   Then send a message to the small account from your big account.
echo   The bridge watches that window and replies there.
echo   Press Ctrl+C to stop.
echo.

.venv-wx\Scripts\python.exe brain\wechat_bot\wxauto_bridge.py --with "%TARGET%"

echo.
echo   Exited.
pause