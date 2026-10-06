# -*- coding: utf-8 -*-
"""发言闸门：判断"这里真人会不会接话"，PASS 就不开口。

挂在 `on_turn_in` —— 唯一能一票否决的入站钩子：

    消息进来 → 本机快筛（没@就没戏）→ 本插件判定 → OPEN 才问大脑

位置很关键：它在"要不要问大脑"之前，不在"她怎么回答"里。所以人设里那条
"话头明显能接就接"可以整条删掉，判定用便宜小模型（temperature 0.3），
统计结果落地成数据。

温度必须压到 0.3：热采样下同一段对话跑两次可能一个 PASS 一个 OPEN，
群里表现就是"她说话没规律"。判定要的是稳定，不是创造力。

🔴 **给判据、不给场景映射**。「什么场景用什么方案」那种规则每加一个场景
就得补一条、永远补不完。所以只给判据和一份 PASS 清单，"明显能接"由她自己判断。
"""
import os
import re
import json
import time
import threading

NAME = "gate"
DESC = "发言闸门：拿不准该不该接话的时候，先判一下再开口"
# 声明开关：这是个可能误拦的判定层，出问题要能一键关掉退回旧行为。
FEATURE = "gate"

_TIMEOUT = 15                 # 秒。实测 flash 模型 1.8~2.0 秒，8 秒偶尔会网络抖动超时。
    # 超时不是错、是降级放行 —— 但放过多次统计就没意义了，所以给够。
    # 别设成 30：群里等 30 秒比不说话还假。
# 只要两个词，但**别把 max_tokens 掐到 5** —— 那样会把模型的思考过程截断在半路，
# 吐出 "PASSPASS" / "OPEN</think>" 这种半截输出，反而要多花一次去猜它想说什么。
# 给够空间，靠提示词要求简短（输出干净且同速）。
_MAX_TOKENS = 200
_CTX_CHARS = 1200# 塞给闸门的上下文上限

# 同一段对话里连着被判几次 PASS 就直接放行。防"她一直不接话"这种
# 越狱 —— 闸门误拦一次是运气，连着拦就说明她被关起来了。
#
# 🔴 写死 **3** 太密。群里绝大多数消息都不是找她的，于是基本每三条
# 她就会被强制放行一次 —— 而放行的那一句是在接**别人**的话题。日志形态：
#
#     [收到] 懒得起：@Echo 你玩不玩
#     [发送] OK '那是问Echo的，不是我。'
#
# 群里看着就是"牛头不对马嘴"。改成可配（gate.max_consecutive_pass），默认 8。
_MAX_CONSECUTIVE_PASS = 8


def _max_pass(cfg):
    """连着 PASS 几次就强制放行。config 的 `gate.max_consecutive_pass` 可调。

    给个下限 2：设成 1 等于"一次不接话就必须开口"，比默认还吵；
    非法值一律回落默认，别让一个写错的配置把她变成复读机。
    """
    try:
        v = int((cfg or {}).get("max_consecutive_pass"))
    except (TypeError, ValueError):
        return _MAX_CONSECUTIVE_PASS
    return v if v >= 2 else _MAX_CONSECUTIVE_PASS

# 统计按渠道分开存 —— 她在群里和App 里的"沉默率"完全是两回事，
# 混在一起看不出是哪边判错了。扁平 key 会让这个永远查不出来。
_STATS = {"wechat": {"open": 0, "pass": 0},
          "app": {"open": 0, "pass": 0},
          "other": {"open": 0, "pass": 0},
          "forced_open": 0}
_stats_lock = threading.Lock()
_pass_streak = {}            # 作用域名 -> 连着 PASS 了几次
_last_judge = {}             # 作用域名 -> 最近一次判定结果（给面板/调试看）


def _cfg():
    """闸门配置。读 config.json 的 gate 段。"""
    try:
        from runtime import CFG_PATH
        with open(CFG_PATH, encoding="utf-8") as f:
            return json.load(f).get("gate") or {}
    except Exception:
        return {}


def _model(api_config, cfg):
    """用哪个模型判定。没配就用主模型 —— 宁可用贵的模型，也不能不判。"""
    return str(cfg.get("model") or api_config.get("model") or "")


def _bump(src, field):
    """给某个渠道记一次 open/pass。"""
    try:
        with _stats_lock:
            key = src if src in _STATS else "other"
            _STATS.setdefault(key, {"open": 0, "pass": 0})
            _STATS[key][field] = _STATS[key].get(field, 0) + 1
    except Exception:
        pass


