# 验「微信能看图」这条链路。**假对象，不连真微信**——
# 目的是在动真微信之前把逻辑钉死，改错也不会碰用户的号。
#
# 判据分三层，跟真跑一样：
#   ① 认得出图片消息（含 type 不可靠的退路）
#   ② 图能变成 base64，且两张不同的图指纹不同
#   ③ 图确实进了 /api/chat 的 body（起个假 HTTP 服务验）
import sys, os, io, json, types, base64, threading, http.server, socketserver

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wxauto_bridge as B

PASS = FAIL = 0
def chk(name, cond, extra=None):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  PASS  " + name)
    else:
        FAIL += 1; print("  FAIL  " + name + ("\n        -> " + json.dumps(extra, ensure_ascii=False) if extra is not None else ""))

def mk_png(color, size=(60, 40)):
    """造一张真 PNG。故意不用字节串硬凑 —— 缩图那段要PIL 真解。"""
    try:
        from PIL import Image
        im = Image.new("RGB", size, color)
        b = io.BytesIO(); im.save(b, format="PNG"); return b.getvalue()
    except Exception:
        # 没有 PIL 就退成最小合法 PNG（1x1）
        return base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class FakeMsg:
    """模拟 wxauto4 的消息对象。type 可控，为了验退路。"""
    def __init__(self, sender, content, mtype, payload=None, raiser=False):
        self.sender = sender; self.content = content; self.type = mtype
        self._payload = payload; self._raiser = raiser
    def download(self, *a, **k):
        if self._raiser:
            raise RuntimeError("模拟下载失败")
        p = os.path.join(os.environ.get("TEMP", "."), "_zx_test_img_%d.png" % id(self))
        with open(p, "wb") as f:
            f.write(self._payload or mk_png((200, 30, 30)))
        return p


def main():
    br = B.Bridge.__new__(B.Bridge)      # 只借方法，不跑 __init__（那会去连云端）
    br.cfg = {}

    print("① 认得出图片消息")
    chk("type=='image' 认得出", br._is_image(FakeMsg("甲", "[图片]", "image"), "[图片]"))
    chk("type 空但内容是 [图片] 也认（退路）", br._is_image(FakeMsg("甲", "[图片]", ""), "[图片]"))
    chk("表情包不算图", not br._is_image(FakeMsg("甲", "[动画表情]", "emoticon"), "[动画表情]"))
    chk("普通文字不算图", not br._is_image(FakeMsg("甲", "在吗", "text"), "在吗"))

    print("\n② 图变成 base64")
    txt, b64 = br._grab_image(FakeMsg("甲", "[图片]", "image"), "[图片]", "甲")
    chk("拿到了 base64", bool(b64) and len(b64) > 50, {"len": len(b64 or "")})
    chk("纯图片不编造文字（不抢视觉模型的活）", txt == "", txt)
    try:
        base64.b64decode(b64)
        chk("base64 能解回字节", True)
    except Exception as e:
        chk("base64 能解回字节", False, str(e))

    txt2, b64_2 = br._grab_image(FakeMsg("甲", "[图片] 这个啥意思", "image"), "[图片] 这个啥意思", "甲")
    chk("图+文字时文字保留", txt2 == "这个啥意思", txt2)

    t3, b3 = br._grab_image(FakeMsg("甲", "[图片]", "image", raiser=True), "[图片]", "甲")
    chk("下载失败返回空（整条会被丢掉，不送占位符）", (t3, b3) == ("", ""), {"t": t3, "b": b3})

    print("\n③ 临时文件不留")
    # 自己造一个确定路径的文件，让 download() 返回它 —— 不能用 FakeMsg
    # 那个按 id(self) 拼的路径，否则测的是另一个文件，删没删都无所谓。
    p = os.path.join(os.environ.get("TEMP", "."), "_zx_leak_test.png")
    with open(p, "wb") as f:
        f.write(mk_png((1, 2, 3)))
    m = FakeMsg("甲", "[图片]", "image")
    m.download = lambda *a, **k: p
    br._grab_image(m, "[图片]", "甲")
    chk("下载的文件用完就删（不然微信桌面一晚堆几百张）", not os.path.exists(p))

    print("\n④ 两张不同的图指纹不同（否则第二张被当重复，永远不处理）")
    a = ("甲", "", base64.b64encode(mk_png((255, 0, 0), (50, 50))).decode())
    b = ("甲", "", base64.b64encode(mk_png((0, 255, 0), (90, 90))).decode())
    chk("不同图 → 不同指纹", B.Bridge._fp(a) != B.Bridge._fp(b))
    chk("同一张图 → 同一指纹（不能每轮都当新的）", B.Bridge._fp(a) == B.Bridge._fp(a))
    chk("纯文字消息指纹跟以前一样（两元组也兼容）",
        B.Bridge._fp(("甲", "在吗")) == "甲|在吗", B.Bridge._fp(("甲", "在吗")))

    print("\n⑤ 超大图会缩（小图不该被破坏）")
    small = br._shrink.__func__(br, _write(mk_png((9, 9, 9)), "_zx_small.png"))
    chk("小图原样返回", bool(small) and len(small) > 0, {"len": len(small or b"")})
    big = mk_png((7, 7, 7), (3000, 3000))
    try:
        from PIL import Image
        has_pil = True
    except Exception:
        has_pil = False
    got = br._shrink.__func__(br, _write(big, "_zx_big.png"))
    if has_pil:
        chk("大图被缩到限额内", got is not None and len(got) <= br.IMG_MAX_BYTES,
            {"原": len(big), "缩后": len(got or b""), "限额": br.IMG_MAX_BYTES})
    else:
        chk("没 PIL 时大图宁可放弃也不硬发", got is None or len(got) <= br.IMG_MAX_BYTES)
    for f in ("_zx_small.png", "_zx_big.png"):
        pth = os.path.join(os.environ.get("TEMP", "."), f)
        if os.path.exists(pth):
            os.remove(pth)

    print("\n⑥ 图真的进了 /api/chat 的请求体（起假服务验）")
    got_body = {}
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            got_body.update(json.loads(self.rfile.read(n).decode("utf-8")))
            out = json.dumps({"reply": "这是只猫"}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out))); self.end_headers()
            self.wfile.write(out)
    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    bk = B.Brain.__new__(B.Brain)      # ask() 在 Brain 上，不在 Bridge
    bk.picked = "http://127.0.0.1:%d" % port
    bk.token = ""; bk.timeout = 10
    bk._opener = B.urllib.request.build_opener(B.urllib.request.ProxyHandler({}))
    bk._brain_errs = 0
    r = bk.ask("甲：（发来一张图）", base64.b64encode(mk_png((5, 5, 5))).decode())
    chk("拿到回复", r == "这是只猫", r)
    chk("img 字段在请求体里", bool(got_body.get("img")), {"字段": sorted(got_body.keys())})
    chk("src 仍是 wechat（不能被改掉）", got_body.get("src") == "wechat", got_body.get("src"))
    # 没有图的时候不能多发一个空 img 字段 —— 云端会拿 "" 去 b64decode
    got_body.clear()
    bk.ask("在吗")
    chk("无图时不发 img 字段", "img" not in got_body, {"字段": sorted(got_body.keys())})
    srv.shutdown()

    print("\n通过 %d / %d" % (PASS, PASS + FAIL))
    return 0 if FAIL == 0 else 1


def _write(data, name):
    p = os.path.join(os.environ.get("TEMP", "."), name)
    with open(p, "wb") as f:
        f.write(data)
    return p


if __name__ == "__main__":
    sys.exit(main())
