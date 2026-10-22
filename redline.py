"""红线自检。写完一章先过这里，P0 清零才准往下走。

跟 skill 里那几个脚本的分工：
  novel_check.py  = 成稿交付前的大扫（24 项）
  redline.py      = 生成器的闸门，只查"这一章能不能发出去"的硬项，
                    而且**能自动打回重写**——这是生成器闭环里的一环。
"""

import re
from typing import Any, Dict, List, Tuple

from rules import (BAN_WORDS, CLICHE, CV_MIN, DIALOG_MIN, MIN_CHARS, P0, P1,
                   SENT_AVG_MAX, SENT_AVG_MIN, SHORT_ANSWER, SHORT_SENT_MAX,
                   TEMPLATE_DENSITY, TEMPLATE_PATTERNS)

_SENT = re.compile(r"[^。！？…\n]+[。！？…]?")
_REAL = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")

# ★ 中文数字必须带量词／单位才算"具体的数"。
#   以前是 [零一二三四五六七八九十百千万两] 单字就命中——而"一"是汉语
#   最高频字（一个、一下、一起）⇒ "全章没有一个数字"这条 P0 几乎永不
#   触发，形同虚设。现在：数字串后面必须跟量词，或"第N"序数。
_CJK_NUM = re.compile(
    r"(?:[零一二三四五六七八九十百千万两半]+"
    r"[个只条件条块件张台部段落章层片位名口声趟遍顿场轮招式拳脚刀剑"
    r"钱两斤里尺寸亩顷年日月天号点岁成折倍分秒时刻更文贯圈局盘碗杯"
    r"袋包箱车匹头串挂顶双副对组下次]"
    r"|第[零一二三四五六七八九十百千]+)")


def sentences(text: str, min_len: int = 1) -> List[str]:
    """分句。只按句末标点切，引号内的对话也算一句——人说话天生短，
    那正是人味，不该被当成破绽切掉。

    ★ min_len 默认 1，不是 2。rules.py 要求"有 4 个字的碎句"，
      而 CV 统计原来把 1 字句全砍了——碎句恰恰是句长方差的主要来源，
      砍掉它们 CV 系统性偏高，"该拦的判成过"。

    ★★ 但 min_len=1 带进来一个副作用：句末标点之后的右引号（”）会被
      切成 1 字的"句子"。实测第 3 章：17% 的"短句"里有 6 个是这种引号碎片，
      它们既不是句子也不该进 CV。⇒ 按"有没有实字符"剔，不按长度剔。
    """
    out = []
    for s in _SENT.findall(text):
        s = s.strip()
        if not s or not _REAL.search(s):   # 纯标点碎片不是句子
            continue
        if len(s) >= min_len:
            out.append(s)
    return out


def cv(vals: List[float]) -> float:
    """变异系数。★ 均匀才是机器指纹，短句不是。"""
    if len(vals) < 4:
        return 0.0
    m = sum(vals) / len(vals)
    if m == 0:
        return 0.0
    var = sum((v - m) ** 2 for v in vals) / len(vals)
    return var ** 0.5 / m


# ★ markdown 排版符不算字数。以前 # ** --- ``` 全计进"字数"，
#   一章 2000 字的稿能报出 2050，密度类指标（每千字）全跟着假。
_MD_LINE = re.compile(r"(?m)^[ \t]*(?:#{1,6}[ \t]|>[ \t]?|-{3,}[ \t]*$"
                      r"|\*{3,}[ \t]*$|`{3,}.*$)")


def bare(text: str) -> str:
    t = _MD_LINE.sub("", text)
    t = re.sub(r"\*\*|__|~~|`", "", t)     # 行内强调／代码标记
    return re.sub(r"\s+", "", t)


def _dlg_chars(body: str) -> int:
    """引号内的正文字数（去空白）。不成对的引号自然匹配不上，不计。"""
    tot = 0
    for pat in (r"“([^”\n]*)”", r"「([^」\n]*)」", r'"([^"\n]*)"'):
        for m in re.finditer(pat, body):
            tot += len(bare(m.group(1)))
    return tot


