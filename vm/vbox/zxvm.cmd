@echo off
REM ============================================================
REM  Zhixia VM helper  (ASCII only - GBK would break this file)
REM  Must run as Administrator for VBoxManage config changes.
REM  Usage:  zxvm.cmd <command>
REM    prep     - shutdown VM, apply all config, boot GUI
REM    who      - show status / ports
REM    stop     - hard power off
REM ============================================================
setlocal
set VBOX="D:\xuniji\VBoxManage.exe"
set VM=weixin

if "%~1"=="prep" goto prep
if "%~1"=="who"  goto who
if "%~1"=="stop" goto stop
echo Unknown command. Use: prep ^| who ^| stop
exit /b 1

:prep
echo [1/4] Shutting down VM (GUI must release the lock)...
%VBOX% controlvm %VM% poweroff
timeout /t 12
echo [2/4] Applying config while OFF...
%VBOX% modifyvm %VM% --paravirtprovider none
%VBOX% modifyvm %VM% --graphicscontroller vmsvga
%VBOX% modifyvm %VM% --vram 256
%VBOX% modifyvm %VM% --sharedfolder add "zhixia" "D:\zhixia_share" --readonly
echo [3/4] Verifying...
%VBOX% showvminfo %VM% --machinereadable | findstr /i "vram graphicscontroller effparavirtprovider SharedFolder Forwarding"
echo [4/4] Booting GUI...
%VBOX% startvm %VM% --type gui
echo.
echo Done. Wait ~60s for Windows, then log in with vboxuser / limingyi2
exit /b 0

:who
%VBOX% list runningvms
echo --- ports on host ---
powershell -NoProfile -Command "$r=@(); foreach($p in 2222,13389){$r += ('  ' + $p + ' : ' + $(if((Test-NetConnection -ComputerName 127.0.0.1 -Port $p -InformationLevel Quiet -WarningAction SilentlyContinue){'OPEN'}else{'closed'}))}; $r -join [Environment]::NewLine"
echo --- share mount point inside Windows ---
echo   \\vboxsrv\zhixia
exit /b 0

:stop
%VBOX% controlvm %VM% poweroff
exit /b 0
