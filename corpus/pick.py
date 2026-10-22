"""开书台生成时，把蒸馏好的写法规则取出来，按任务选类，拼成给模型的约束块。

★ 跟上一版（--已废弃--）的区别：
  旧版是把教程原文按篇挑出来塞进 prompt —— 那是"搬资料"，模型只会照抄书名、
  抄丹药名录，7B 还容易被长文本冲垮。
  现在喂的是 corpus/rules.json：151 篇教程蒸馏出来的**可执行指令**
  （"开篇三百字内必须出现主角"这种），按任务只取相关的类别：
    钩子 → 开篇 + 爽点      书名 → 桥段       章纲 → 结构 + 爽点 + 桥段 + 毒点

★ 用法：launch._corpus(book, task) 调 rules_for(book, task)
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RULES = os.path.join(HERE, "rules.json")
INDEX = os.path.join(HERE, "index.json")

# ★ 用户补写的规则必须落在 exe 旁边，不能落在打包的临时目录里
#   （frozen 时 HERE 是 _MEIxxxx，进程一退出就没了，等于没存）。
if getattr(sys, "frozen", False):
    ROOT = os.path.dirname(sys.executable)
else:
    ROOT = os.path.dirname(HERE)
CUSTOM = os.path.join(ROOT, "corpus", "custom.json")

# 题材别名：用户填的题材五花八门，先归一到规则库里的类名
ALIAS = {
    "玄幻": "玄幻", "东方玄幻": "玄幻", "异世大陆": "玄幻", "高武": "玄幻",
    "修仙": "仙侠", "仙侠": "仙侠", "修真": "仙侠", "洪荒": "仙侠",
    "都市": "都市", "都市脑洞": "都市", "都市异能": "都市", "商战": "都市",
    "古言": "古言", "古代言情": "古言", "宫斗": "古言", "宅斗": "古言",
    "历史": "历史", "架空历史": "历史", "历史军事": "历史",
    "科幻": "科幻", "末世": "科幻", "星际": "科幻", "机甲": "科幻",
    "游戏": "网游", "网游": "网游", "电竞": "网游", "游戏竞技": "网游",
    "灵异": "灵异", "悬疑": "悬疑", "推理": "悬疑", "规则怪谈": "悬疑",
    "军事": "军事", "抗战": "军事", "年代": "重生年代", "重生年代": "重生年代",
    "短剧": "短剧", "校园": "校园", "青春": "校园",
}

# 不同生成任务要不同类别的规则 —— 这是"程序专用"的核心：
# 不是每次都把同一坨东西塞进去，而是这一步要什么给什么。
# ★ "own"（用户自己补写的规则）排在最前且不限条数：
#   那是他亲手写的要求，优先级高于教程蒸馏出来的任何一条。
TASK_CATS = {
    "hook":    [("own", 99), ("hook", 8), ("beat", 5)],
    "title":   [("own", 99), ("trope", 5), ("hook", 4)],
    "outline": [("own", 99), ("struct", 8), ("beat", 8), ("trope", 6), ("taboo", 8)],
    # 金手指：吃"桥段套路"和"毒点"（哪些金手指写烂了、哪些是雷）
    "cheat":   [("own", 99), ("trope", 6), ("taboo", 6)],
    # 简介：吃"开篇"（第一句怎么抓人）和"爽点"（亮什么才有点击欲）
    "intro":   [("own", 99), ("hook", 6), ("beat", 6)],
}
CAT_CN = {"own": "我补写的", "hook": "开篇", "beat": "爽点节奏",
          "trope": "桥段套路", "taboo": "毒点避雷", "struct": "结构章纲"}


def load_rules():
    """蒸馏规则 + 用户自己补的规则（custom.json），后者优先。

    ★ 让规则库能长：他在界面里写进去的写法心得，跟 151 篇教程蒸馏出来的
      一起生效，而且排在前面。这才是"程序自己的规则"，不是一份死资料。
    """
    try:
        base = json.load(open(RULES, encoding="utf-8"))
    except Exception:
        base = {}
    try:
        cust = json.load(open(CUSTOM, encoding="utf-8"))
    except Exception:
        return base
    for g, d in (cust or {}).items():
        if g == "_meta":
            continue
        for c, items in (d or {}).items():
            base.setdefault(g, {}).setdefault(c, [])
            base[g][c] = list(items) + [x for x in base[g][c] if x not in items]
    return base


def save_custom(genre, cat="own", items=None):
    """把用户写的规则存下来。genre 用 __全部__ 表示对所有题材生效。

    ★ 题材必须归一：界面里填的是"都市脑洞"，规则桶叫"都市"，
      不归一写进去就永远取不到（存进去了却没生效，最难查的那种 bug）。
    """
    try:
        cust = json.load(open(CUSTOM, encoding="utf-8"))
    except Exception:
        cust = {}
    gs = norm_genre(genre)
    g = (gs[0] if gs else "__全部__")
    cust.setdefault(g, {})[cat or "trope"] = [
        x.strip() for x in (items or []) if 5 <= len(x.strip()) <= 40][:40]
    json.dump(cust, open(CUSTOM, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return True


def norm_genre(s):
    s = (s or "").strip()
    if not s:
        return []
    out = []
    for k, v in ALIAS.items():
        if k in s:
            out.append(v)
    if not out:
        out.append(s)
    return list(dict.fromkeys(out))


def _genre_keys(book, rules):
    """这本书该用哪几个题材的规则桶（含女频变体和通用）。"""
    mf = (book.get("mf") or "").strip()
    gs = norm_genre(book.get("genre"))
    keys = []
    for g in gs:
        if mf == "女频":
            if g + "@女" in rules:
                keys.append(g + "@女")
            if g in rules:
                keys.append(g)
        else:
            if g in rules:
                keys.append(g)
            if g + "@女" in rules:
                keys.append(g + "@女")
    # 没命中就退到通用
    if not any(k in rules for k in keys):
        keys.append("通用@女" if mf == "女频" else "通用")
        keys.append("通用")
    # ★ 通用桶永远兜底：女频也吃"通用"（跨频道的方法论），
    #   只吃"通用@女"会让女频只剩两三条——女频语料本来就只有 15 篇。
    if mf == "女频" and "通用@女" in rules:
        keys.append("通用@女")
    keys.append("通用")
    keys.append("__全部__")   # 用户写的、不限题材的规则，永远最优先
    return list(dict.fromkeys([k for k in keys if k in rules]))


# ★ 模型归纳时偶尔会把"作者对读者说的话"也当成规则
#   （"不要只看不思考""保持对编剧的热情"）——那不是写作指令，挡掉。
BAD = re.compile(r"(思考|思想|热情|被骂|看小说|读者朋友|建议先看看|上一个帖子|"
                 r"篇幅有限|下期|收藏|投票|推荐票|月票|加更|打赏)")
PREFIX = re.compile(r"^(公式|步骤|方法|要点|注意)[一二三四五六七八九十\d]{1,3}[:：]?\s*")


def _ok(t):
    t = PREFIX.sub("", t).strip()
    if not (6 <= len(t) <= 34):
        return ""
    if BAD.search(t):
        return ""
    return t


def _keyset(s):
    """用"字集合"判重，比 2-gram 宽容：
    「主角必须在开篇三百字内出场」和「开篇三百字内必须出现主角」
    句式完全不同，但说的是同一件事 —— 2-gram 抓不住，字集合能抓住。
    """
    s = re.sub(r"[^\u4e00-\u9fa5]", "", s)
    return set(s) - {"的", "了", "是", "在", "和", "与", "要", "有", "不", "就"}


def rules_for(book, task="outline", budget=1600):
    """返回 (规则块文本, 说明)。没有规则库就返回空串，不能拖垮生成。

    ★ 两条纪律：
      1. 每类独立限额 —— 否则"开篇"条目多就把"毒点"全挤没了，
         而毒点恰恰是章纲最该守的。
      2. 同义去重 —— 题材桶和通用桶常把同一条写了两遍
         （"开篇三百字内必须出现主角"／"主角必须在开篇三百字内出场"），
         按 2-gram 相似度判重，留先出现的（＝更贴本题材的）。
    """
    rules = load_rules()
    if not rules:
        return "", []
    keys = _genre_keys(book, rules)
    plan = TASK_CATS.get(task, TASK_CATS["outline"])

    got, used, seen_g = [], 0, []
    for cat, cap in plan:
        per = 0
        for k in keys:                       # 题材在前、通用在后 ⇒ 贴题的优先
            for t in (rules.get(k) or {}).get(cat) or []:
                if per >= cap or used > budget:
                    break
                t = _ok(t)
                if not t:
                    continue
                g = _keyset(t)
                if len(g) > 4 and any(len(g & x) / (len(g | x) or 1) > 0.55
                                      for x in seen_g):
                    continue
                seen_g.append(g)
                got.append((cat, t))
                used += len(t) + 12
                per += 1
    if not got:
        return "", []

    order = [c for c, _ in plan]
    got.sort(key=lambda x: order.index(x[0]))
    parts, cur = [], None
    for cat, t in got:
        if cat != cur:
            parts.append("·%s" % CAT_CN.get(cat, cat))
            cur = cat
        parts.append("  - " + t)
    names = list(dict.fromkeys(
        [k.split("@")[0] for k in keys if not k.startswith("通用")] + ["通用"]))
    head = ("下面是本程序正在使用的【%s写法规则】，生成时必须遵守、不得照抄原文"
            "（「我补写的」是你自己存进去的，优先级最高；其余是从 %d 篇教程"
            "蒸馏出来的本题材做法）：\n"
            % ("／".join(names[:2]), _doc_count()))
    return head + "\n".join(parts), ["%s%d条" % (CAT_CN[c],
                                                 sum(1 for x in got if x[0] == c))
                                     for c in dict.fromkeys(x[0] for x in got)]


_DOCN = [0]


def _doc_count():
    if _DOCN[0]:
        return _DOCN[0]
    try:
        _DOCN[0] = len(json.load(open(INDEX, encoding="utf-8")))
    except Exception:
        _DOCN[0] = 149
    return _DOCN[0]


# 旧接口：保留兼容（有些地方可能还在调 pick）
def pick(book, budget=2600, topn=4, per=650):
    txt, names = rules_for(book, "outline", budget)
    return txt, names