def newinfo(text: str) -> Dict[str, List[str]]:
    """抠出正文末尾那行 `<!-- new: ... -->`。"""
    m = re.search(r"<!--\s*new:\s*(.*?)-->", text, re.S)
    out: Dict[str, List[str]] = {}
    if not m:
        return out
    for seg in re.split(r"[|｜]", m.group(1)):
        if "=" not in seg:
            continue
        k, v = seg.split("=", 1)
        items = [x.strip() for x in re.split(r"[,，、]", v) if x.strip() and x.strip() != "无"]
        if items:
            out[k.strip()] = items
    return out


def check(text: str, first_lines: List[str] = None, cv_min: float = None,
          corpus: set = None) -> Dict[str, Any]:
    """返回 {"issues": [(级别, 问题, 怎么改)], "stats": {...}}"""
    first_lines = first_lines or []
    issues: List[Tuple[str, str, str]] = []
    # ★ 只删**整行**的注释标注。用 <!--.*?--> 跨行匹配会出大事：
    #   模型常在开头漏写一个 <!-- ，末尾又写了 --> ，结果中间整章正文
    #   被当成注释删掉——实测一章 1831 字被算成 16 字，红线全乱。
    body = re.sub(r"(?m)^[^\n]*<!--[^\n]*-->[^\n]*$", "", text)
    b = bare(body)
    n = len(b)

    # 1 字数
    if n < MIN_CHARS:
        issues.append((P0, f"正文 {n} 字，不足 {MIN_CHARS}",
                       "补一段有因果的戏，不许靠拆行和注水凑"))

    # 2 禁用词（0 容忍）
    for w in BAN_WORDS:
        c = body.count(w)
        if c:
            issues.append((P1, f"禁用词「{w}」{c} 次",
                           "那是视角越界：改成看得见的动作"))

    # 3 套话
    for w in CLICHE:
        c = body.count(w)
        if c:
            issues.append((P1, f"套话「{w}」{c} 次", "删掉，换成一个具体动作"))

    # 4 "有的…有的…"
    c = len(re.findall(r"有的[^，。！？]{1,12}[，、][^。！？]{0,20}有的", body))
    if c:
        issues.append((P1, f"「有的…有的…」句式 {c} 处", "AI 味高频句式，改成交锋或删"))

    # 5 短应答密度
    cnt = sum(body.count(s) for s in SHORT_ANSWER)
    if cnt > 3:
        issues.append((P1, f"短应答 {cnt} 次（单章 ≤3）",
                       "连续出现时第二个必须改写或直接删"))

    # 6 模板句密度（每千字）。★ 以前这项拿短应答计数（cnt）充数——
    #    同一个数既判"短应答"又判"模板句"，README 里
    #    "模板句密度 ≤8.0/千字"这条其实根本不存在。现在各自归位：
    #    短应答论次数（第 5 项），模板句论公式化框架的密度（这里）。
    tpl = sum(len(re.findall(p, body)) for p in TEMPLATE_PATTERNS)
    per_k = tpl * 1000.0 / max(n, 1)
    if per_k > TEMPLATE_DENSITY:
        issues.append((P1, f"模板句密度 {per_k:.1f}/千字（上限 {TEMPLATE_DENSITY}）",
                       "同一框架一章里别超过七八处：换主语／换动词／删"))

    # 7 相邻段前 4 字重复
    paras = [p.strip() for p in body.split("\n") if p.strip()]
    dup = sum(1 for i in range(1, len(paras))
              if paras[i][:4] == paras[i - 1][:4] and len(paras[i]) > 4)
    if paras and dup / len(paras) > 0.03:
        issues.append((P1, f"相邻段同前 4 字 {dup} 处（>3%）", "改开头，别让两段站成一排"))

    # 8 必须有数字（无数字的章＝0 容忍）
    has_num = bool(re.search(r"\d", body)) or bool(_CJK_NUM.search(body))
    if not has_num:
        issues.append((P0, "全章没有一个数字",
                       "补一个具体的数：钱数、天数、件数、人数"))

    # 9 分隔行
    if re.search(r"^\s*---+\s*$", text, re.M):
        issues.append((P1, "有 --- 分隔行", "删掉；不清的话字数和密度全是假数"))

    # 10 标点成对
    for l, r in (("“", "”"), ("（", "）"), ("《", "》")):
        if body.count(l) != body.count(r):
            issues.append((P1, f"{l}{r} 不成对（{body.count(l)}/{body.count(r)}）", "补齐"))

    # 11 章首句复用
    head = (paras[0][:20] if paras else "")
    if head and any(head == h[:20] for h in first_lines):
        issues.append((P0, "章首句跟前面某章撞了", "换切入角度，从动作或对白中间进"))

    # 12 机器指纹：句长方差
    lens = [len(s) for s in sentences(body)]
    v = cv(lens)
    lim = CV_MIN if cv_min is None else cv_min
    if lens and v < lim:
        issues.append((P1, f"句长变异系数 {v:.2f}（下限 {lim}）",
                       "开篇 300 字内插一句 ≤4 字碎句或一句 ≥45 字长句；留白要扎堆"))

    # 12b ★ 句均长度。★ 只看 CV 不够：CV 高也可能是"满篇短句 + 偶尔一句长的"，
    #      方差合格但均值塌到 14，读者读到的是蹦豆子，同样是机器指纹。
    if lens:
        avg = sum(lens) / len(lens)
        if avg < SENT_AVG_MIN:
            issues.append((P1, f"句均 {avg:.1f} 字（下限 {SENT_AVG_MIN}）",
                           "把相邻短句并进长句，让一句背得住一个完整动作"))
        elif avg > SENT_AVG_MAX:
            issues.append((P1, f"句均 {avg:.1f} 字（上限 {SENT_AVG_MAX}）",
                           "拆：一句只留一个动作"))

    # 12c ★ 短句占比。判据：短句要扎堆用在交锋处，散布全篇就是碎。
    _short = sum(1 for x in lens if x <= 7)
    if lens and _short / len(lens) > SHORT_SENT_MAX:
        issues.append((P1, f"短句占比 {_short / len(lens) * 100:.0f}%"
                            f"（上限 {SHORT_SENT_MAX * 100:.0f}%）",
                       "短句收拢到对话和转折处，其余并进长句"))

    # 12d ★ 对话占比下限。他手写的章对话也不多，但**静态观察必须改成交锋**——
    #      嘴问出来的东西有对手，有对手就有信息。
    _dlg = _dlg_chars(body) * 100.0 / max(n, 1)
    if _dlg < DIALOG_MIN:
        issues.append((P1, f"对话占比 {_dlg:.1f}%（下限 {DIALOG_MIN}）",
                       "把一处静态观察改成一次交锋"))

    # 13 章末是不是在总结
    tail = "\n".join(paras[-3:]) if paras else ""
    if re.search(r"(这就是|从此|他知道那一刻|不由|心中|感慨|明白了一个道理)", tail):
        issues.append((P1, "章末像在总结或抒情", "换成给东西：问题／没开的门／没说的话／变了的数字"))

    # 13b ★ 车轱辘话：同一个长句在正文里出现三次以上
    #   这是小模型最常见的烂法——它不会推进，就把刚写过的那句换个位置再说一遍。
    #   人读三行就烦，所以这条必须能自动抓出来打回，不能靠肉眼。
    seen: Dict[str, int] = {}
    for p in paras:
        # ★ 别用 n 当循环变量——外面 n 是"字数"，会被覆盖（实测字数被算成 16）
        for L in (12, 16):
            for i in range(0, max(0, len(p) - L + 1)):
                seen[p[i:i + L]] = seen.get(p[i:i + L], 0) + 1
    hot = sorted([(c, s) for s, c in seen.items() if c >= 3 and len(s) >= 12],
                 reverse=True)
    if hot:
        top = hot[0][1]
        # ★ 2026-10-22 从 P1 提到 P0：同一句重复三次以上是最典型的机器灌水，
        #   读者一翻就跑，不该只给警告。全书 60 章实测 0 章命中 ⇒ 提级不会误伤。
        issues.append((P0, f"车轱辘话：『{top[:14]}…』出现 {hot[0][0]} 次",
                       "删到只留一处，其余换成推进：他做了什么、对方回了什么"))

    # 13c 原地打圈：一件事反复撞同一堵墙。
    #   ★ 2026-10-22 修误报：原判据把"还是/依旧/仍然"按词频直接计数，
    #     但中文里这三个 90% 是"仍然是"或"是A还是B"的选择疑问，跟"又来一次"
    #     毫无关系——实测第 3 章因此被误判 6 次命中，那六处全是"仍然是"。
    #   ⇒ 真正的重复信号只有两类：① 试图/尝试/再次/重新 ② "还是没/还是不/还是又"。
    loop = 0
    for w in ("试图", "尝试", "再次", "重新"):
        loop += body.count(w)
    loop += len(re.findall(r"还是(?:没|不|又)", body))
    if loop >= 5:
        issues.append((P0, f"原地打圈：『试图/尝试/再次/还是没』类词 {loop} 次",
                       "一件事最多试一次就换招，别让它反复撞同一堵墙"))

    # 14 跟语料的重合：模仿和抄袭的分界
    if corpus:
        ov = overlap4(body, corpus)
        if ov > 0.01:
            issues.append((P0, f"跟语料 8-gram 重合 {ov * 100:.2f}%（>1%）",
                           "这是抄不是模仿：重写这几处，别照搬原句"))
        elif ov > 0.003:
            issues.append((P1, f"跟语料 8-gram 重合 {ov * 100:.2f}%",
                           "盯一下，别整句照搬"))

    # 15 新信息标注
    info = newinfo(text)
    if not info:
        issues.append((P1, "没写 <!-- new: --> 标注",
                       "让模型在末尾标出本章新增的人、地、数、物"))

    return {
        "issues": issues,
        "stats": {
            "字数": n, "段落": len(paras), "句数": len(lens),
            "句长均值": round(sum(lens) / len(lens), 1) if lens else 0,
            "变异系数": round(v, 2),
            # ★ 以前是 (引号数 + 直引号/2) * 2 / 总字数——量出来的是
            #   "引号密度"不是"对话占比"，那个 *2 没有任何量纲依据，
            #   数值不可解释（只能相对比）。现在量引号里的正文字数，
            #   绝对含义：这一章有百分之多少的字是在对话里。
            "对话占比": round(_dlg_chars(body) * 100.0 / max(n, 1), 1),
            "新信息": info,
        },
    }


