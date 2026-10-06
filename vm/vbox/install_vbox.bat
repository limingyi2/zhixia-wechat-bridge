@echo off
REM Install VirtualBox silently to D drive.
REM /S = silent, /D = target dir (must be LAST parameter, no quotes around it).
REM NOTE: no "timeout"/"find" here -- when this bat runs under Git Bash those
REM resolve to the Unix tools, not Windows'. Use PowerShell for waiting.
set "EXE=C:\Users\limingyi\AppData\Local\Temp\WinGet\Oracle.VirtualBox.7.2.20\VirtualBox-7.2.20-175154-Win.exe"

if not exist "%EXE%" (
  echo Installer not found: %EXE%
  exit /b 1
)

echo Installing VirtualBox silently to D:\VirtualBox ...
"%EXE%" /S /D=D:\VirtualBox
echo Installer returned. Checking result...

if exist "D:\VirtualBox\VirtualBox.exe" (
  echo INSTALLED: D:\VirtualBox\VirtualBox.exe
  exit /b 0
)
if exist "C:\Program Files\Oracle\VirtualBox\VirtualBox.exe" (
  echo INSTALLED: C:\Program Files\Oracle\VirtualBox\VirtualBox.exe
  exit /b 0
)
echo FAILED - VirtualBox.exe not found in either location
exit /b 1