def _bump_global(field):
    """顶层计数（如forced_open），**不能**带渠道 —— 它不是某个渠道的量。

    跟 _bump 分开就是为了防止再出现"往渠道 dict 里塞了全局字段"那种错：
    那样查wechat 的数据里会混进forced_open，看着像她频繁被强制放行。
    """
    try:
        with _stats_lock:
            _STATS[field] = _STATS.get(field, 0) + 1
    except Exception:
        pass


def _scope(ctx):
    """判定的作用域。同一群是同一个闸门，不同群各自计连PASS 次数。

    按 (渠道, 人设) 分 —— 换人设就换一套人，落成一个人的连续判定会串味。

    🔴 **只取人设的 key，绝不能取 brain.persona 那个 dict**。
    brain.persona 是完整人设（几千字），str() 出来就是它的 repr ——
    于是 `_pass_streak` / `_last_judge` 的键变成一整段人设文本：
    连着PASS 的计数按"人设全文"分组，等于每次判定都是新的一组、
    连PASS 保护永远不触发；而 _last_judge 会把整个 dict 塞进 /api/gate/stats
    的返回值（几百字），面板直接被撑爆。
    取法用 persona_key_of(src)，那是 Brain 专门给的只读入口。
    """
    src = str(getattr(ctx, "src", "") or "app")
    persona = ""
    try:
        p = getattr(ctx, "brain", None)
        if p is not None:
            f = getattr(p, "persona_key_of", None)
            if callable(f):
                persona = str(f(src) or "")
            if not persona:
                # 没有这个方法就退回旧属性，但只接受 str ——
                # brain.persona 是 dict，拼进去就是上面那个坑。
                v = getattr(p, "persona_key", "")
                persona = str(v or "") if isinstance(v, str) else ""
    except Exception:
        pass
    return "%s.%s" % (src, persona)


def _recent_context(ctx):
    """闸门看的"最近在聊什么"。

    刻意只取最近几条：闸门要判断的是**这一刻**，不需要翻历史。
    它不是记忆检索，翻多了既慢又容易让它以为该回忆旧账。
    """
    brain = getattr(ctx, "brain", None)
    if brain is None:
        return ""
    for attr in ("recent_msgs", "_recent", "last_msgs"):
        v = getattr(brain, attr, None)
        if isinstance(v, (list, tuple)) and v:
            lines = []
            for m in list(v)[-8:]:
                try:
                    who = m.get("role") or m.get("sender") or "?"
                    body = str(m.get("content") or m.get("text") or "").strip()
                    if body:
                        lines.append("%s: %s" % (who, body[:120]))
                except Exception:
                    continue
            if lines:
                return "\n".join(lines)[-_CTX_CHARS:]
    return ""


def _nicknames(ctx):
    """她能被叫的名字：配置 + 人设里的 nicknames。

    两个来源都要。人设那份跟着换人设变（人格 A→人格 B），只看配置会漏
    —— 而漏了就意味着被叫了也不回，那是最明显的 bug。
    拿不到就算，宁可多问一次模型。

    🔴 人设那份必须用 **persona_key_of(src)** 取，不能getattr(b, "persona_key")。
    Brain 上没有 persona_key 这个属性（只有 persona_key_of 方法），
    getattr 恒为 "" → 人设那份永远读不到 → 配置里叫什么她就被叫什么。
    更糟的是这一直是"看起来能用"的状态：配置那份刚好有名字，
    所以被叫名字照样有反应，只有换人设改昵称时才暴露。

    顺带去重：两个来源重叠是常态（配置和人设都写了同一个名字），
    重复项在 _called_by_name 里会白比一遍，无害但没必要。
    """
    out = []
    try:
        c = _cfg()
        for k in ("bot_nicknames", "nicknames"):
            out.extend(c.get(k) or [])
    except Exception:
        pass
    try:
        b = getattr(ctx, "brain", None)
        key = ""
        if b is not None:
            f = getattr(b, "persona_key_of", None)
            if callable(f):
                key = str(f(getattr(ctx, "src", "") or "app") or "")
        if key:
            import persona_store
            data = persona_store.load(key) or {}
            out.extend(data.get("nicknames") or [])
    except Exception:
        pass
    seen = set()
    res = []
    for n in out:
        s = str(n or "").strip()
        if s and s not in seen:
            seen.add(s)
            res.append(s)
    return res


