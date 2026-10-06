@echo off
REM Connect to the VM's built-in RDP (VRDE) server.
REM 127.0.0.1:13389 is the NAT forward -> guest 3389.
REM Start minimized so it does not steal focus at login; the
REM session lives in the taskbar until the user opens it.
start "" mstsc /v:127.0.0.1:13389 /admin
timeout /t 6
echo RDP window requested. If the screen is black, click the window once.
