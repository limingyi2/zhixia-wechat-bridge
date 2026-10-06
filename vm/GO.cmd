@echo off
REM ======================================================================
REM  Zhixia setup - unpack and install in one go.
REM
REM  Send GO.cmd and zhixia_pkg.zip together, then double-click GO.cmd.
REM  If WeChat drops them in different folders, this script searches for
REM  the zip itself and says out loud where it looked.
REM
REM  ASCII only on purpose: Chinese text in a .cmd corrupts the structure.
REM ======================================================================
setlocal EnableExtensions
set HERE=%~dp0
set PKG=%HERE%zhixia_pkg
set ZIP=
set WANT=13500000

echo.
echo   Zhixia setup
echo   ------------
echo   running from: %HERE%
echo.

REM ------------------------------------------------------------------
REM  [1/3] Find the payload zip.
REM
REM  This must be loud. The previous version piped PowerShell's error
REM  into 2>nul, so a missing file and a corrupt file both surfaced as
REM  the same useless "system cannot find the path specified".
REM
REM  Search order: next to me -> above me -> Desktop -> Downloads ->
REM  WeChat download folders -> a full walk of Documents (slow, last).
REM ------------------------------------------------------------------
echo   [1/3] looking for zhixia_pkg.zip ...

if exist "%HERE%zhixia_pkg.zip" set ZIP=%HERE%zhixia_pkg.zip
if not defined ZIP if exist "%HERE%..\zhixia_pkg.zip" set ZIP=%HERE%..\zhixia_pkg.zip
if not defined ZIP if exist "%USERPROFILE%\Desktop\zhixia_setup\zhixia_pkg.zip" set ZIP=%USERPROFILE%\Desktop\zhixia_setup\zhixia_pkg.zip
if not defined ZIP if exist "%USERPROFILE%\Downloads\zhixia_setup\zhixia_pkg.zip" set ZIP=%USERPROFILE%\Downloads\zhixia_setup\zhixia_pkg.zip
if not defined ZIP if exist "%USERPROFILE%\Downloads\zhixia_pkg.zip" set ZIP=%USERPROFILE%\Downloads\zhixia_pkg.zip

REM  WeChat 4.x drops received files in
REM    Documents\WeChat Files\<acct>\FileStorage\File\<YYYY-MM>\
REM  and in the 4.0+ tree
REM    Documents\xwechat_files\<...>\msg\attach\<hash>\
REM  Walking directories with `for /d` is fast; `for /r` would stat
REM  every image and video ever received, which can take minutes.
if not defined ZIP for /d %%A in ("%USERPROFILE%\Documents\WeChat Files\*") do call :scan_dir "%%A"
if not defined ZIP for /d %%A in ("%USERPROFILE%\Documents\xwechat_files\*") do call :scan_dir "%%A"
if not defined ZIP if exist "%USERPROFILE%\Documents" for /d %%B in ("%USERPROFILE%\Documents\*") do call :scan_dir "%%B"

if not defined ZIP (
    echo.
    echo         still nothing, walking all of Documents. this is slow ...
    for /r "%USERPROFILE%\Documents" %%Z in (zhixia_pkg*.zip) do call :setzip "%%Z"
    for /r "%USERPROFILE%\Desktop" %%Z in (zhixia_pkg*.zip) do call :setzip "%%Z"
    for /r "%USERPROFILE%\Downloads" %%Z in (zhixia_pkg*.zip) do call :setzip "%%Z"
)

if not defined ZIP (
    echo.
    echo   [FAIL] zhixia_pkg.zip not found anywhere.
    echo.
    echo   Searched: next to me, one level up, Desktop, Downloads,
    echo            and every subfolder of Documents.
    echo.
    echo   Most likely cause: you sent GO.cmd without the zip.
    echo.
    echo   FIX: send zhixia_pkg.zip too - it is about 13 MB, and
    echo        the name must start with  zhixia_pkg
    echo.
    pause
    exit /b 1
)

