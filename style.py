"""风格学习：把成品小说压成一张"风格卡"，让生成器照着它的节奏写。

★ 为什么特征提取必须由脚本做，不能丢给模型：
  跟模型说"请模仿《XX》的文风"，它学不会——它只会回一段它以为的
  "那种感觉"。但它看得懂**数字和样本**：句长均值多少、变异系数多少、
  对话占多少、段落平均多长、章首通常怎么切。这些是能算出来的。

★ 学什么、不学什么（这条比技术重要）：
  学：可量化的**表面节奏**——句长分布、对话占比、段落长短、章首切入方式、
      标点习惯、称谓习惯。
  不学：情节、人设、专属设定词、别人的招牌句式。
      ★ 学后者的后果：抄。平台查重会死，而且你自己的下一本会变成前一本的影子。

⇒ 所以生成完必须过**重合检测**（`overlap`）：跟语料的 4-gram 重合率超标就是抄，
  不是模仿。这条是硬闸门，不是提醒。
"""

import os
import re
from typing import Any, Dict, List, Set, Tuple

BAN = ("仿佛", "似乎", "宛如")
PUNCT = "。！？…；：，、"
STOP = set("的了是不在他有我你这那就都也很和与及对为以为把被让给还没可所"
           "一个上下中大小多少好几么什如何")

# ★ 引号不只有全角。实测《你欠我几天》整本用直双引号（8.8 万处），
#   只认 “” 的话对话占比会算成 0.1%——数字错了，风格卡就全错。
_Q = r"“[^”]{1,300}”|\"[^\"\n]{1,300}\""
_OPEN = ("“", '"')
_CHAP = re.compile(r"^\s*第[0-9一二三四五六七八九十百千零两]+章.*$", re.M)


# ---------------------------------------------------------------------------
# 读语料
# ---------------------------------------------------------------------------

def read_corpus(path: str) -> str:
    """吃一个 txt 或一个目录（目录里所有 .txt 拼起来）。"""
    if os.path.isfile(path):
        for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
            try:
                return open(path, encoding=enc, errors="ignore").read()
            except Exception:
                continue
        return ""
    out = []
    for root, _, files in os.walk(path):
        for f in sorted(files):
            if f.lower().endswith(".txt"):
                out.append(read_corpus(os.path.join(root, f)))
    return "\n".join(out)


def split_chapters(text: str) -> List[str]:
    if _CHAP.search(text):
        idx = [(m.start(), m.group(0).strip()) for m in _CHAP.finditer(text)]
        out = []
        for i, (pos, title) in enumerate(idx):
            end = idx[i + 1][0] if i + 1 < len(idx) else len(text)
            body = text[pos:end].strip()
            if len(body) > 300:
                out.append(body)
        if len(out) >= 3:
            return out
    paras = [p.strip() for p in text.split("\n") if len(p.strip()) > 200]
    return paras


def take_chapters(text: str, start: int = 1, end: int = 0) -> str:
    """只取第 start–end 章。end=0 表示到结尾。

    ★ 为什么要能切：一本书里可能只有一部分是人写的，其余是模型生成的。
      拿 AI 写的部分当"文风范本"，等于把 AI 指纹当标准复制一遍再放大。
      ⇒ **只有人写的那些章才配当语料**，其余的只能当反面参照。
    """
    idx = [(m.start(), m.group(0).strip()) for m in _CHAP.finditer(text)]
    if not idx:
        return text
    end = end or len(idx)
    # ★ 边界坑：只挑了前 10 章时，第 10 章的结尾不能用"全文结尾"，
    #   要取**第 11 章的起点**。用错了，切出来就是整本书。
    sel = [i for i in range(len(idx)) if start <= i + 1 <= end]
    if not sel:
        return ""
    out = []
    for k, i in enumerate(sel):
        pos = idx[i][0]
        if k + 1 < len(sel):
            nxt = idx[sel[k + 1]][0]
        elif i + 1 < len(idx):
            nxt = idx[i + 1][0]
        else:
            nxt = len(text)
        out.append(text[pos:nxt])
    return "\n".join(out)