def _called_by_name(ctx, msg):
    """这条消息里有没有人喊她。

    **代码层判，不问模型。** 见 _judge 里的说明：这条判错的后果最严重
    （被@ 却不回），而提示词里写"被叫了就OPEN"小模型不认。
    """
    t = str(msg or "")
    if not t:
        return False
    for n in _nicknames(ctx):
        if len(n) < 2:# 太短的词（单字）容易误命中，宁可漏
            continue
        if ("@" + n) in t or n in t:
            return True
    return False


# 微信的 @ 是正文里的"@名字"。要求 @ 前面是行首/空白/中文标点，
# 免得把邮箱、链接里的 @（a@b.com）当成点名。
_AT_RE = re.compile(r"(?:^|[\s：:，,（(])@([^\s]+)")
# @所有人 也包含她，不能当成"在叫别人"
_AT_ALL = ("所有人", "全体成员", "all")


def _at_names(msg):
    """这条消息里 @ 了谁。返回被点到的名字列表（去重、保持顺序）。"""
    out = []
    for m in _AT_RE.finditer(str(msg or "")):
        n = (m.group(1) or "").strip()
        if n and n not in out:
            out.append(n)
    return out


def _called_someone_else(ctx, msg):
    """这条消息是不是在 @ **别人**（不是她）。

    ## 为什么这条必须在代码层判

    跟 `_called_by_name` 完全对称，理由也一样：**提示词里写清楚了，小模型不认。**
    `_JUDGE_PROMPT` 第一条确实写着"两个人在对话 → PASS"，但它的条件是
    "其中一个在 @ 另一个、**另一个在回他**"。群里更常见的却是

        @Echo 你玩不玩          ← @ 了别人，但对方还没回

    这一句不满足"另一个在回他"，于是掉进第四条「剩下的都是 OPEN」——
    而第四条里正好写着「提了个问题 → OPEN」。判成 OPEN，她就把**问别人的问题**
    当成问自己的答了。实测（2026-10-06 群日志）：

        [收到] 懒得起：@Echo 你玩不玩  -> 她："那是问Echo的，不是我。"
        [收到] 懒得起：@。 你玩不玩     -> 她："还是问别人的，我可没接这个茬。"
        [收到] 懒得起：@Echo           -> 她："他们聊他们的，我玩我的。"

    她自己清楚不是在问她，但闸门放行了就总得说点什么 —— 群里看着就是
    "牛头不对马嘴"。判"这条不是在跟我说"是纯字符串匹配，零成本、不会抖，
    没有任何理由交给模型。

    ## 没 @ 任何人的消息不归它管

    只处理"有人 @ 了谁"的情形。既没人 @、也没提她名字的普通消息，
    还是交给模型判该不该接 —— 那才是"像不像真人"的部分。
    """
    names = _at_names(msg)
    if not names:
        return False
    mine = _nicknames(ctx)
    for name in names:
        if name in _AT_ALL:
            return False                    # @所有人 包括她，交给模型
        for n in mine:
            if n and (name.startswith(n) or n in name):
                return False                # @ 的人里有她 -> 不是"只叫了别人"
    return True


def _self_share(ctx, lookback=6):
    """最近几条消息里她自己的占比，0.0 ~ 1.0。

    刷屏抑制的输入。取的是**她刚说得多不多**，不是"该不该回"——
    后者由模型判，前者是纯统计、零成本、不会抖。

    认不出的role 一律不算她的（宁可低估自己的占比，也别高估——
    高估会让她变得过度沉默，比刷屏更糟）。
    """
    brain = getattr(ctx, "brain", None)
    if brain is None:
        return 0.0
    for attr in ("recent_msgs", "_recent", "last_msgs"):
        v = getattr(brain, attr, None)
        if not isinstance(v, (list, tuple)) or not v:
            continue
        rows = list(v)[-lookback:]
        mine = 0
        total = 0
        for m in rows:
            try:
                who = str(m.get("role") or m.get("sender") or "").lower()
                body = str(m.get("content") or m.get("text") or "").strip()
            except Exception:
                continue
            if not body:
                continue
            total += 1
            # assistant / self / bot / 她的昵称 都算她自己写的
            if who in ("assistant", "self", "bot", "ai", "me"):
                mine += 1
                continue
            if any(n and n in who for n in _nicknames(ctx)):
                mine += 1
        if total:
            return float(mine) / float(total)
    return 0.0


