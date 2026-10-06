@echo off
REM ======================================================================
REM  Zhixia WeChat bridge - ONE CLICK launcher for the VM
REM
REM  Double-click this file. That is the whole procedure.
REM    - first run  : copies runtime + bridge into C:\zx, wires sys.path
REM    - every run  : opens the SSH tunnel, starts the bridge
REM
REM  ASCII only on purpose: Chinese text in a .cmd corrupts the structure.
REM ======================================================================
setlocal EnableExtensions
set SHARE=\\vboxsrv\zhixia
set DST=C:\zx

REM ------------------------------------------------------------------
REM  Where do the payload files come from?
REM  Preference order:
REM    1. My own folder. GO.cmd unpacks next to itself, so this is the
REM       normal case. It MUST be first: an earlier version only looked
REM       at the CD-ROM and the share, so a successful unpack was
REM       followed by "cannot find payload" anyway.
REM    2. D:\ E:\ F:\ -- the CD-ROM we mounted the ISO on. Hardware level,
REM       does not depend on Guest Additions, which keeps dropping out.
REM    3. \\vboxsrv\zhixia\_setup -- the shared folder, when it happens
REM       to be alive.
REM  The log goes to C:\zx\logs, NOT to the share: writing logs to a
REM  share that dies mid-run loses exactly the evidence we need.
REM ------------------------------------------------------------------
set LOG=%DST%\logs\zx.log
set SRC=
if exist "%~dp0pkgs" set SRC=%~dp0
if not defined SRC for %%D in (D:\ E:\ F:\) do (
    if not defined SRC if exist "%%Dpkgs" set SRC=%%D
)
if not defined SRC if exist "%SHARE%\_setup\pkgs" set SRC=%SHARE%\_setup\
if not defined SRC (
    echo.
    echo   [FAIL] cannot find the payload folder "pkgs".
    echo.
    echo   I looked next to me, on D: E: F: and on the share:
    echo     next to me : %~dp0
    echo     D: E: F:\
    echo     %SHARE%\_setup\
    echo.
    echo   The folder I am in should look like this:
    echo     zx.cmd  pkgs\  bridge\  zx_key
    echo.
    pause
    exit /b 1
)
if not exist "%DST%\logs" mkdir "%DST%\logs"
if not exist "%DST%\keys" mkdir "%DST%\keys" 2>nul
if not exist "%DST%\bridge" mkdir "%DST%\bridge" 2>nul
if not exist "%DST%\logs" (
    echo   [FAIL] cannot create %DST%\logs
    echo.
    pause
    exit /b 1
)
>>"%LOG%" echo payload source = %SRC%

> "%LOG%" echo ==========================================================
>>"%LOG%" echo  Zhixia bridge   %DATE%  %TIME%
>>"%LOG%" echo ==========================================================

REM ------------------------------------------------------------------
REM  Locate Python
REM
REM  Trap: %LOCALAPPDATA%\Microsoft\WindowsApps\python.exe is a Store stub.
REM  It exists, so `if exist` accepts it, then every later command dies
REM  with "Python was not found". Must blacklist that path AND require the
REM  candidate to actually run.
REM
REM  Deliberately enumerating folders instead of shelling out to `py -3`:
REM  bare `py -3` opens an interactive REPL and the batch hangs forever,
REM  and quoting a -c argument through a backtick `for /f` is a trap of
REM  its own. A directory listing cannot do either.
REM ------------------------------------------------------------------
set PY=
REM  No `if not defined PY` guard in the loop head: the whole `for` body is
REM  expanded before the first iteration, so that test would freeze at its
REM  initial value and keep testing after PY is set. The subroutine checks
REM  PY itself and returns immediately once it is set.
for %%D in (
    "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "C:\Python314\python.exe"
    "C:\Python313\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
) do call :try_py "%%~D"
call :try_sys python
if not defined PY (
    echo.
    echo   [FAIL] No Python 3.11+ in this VM.
    >>"%LOG%" echo RESULT: NO PYTHON FOUND
    echo   Install Python 3.11 or newer, then double-click me again.
    echo.
    pause
    exit /b 2
)
>>"%LOG%" echo PYTHON = %PY%
"%PY%" -V >>"%LOG%" 2>&1

