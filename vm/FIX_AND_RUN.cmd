@echo off
REM ======================================================================
REM  Fix the Guest Additions share, then install.
REM  The share keeps dropping because VBoxService is not running.
REM  This restarts it, waits, verifies, and only then installs.
REM ======================================================================
setlocal EnableExtensions
set SHARE=\\vboxsrv\zhixia
set DST=C:\zx
set LOG=%SHARE%\_zx_log.txt

> "%LOG%" echo ==========================================================
>>"%LOG%" echo  Zhixia fix+install   %DATE%  %TIME%
>>"%LOG%" echo ==========================================================

REM ---- 1. VBoxService up? ----
echo   [1/4] checking Guest Additions service ...
sc query VBoxService > "%TEMP%\_zx_svc.txt" 2>&1
findstr /C:"RUNNING" "%TEMP%\_zx_svc.txt" >nul
if errorlevel 1 (
    echo         not running, starting ...
    >>"%LOG%" echo VBoxService was not RUNNING
    sc start VBoxService >>"%LOG%" 2>&1
    timeout /t 6 /nobreak >nul
) else (
    echo         already running
    >>"%LOG%" echo VBoxService already RUNNING
)

REM ---- 2. share reachable? ----
echo   [2/4] checking the share ...
if not exist "%SHARE%\_setup\zx.cmd" (
    echo         [FAIL] still cannot see the share.
    >>"%LOG%" echo RESULT: SHARE UNREACHABLE after service start
    >>"%LOG%" echo --- sc query output ---
    type "%TEMP%\_zx_svc.txt" >>"%LOG%"
    echo.
    echo   The service did not come up. Reboot the VM, then run me again.
    echo.
    pause
    exit /b 10
)
echo         share is visible
>>"%LOG%" echo share visible

REM ---- 3. hand over to the installer ----
echo   [3/4] running the installer ...
>>"%LOG%" echo handing to zx.cmd
call "%SHARE%\_setup\zx.cmd"
set RC=%ERRORLEVEL%
>>"%LOG%" echo installer returned rc=%RC%

echo.
echo   [4/4] done (rc=%RC%)
pause
exit /b %RC%
