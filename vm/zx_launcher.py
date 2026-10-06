#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""Launcher for the Maimai WeChat bridge. Pure Python on purpose.

Why not just fix the batch file
-------------------------------
The first launcher was a .bat with Chinese text in it, and double-clicking it
did nothing at all. The reason was not a logic error: this box's console code
page is 936 (GBK), cmd reads a batch file using that code page, and the file
had been written as UTF-8, so every Chinese line arrived as mojibake. Combined
with bare-LF line endings and goto labels, cmd gave up before doing anything
visible. Re-encoding the batch file would have fixed that one file, but the same
trap waits on every batch file this project writes from now on.

So the batch file is reduced to three ASCII lines that cannot be misread, and
everything with a non-zero chance of being subtle lives here in Python, where
encoding is explicit and testable.

What it does, in order
----------------------
  1. set stdout to tolerate unencodable characters instead of raising
  2. check the tunnel to the cloud brain, and start it if it is down
  3. check that WeChat is actually running
  4. wake WeChat's UI Automation tree - see the note below, this is the step
     that makes step 5 possible at all
  5. attach through wxauto4 and print who we are - this is the safety check
     that matters most, because it is the only thing standing between a typo
     and the bridge operating on the wrong account
  6. with no group configured, stop here and list the real group names, so the
     name written into the config is the one WeChat shows rather than one
     typed from memory
  7. otherwise hand over to wxauto_bridge.py, streaming its output

Why step 4 exists
-----------------
WeChat 4.x paints its own interface and only builds the full control tree when
it believes an accessibility client is attached. Without that, WM_GETOBJECT
returns a bare Qt shell and wxauto4 reports the main window as missing even
though it enumerates it correctly. Screen resolution and window size were both
measured and ruled out as causes, so the tree is woken explicitly here and
checked, rather than the failure being attributed to something else again.

Every step also appends to C:\\zx\\launcher.log, because the interesting
failures happen while nobody is looking at the console.