REM ------------------------------------------------------------------
REM  Where the runtime packages live.
REM
REM  Top level on purpose: it must be in effect on EVERY run, not just the
REM  first. The bridge is launched long after the install block closes, and
REM  a variable set inside `if ( ... )` would be gone by then - imports
REM  would break on the second launch while the first one worked fine.
REM
REM  PYTHONPATH, not a .pth in site-packages: if the user said yes to the
REM  installer's UAC prompt, site-packages belongs to Administrators and a
REM  normal-user write fails with "Access is denied".
REM ------------------------------------------------------------------
set PYTHONPATH=%DST%\pkgs
>>"%LOG%" echo PYTHONPATH = %PYTHONPATH%

REM ------------------------------------------------------------------
REM  Make sure the payload in C:\zx is actually runnable, then install.
REM
REM  There used to be a `.installed` stamp that skipped all of this on every
REM  later run. That was wrong: the stamp is written even when the copy only
REM  half-succeeded, and worse, `xcopy` without /Y keeps files that already
REM  exist. So a stale or partial C:\zx survived forever and no rerun could
REM  fix it - which is exactly what happened (the old package's uiplug.py was
REM  still being loaded while the new DLLs never arrived).
REM
REM  The rule now: verify FIRST, install only when the check fails. A healthy
REM  C:\zx costs one python -c (~2 s), a broken one is repaired by deleting it.
REM ------------------------------------------------------------------
echo.
echo   checking the runtime ...
>>"%LOG%" echo --- verify ---
if not exist "%DST%\pkgs" goto :install
"%PY%" -c "from wxauto4 import WeChat;import win32gui,win32ui,pythoncom,httpx,comtypes,psutil,PIL" > "%DST%\_v.txt" 2>&1
REM 只看 python 自己的退出码，**不要**拿 findstr 去搜输出里有没有某个词。
REM 原因：python 失败时 traceback 会原样印出 -c 那行命令行，而源码里就写着
REM print('IMPORTS OK') —— findstr 于是判定成功，import 明明坏了也照样跳过安装。
REM 把标记词拆成 'a'+'b' 也不行（实测 traceback 里仍有可拼出的片段）；
REM 唯一稳的是让 python 用退出码说话。
if not errorlevel 1 (
    >>"%LOG%" echo runtime already good - skipping install
    goto :runtime_ok
)

REM  A broken tree cannot be patched reliably: unknown leftovers may shadow
REM  the packaged files. Delete it whole and start from the package.
:install
echo   [1/3] installing packages, about 2 minutes ...
>>"%LOG%" echo --- installing ---
    if exist "%DST%\pkgs" (
        >>"%LOG%" echo removing stale %DST%\pkgs
        rd /S /Q "%DST%\pkgs" >nul 2>&1
    )
    mkdir "%DST%\pkgs" >nul 2>&1

    echo     copying packages ...
    >>"%LOG%" echo copying pkgs
REM  A trailing backslash INSIDE the quotes makes xcopy read the closing
REM  quote as an escaped literal quote, so the path never terminates and it
REM  complains "Invalid number of parameters". Drop it.
REM  Also: /NFL /NDL are ROBOCOPY switches, not xcopy. With them xcopy dies
REM  with the same "Invalid number of parameters" - proven on the host with
REM  a byte-identical line.
REM  /Y overwrites, so a leftover file can never survive an install.
    xcopy "%SRC%pkgs\*" "%DST%\pkgs" /E /I /Q /Y >nul
    if errorlevel 1 (
        echo   [FAIL] could not copy packages.
        >>"%LOG%" echo RESULT: PKGS COPY FAILED
        echo   The folder I am copying from:
        echo     %SRC%pkgs
        echo.
        pause
        exit /b 3
    )

    echo   [2/3] copying bridge ...
    >>"%LOG%" echo copying bridge
    copy /Y "%SRC%bridge\*" "%DST%\bridge" >nul 2>&1
    if not exist "%DST%\bridge\wxauto_bridge.py" (
        echo   [FAIL] could not copy the bridge.
        >>"%LOG%" echo RESULT: BRIDGE COPY FAILED
        echo.
        pause
        exit /b 4
    )