REM  A truncated transfer produces a valid-looking zip that is missing
REM  most of its content. Catching it here beats a confusing unpack
REM  failure three steps later.
for %%Z in ("%ZIP%") do set GOT=%%~zZ
echo         found: %ZIP%
echo         size : %GOT% bytes
if %GOT% LSS 10000000 (
    echo.
    echo   [FAIL] that zip is only %GOT% bytes, it should be about %WANT%.
    echo          The transfer was cut short.
    echo.
    echo          Send zhixia_pkg.zip again, on its own, not inside
    echo          another zip or a chat with other files.
    echo.
    pause
    exit /b 1
)
echo.

REM ------------------------------------------------------------------
REM  [2/3] Unpack.
REM
REM  tar first: it ships with Windows 10 build 1803 and newer, needs no
REM  PowerShell, no execution policy, no locale handling. The previous
REM  order (PowerShell first) is why an old box produced nothing at all.
REM ------------------------------------------------------------------
echo   [2/3] unpacking, about a minute ...

if not exist "%PKG%" mkdir "%PKG%" 2>nul
if not exist "%PKG%" (
    echo   [FAIL] cannot create %PKG%
    echo.
    pause
    exit /b 1
)

set UNPACKED=
tar -xf "%ZIP%" -C "%PKG%" >nul 2>&1
if exist "%PKG%\zx.cmd" set UNPACKED=1
if not defined UNPACKED (
    echo         tar unavailable, trying PowerShell ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath '%ZIP%' -DestinationPath '%PKG%' -Force" 2>&1 | find /i /c "error" >nul
    if exist "%PKG%\zx.cmd" set UNPACKED=1
)
if not defined UNPACKED (
    echo.
    echo   [FAIL] could not unpack the zip into:
    echo         %PKG%
    echo.
    echo   If the zip opened fine when you double-clicked it, use this
    echo   instead: right-click zhixia_pkg.zip - Extract All - into
    echo.
    echo         %PKG%
    echo.
    echo   then run GO.cmd again.
    echo.
    pause
    exit /b 1
)
echo         unpacked ok
echo.

REM ------------------------------------------------------------------
REM  [3/3] Install and start.
REM ------------------------------------------------------------------
echo   [3/3] installing and starting ...
echo.
call "%PKG%\zx.cmd"
set RC=%ERRORLEVEL%
echo.
echo   finished, exit code %RC%
echo   full log: C:\zx\logs\zx.log
echo.
pause
exit /b %RC%

REM ======================================================================
REM  Subroutines live after the main flow on purpose: a `call :label`
REM  above them jumps forward, runs them, and comes back. Placed in the
REM  middle they would run as if they were main flow.
REM ======================================================================

REM  Why a subroutine and not an inline `if not defined ZIP set ZIP=%%Z`:
REM  the whole for-body is expanded ONCE before the first iteration, so
REM  that test would freeze at "ZIP is empty" and the LAST match would
REM  win. Taking the first match has to happen inside a call.
:setzip
if defined ZIP goto :eof
set ZIP=%~1
goto :eof

REM  Look one level deeper in a directory that might be a WeChat account
REM  folder, a FileStorage folder, or an attach folder. Depth is capped
REM  so this cannot walk a whole disk.
:scan_dir
if defined ZIP goto :eof
if exist "%~1\zhixia_pkg*.zip" (
    for %%Z in ("%~1\zhixia_pkg*.zip") do call :setzip "%%Z"
)
if defined ZIP goto :eof
for /d %%C in ("%~1\*") do if not defined ZIP if exist "%%~fC\zhixia_pkg*.zip" call :setzip "%%~fC\zhixia_pkg*.zip"
if defined ZIP goto :eof
for /d %%C in ("%~1\*") do if not defined ZIP for /d %%D in ("%%~fC\*") do if exist "%%~fD\zhixia_pkg*.zip" call :setzip "%%~fD\zhixia_pkg*.zip"
goto :eof