def sentences(text: str) -> List[str]:
    out = []
    for s in re.findall(r"[^。！？…\n]+[。！？…]?", text):
        s = s.strip()
        if len(s) >= 2:
            out.append(s)
    return out


def _pct(vals: List[float], p: float) -> float:
    if not vals:
        return 0.0
    v = sorted(vals)
    return v[min(len(v) - 1, int(len(v) * p))]


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------

def stats(text: str) -> Dict[str, Any]:
    bare = re.sub(r"\s+", "", text)
    chaps = split_chapters(text)
    sents = sentences(text)
    lens = [len(s) for s in sents]
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    plen = [len(re.sub(r"\s+", "", p)) for p in paras]

    # 对话占比：成对引号里的字数 / 总字数
    quote = sum(len(m) for m in re.findall(_Q, text))
    # 独句成段：段落里只有一个句子且比较短
    solo = sum(1 for p in paras if len(sentences(p)) == 1 and 2 <= len(p) <= 30)

    # 章首怎么切：前 30 字里有没有引号 / 是不是短句
    # ★ 跳过标题行和空行。取到空行的话，章首切入方式会全算成 0%
    def _lines(c: str) -> List[str]:
        return [x.strip() for x in c.split("\n")[1:] if x.strip()]

    heads = [(l[0] if (l := _lines(c)) else "") for c in chaps[:300]]
    head_dialogue = sum(1 for h in heads if any(q in h[:40] for q in _OPEN))
    head_short = sum(1 for h in heads if 0 < len(h.split("。")[0]) <= 12)

    # 章末：以对话收尾 / 以短句收尾
    tails = [(l[-1] if (l := _lines(c)) else "") for c in chaps[:300]]
    tail_dialogue = sum(1 for t in tails if any(q in t[-40:] for q in _OPEN))
    tail_short = sum(1 for t in tails if len(t) <= 30)

    words = re.findall(r"[\u4e00-\u9fa5]{2,3}", bare)
    freq: Dict[str, int] = {}
    for w in words:
        if w in STOP:
            continue
        freq[w] = freq.get(w, 0) + 1
    top = sorted(freq.items(), key=lambda x: -x[1])[:30]

    m = len(lens)
    mean = sum(lens) / m if m else 0
    var = (sum((v - mean) ** 2 for v in lens) / m) ** 0.5 if m else 0
    return {
        "总字数": len(bare),
        "章数": len(chaps),
        "章均字数": round(len(bare) / max(len(chaps), 1)),
        "句长均值": round(mean, 1),
        "句长p10": round(_pct(lens, 0.1)),
        "句长p90": round(_pct(lens, 0.9)),
        "变异系数": round(var / mean, 2) if mean else 0,
        "段均字数": round(sum(plen) / max(len(plen), 1)),
        "段p90": round(_pct(plen, 0.9)),
        "独句成段占比": round(solo * 100.0 / max(len(paras), 1), 1),
        "对话占比": round(quote * 100.0 / max(len(bare), 1), 1),
        "章首对话切入": round(head_dialogue * 100.0 / max(len(heads), 1)),
        "章首短句切入": round(head_short * 100.0 / max(len(heads), 1)),
        "章末对话收": round(tail_dialogue * 100.0 / max(len(tails), 1)),
        "章末短句收": round(tail_short * 100.0 / max(len(tails), 1)),
        "禁用词": {w: text.count(w) for w in BAN},
        "高频词": top,
        "标点密度": {p: round(text.count(p) * 1000.0 / max(len(bare), 1), 1)
                     for p in PUNCT},
    }