REM  PYTHONPATH is already set at top level; this block only reports it.
    >>"%LOG%" echo using PYTHONPATH = %PYTHONPATH%

:runtime_ok
echo   [runtime ok]
REM  Verify what the BRIDGE actually imports, not the top-level package.
REM  `import wxauto4` passes while `from wxauto4 import WeChat` still dies:
REM  the latter reaches wxauto4.uia.uiautomation, which needs win32ui, and a
REM  plain wxauto4/__init__ does not. That false OK is exactly what shipped
REM  a broken install several times. One extra line of verification is cheaper
REM  than another round trip to the user.
REM  Diagnostics go to stderr, so failures name the missing piece instead of
REM  just saying "No module named win32ui".
"%PY%" -c "from wxauto4 import WeChat;import win32gui,win32ui,pythoncom,httpx,comtypes,psutil,PIL" > "%DST%\_v.txt" 2>&1
type "%DST%\_v.txt" >>"%LOG%"
REM 同上：靠退出码判定，不靠 findstr 搜词（traceback 里会带源码，搜词必假通过）。
if errorlevel 1 (
    echo   [FAIL] imports still broken after a clean install.
    >>"%LOG%" echo RESULT: IMPORT FAILURE
    echo.
    echo   --- import test output ---
    type "%DST%\_v.txt"
    echo   -------------------------
    echo.
    echo   Full log: %LOG%
    echo.
    pause
    exit /b 6
)
>>"%LOG%" echo RUNTIME OK

REM ------------------------------------------------------------------
REM  Tunnel
REM ------------------------------------------------------------------
echo.
echo   [2/3] tunnel to the cloud brain ...

REM Windows OpenSSH refuses a key that other users can read. The share copy
REM inherits wide ACLs, so fix it here or ssh exits with "bad permissions".
where ssh >nul 2>&1
if errorlevel 1 (
    echo   [FAIL] this VM has no ssh client.
    >>"%LOG%" echo RESULT: NO SSH CLIENT
    echo   Enable it: Settings - Apps - Optional features - OpenSSH Client
    echo.
    pause
    exit /b 8
)
if not exist "%DST%\keys\zx_key" (
    copy /Y "%SRC%zx_key" "%DST%\keys\zx_key" >nul
)
icacls "%DST%\keys\zx_key" /inheritance:r /grant:r "%USERNAME%:F" >nul 2>&1

tasklist /fi "imagename eq ssh.exe" 2>nul | find /i "ssh.exe" >nul
if errorlevel 1 (
    if not exist "%DST%\keys\zx_key" (
        copy /Y "%SRC%zx_key" "%DST%\keys\zx_key" >nul
    )
    REM 云机地址从环境变量读 —— 跟下面的 ZX_BRAIN_TOKEN 同一个道理：
    REM 仓库是 PUBLIC，真地址不写死在脚本里。在这台机器上设一次：
    REM     setx ZX_BRAIN_HOST 你的云机IP
    if "%ZX_BRAIN_HOST%"=="" (
        echo [错误] 没设 ZX_BRAIN_HOST，不知道要连哪台机器。
        echo         执行一次：setx ZX_BRAIN_HOST 你的云机IP
        >>"%LOG%" echo [ERROR] ZX_BRAIN_HOST not set
        exit /b 1
    )
    start "zhixia-tunnel" /min ssh -N -o StrictHostKeyChecking=no ^
        -o UserKnownHostsFile=NUL -o ServerAliveInterval=30 ^
        -o ExitOnForwardFailure=yes ^
        -i "%DST%\keys\zx_key" ^
        -L 18787:127.0.0.1:8788 administrator@%ZX_BRAIN_HOST%
    >>"%LOG%" echo ssh tunnel started
    timeout /t 5 /nobreak >nul
) else (
    >>"%LOG%" echo tunnel already up
)