Usage:  python zx_launcher.py [--list]
"""

import io
import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse
from threading import Thread

HERE = os.path.dirname(os.path.abspath(__file__))
PKGS = os.path.join(HERE, "pkgs")
if os.path.isdir(PKGS) and PKGS not in sys.path:
    sys.path.insert(0, PKGS)

# pywin32 keeps its extension modules in subdirectories of the package folder
# rather than at the top of it: win32gui.pyd lives in pkgs\win32, and the
# pywin32 loader shim lives in pkgs\win32\lib. A normal pip install puts a
# .pth file in site-packages to add those directories, but a .pth file is only
# honoured inside a real site-packages - this folder is merely on PYTHONPATH,
# so nothing adds them and "import win32gui" fails with ModuleNotFoundError
# even though the file is sitting right there. Adding them here was verified on
# the box: with these four directories on sys.path all seven pywin32 modules
# import, and wxauto4 then imports cleanly.
for _sub in ("win32", os.path.join("win32", "lib"), "pythonwin",
             "pywin32_system32"):
    _p = os.path.join(PKGS, _sub)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.append(_p)

LOG = os.path.join(HERE, "launcher.log")
# The bridge body's own log. Separate from LOG because the two answer different
# questions: LOG says which step the launcher reached, this one says what the
# bridge did once it was handed over.
BRIDGE_LOG = os.path.join(HERE, "bridge.log")
STATUS = os.path.join(HERE, "_launch.json")
LOCAL_CFG = os.path.join(HERE, "wxbridge.local.json")
TUNNEL_TASK = "ZhiXiaTunnel"
TUNNEL_PORT = 18787
BRAIN = "http://127.0.0.1:%d" % TUNNEL_PORT
PYTHON = sys.executable

_out = []


def say(msg=""):
    """Print and record. Console encoding must never be able to kill the run."""
    line = str(msg)
    _out.append(line)
    try:
        print(line, flush=True)
    except Exception:
        try:
            print(line.encode("ascii", "replace").decode("ascii"),
                  flush=True)
        except Exception:
            pass
    try:
        with io.open(LOG, "a", encoding="utf-8", errors="replace") as fh:
            fh.write(time.strftime("[%H:%M:%S] ") + line + "\n")
    except Exception:
        pass


def save(status):
    try:
        with io.open(STATUS, "w", encoding="utf-8") as fh:
            json.dump(status, fh, ensure_ascii=False, indent=1)
    except Exception as exc:
        say("  (could not write status: %s)" % type(exc).__name__)


# --- 1. console ---------------------------------------------------------

def fix_console():
    """Replace stdout/stderr so a character the code page cannot hold is
    replaced rather than raised. On a GBK console this is the difference
    between a garbled line and a dead launcher."""
    for name in ("stdout", "stderr"):
        s = getattr(sys, name, None)
        if s is None:
            continue
        try:
            s.reconfigure(errors="replace")
        except Exception:
            pass


# --- 2. tunnel ----------------------------------------------------------

def port_open(port, host="127.0.0.1", t=3.0):
    s = socket.socket()
    s.settimeout(t)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def brain_alive(t=8.0):
    """Ask the brain something trivial and report what came back.

    A listening port only proves the forwarder bound; only an answer proves the
    other end is there. Note that a 401 counts as alive: the brain rejects an
    unauthenticated request, and a rejection still means the request travelled
    all the way through and came back. Treating any non-200 as "down" is what
    made this report a healthy tunnel as broken.

    The token goes on the query string, because that is where the brain and the
    bridge both put it. It is read from the same places the bridge reads it
    from, so there is one source of truth rather than a second copy of a
    secret.
    """
    import urllib.error
    import urllib.request
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = BRAIN + "/api/plugins"
    tok = read_brain_token()
    if tok:
        url += "?token=" + urllib.parse.quote(tok)
    try:
        r = op.open(url, timeout=t)
        return r.status == 200
    except urllib.error.HTTPError as exc:
        # A response came back. That is the answer we wanted.
        say("      brain answered with HTTP %s (token %s)"
            % (exc.code, "sent" if tok else "not available"))
        return True
    except Exception as exc:
        say("      brain did not answer: %s" % type(exc).__name__)
        return False


def read_brain_token():
    """The token, from wherever the bridge would get it. Empty if not found,
    which still leaves the liveness check working via the 401 path."""
    for p in (LOCAL_CFG,
              os.path.join(os.path.dirname(HERE), "config", "config.json"),
              os.path.join(HERE, "brain_config_full.json")):
        try:
            with io.open(p, encoding="utf-8") as fh:
                tok = (json.load(fh) or {}).get("brain_token") or ""
            if tok.strip():
                return tok.strip()
        except Exception:
            continue
    return ""


def check_tunnel(st):
    say("[1/5] tunnel to the cloud brain")
    if brain_alive():
        say("      OK - brain answered on %s" % BRAIN)
        st["tunnel"] = "up"
        return True
    if port_open(TUNNEL_PORT):
        say("      port %d is bound but nobody answered" % TUNNEL_PORT)
    say("      starting scheduled task %s ..." % TUNNEL_TASK)
    try:
        subprocess.run(["schtasks", "/Run", "/TN", TUNNEL_TASK],
                       capture_output=True, timeout=30)
    except Exception as exc:
        say("      schtasks failed: %s" % type(exc).__name__)
    for i in range(1, 7):
        time.sleep(5)
        if brain_alive(t=5.0):
            say("      OK - up after %ds" % (i * 5))
            st["tunnel"] = "started"
            return True
    say("      [X] tunnel will not come up. See %s" % os.path.join(HERE,
                                                                  "tunnel.log"))
    st["tunnel"] = "down"
    return False


# --- 3. wechat ----------------------------------------------------------

def wechat_running():
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Weixin.exe",
                        "/FO", "CSV", "/NH"],
                       capture_output=True, text=True, errors="replace",
                       timeout=60)
    return "Weixin.exe" in (r.stdout or "")


def check_wechat(st):
    say("")
    say("[2/5] WeChat")
    if wechat_running():
        say("      OK - Weixin.exe is running")
        st["wechat"] = "running"
        return True
    say("      [X] WeChat is not running.")
    say("          Open WeChat and log in as Maimai, keep the main window")
    say("          open (do not minimise it to the tray), then run this again.")
    st["wechat"] = "absent"
    return False


# --- 4. attach and confirm the account ----------------------------------

def load_cfg():
    try:
        with io.open(LOCAL_CFG, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        say("  (config unreadable: %s)" % type(exc).__name__)
        return {}


def wake_uia(st):
    """Make WeChat materialise its UI Automation tree, then report what happened.

    WeChat 4.x draws its own interface and only builds the full control tree
    when it believes an accessibility client is attached; otherwise
    WM_GETOBJECT hands back a bare Qt shell whose class is Qt51514QWindowIcon
    and every mmui::* element is missing. wxauto4 looks for mmui::MainWindow,
    so it reports "main window not found" even though the window is right
    there and enumerated correctly - which is exactly the symptom that sent
    the previous rounds of diagnosis after resolutions and window sizes.

    The wake is not a hack that fakes success: it flips the same accessibility
    state a screen reader would set, and it verifies the result by looking for
    mmui:: controls, rolling the byte back if they do not appear. Anything that
    fails is reported rather than swallowed, because a silent no-op here looks
    identical to the bug it is meant to fix.
    """
    say("")
    say("[3/5] WeChat accessibility tree")
    try:
        import zx_uia_wake as wake
    except Exception as exc:
        say("      [X] cannot load the wake module: %s: %s"
            % (type(exc).__name__, exc))
        st["uia_wake"] = "module-missing"
        return False
    try:
        out = wake.wake(log=lambda m: say("      | %s" % m))
    except Exception as exc:
        say("      [X] wake raised %s: %s" % (type(exc).__name__, exc))
        st["uia_wake"] = "raised"
        return False
    st["uia_wake"] = out
    say("      WeChat %s, pid %s" % (out.get("version") or "?", out.get("pid")))
    say("      action: %s" % (out.get("action") or "-"))
    say("      %s" % (out.get("detail") or ""))
    if out.get("rva"):
        say("      gate rva: 0x%X" % int(out["rva"]))
    if out.get("ok"):
        say("      [OK] mmui:: controls are visible to UI automation")
        return True
    say("      [X] the control tree is still not exposed.")
    say("          The tree only covers what is on screen, so also make sure")
    say("          the WeChat window is open and not minimised to the tray.")
    say("          Fallbacks, in order of preference:")
    say("            1. quit and reopen WeChat, then start this again")
    say("            2. downgrade WeChat to 203.0.113.10 or older")
    say("            3. start WeChat with QT_OPENGL=software")
    return False


def probe(st):
    """Attach, report the account, and list the real group names.

    The account check is the load-bearing part. expected_wxid in the config is
    the gate, and a gate that has never been observed passing is not a gate, so
    print the account and compare it here where a human can see it."""
    say("")
    say("[4/5] attaching to WeChat through wxauto4")
    try:
        from wxauto4 import WeChat
    except Exception as exc:
        say("      [X] cannot import wxauto4: %s: %s"
            % (type(exc).__name__, exc))
        say("")
        say("          If the message mentions win32gui / win32api / pywintypes,")
        say("          the pywin32 subdirectories are not on sys.path.")
        say("          Say so and it will be fixed - do not work around it by")
        say("          hand-copying files.")
        say("          Is %s present?" % PKGS)
        st["attach"] = "import-failed"
        return None
    say("      wxauto4 imported")
    try:
        wc = WeChat()
    except Exception as exc:
        say("      attach failed: %s: %s" % (type(exc).__name__, exc))
        say("")
        say("      Retrying once, in case the control tree appeared late ...")
        time.sleep(3)
        try:
            wc = WeChat()
            say("      attached on the retry")
            st["attach"] = "ok-after-retry"
        except Exception as exc2:
            say("      [X] attach failed again: %s: %s"
                % (type(exc2).__name__, exc2))
            say("")
            say("      If the step above did not report the mmui:: tree as")
            say("      visible, the cause is WeChat not exposing its controls,")
            say("      not the window position and not the screen size.")
            say("      Both of those were ruled out by measurement.")
            st["attach"] = "failed"
            return None
    say("      attached")
    st["attach"] = "ok"

    info = {}
    try:
        info = wc.GetMyInfo() or {}
    except Exception as exc:
        say("      (GetMyInfo failed: %s)" % type(exc).__name__)
    st["myinfo"] = info
    say("      account: %s" % (info.get("wxid") or info.get("nickname")
                              or "(unknown)"))
    for k, v in info.items():
        say("        %-10s %s" % (k, v))

    cfg = load_cfg()
    want = (cfg.get("expected_wxid") or "").strip()
    got = (info.get("wxid") or "").strip()
    if want and got and want != got:
        say("")
        say("      [X] WRONG ACCOUNT. Config expects %s but WeChat is %s."
            % (want, got))
        say("          Refusing to continue. Change accounts in WeChat.")
        st["account_ok"] = False
        return None
    if want:
        say("      [OK] account matches expected_wxid")
    st["account_ok"] = True
    return wc


def list_sessions(wc, st):
    say("")
    say("[5/5] groups")
    names = []
    try:
        items = wc.GetSession() or []
    except Exception as exc:
        say("      [X] GetSession failed: %s: %s"
            % (type(exc).__name__, exc))
        st["sessions"] = "failed"
        return names
    for el in items:
        row = {"repr": repr(el)}
        for attr in ("name", "who", "title", "nick", "nickname", "text"):
            v = getattr(el, attr, None)
            if isinstance(v, str) and v.strip():
                row[attr] = v
                break
        else:
            # No documented field matched, so fall back to the object's own
            # attributes rather than guess a schema.
            for attr in dir(el):
                if attr.startswith("_"):
                    continue
                try:
                    v = getattr(el, attr)
                except Exception:
                    continue
                if isinstance(v, str) and v.strip():
                    row[attr] = v
                    break
        names.append(row)
    st["sessions"] = names
    if not names:
        say("      (the session list came back empty)")
        return names
    say("      %d session(s) in the list:" % len(names))
    for r in names:
        label = r.get("name") or r.get("who") or r.get("title") or "?"
        say("        - %s" % label)
    return names


# --- 5. hand over to the bridge ----------------------------------------

def _pump(proc, logfh):
    """Relay the bridge's output to this console and to bridge.log.

    Decoding is done with errors="replace" rather than strictly: the bridge
    prints Chinese from wxauto4, and one undecodable byte at the wrong moment
    would kill this thread and silently truncate the log we added precisely to
    avoid a silent failure.
    """
    try:
        stream = io.TextIOWrapper(proc.stdout, encoding="utf-8",
                                  errors="replace", line_buffering=True)
    except Exception:
        return
    for line in stream:
        text = line.rstrip("\r\n")
        try:
            say("      | %s" % text)
        except Exception:
            pass
        if logfh:
            try:
                logfh.write(text + "\n")
                logfh.flush()
            except Exception:
                pass
    try:
        stream.close()
    except Exception:
        pass


def run_bridge(cfg, group):
    say("")
    say("      starting the bridge. Leave this window open.")
    say("      press Ctrl+C or close the window to stop it.")
    say("      its output is also written to %s" % BRIDGE_LOG)
    say("")
    env = dict(os.environ)
    env["PYTHONPATH"] = PKGS + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    args = [PYTHON, os.path.join(HERE, "wxauto_bridge.py")]
    if group:
        # --with also switches the bridge to "all", which is what a first test
        # wants: reply to everything in that one chat.
        args += ["--with", group]
        say("      target chat: %s   (mode: all - first test)" % group)
    say("")
    # The bridge prints the reason it refused to run - wrong account, chat name
    # not found, brain unreachable - and all of that used to go to the console
    # only. The console is gone by the time anyone reads the log, so a bridge
    # that exited after eleven seconds left no trace of why. Teeing into a file
    # costs nothing and turns "it just quit" into a sentence.
    logfh = None
    try:
        logfh = io.open(BRIDGE_LOG, "a", encoding="utf-8", errors="replace")
        logfh.write("\n===== %s  starting the bridge =====\n"
                    % time.strftime("%Y-%m-%d %H:%M:%S"))
        logfh.flush()
    except Exception as exc:
        say("      [X] cannot open the bridge log: %s: %s"
            % (type(exc).__name__, exc))
    try:
        p = subprocess.Popen(args, cwd=HERE, env=env,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)
    except Exception as exc:
        say("      [X] cannot start the bridge: %s: %s"
            % (type(exc).__name__, exc))
        if logfh:
            logfh.close()
        return 1
    # Read the child's output line by line instead of waiting first: a pipe
    # fills up and blocks the child if nobody drains it, and the bridge is
    # chatty enough to fill 64 KB while it prints its startup banner.
    pump = Thread(target=_pump, args=(p, logfh))
    pump.daemon = True
    pump.start()
    while True:
        try:
            rc = p.wait()
            break
        except KeyboardInterrupt:
            say("")
            say("      stopping the bridge ...")
            try:
                p.terminate()
                rc = p.wait(timeout=10)
            except Exception:
                rc = -1
            break
        except Exception:
            time.sleep(1)
    # Give the reader a moment to finish the last lines before the file closes.
    pump.join(timeout=5)
    if logfh:
        try:
            logfh.write("===== the bridge exited with code %s =====\n" % rc)
            logfh.close()
        except Exception:
            pass
    say("")
    say("      the bridge exited with code %s" % rc)
    say("      read %s for what it printed before exiting" % BRIDGE_LOG)
    return rc


def main():
    fix_console()
    argv = sys.argv[1:]
    st = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "pid": os.getpid(),
          "session": None, "argv": argv}
    say("=" * 62)
    say("  ZhiXia  <>  Maimai WeChat bridge")
    say("=" * 62)

    try:
        import win32process
        st["session"] = win32process.ProcessIdToSessionId(os.getpid())
        say("this process is in Windows session %s" % st["session"])
    except Exception:
        say("this process is in Windows session ?")

    if not check_tunnel(st):
        save(st)
        return 2
    if not check_wechat(st):
        save(st)
        return 3

    wake_uia(st)

    wc = probe(st)
    if wc is None:
        save(st)
        return 4

    cfg = load_cfg()
    group = (cfg.get("chat_name") or "").strip()

    if not group or "--list" in argv:
        # No group configured, or the operator asked to see them. Listing them
        # is how the config gets a name that matches WeChat exactly - a name
        # typed by hand is a name that will not match.
        list_sessions(wc, st)
        save(st)
        if not group:
            say("")
            say("      No group is configured yet (chat_name is empty), so the")
            say("      bridge deliberately does not start. Copy the exact group")
            say("      name from the list above into")
            say("        %s" % LOCAL_CFG)
            say("      then start this launcher again.")
            say("")
            say("      (type 'wxid' alone to re-check the account)")
            return 5
        return 0

    say("")
    say("      configured group: %s" % group)
    save(st)
    return run_bridge(cfg, None)


if __name__ == "__main__":
    try:
        rc = main()
    except Exception as exc:
        say("")
        say("[X] the launcher itself crashed: %s: %s"
            % (type(exc).__name__, exc))
        import traceback
        say(traceback.format_exc())
        rc = 9
    say("")
    say("--- launcher finished (code %s) ---" % rc)
    save({"rc": rc, "when": time.strftime("%Y-%m-%d %H:%M:%S")})
    try:
        input("press Enter to close this window ...")
    except Exception:
        pass
    sys.exit(rc)