def overlap4(text: str, corpus: set, n: int = 8) -> float:
    """跟语料的 n-gram 重合率。模仿和抄袭的分界线。

    ★ n 取多少是量出来的，不是拍的。实测（语料＝128 万字的《你欠我几天》）：
        n=4  → 完全无关的另一本书撞出 10.08%，生成稿 12.6% ⇒ 误报
        n=8  → 无关书 0.11%，生成稿 0.11%，语料自身片段 100% ⇒ 分得开
      n=4 在百万字语料上等价于"常见四字词组查重"，谁都撞。
      判"抄"要连续 8 字以上相同才成立。
    """
    if not corpus:
        return 0.0
    t = bare(text)
    g = {t[i:i + n] for i in range(max(0, len(t) - n + 1))}
    return len(g & corpus) / len(g) if g else 0.0


def overlap_sentences(body: str, corpus: set, topn: int = 5,
                      n: int = 8) -> List[Tuple[float, str]]:
    """哪几句抄得最多。★ 光跟模型说"别照搬"它改不了，得点名。

    ★ n 跟 overlap4 的主判据保持同一把尺（8）。以前这里用 4-gram
      挑句子，而 overlap4 的注释自己论证了 4-gram 在百万字语料上
      必然误报 10% ⇒ 挑出来的"抄袭句"一半是常见四字词组，
      喂给模型让它"逐句改写"，越改越糟。
    """
    out = []
    for s in sentences(body):
        t = bare(s)
        if len(t) < n:
            continue
        g = {t[i:i + n] for i in range(len(t) - n + 1)}
        if g:
            out.append((len(g & corpus) / len(g), s))
    out.sort(key=lambda x: -x[0])
    return out[:topn]


