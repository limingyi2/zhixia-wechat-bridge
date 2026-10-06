"""对照实验：新旧两版代码在同一场景下各 download 几次。

只回答一个问题：把下载从 _snapshot 挪到 _diff 之后之后，
「窗口内容一动不动」时 download 次数到底从多少变到多少。

跑法：python _probe_dlcount.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE_DIR = os.path.abspath(HERE)

BODY = '''
import os, sys, tempfile, base64
sys.path.insert(0, __BRIDGE_DIR__)
import wxauto_bridge as M


class Msg:
    def __init__(self, sender, content, mtype="text"):
        self.sender = sender
        self.content = content
        self.type = mtype
        self.runtimeid = "rid-" + content
        self.dl = 0

    def download(self):
        self.dl += 1
        p = os.path.join(tempfile.gettempdir(),
                         "_zx_probe_%s_%d" % (id(self), self.dl))
        open(p, "wb").write(b"IMGBYTES")
        return p


b = M.Bridge.__new__(M.Bridge)
b._sub = None
b.wx = None
b.my_name = "麦麦"
b.cfg = {"pause_when_away": False}
b._synced = False
b._prev = []
b._seen_img = None
b._close_image_viewer = lambda: None

img = Msg("laozhang", "[image]", "image")
img.content = M.IMG_PLACEHOLDER
txt = Msg("laozhang", "哎久等了")
window = [img, txt]


class W:
    def GetAllMessage(self):
        return list(window)


b._sub = W()

__PATCH__

ROUNDS = 12
for _ in range(ROUNDS):
    try:
        items = b._snapshot()
        new = b._diff(items)
    except Exception as e:
        print("ERRLOOP", type(e).__name__, e)

print("DOWNLOADS=" + str(img.dl))
'''

# 旧版：_snapshot 里直接下载（这正是修复前的写法）
PATCH_OLD = '''

def _snap_old(self):
    out = []
    for m in self._sub.GetAllMessage():
        c = str(getattr(m, "content", "") or "").strip()
        s = str(getattr(m, "sender", "") or "").strip()
        img = ""
        if M._looks_like_image(m, c):
            c = M._strip_img_placeholder(c)
            data = m.download()
            if data:
                img = base64.b64encode(data).decode()
            else:
                continue
        out.append((s, c, img))
    return out


def _grab_old(self, m, content, sender):
    data = m.download()
    return content, base64.b64encode(data).decode()


M.Bridge._snapshot = _snap_old
M.Bridge._grab_image = _grab_old
'''


def run(patch):
    code = BODY.replace("__BRIDGE_DIR__", repr(BRIDGE_DIR)).replace("__PATCH__", patch)
    p = subprocess.run([sys.executable, "-c", code],
                       capture_output=True, text=True, cwd=BRIDGE_DIR)
    for line in (p.stdout or "").splitlines():
        if line.startswith("DOWNLOADS="):
            return line.split("=", 1)[1], ""
        if line.startswith("ERRLOOP"):
            err = line
    err = (p.stderr or "").strip().splitlines()
    return None, (err[-1] if err else "无输出")


old, e1 = run(PATCH_OLD)
new, e2 = run("")

print("窗口内容 %s 轮一动不动，那张历史图被 download 了几次："
      % "12")
print("  旧版（下载在 _snapshot 里）: %s%s"
      % (old if old is not None else "ERR", ("  " + e1) if e1 else ""))
print("  新版（下载在 _diff 之后）  : %s%s"
      % (new if new is not None else "ERR", ("  " + e2) if e2 else ""))

if old is not None and new is not None:
    o, n = int(old), int(new)
    print()
    if o > n:
        print("结论：从 %d 次降到 %d 次 —— 旧版每轮都右键开窗一次，"
              "这就是那个死循环。" % (o, n))
    elif o == n:
        print("⚠️ 两者相同，这次改动没起作用，得复查")
    else:
        print("⚠️ 新版反而更多，必须查")
else:
    print()
    print("有一侧没跑成功，需要人工看")