def _last_self_ago(ctx):
    """她上一次发言距今多少秒。算不出来就返回 None。

    🔴 **不要用 ctx.last_reply_at —— Turn 里没有这个字段。** Turn 只有
    text / src / api_config / brain / meta 五个字段，meta 还是同轮内传递的，
    跨轮不可靠。硬取会永远拿到 None，于是这条判断等于没写（这种"看着
    实现了其实没生效"最难查）。

    所以改成读**聊天存档**：她自己的每一句都落在按人设×渠道分区的文件里，
    取最后一条的时间戳即可。读不到就返回 None，让调用方降级到只用占比。
    """
    import os
    import time as _t
    try:
        brain = getattr(ctx, "brain", None)
        if brain is None:
            return None
        p = brain._log_for(getattr(ctx, "src", "app"))   # 已按人设×渠道分区
        if not p or not os.path.isfile(p):
            return None
        rows = []
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        if not rows:
            return None
        last = rows[-1]
        if str(last.get("role") or "") not in ("assistant", "self"):
            return None            # 最后一条是别人说的，间隔无从算起
        ts = last.get("ts") or last.get("time") or last.get("timestamp")
        if not ts:
            return None
        # ts 可能是秒级或毫秒级；太大就是毫秒
        ts = float(ts)
        if ts > 1e11:
            ts /= 1000.0
        if ts > 1e9 + 5e8:         # 明显是毫秒（2033 年之后）
            ts /= 1000.0
        return max(0.0, _t.time() - ts)
    except Exception:
        return None


# 刷屏抑制阈值。**先拍一个保守值，等有真实数据再调** ——
# MaiBot 那个 +2.65 是从几百轮真实记录拟合出来的，我们没有那些数据，
# 硬套它的数会误判。宁可漏判（刷屏）也不误判（该说不说）。
#
# 三档而不是一档：人的刷屏感是渐进的，一次说 2 句不刺眼，连着 4 句就刺眼。
_SUPPRESS_AT = {        # 自己的占比 >= 这个值 -> 拦
    0.50: "连着说",
    0.34: "说得多",
    0.20: "刚说过",
}
# 距离上次发言多久之内也算"刚说过"（秒）。真人不会隔 5 秒再补一句。
_JUST_SAID_SECS = 90


def _flood_guard(ctx):
    """刷屏抑制。返回 None 放行，返回原因字符串则拦下。

    **纯代码，不问模型。** 两个理由：一是省一次调用（闸门本来就 2 秒），
    二是这条判错的后果是"该说的没说"，比刷屏更明显 —— 让小模型算这种
    可数的事是不必要的风险。

    被 @ 的**不走这里**（在 _judge 里更早放行了）：有人点名还沉默，比刷屏
    难看一百倍。
    """
    try:
        cfg = _cfg().get("flood") or {}
    except Exception:
        cfg = {}

    # 🔴 默认必须是开的 —— 配错一项就整个失效，那是白做。
    enabled = cfg.get("enabled", True)
    if not enabled:
        return None

    share = _self_share(ctx)
    gap = _last_self_ago(ctx)

    # 🔴 **时间信号是主判据，占比只是佐证。**
    # 第一版反着写了（占比到了就拦，读不到时间也拦），测试立刻抓出两个 case：
    # 存档读不到、以及最后一条是别人说的话时，她会因为"最近 6 句里她占一半"
    # 而闭嘴 —— 但这两种情况下"她刚说过"根本无从判断，拦她是瞎拦。
    # 所以：拿不到 gap 就一律放行，宁可漏判也不误判。
    if gap is None:
        return None

    for thr, label in sorted(_SUPPRESS_AT.items(), reverse=True):
        if share < thr:
            break
        # 占比高但隔了很久才回—— 那是"过了一阵才想接话"，不是刷屏。
        # 只在最高那档才不看时间：连着说 4 句就算隔几分钟也刺眼。
        if gap >= _JUST_SAID_SECS and thr < 0.50:
            return None
        return "%s（自己占 %d%%、距上次 %d 秒）" % (
            label, int(share * 100), int(gap))
    return None


def _recent_msgs(ctx):
    brain = getattr(ctx, "brain", None)
    if brain is None:
        return []
    for attr in ("recent_msgs", "_recent", "last_msgs"):
        v = getattr(brain, attr, None)
        if isinstance(v, (list, tuple)) and v:
            return list(v)
    return []