def banned_hits(body: str) -> List[str]:
    """把含禁用词的句子挑出来。定向修补用得着。"""
    out = []
    for s in sentences(body):
        if any(w in s for w in BAN_WORDS):
            out.append(s)
    return out


def report(res: Dict[str, Any]) -> str:
    st = res["stats"]
    lines = [f"字数 {st['字数']} · 段 {st['段落']} · 句 {st['句数']} · "
             f"句长均值 {st['句长均值']} · CV {st['变异系数']} · "
             f"对话 {st['对话占比']}%"]
    p0 = [i for i in res["issues"] if i[0] == P0]
    p1 = [i for i in res["issues"] if i[0] == P1]
    if not p0 and not p1:
        lines.append("  ✔ 红线全过")
        return "\n".join(lines)
    for lv, msg, how in p0:
        lines.append(f"  ✘ [{lv}] {msg} → {how}")
    for lv, msg, how in p1:
        lines.append(f"  · [{lv}] {msg} → {how}")
    lines.append(f"  ⇒ P0 {len(p0)} 项（清零才准发）· P1 {len(p1)} 项")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法：python redline.py 章节.md")
        sys.exit(1)
    t = open(sys.argv[1], encoding="utf-8").read()
    print(report(check(t)))

# ══════════════════════════════════════════════════════════════
# _AI_STYLE_ADDED  机器味检查（P1：可改可不改，但改了更像人写的）
#   这些特征不靠"词表"，靠结构：太顺、太整齐、太干净、太少身体。

