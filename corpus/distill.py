"""把 corpus/raw 里的 151 篇原始资料，蒸馏成**本程序专用的写法规则库** rules.json。

★ 为什么要有这一步（不是直接把原文塞进 prompt）：
  原文是"人写给人的教程"，里面有作者的口水、举例、目录、设定名录，
  直接喂给 7B，它只会照抄或者被冲垮。程序要的不是一个文本块，而是
  **一张能直接约束生成的规则表**：这个题材开篇该干什么、爽点怎么排、
  哪些桥段能用、哪些是毒点。这才是这个程序相对于"文本编辑器"的价值。

两级：
  A 程序抽取 —— 从全文里捞"可操作的短条目"（小标题／列表行／带方法论的句子），
                按题材×类别分桶，去重，跨文档计数（多篇都说的才是共识）。
  B LLM 归纳 —— 把 A 的候选按（题材×类别）成批喂 7B，让它压成 N 条
                ≤30 字的**可操作规则**，写成 rules.json。
                不加 --llm 时 B 退化成程序化排序选 top（保证永远有产出）。

输出结构：
  { "都市": {"hook": [...], "beat": [...], "trope": [...], "taboo": [...],
              "struct": [...]}, "通用": {...}, "_meta": {...} }
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
INDEX = os.path.join(HERE, "index.json")
CAND = os.path.join(HERE, "_cand.json")
RULES = os.path.join(HERE, "rules.json")

CATS = ["hook", "beat", "trope", "taboo", "struct"]
CAT_CN = {"hook": "开篇", "beat": "爽点节奏", "trope": "桥段套路",
          "taboo": "毒点避雷", "struct": "结构章纲"}

# 类别关键词（命中即归该类；可多归属，但只取分最高的一类）
KW = {
    "hook": ["开局", "开头", "开篇", "首章", "第一章", "前三章", "前三十", "序章",
             "切入", "入局", "钩子", "抓人", "留人", "黄金三章", "起手", "引子",
             "第一印象", "开篇期待"],
    "beat": ["爽点", "打脸", "反转", "高潮", "铺垫", "期待感", "卡点", "断章",
             "节奏", "压抑", "爆发", "情绪", "装逼", "装比", "爽感", "爆点",
             "爽文", "钩子", "追读", "磕点", "甜点", "拉扯"],
    "trope": ["套路", "桥段", "梗", "模板", "常见设定", "经典", "玩法", "流派",
              "剧情", "情节", "设定", "金手指", "系统", "重生", "穿越", "废柴",
              "退婚", "宗门", "秘境"],
    "taboo": ["不要", "别写", "避免", "忌", "禁忌", "禁止", "毒点", "扑街",
              "失败", "雷点", "坑", "崩", "烂大街", "写砸", "大忌", "切记"],
    "struct": ["大纲", "章纲", "结构", "分卷", "卷", "主线", "暗线", "支线",
               "伏笔", "阶段", "三步", "节奏表", "框架", "骨架", "布局", "规划"],
}

# 明显不是方法论的行（目录、页码、联系方式、纯数据）
JUNK = re.compile(
    r"(http|www\.|@|\.com|\.cn|qq|微信|邮箱|第\s*\d+\s*页|版权|ISBN|"
    r"\d{4}年\d{1,2}月|电话|地址|QQ群|公众号)", re.I)
# ★ 书名行（《开局签到XX》）：那是"举例"，不是规则。喂进去只会让模型抄书名。
BOOKNAME = re.compile(r"^《.{2,20}》?[\s:：]?", re.U)
# ★ 设定名录：丹药/等级/种族/阵法一类的素材表，对"怎么写"没有指导
LORE = re.compile(r"(功效|丹药|等级划分|修炼等级|第[一二三四五六七八九十]重|"
                  r"上古|太古|洪荒|神话人物|异兽|法宝|符箓|阵法|血脉|体质|"
                  r"神火榜|门派职位|六界|冥界|天界|妖界|魔界)")
# 口水句：作者在对读者说话，不是方法论
CHAT = re.compile(r"(宝子|各位|小伙伴们|这节课|大家好|笔者|我个人|我觉得|"
                  r"我建议你们|想月入|过万|稿费|签约流程|投稿|网站|编辑|"
                  r"扑街秒变|立竿见影|我们下期|关注我|点赞)")
# 举例句
EXAMPLE = re.compile(r"^(比如|例如|譬如|像《|就拿|以.{0,6}为例)")
# 指令感：这条能不能当"该做什么"
IMPER = re.compile(r"(必须|应该|不要|别写|避免|切忌|切记|先.{0,4}再|"
                   r"然后|最好|建议|记住|核心|关键|重点|本质|原则|公式|"
                   r"分[为成]|包括|三步|几分|成$|要$|要有|能$|才能)")
# 问句（教程小标题）——问句不是可执行的规则
QUESTION = re.compile(r"[？?]$")
# 行首的编号/项目符号
BULLET = re.compile(r"^\s*[\(（【\[]?\s*(\d{1,3}|[一二三四五六七八九十百]+|[a-zA-Z])"
                    r"\s*[\.、，,：:）)\]】]\s*")
BULLET2 = re.compile(r"^\s*([-·•※★☆○●◆◇►>]+)\s*")
CN = re.compile(r"[\u4e00-\u9fa5]")
SENT_SPLIT = re.compile(r"[。！？；;!?\n]+")

STOP = set("的了是在和与及就都也很而要会能有着被把从对为以之其这个那我们你们他们"
           "你们自己什么怎么如何可以不要不能如果因为所以但是还是这样那样"
           "一下一个一种一些一点一部一本一名一位")


def norm(s):
    s = re.sub(r"\s+", "", s or "")
    s = s.replace("，", ",").replace("：", ":").replace("；", ";")
    return s.strip()


def is_heading(line, lines, i):
    """小标题行：短、无句末标点、整行独立。"""
    L = len(line)
    if not (4 <= L <= 28):
        return False
    if line[-1] in "。！？；，、：,.!?;:":
        return False
    if JUNK.search(line):
        return False
    # 前后都是空行（真小标题），或是列表里的加粗项
    prev_blank = (i == 0 or not lines[i - 1].strip())
    next_blank = (i + 1 >= len(lines) or not lines[i + 1].strip())
    return prev_blank or next_blank


def grams(s, n=2):
    s = re.sub(r"[^\u4e00-\u9fa5]", "", s)
    return {s[i:i + n] for i in range(len(s) - n + 1)} - {""}


def classify(line):
    """给一行打类别，返回 (类别, 命中数)。打不出就 None。"""
    best, hit = None, 0
    for c, ws in KW.items():
        n = sum(1 for w in ws if w in line)
        if n > hit:
            best, hit = c, n
    return (best, hit) if hit else (None, 0)


def usable(line):
    """一条能不能当'规则候选'。

    ★ 关键：要的是"该怎么做"，不是"资料里有什么"。
      书名、设定名录、口水、举例、问句全部挡掉——这些就是"原封不动搬资料"
      的那部分，也是 7B 照抄的源头。
    """
    L = len(line)
    if not (7 <= L <= 46):
        return False
    if JUNK.search(line) or QUESTION.search(line):
        return False
    if BOOKNAME.search(line) or EXAMPLE.search(line):
        return False
    if LORE.search(line) or CHAT.search(line):
        return False
    cn = len(CN.findall(line))
    if cn < 6 or cn / L < 0.55:
        return False
    digits = len(re.findall(r"\d", line))
    if digits / L > 0.2:
        return False
    # 纯名录（神仙名字表、地名表）：逗号太多且每节极短
    segs = [s for s in re.split(r"[,，、;；]", line) if s.strip()]
    if len(segs) >= 5 and max(len(s) for s in segs) <= 6:
        return False
    if re.fullmatch(r"[\d\W_]+", line):
        return False
    # ★ 必须有"指令感"或"结构感"，纯叙述不要
    if not IMPER.search(line):
        return False
    return True


def extract_file(path):
    """一篇文档 → 候选条目列表 [(文本, 类别)]"""
    try:
        txt = open(path, encoding="utf-8", errors="ignore").read()
    except Exception:
        return []
    lines = [l.strip() for l in txt.splitlines()]
    out = []
    for i, l in enumerate(lines):
        if not l or len(l) > 200:
            continue
        l = BULLET.sub("", l)
        l = BULLET2.sub("", l).strip()
        if not l:
            continue
        cands = []
        if is_heading(l, lines, i) and usable(l):
            cands.append(l)
        elif BULLET.match(lines[i].strip()) or BULLET2.match(lines[i].strip()):
            if usable(l):
                cands.append(l)
        # 句子级：只在含类别词时才切出来，避免全篇都收
        if len(l) > 14:
            for s in SENT_SPLIT.split(l):
                s = s.strip()
                if usable(s) and classify(s)[0]:
                    cands.append(s)
        for c in cands:
            c = norm(c)
            if not c:
                continue
            cat, hit = classify(c)
            if cat is None:
                continue
            out.append((c, cat, hit))
    return out


def dedup(items):
    """同桶内合并相似条目，保留出现次数（跨文档数）。"""
    buckets = defaultdict(list)
    for text, docs in items:  # [(文本, {文档})]
        g = grams(text)
        if not g:
            continue
        key = "".join(sorted(g)[:3])[:6]
        buckets[(len(text) // 8, key)].append((text, g, docs))
    out = []
    for _, group in buckets.items():
        group.sort(key=lambda x: -len(x[1]))
        reps = []
        for text, g, docs in group:
            hit = False
            for r in reps:
                inter = len(r[1] & g)
                uni = len(r[1] | g) or 1
                if inter / uni >= 0.55:
                    r[2] |= docs
                    r[3] += 1
                    hit = True
                    break
            if not hit:
                reps.append([text, g, set(docs), 1])
        out.extend(reps)
    return [(r[0], r[2], r[3]) for r in out]


def stage_a():
    rows = json.load(open(INDEX, encoding="utf-8"))
    meta = {r["file"]: r for r in rows}
    # (题材×类别) → [(文本, {文件})]
    pool = defaultdict(lambda: defaultdict(set))
    files = [f for f in os.listdir(RAW) if f.endswith(".txt")]
    for f in files:
        m = meta.get(f)
        if not m:
            continue
        uses = set(m.get("uses") or [])
        # ★ 投稿/签约流程类对"怎么写"零价值，整篇跳过
        if "流程规则" in uses and len(uses) == 1:
            continue
        # ★ 纯设定名录（丹药/等级/异兽）只留"套路爽点""大纲结构"之外的少量句子
        lore_only = uses and uses <= {"设定素材"}
        genres = list(m["genres"])
        keys = genres + ["通用"]
        if m["channel"] == "女频":
            keys = [k + "@女" for k in keys] + keys
        if lore_only:
            keys = []  # 名录类不进规则库（生成设定时才用，另说）
        for text, cat, hit in extract_file(os.path.join(RAW, f)):
            for k in set(keys):
                pool[(k, cat)][text].add(f)
        print("  %-46s %5d 条" % (f[:44], sum(1 for _ in pool)), end="\r")
    print()
    cand = {}
    for (k, cat), d in pool.items():
        merged = dedup([(t, fs) for t, fs in d.items()])
        # 排序：跨文档数优先，然后是信息量
        merged.sort(key=lambda x: (-len(x[1]), -len(x[0])))
        cand["%s|%s" % (k, cat)] = [
            {"t": t, "docs": sorted(fs)[:6], "n": len(fs)} for t, fs, _ in merged[:160]
        ]
    json.dump(cand, open(CAND, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("候选已写 %s，共 %d 桶" % (CAND, len(cand)))
    return cand


# ---------------------------------------------------------------------------
# B：归纳成规则
# ---------------------------------------------------------------------------

SYS = """你是网文写作方法论的提炼器，服务一个自动生成长篇连载的程序。