_JUDGE_PROMPT = """你在替一个人判断：群里的最新一条消息，如果她本人在这里，\
她会接话吗？

只回一个词：OPEN 或 PASS

（有人喊她名字的情况已经在代码里放行了，走不到你这里。）

## 一、这条消息是在跟谁说的？

**只要有人 @ 了别人（不是她），就是在跟别人说话 → PASS。**
不管对方回没回，也不管那句话看着像不像个问句 —— 那不是问她的。
（"有人在 @ 别人"这种情况多半已经在代码里被拦掉了，走不到你这里；
真走到这里，按这条判。）

两个人在一来一回地聊，更是在聊他们自己的 → PASS

### 二、他现在的处境是什么？（**决定分寸，别跳过**）

同一句负面的话要分两种，区别在于**说的是处境还是段子**：

| 他说的 | 处境 | 判定 |
|---|---|---|
| 今天被裁了，晚上去哪儿都挺没意思的 | 难受 | **PASS** 安静待着，插科打诨很讨打 |
| 说实话有点想辞职了 | 难受 | **PASS** |
| 搬了一下午砖，累麻了 | 累 | **PASS** |
| 说今天什么都不想做，谁也不想见 | 难受 | **PASS** |
| 这破程序又崩了，它是不是跟我有仇 | 段子 | **OPEN** 好接 |
| 我把闹钟设成震动，然后睡了十小时 | 段子 | **OPEN** |
| 我把生产库密码改成自己生日，昨天忘了 | 段子 | **OPEN** |
| 睡过头了，上班迟到了哈哈 | 段子 | **OPEN** |

分不清的时候当段子（宁可接，也不要在他难受的时候插嘴）。

### 三、这几类一律 PASS

- 客套收尾：哦 / 好的 / 行 / 睡了 / 晚安 / 回头聊
- 碎片消息：单个字母、乱码、纯标点、图片
- 同一个人还在连着发，最后一条明显没说完（"就是那个"、"然后…"）
- 同一个梗她已经接过两次了，接第三次就是复读
- 话题转到很技术的细节上，跟她没关系

### 四、剩下的都是 OPEN

分享好消息、提了个问题、说了件离谱的事、抛出个梗、说了句蠢话、
她刚冷过场想找个话头。**别因为"我也不确定"就 PASS** —— 你不是要完美
判断，是要像个真人。真人在群里也会多嘴一句。

真的判断不出来（信息太少、看不出在说什么），就 PASS。"""


def _judge(ctx, cfg):
    """问便宜模型一次。返回 (should_speak, reason)。任何异常都放行。"""
    api = getattr(ctx, "api_config", None) or {}
    if not api.get("api_key") or not api.get("api_base"):
        return True, "没有 api 配置"
    msg = str(getattr(ctx, "text", "") or "").strip()
    if not msg:
        return True, "空消息"
    if not _model(api, cfg):
        return True, "没配模型"

    #🔴 **被叫名字必须在代码层直接放行，不许问模型。**
    # 失效方式：提示词里写了「@ 了就回 OPEN、不用往下想」，模型照样
    # 判 PASS —— 小模型不吃这种"软指令"，思考两句就把它推翻了。
    # 而这条判错的后果是"她被@了却不回"，是最明显的不像人。
    # 关键词匹配在代码层做一次，零成本、不出错、不会抖。
    if _called_by_name(ctx, msg):
        return True, "有人喊了名字"

        # 🔴 刷屏抑制必须放在被@ 放行之后、问模型之前。
    # 顺序很要紧：被叫了名字必须无条件说话，别的都不管。
    why = _flood_guard(ctx)
    if why:
        return False, "刷屏抑制：" + why

    body = "最近在聊：\n%s\n\n最新一条：\n%s" % (
        _recent_context(ctx) or "（没历史）", msg[:600])

    try:
        import httpx
        r = httpx.post(
            api["api_base"].rstrip("/") + "/chat/completions",
            headers={"Authorization": "Bearer " + api["api_key"]},
            json={"model": _model(api, cfg),
                  "messages": [
                      {"role": "system", "content": _JUDGE_PROMPT},
                      {"role": "user", "content": body}],
                  # 判定要稳定，不要创造力 —— 见文件头
                  "temperature": 0.3,
                  "max_tokens": _MAX_TOKENS},
            timeout=_TIMEOUT)
        r.raise_for_status()
        out = str(r.json()["choices"][0]["message"]["content"] or "").strip()
        # 取**最后**一个词，不是第一个。模型有时会先写一段再给结论，
        # 或者吐出 "PASSPASS" 这种重复 —— 最后一个才是它真正的答案。
        # 用 search 会让 "……所以 OPEN" 判成沉默，而她本该开口。
        hits = re.findall(r"PASS|OPEN", out.upper())
        if not hits:
            return True, "看不懂：" + out[:20]
        return (hits[-1] == "OPEN"), (hits[-1] + " | " + out[:20])
    except Exception as e:
        return True, "判不了：%s" % (str(e)[:60])


