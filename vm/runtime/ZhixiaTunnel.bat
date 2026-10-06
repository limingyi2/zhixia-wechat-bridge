@echo off
REM ============================================================
REM  Zhixia tunnel: keep127.0.0.1:18787 -> brain:8788 alive.
REM  The brain API port is not open to the public, so the local
REM  bridge needs this tunnel. Started detached, survives reboots
REM  only if you add it to Task Scheduler yourself (see run_tunnel.ps1).
REM  Keep this window OPEN while running -- closing it stops the tunnel.
REM ============================================================
cd /d "F:\Vigil"

REM ============================================================
REM  云机地址从**环境变量**读，不写死在文件里 —— 仓库是 PUBLIC。
REM  设一次就行（设完要重开一个窗口才生效）：
REM      setx ZX_BRAIN_HOST 你的云机IP
REM  没设就明确报错退出，别拿个空地址去连然后让人以为是隧道的问题。
REM ============================================================
if "%ZX_BRAIN_HOST%"=="" (
  echo [错误] 没设 ZX_BRAIN_HOST，不知道要连哪台机器。
  echo         执行一次：setx ZX_BRAIN_HOST 你的云机IP
  echo         然后**重开一个命令行窗口**再跑这个脚本。
  pause
  exit /b 1
)

echo Starting tunnel 127.0.0.1:18787 -> %ZX_BRAIN_HOST%:8788 ...

start "Zhixia tunnel" /min "%SystemRoot%\System32\OpenSSH\ssh.exe" ^
  -i "F:\Vigil\deploy\sshkeys\linzhixia_deploy" ^
  -o StrictHostKeyChecking=no ^
  -o ServerAliveInterval=15 ^
  -o ServerAliveCountMax=3 ^
  -o ExitOnForwardFailure=yes ^
  -N -L 18787:127.0.0.1:8788 ^
  administrator@%ZX_BRAIN_HOST%

echo Waiting for the tunnel...
set /a T=0
:WAIT
timeout /t 2 /nobreak >nul
set /a T+=1
.venv-wx\Scripts\python.exe -c "import socket,sys; s=socket.socket(); s.settimeout(2); sys.exit(0 if s.connect_ex(('127.0.0.1',18787))==0 else 1)"
if %T% lss 10 goto WAIT
echo   Tunnel is up.
echo.
echo   Now run ZhixiaSelfTest.bat in another window.
echo   OR: just run the bridge directly:
echo     .venv-wx\Scripts\python.exe brain\wechat_bot\wxauto_bridge.py --with "??????"
echo.
echo   Close this window to stop the tunnel.
echo.
pause