给你一批从写作教程里抽出来的原始条目。它们是作者口语、有重复、有举例、有水话。
你要把它们压成**程序能直接拿去约束生成的写作规则**。

规则的样子（照这个标准写）：
  ✓ "开篇三百字内必须出现主角"
  ✓ "每章场景不超过三个"
  ✓ "打脸前先让读者相信对手很强"
  ✗ "我觉得开局挺重要的"        ← 看法，不是动作
  ✗ "《开局签到荒古圣体》"      ← 书名，不是规则
  ✗ "前面铺垫了主角的无敌"      ← 描述，不是指令

要求：
1. 每条必须是一个**动作**：该做什么、不该做什么。用祈使句。
2. 每条 ≤30 字，一行一条，以 "-" 开头，不要编号、不要引号、不要解释。
3. 只留对写长篇连载有用的，重复/口水/举例/投稿签约流程全部丢掉。
4. 不要发明原文没有的东西，也不要照抄原句——压成指令。
5. 每类不超过 {n} 条，宁缺毋滥。

输出格式（严格照此，五个段落一个都不能少）：""" + \
    "".join("\n【%s】" % CAT_CN[c] for c in CATS)


def _parse_block(raw):
    """按【类别】分段解析模型输出。"""
    got = {c: [] for c in CATS}
    cur = None
    cn2cat = {v: k for k, v in CAT_CN.items()}
    for line in raw.splitlines():
        line = line.strip()
        m = re.match(r"^【(.{1,8})】", line)
        if m:
            k = m.group(1).strip()
            cur = cn2cat.get(k)
            if cur is None:
                for c, cn in CAT_CN.items():
                    if cn in k or k in cn:
                        cur = c
                        break
            continue
        if not cur or not line:
            continue
        s = re.sub(r"^\s*[\d一二三四五六七八九十]+[\.、）\)]\s*", "", line)
        s = re.sub(r"^[-•·*]\s*", "", s).strip().strip('"“”「」')
        s = s.rstrip("。.!！").strip()
        if not (5 <= len(s) <= 34):
            continue
        if not CN.search(s):
            continue
        if any(len(grams(s) & grams(x)) / (len(grams(s) | grams(x)) or 1) > 0.62
               for x in got[cur]):
            continue
        if s in sum(got.values(), []):
            continue
        got[cur].append(s)
    return got


def _llm_refine(genre, by_cat, topn):
    # ★ 直接 `python corpus/distill.py` 跑时 sys.path[0] 是 corpus/，
    #   上两级才是项目根（llm.py 在那儿）。不补这一句就会 No module named 'llm'。
    root = os.path.dirname(HERE)
    if root not in sys.path:
        sys.path.insert(0, root)
    import llm
    usr = "题材：%s\n\n" % genre
    for c in CATS:
        ts = by_cat.get(c) or []
        if not ts:
            continue
        usr += "\n【%s·原始条目】\n" % CAT_CN[c]
        usr += "\n".join("- " + t for t in ts[:40])
    usr += "\n\n请归纳。"
    raw = llm.complete(SYS.format(n=topn), usr, temperature=0.3,
                       max_tokens=1600, num_ctx=16384)
    got = _parse_block(raw)
    for c in CATS:
        got[c] = got[c][:topn]
    return got


def stage_b(cand, use_llm=False, topn=8):
    # 按题材聚合：(题材) → {类别: [条目]}
    by_genre = defaultdict(dict)
    for key, items in cand.items():
        genre, cat = key.split("|")
        by_genre[genre][cat] = [x["t"] for x in items]
    rules = {}
    for genre in sorted(by_genre):
        got = None
        if use_llm:
            try:
                got = _llm_refine(genre, by_genre[genre], topn)
                print("  %-12s %s" % (genre, "".join(
                    "%s%d " % (CAT_CN[c][:2], len(got.get(c, []))) for c in CATS)))
            except Exception as e:
                print("  LLM 失败 %s：%s" % (genre, e))
                got = None
        for c in CATS:
            items = by_genre[genre].get(c) or []
            if not items:
                continue
            want = (got or {}).get(c) or []
            if not want:
                # 退化：程序化选 top（跨文档共识优先，短的优先）
                pool = sorted(cand["%s|%s" % (genre, c)],
                              key=lambda x: (-x["n"], len(x["t"])))
                seen = []
                for x in pool:
                    t = x["t"]
                    if not (6 <= len(t) <= 34):
                        continue
                    if any(len(grams(t) & grams(y)) / (len(grams(t) | grams(y)) or 1) > 0.6
                           for y in seen):
                        continue
                    seen.append(t)
                    want.append(t)
                    if len(want) >= topn:
                        break
            rules.setdefault(genre, {})[c] = want
    rules["_meta"] = {"cats": CATS, "cat_cn": CAT_CN, "llm": use_llm,
                      "topn": topn,
                      "note": "由 E:/小说资料 151 篇教程蒸馏而成，非原文摘录"}
    json.dump(rules, open(RULES, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    n = sum(len(v) for g, d in rules.items() if g != "_meta" for v in d.values())
    print("规则已写 %s，共 %d 条（llm=%s）" % (RULES, n, use_llm))
    return rules


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true", help="用本地 7B 做归纳（慢，约 10-20 分钟）")
    ap.add_argument("--topn", type=int, default=8)
    ap.add_argument("--skip-a", action="store_true")
    a = ap.parse_args()
    if a.skip_a and os.path.exists(CAND):
        cand = json.load(open(CAND, encoding="utf-8"))
    else:
        cand = stage_a()
    stage_b(cand, a.llm, a.topn)
