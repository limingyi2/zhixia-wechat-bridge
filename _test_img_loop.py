r"""微信图片重复下载死循环的回归测试。

复现的是用户截图里那个现象：右键头像 → 打开图片 → 又右键 → 无限重复，
而真正的文字消息之后再也没被处理过。

🔴 真凶不是"漏了个判据"，是**下载的位置错了**：
_snapshot() 跑在 _diff() 之前，而它对窗口里**所有**消息（含历史）
都调 _grab_image()。微信 4.x 的 download() 内部就是"右键头像 → 在新窗口
里打开图"，于是即使一条新消息都没有，也每 3 秒右键一次、开窗一次、
下完才发现"处理过了"、丢弃、再关窗 —— 无限循环。

所以这里最要紧的一条判据是：**_snapshot 自己绝不碰 download()**。
后面所有 seen / _diff / 关窗的闸都是二线防线。

跑法：python _test_img_loop.py     （不需要微信，纯构造消息列表）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else ""
ROOT = os.path.abspath(os.path.join(HERE, "..", "..")) if HERE \
    else os.path.abspath(os.getcwd())
sys.path.insert(0, os.path.join(ROOT, "wechat_bridge"))

try:
    from wxauto_bridge import (
        Bridge, _looks_like_image, _strip_img_placeholder, _PendingImg)
except Exception as e:
    print("import 失败：%s" % e)
    sys.exit(1)

import base64
import tempfile
import time

fails = []


def ck(name, ok, extra=""):
    print(("  OK   " if ok else "  FAIL ") + name + (("   " + extra) if extra else ""))
    if not ok:
        fails.append(name)


# --- 构造假的 wxauto 消息 ------------------------------------------------
class FakeImgMsg:
    """图片消息。

    download() 模拟真实副作用：打开图片查看窗并抢走活动窗口。
    dl_calls 就是用户看到的"右键次数"。
    """
    def __init__(self, sender, img_bytes, runtimeid="rid-1"):
        self.sender = sender
        self.content = "[图片]"
        self.type = "image"
        self._img = img_bytes
        self.runtimeid = runtimeid
        self.dl_calls = 0

    def download(self):
        self.dl_calls += 1
        # 文件名带上 id：不同消息各自计数，否则两个 FakeImgMsg 会算出
        # 同一个临时文件名、互相覆盖，测出来的"两张图内容相同"是假的。
        p = os.path.join(tempfile.gettempdir(),
                         "_zx_fake_%s_%d.png" % (id(self), self.dl_calls))
        open(p, "wb").write(self._img)
        return p


class FakeTextMsg:
    def __init__(self, sender, content, runtimeid=""):
        self.sender = sender
        self.content = content
        self.type = "text"
        if runtimeid:
            self.runtimeid = runtimeid


def img_bytes(tag):
    return ("IMG-" + tag).encode()


class BadSrc:
    """GetAllMessage 抛异常 —— 模拟 wxauto4 那个 COMError。"""
    def GetAllMessage(self):
        raise RuntimeError("COM 炸了")


def make_bridge():
    """造一个只带必要方法的桥接实例（不碰微信、不读配置）。"""
    b = Bridge.__new__(Bridge)
    b._sub = None
    b.wx = None
    b.my_name = "麦麦"
    # group_reply_mode=all：让所有消息都往上送，测试才能聚焦图片逻辑。
    # 用默认的 mention 模式（无 nicknames/keywords）会全部被跳过，
    # 那样测的就不是图片链路了。
    b.cfg = {"pause_when_away": False, "group_reply_mode": "all"}
    b._synced = False
    b._prev = []
    b._seen_img = None
    # _read_all 用到的三个字段（真跑起来是 __init__ 设的，这里 __new__
    # 绕过了 __init__，必须补上 —— 少一个就 AttributeError）
    b._read_busy = False
    b._read_stuck_until = 0.0
    b._read_stuck_n = 0
    b._my_recent = set()
    b._fake_window = None
    # 关窗这步要真调的话会枚举 Windows 窗口，纯测试里绕开（单独测）
    b._close_image_viewer = lambda: None
    return b


def b_window(b):
    class W:
        def GetAllMessage(self):
            return b._fake_window() if b._fake_window else []
    return W()


def pump(b):
    """跑一轮真实的 step 逻辑：snapshot → diff → 两道闸 → 下载。

    刻意不调 step() 本体（它会去问云端大脑、掷骰子判冷场）。
    这里的闸顺序必须和 step() 里一致，否则测的不是真实路径：
    **先判断要不要理，再下载**。
    """
    items = b._snapshot()
    new = b._diff(items)
    got = []
    for sender, content, img in new:
        if b._is_mine(sender, content):
            continue
        if not b._should_reply(content):
            continue
        if isinstance(img, _PendingImg):
            img = b._resolve_img(img)
            if img is None:
                continue
        got.append((sender, content, img))
    return got


print("== 场景一：🔴 _snapshot 本身绝不能下载任何图片 ==")
b = make_bridge()
msg = FakeImgMsg("老张", img_bytes("A"))
b._sub = b_window(b)
b._fake_window = lambda: [msg, FakeTextMsg("老张", "哎久等了")]
items = b._snapshot()
ck("_snapshot 没调 download()", msg.dl_calls == 0,
   "调了 %d 次" % msg.dl_calls)
ck("图片被登记成待下载", isinstance(items[0][2], _PendingImg),
   "实际 %r" % (type(items[0][2]).__name__,))
ck("待下载项不是 base64 字符串", isinstance(items[0][2], str) is False)
ck("文字消息正常在列", ("老张", "哎久等了", "") in items)

print()
print("== 场景二：🔴 没有新消息时，一轮都不该右键 ==")
b = make_bridge()
msg = FakeImgMsg("老张", img_bytes("A"))
txt = FakeTextMsg("老张", "哎久等了")
b._sub = b_window(b)
b._fake_window = lambda: [msg, txt]

pump(b)                      # 第一轮：这两条都算"历史"，只建基线
after_first = msg.dl_calls
ck("建基线那轮也不下载", after_first == 0, "调了 %d 次" % after_first)
for _ in range(10):          # 再跑十轮，窗口内容一动不动
    got = pump(b)
ck("空转 10 轮 download() 一次没调", msg.dl_calls == 0,
   "调了 %d 次" % msg.dl_calls)
ck("空转 10 轮没有新消息被处理", got == [])

print()
print("== 场景三：🔴 用户看到的现象 —— 同一张图不会反复被打开 ==")
Bridge._seen_img = None
b = make_bridge()
msg = FakeImgMsg("老张", img_bytes("A"))
txt = FakeTextMsg("老张", "哎久等了")
b._sub = b_window(b)
state = {"w": [msg, txt]}
b._fake_window = lambda: state["w"]

pump(b)                                  # 建基线：这一轮认不出新增
state["w"] = [msg, txt, FakeTextMsg("老张", "新来一条")]
pump(b)                                  # 新增一条文字，不含图
after_text = msg.dl_calls
ck("新来文字那轮没有重下旧图", after_text == 0,
   "调了 %d 次" % after_text)
# 模拟死循环里"图窗抢走活动窗口、窗口内容短暂错乱"再回到聊天窗
for _ in range(15):
    pump(b)
ck("再 15 轮右键次数不涨", msg.dl_calls == 0,
   "从 %d 涨到 %d" % (0, msg.dl_calls))
ck("整场右键 0 次（原来是无上限）", msg.dl_calls == 0,
   "调了 %d 次" % msg.dl_calls)

Bridge._seen_img = None
print()
print("== 场景四：新图来了要真处理，文字消息不被饿死 ==")
b = make_bridge()
old_img = FakeImgMsg("老张", img_bytes("A"), "rid-old")
old_txt = FakeTextMsg("老张", "哎久等了", "rid-t1")
b._sub = b_window(b)
state = {"w": [old_img, old_txt]}
b._fake_window = lambda: state["w"]
pump(b)                                  # 建基线

new_img = FakeImgMsg("老张", img_bytes("B"), "rid-new")
new_txt = FakeTextMsg("老张", "接着说", "rid-t2")
state["w"] = [old_img, old_txt, new_img, new_txt]
got = pump(b)
senders = [(s, c) for s, c, _ in got]
ck("认出了两条新增", len(got) == 2, "得到 %r" % (senders,))
ck("新图被下载并送出去", any(im for _, _, im in got))
ck("新文字也在", ("老张", "接着说") in senders)
ck("旧图没被重下", old_img.dl_calls == 0, "调了 %d 次" % old_img.dl_calls)
ck("新图只下一次", new_img.dl_calls == 1, "调了 %d 次" % new_img.dl_calls)

Bridge._seen_img = None
print()
print("== 场景五：🔴 第二道闸 —— 同一张图不会送云端两次 ==")
b = make_bridge()
m = FakeImgMsg("老张", img_bytes("A"))
b._sub = b_window(b)
b._fake_window = lambda: [m, FakeTextMsg("老张", "哎久等了")]
pump(b)
p1 = b._resolve_img(b._snapshot()[0][2])
ck("第一次能拿到图", bool(p1))
p2 = b._resolve_img(b._snapshot()[0][2])
ck("第二次被内容哈希拦下", p2 is None, "返回了 %r" % (p2,))

Bridge._seen_img = None
print()
print("== 场景六：连着发两张不同的图，两张都要处理 ==")
b = make_bridge()
i1 = FakeImgMsg("老张", img_bytes("A"), "rid-1")
i2 = FakeImgMsg("老张", img_bytes("B"), "rid-2")
t1 = FakeTextMsg("老张", "看这个", "rid-t1")
b._sub = b_window(b)
state = {"w": [t1]}
b._fake_window = lambda: state["w"]
pump(b)
state["w"] = [t1, i1, i2]
got = pump(b)
imgs = [im for _, _, im in got if im]
ck("两张图都下载了", i1.dl_calls == 1 and i2.dl_calls == 1,
   "分别 %d / %d 次" % (i1.dl_calls, i2.dl_calls))
ck("两张都送出去了", len(imgs) == 2, "拿到 %d 张" % len(imgs))
ck("两张内容不同", len(set(imgs)) == 2)

print()
print("== 场景七：_diff 认不出锚点时不污染 _prev ==")
b = make_bridge()
b._prev = ["老张|图片#img:rid-old", "老张|哎久等了"]
b._synced = True
before = list(b._prev)
got = b._diff([("完全不同的窗口", "内容"), ("也是新的", "别的")])
ck("认不出锚点时返回空", got == [], "返回了 %r" % (got,))
ck("_prev 没有被改成新窗口的内容", b._prev == before,
   "被改成了 %r" % (b._prev,))

print()
print("== 场景八：认得出锚点时照旧正常追加 ==")
b = make_bridge()
b._prev = ["老张|第一条", "老张|第二条"]
b._synced = True
got = b._diff([("老张", "第一条", ""), ("老张", "第二条", ""),
               ("老张", "第三条", "")])
ck("认出新增那条", got == [("老张", "第三条", "")], "得到 %r" % (got,))
ck("_prev 已更新", len(b._prev) == 3)

print()
print("== 场景九：图片内容哈希 ==")
ck("同内容同哈希", Bridge._img_key("AAAA") == Bridge._img_key("AAAA"))
ck("不同内容不同哈希", Bridge._img_key("AAAA") != Bridge._img_key("BBBB"))
ck("空图返回空串", Bridge._img_key("") == "")

print()
print("== 场景十：seen 图集合是类级共享的 ==")
Bridge._seen_img = None
bb = make_bridge()
bb._seen_images().add("k1")
b2 = make_bridge()
ck("两个实例共享同一份 seen", "k1" in b2._seen_images())
Bridge._seen_img = None

print()
print("== 场景十一：下载前指纹 _fp ==")
b = make_bridge()
p = _PendingImg(FakeImgMsg("老张", b"x", "rid-zzz"), "老张", "")
p.fp = "rid-zzz"
ck("待下载项的指纹带 img 维", "#img:rid-zzz" in Bridge._fp(("老张", "", p)))
ck("已下载项的指纹带长度", "#img4:AAAA" in Bridge._fp(("老张", "", "AAAA")))
ck("纯文字没有 img 维", Bridge._fp(("老张", "嗨", "")) == "老张|嗨")

print()
print("== 场景十二：占位符判别与剥离 ==")
ck("type=image 认得出", _looks_like_image(FakeImgMsg("x", b""), "[图片]"))
ck("空 type 靠占位符认得出",
   _looks_like_image(FakeTextMsg("x", "[图片]"), "[图片]"))
ck("动画表情不算图",
   not _looks_like_image(FakeTextMsg("x", "[动画表情]"), "[动画表情]"))
ck("普通文字不算图",
   not _looks_like_image(FakeTextMsg("x", "哎久等了"), "哎久等了"))
ck("纯图片→空", _strip_img_placeholder("[图片]") == "")
ck("图+话→留话", _strip_img_placeholder("[图片]这个啥意思") == "这个啥意思")

print()
print("== 场景十三：自己发的图不下载 ==")
b = make_bridge()
b._close_image_viewer = lambda: None
mine = FakeImgMsg("self", img_bytes("A"))
b._sub = b_window(b)
b._fake_window = lambda: [mine]
items = b._snapshot()
ck("自己的图也只登记不下载", mine.dl_calls == 0)
ck("sender 换成了我的昵称", items[0][0] == "麦麦", "实际 %r" % (items[0][0],))

print()
print("== 场景十四：🔴 读消息卡住时能被放弃，不会无限卡住 ==")


class SlowSrc:
    """模拟 GetAllMessage 卡死在界面操作里。"""
    def __init__(self, delay=99.0):
        self.delay = delay
        self.calls = 0

    def GetAllMessage(self):
        self.calls += 1
        t0 = time.time()
        while time.time() - t0 < self.delay:
            time.sleep(0.02)
        return [FakeTextMsg("老张", "终于出来了")]


b = make_bridge()
b._close_image_viewer = lambda: None
src = SlowSrc(delay=99.0)
t0 = time.time()
got = b._read_all(src, timeout=1.0)
dt = time.time() - t0
ck("卡住时返回 None 而不是干等", got is None)
ck("等了约 1 秒就放弃（没等 99 秒）", 0.8 < dt < 3.0, "等了 %.1f 秒" % dt)
ck("记下了卡过一次", b._read_stuck_n == 1, "记了 %d 次" % b._read_stuck_n)
ck("进了冷却", b._read_stuck_until > time.time())

print()
print("== 场景十五：🔴 卡住后不能再开第二个线程 ==")
# 这是"疯狂右键"的放大器：卡住的线程还在操作界面，再叠一个就更乱
b = make_bridge()
b._close_image_viewer = lambda: None
src2 = SlowSrc(delay=99.0)
b._read_all(src2, timeout=0.8)
after_first = src2.calls
got = b._read_all(src2, timeout=0.8)
ck("冷却期内直接返回 None", got is None)
ck("冷却期内没有再调 GetAllMessage", src2.calls == after_first,
   "调了 %d 次" % src2.calls)
ck("冷却期内卡住计数不继续涨", b._read_stuck_n == 1,
   "记了 %d 次" % b._read_stuck_n)

print()
print("== 场景十六：冷却结束后会再试一次 ==")
b._read_stuck_until = time.time() - 1.0      # 假装冷却过了
src3 = SlowSrc(delay=99.0)
b._read_all(src3, timeout=0.8)
ck("冷却后会再尝试", src3.calls >= 1, "调了 %d 次" % src3.calls)
ck("再试又卡住会继续计数", b._read_stuck_n == 2,
   "记了 %d 次" % b._read_stuck_n)

print()
print("== 场景十七：正常读消息不受影响 ==")
b = make_bridge()
b._close_image_viewer = lambda: None


class FastSrc:
    def __init__(self, msgs):
        self.msgs = msgs

    def GetAllMessage(self):
        return list(self.msgs)


msgs = [FakeTextMsg("老张", "在吗"), FakeTextMsg("老张", "哎久等了")]
b._sub = FastSrc(msgs)
items = b._snapshot()
ck("正常读出两条", len(items) == 2, "读出 %d 条" % len(items))
ck("内容对得上", [x[1] for x in items] == ["在吗", "哎久等了"],
   "得到 %r" % [x[1] for x in items])
ck("没有卡住计数", b._read_stuck_n == 0)

print()
print("== 场景十八：读消息抛异常时不让整轮死掉 ==")
b = make_bridge()
b._close_image_viewer = lambda: None
b._sub = BadSrc()
items = b._snapshot()
ck("异常时返回空列表而不是崩", items == [], "得到 %r" % (items,))

print()
print("== 场景十四之二：卡住后会复位界面 ==")
b = make_bridge()
b._close_image_viewer = lambda: None
called = []
b._reset_wechat_ui = lambda: called.append(1)
b._read_all(SlowSrc(delay=99.0), timeout=0.6)
ck("卡住后调用了界面复位", len(called) == 1, "调了 %d 次" % len(called))

print()
print("== 场景十四之三：🔴 复位不能抛异常把主循环带崩 ==")
b = make_bridge()
b._close_image_viewer = lambda: None
b._sub = BadSrc()
try:
    items = b._snapshot()
    ok = True
except Exception as e:
    ok = False
    print("   抛了：%r" % e)
ck("复位/读消息异常都不会让_snapshot 抛出去", ok)

print()
print("FAIL %d" % len(fails))
for f in fails:
    print("  - %s" % f)
sys.exit(1 if fails else 0)
