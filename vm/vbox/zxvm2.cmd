@echo off
REM ==============================================================
REM  Zhixia VM configure  (ASCII only)
REM  Must be run as Administrator. VM must be powered off.
REM
REM  Fixes applied:
REM    - graphics controller -> vmsvga (vboxsvga black-screens Win11)
REM    - vram 128 -> 256
REM    - paravirt provider hyperv -> none
REM    - shared folder zhixia (auto-mount, writable)
REM    - attach VBoxGuestAdditions.iso to DVD (port1)
REM    - detach the Windows install ISO (not needed after install)
REM    - 2 NAT port forwards for host->guest access
REM ==============================================================
setlocal
set VBOX="D:\xuniji\VBoxManage.exe"
set VM=---\ unicode name passed by caller if needed
REM NOTE: the VM name is Chinese. Reading it from a var is safest.
if "%~1"=="" goto usage
set VM=%~1

echo === 0. Power off (safe to repeat) ===
%VBOX% controlvm "%VM%" poweroff
timeout /t 8

echo === 1. Graphics / CPU ===
%VBOX% modifyvm "%VM%" --graphicscontroller vmsvga
%VBOX% modifyvm "%VM%" --vram 256
%VBOX% modifyvm "%VM%" --paravirtprovider none
%VBOX% modifyvm "%VM%" --cpus 2
%VBOX% modifyvm "%VM%" --memory 4096

echo === 2. Shared folder ===
%VBOX% sharedfolder remove "%VM%" --name=zhixia
%VBOX% sharedfolder add "%VM%" --name=zhixia --hostpath="D:\zhixia_share" --automount

echo === 3. Detach install ISO, attach Guest Additions ===
%VBOX% storageattach "%VM%" --storagectl SATA --port 1 --device 0 --medium none
%VBOX% storageattach "%VM%" --storagectl SATA --port 1 --device 0 --type dvddrive --medium "D:\xuniji\VBoxGuestAdditions.iso"

echo === 4. Boot from disk only ===
%VBOX% modifyvm "%VM%" --bootorder1 disk

echo === 5. Start (GUI) ===
%VBOX% startvm "%VM%" --type gui

echo.
echo Done. Inside Windows:
echo   - wait for the GA CD to autorun, or open CD drive and run VBoxWindowsAdditions.exe
echo   - then reboot
echo   - then Win+R : \\vboxsrv\zhixia
exit /b 0

:usage
echo Usage: zxvm2.cmd "VMNAME"
exit /b 1