def pick_samples(text: str, n: int = 3, each: int = 320) -> List[str]:
    """挑几段有代表性的原文，给模型当节奏参考。

    ★ 只挑"段落长短接近均值、有对话"的——挑到一段纯描写，
      模型会以为这本书通篇都在写景。
    """
    chaps = split_chapters(text)
    if not chaps:
        chaps = [text]
    out = []
    step = max(1, len(chaps) // (n * 3))
    for c in chaps[::step][: n * 3]:
        body = "\n".join(c.split("\n")[1:]).strip()
        if len(body) < each:
            continue
        mid = len(body) // 2
        seg = body[max(0, mid - each // 2): mid + each // 2]
        if any(q in seg for q in _OPEN):
            out.append(seg)
        if len(out) >= n:
            break
    return out or [c[:each] for c in chaps[:n]]


# ---------------------------------------------------------------------------
# 风格卡
# ---------------------------------------------------------------------------

def build_card(st: Dict[str, Any], samples: List[str], src: str) -> str:
    high = "、".join(w for w, _ in st["高频词"][:12])
    return f"""# 风格卡（从《{src}》量出来的）

★ 学的是下面这些**能算出来的节奏**，不是情节、不是措辞、不是专属设定词。

## 一、数字（写的时候往这些数上靠）
- 章均 {st['章均字数']} 字；句长均值 {st['句长均值']} 字（p10 {st['句长p10']} / p90 {st['句长p90']}）
- 句长变异系数 {st['变异系数']}　★ 这个数决定"疏密感"，别写均匀了
- 段均 {st['段均字数']} 字（p90 {st['段p90']}），独句成段占 {st['独句成段占比']}%
- 对话占全文 {st['对话占比']}%
- 章首：{st['章首对话切入']}% 用对话切、{st['章首短句切入']}% 用短句切
- 章末：{st['章末对话收']}% 用对话收、{st['章末短句收']}% 用短句收
- 禁用词实测：{st['禁用词']}　★ 原书有的话别跟着学，那是不该学的部分

## 二、标点习惯（每千字）
{_fmt_punct(st['标点密度'])}

## 三、常用词（只当语气参考，不许直接搬原句）
{high}

## 四、节奏样本（照它的**长短和疏密**写，不许照抄句子）
""" + "\n\n".join("----\n" + s for s in samples) + "\n"


def translate(st: Dict[str, Any]) -> str:
    """把统计数字翻译成模型听得懂的动作指令。

    ★ 为什么非翻不可：实测把"对话占 37.4%、CV 0.63"原样给 7B，
      它写出来的对话占比是 1.9%、CV 0.25 —— 它看不懂统计值。
      换成"写两三句就要有人开口"这种动作指令，它才照做。
      ⇒ **数字是给人看的，动作指令是给模型看的。**
    """
    out = []
    mean, cv = st["句长均值"], st["变异系数"]
    if mean <= 12:
        out.append(f"句子要短：多数七八个字到十几个字就收，别写长句。"
                   f"（原书句长均值 {mean} 字）")
    elif mean >= 30:
        out.append(f"句子可以长：常态三十字上下，靠逗号铺开。（原书 {mean} 字）")
    else:
        out.append(f"句子中等长度，均值 {mean} 字左右，别走极端。")

    if st["独句成段占比"] >= 60:
        out.append(f"大量独句成段：一个动作、一句对话就换段，"
                   f"原书 {st['独句成段占比']}% 的段落只有一句。")
    if st["对话占比"] >= 25:
        out.append(f"对话要多：写两三句叙述就要有人开口原话，"
                   f"原书对话占 {st['对话占比']}%。")
    elif st["对话占比"] <= 10:
        out.append(f"少写对话，以叙述推进为主（原书只有 {st['对话占比']}%）。")

    if cv >= 0.6:
        out.append("句长要忽疏忽密：短句（五字以内）和长句（四十字以上）交替出现，"
                   "不要全章一个节奏。")
    else:
        out.append("句子长短比较均匀，别忽长忽短。")

    if st["章首短句切入"] >= 60:
        out.append("章首常用一句短句硬切进去，不要铺垫、不要写景。")
    elif st["章首短句切入"] >= 25:
        # ★ 这条是反指纹的关键，别照抄"越多越好"。
        #   实测：人写章首短句切入 40%，AI 写 79% —— AI 把一种手法推到极端
        #   并且每章保持一致，那就是机器指纹。
        out.append(f"章首**不要总是**短句硬切（原书只有 {st['章首短句切入']}%）："
                   "换着来——一句对白、一个动作、一个具体物件都行。"
                   "每章都用同一种方式开头＝机器指纹。")
    if st["章首对话切入"] >= 30:
        out.append("章首常从一句对白开始。")
    if st["章末短句收"] >= 70:
        out.append("章末收在一句短句上，不总结、不抒情。")
    elif st["章末短句收"] >= 40:
        out.append("章末多数收在短句上，也留出几章收在别处（半句话、一个动作）。")
    if st["章末对话收"] >= 30:
        out.append("章末常收在一句对白上。")
    if st["章均字数"]:
        out.append(f"整章写 {st['章均字数']} 字左右。")
    return "\n".join("- " + s for s in out)


# AI 味的四条画像（从《你欠我几天》人写 vs AI 写的实测对照得出）：
#   ① 没有长句　② 太均匀　③ 同一种开头用到 100%　④ 模糊指代代替具体名字
FUZZY_WORDS = ("那个人", "那东西", "什么东西", "在那儿", "一下", " somehow",
               "某种", "这个人", "什么东西")


def ai_smell(st: Dict[str, Any]) -> Tuple[int, List[str]]:
    """像不像 AI 写的。0–5 分，越高越可疑。

    ★ 这是**启发式筛查，不是判定**——它只负责在 50 本里帮你把可疑的挑出来，
      最后哪本能当语料，你说了算。判据来自实测的人写/AI 写对照，
      换一批书可能要重新校准，别把它当圣旨。
    """
    why = []
    n = 0
    if st["句长p90"] < 20:
        n += 1; why.append(f"几乎没有长句（p90 {st['句长p90']}）")
    if st["变异系数"] < 0.60:
        n += 1; why.append(f"句长太均匀（CV {st['变异系数']}）")
    if st["章首短句切入"] >= 70:
        n += 1; why.append(f"章首几乎全是短句（{st['章首短句切入']}%）")
    if st["段均字数"] < 12:
        n += 1; why.append(f"段落碎（段均 {st['段均字数']}）")
    top = [w for w, _ in st.get("高频词", [])[:25]]
    fz = [w for w in top if any(f in w for f in FUZZY_WORDS)]
    if len(fz) >= 2:
        n += 1; why.append("高频词里模糊指代多：" + "、".join(fz[:3]))
    return n, why


def survey(corpus_dir: str, rng: str = '') -> List[Dict[str, Any]]:
    """语料普查：一个目录里每本书分别量一遍。

    ★★ 为什么不能"五十本一起平均"：
      榜单上的书风格互相打架（有极短句有长句），平均出来每个指标都落在
      中间——那恰恰是**最没有风格**的位置。模仿平均数＝写一个谁也不像的东西。
      ⇒ 正确做法：先普查，看清分布，**挑一本**当靶子。
    """
    out = []
    for root, _, files in sorted(os.walk(corpus_dir)):
        for f in sorted(files):
            if not f.lower().endswith((".txt", ".md")):
                continue
            p = os.path.join(root, f)
            t = read_corpus(p)
            if rng:
                a1, a2 = (rng.split("-", 1) + ["0"])[:2]
                t = take_chapters(t, int(a1 or 1), int(a2 or 0))
            if len(re.sub(r"\s+", "", t)) < 5000:
                continue
            st = stats(t)
            st["_name"] = os.path.splitext(f)[0]
            st["_path"] = p
            out.append(st)
    return out


def survey_table(rows: List[Dict[str, Any]]) -> str:
    """普查表：每本一行，看得出分布。"""
    keys = ["句长均值", "句长p90", "变异系数", "段均字数",
            "独句成段占比", "对话占比", "章均字数"]
    head = (f"{'书名':18s}" + "".join(f"{k[:6]:>9s}" for k in keys)
            + f"{'  AI味':>8s}")
    lines = [head, "-" * len(head)]
    for r in rows:
        sm, _ = ai_smell(r)
        lines.append(f"{r['_name'][:16]:18s}"
                     + "".join(f"{r[k]:>9}" for k in keys)
                     + f"{sm:>6d}/5")
    if not rows:
        return "（目录里没有够长的 txt/md）"
    return "\n".join(lines)


def typical(rows: List[Dict[str, Any]], n: int = 3) -> List[Tuple[str, float]]:
    """哪几本"最典型"＝各项都离中位数最近。要模仿就挑这类里的。

    ★ 挑"最典型"不是挑"平均"。平均是算出来的一个虚构值，
      最典型是**真实存在的那本书**——模仿它才有具体对象。
    """
    if not rows:
        return []
    keys = ["句长均值", "变异系数", "对话占比", "段均字数", "章均字数"]
    med = {}
    for k in keys:
        v = sorted(r[k] for r in rows)
        med[k] = v[len(v) // 2]
    out = []
    for r in rows:
        d = sum(abs(r[k] - med[k]) / max(med[k], 1) for k in keys)
        out.append((r["_name"], round(d, 3)))
    out.sort(key=lambda x: x[1])
    return out[:n]


def compare(sh: Dict[str, Any], sa: Dict[str, Any]) -> str:
    """人写的 vs AI 写的，逐项对照。★ 这是"AI 味"的量化画像。

    用途不是骂 AI，是知道该往哪改：差别最大的那几项，就是机器指纹的所在。
    """
    keys = ["句长均值", "句长p10", "句长p90", "变异系数", "段均字数",
            "独句成段占比", "对话占比", "章均字数", "章首短句切入",
            "章首对话切入", "章末短句收", "章末对话收"]
    lines = [f"{'':16s}{'人写':>9s}{'AI写':>9s}{'差':>9s}",
             "-" * 44]
    diffs = []
    for k in keys:
        d = sh.get(k, 0) - sa.get(k, 0)
        diffs.append((abs(d), k, d))
        lines.append(f"{k:16s}{sh.get(k, 0):>9}{sa.get(k, 0):>9}{d:>+9.2f}")
    lines.append("-" * 44)

    # 差得最狠的三项，就是指纹所在
    diffs.sort(reverse=True)
    lines.append("差别最大的三项（机器指纹通常藏在这儿）：")
    for _, k, d in diffs[:3]:
        who = "人写更多" if d > 0 else "AI 写更多"
        lines.append(f"  · {k}：{who}（差 {abs(d):.1f}）")

    hv = [w for w, _ in sh.get("高频词", [])[:25]]
    av = [w for w, _ in sa.get("高频词", [])[:25]]
    only_h = [w for w in hv if w not in av][:8]
    only_a = [w for w in av if w not in hv][:8]
    lines.append("")
    lines.append(f"人写常出现、AI 没有的词：{'、'.join(only_h) or '（无）'}")
    lines.append(f"AI 常出现、人写没有的词：{'、'.join(only_a) or '（无）'}")
    return "\n".join(lines)


def _fmt_punct(d: Dict[str, float]) -> str:
    return "　".join(f"{k} {v}" for k, v in d.items() if v > 0)


# ---------------------------------------------------------------------------
# 重合检测：模仿和抄袭的分界线
# ---------------------------------------------------------------------------

def shingles(text: str, n: int = 8) -> Set[str]:
    t = re.sub(r"\s+", "", text)
    return {t[i:i + n] for i in range(max(0, len(t) - n + 1))}


def overlap(gen: str, corpus: Set[str], n: int = 8) -> float:
    """生成稿跟语料的 n-gram 重合率（0–1）。"""
    if not corpus:
        return 0.0
    g = shingles(gen, n)
    if not g:
        return 0.0
    return len(g & corpus) / len(g)


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) < 2:
        print("用法：python style.py 语料.txt 或 目录")
        sys.exit(1)
    t = read_corpus(sys.argv[1])
    st = stats(t)
    print(json.dumps({k: v for k, v in st.items() if k != "高频词"},
                     ensure_ascii=False, indent=2))
    print("高频词：" + "、".join(w for w, _ in st["高频词"][:20]))