def on_turn_in(ctx):
    """入站闸门。要拦就调`ctx.veto(原因)`，别用返回值表达否决。

    ## 为什么不是"返回空串"

    空串在这条链路上没有信息量：`reminder` 插件"没有提醒要注入"返回空串，
    "所有插件都放行"时 `join([])` 也是空串。拿它当否决信号时，
    "她一句话都说不出来"这件事**每个插件单独看都正常**。
    所以改成显式声明 —— 见 `Turn.veto`。

    放行时返回 None：没有提示要注入，也不用否决。
    """
    msg = str(getattr(ctx, "text", "") or "").strip()
    if not msg:
        return None
    src = str(getattr(ctx, "src", "") or "app")
    cfg = _cfg()

    if not cfg.get("enabled", False):
        _bump(src, "open")
        return None
    # 群里才需要闸门；App 是一对一，她本来就该回。
    if src not in (cfg.get("sources") or ["wechat"]):
        _bump(src, "open")
        return None
    # 太短的（"嗯""?"）判不出东西，别为这个花一次调用
    if len(msg) < 2:
        return None

    scope = _scope(ctx)

    # 🔴 **@ 的是别人：直接拦，而且不花模型调用、不计入强制放行。**
    #
    # 两个"不计入"都是有意的：
    #   * 不花调用 —— 纯字符串匹配能定的事，没必要问模型（省一次 flash 调用）；
    #   * **不计入 `_pass_streak`** —— 这条是"确定不是在跟她说话"，不是
    #     "闸门拿不准所以先拦一下"。后者连着几次就该强制放行（怕她被关起来），
    #     但前者是事实，强制放行会把它推翻成"她偏要接一句别人的话题"，
    #     而那正是群里"牛头不对马嘴"的来源。
    if _called_someone_else(ctx, msg):
        _bump(src, "pass")
        _last_judge[scope] = "PASS · 在@别人"
        return ctx.veto("闸门判定 PASS：这条在 @ 别人")

    # 连着 PASS 太多次就强制放行。她不是不能说话，是闸门误判了。
    try:
        if _pass_streak.get(scope, 0) >= _max_pass(cfg):
            _pass_streak[scope] = 0
            _bump_global("forced_open")
            _last_judge[scope] = "强制放行（连PASS 太多次）"
            return None
    except Exception:
        pass

    # 判定本身必须包 try —— 闸门炸了就等于她哑掉，比判错严重得多。
    # 出错一律放行，永远不拦。
    try:
        ok, reason = _judge(ctx, cfg)
    except Exception as e:
        ok, reason = True, "判定出错，放行：%s" % (str(e)[:60])
    try:
        _last_judge[scope] = ("OPEN" if ok else "PASS") + " · " + str(reason)
        if ok:
            _pass_streak[scope] = 0
        else:
            _pass_streak[scope] = _pass_streak.get(scope, 0) + 1
    except Exception:
        pass

    if ok:
        _bump(src, "open")
        return None
    _bump(src, "pass")
    # 拦。ctx.veto() 是显式否决，不是靠返回值 —— 理由见Turn.veto。
    return ctx.veto("闸门判定 PASS：" + str(reason)[:80])


# --- 给面板/调试看的 ---

def stats():
    return dict(_STATS)


def last_verdict(scope=None):
    return _last_judge.get(scope or "", "")


def _r_stats(body, query):
    """看板：她判了多少次、放行/拦下各多少、最近一次判定是什么。

    只读，不改任何状态。
    """
    scope = str(query.get("scope") or "")
    return {"ok": True, "stats": stats(),
            "streak": dict(_pass_streak),
            "last": _last_judge.get(scope, "") if scope else dict(_last_judge)}


# 路由只有 POST：core 的 GET 分派器也是拿这张表接的，别混两种方法
ROUTES = [("POST", "/api/gate/stats", _r_stats)]