REM 健康检查走 api/plugins（微信桥接和启动器都拿它当"大脑活着吗"的探针）
REM 🔴 token 从环境变量 ZX_BRAIN_TOKEN 读，**不许写死在文件里**：
REM   同一个文件里已经带着云机 IP 了，再顺手写死口令，等于把两样一起公开。
REM
REM 🔴🔴 两个坑叠在一起，都实测过：
REM   ① findstr 搜词必假通过 —— python 失败时 traceback 会原样印出 -c 那行源码，
REM      源码里就有 print(...) 那个词，findstr 于是判定成功。
REM      把词拆成'__ZX'+'_BRAIN_OK__' 也只是把可拼片段变短，仍能拼出来。
REM      唯一稳的是让 python 用退出码说话。
REM   ② if errorlevel 写在 (...) 块里不可靠 —— 整段改用 goto，不用嵌套块。
if "%ZX_BRAIN_TOKEN%"=="" goto :nobrain
"%PY%" -c "import os,urllib.request as u;o=u.build_opener(u.ProxyHandler({}));t=os.environ.get('ZX_BRAIN_TOKEN','');r=urllib.request.Request('http://127.0.0.1:18787/api/plugins?token='+t,headers={'Authorization':'Bearer '+t});o.open(r,timeout=8).read(1)" > "%DST%\_b.txt" 2>&1
if errorlevel 1 goto :brainfail
goto :brainok
:nobrain
echo   [SKIP] brain check: ZX_BRAIN_TOKEN not set.
>>"%LOG%" echo RESULT: BRAIN CHECK SKIPPED (no ZX_BRAIN_TOKEN)
goto :brainok
:brainfail
echo   [FAIL] brain unreachable.
>>"%LOG%" echo RESULT: TUNNEL FAILED
type "%DST%\_b.txt" >>"%LOG%"
type "%DST%\_b.txt"
echo.
echo   Is the cloud machine on? Is sshd up?
exit /b 7
:brainok
echo   [1/2] brain reachable
>>"%LOG%" echo brain ok

REM ------------------------------------------------------------------
REM  Bridge
REM ------------------------------------------------------------------
echo.
echo   [3/3] starting the bridge ...
echo.
>>"%LOG%" echo launching bridge
"%PY%" "%DST%\bridge\wxauto_bridge.py"
set RC=%ERRORLEVEL%
>>"%LOG%" echo bridge exited rc=%RC%
echo.
echo   bridge stopped (rc=%RC%).  log: %LOG%
pause
exit /b %RC%

REM ======================================================================
REM  Subroutines. They live AFTER the main flow: a `call :label` above
REM  jumps forward and runs it, then returns. Putting them in the middle
REM  of the main flow means the main flow runs them as if they were its
REM  own steps.
REM ======================================================================

REM  Accept a python only if it is real: not the Store stub, and it can
REM  report a version of 3.11 or newer.
:try_py
if defined PY goto :eof
set CAND=%~1
echo %CAND% | find /i "WindowsApps" >nul && goto :eof
if not exist "%CAND%" goto :eof
"%CAND%" -c "import sys;sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1
if errorlevel 1 goto :eof
>>"%LOG%" echo python accepted: %CAND%
set PY=%CAND%
goto :eof

REM  Last resort: whatever is on PATH. Same test, so the Store stub is
REM  still rejected. `where` only prints paths, it never starts python.
:try_sys
if defined PY goto :eof
REM  same expansion-time trap as the loop above: no `if not defined PY`
REM  in the body, :try_py bails out on its own once PY is set
for /f "usebackq delims=" %%P in (`where %~1 2^>nul`) do call :try_py "%%P"
goto :eof