_BODY = ("疼", "麻", "酸", "冷", "热", "汗", "抖", "喘", "闷", "痒",
         "发紧", "发沉", "发木", "发虚", "发烫", "恶心", "呛", "头晕")
_EYE = ("看见", "看到", "光", "亮", "暗", "颜色", "闪烁", "发白", "发青",
        "阴影", "反光")
_REV = ("可", "但是", "然而", "却", "直到", "只是", "偏偏")


def _ai_style(text):
    out = []
    t = text or ""
    lines = [x.strip() for x in t.splitlines() if x.strip()]
    if not lines:
        return out

    # ① 结尾反转句：整段最后一句用「可/但是/然而/直到」收
    tail = lines[-1]
    for w in _REV:
        if tail.startswith(w) and len(tail) <= 30:
            out.append(("P1",
                        "收尾用了『%s…』这种反转句（第末行：%s）" % (w, tail[:16]),
                        "换个收法：用一个动作或一个物件收，别用转折词兜底"))
            break

    # ② 精确到个位的三档数据（AI 最爱"X笔、Y次、剩W"）
    import re as _r
    hits = _r.findall(r"[一二三四五六七八九十百\d]+\s*[笔次个人天条处行页]", t)
    if len(hits) >= 6:
        nums = [h for h in hits if _r.search(r"\d", h)]
        if len(nums) >= 5:
            out.append(("P1",
                        "数字太密且精确（%s），像在登记而不是在看"
                        % "、".join(hits[:6]),
                        "改成模糊量：『七八笔』『大半是他』『数到第七笔就不数了』"))

    # ③ 视觉远多于身体：看得多、感觉得少
    eye = sum(t.count(w) for w in _EYE)
    body = sum(t.count(w) for w in _BODY)
    if eye >= 8 and body <= 2 and len(t) > 600:
        out.append(("P1",
                    "视觉描写 %d 处，身体感受只有 %d 处" % (eye, body),
                    "加身体：蹲久了腿麻、光刺得眼睛发酸、手沾了灰、耳鸣"))

    # ④ 「然后」式推进：动作排得太整齐
    seq = t.count("然后") + t.count("接着") + t.count("随后")
    if seq >= 3:
        out.append(("P1", "『然后/接着/随后』用了 %d 次，动作在排队" % seq,
                    "删掉连接词，让动作自己跳；或者干脆跳过一步"))

    # ⑤ 全篇零阻力：没有一次被打断、迟疑、算错
    res = t.count("愣") + t.count("没理") + t.count("没接") + t.count("迟疑") \
        + t.count("不确定") + t.count("算错") + t.count("没想到")
    if len(t) > 1200 and res == 0:
        out.append(("P1", "全篇没有一次阻力（没有迟疑、被拒、算错）",
                    "让人挡他一下、让他判断错一次——太顺就是机器味"))
    return out


_check_ai0 = check


def check(*a, **k):
    res = _check_ai0(*a, **k)
    try:
        txt = a[0] if a else (k.get("text") or "")
        for item in _ai_style(txt):
            res.setdefault("issues", []).append(item)
    except Exception:
        pass
    return res
