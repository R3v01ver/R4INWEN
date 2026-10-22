"""给抽取出来的 151 篇资料打标：频道 × 题材 × 用途，并抽要点块。

产出：
  corpus/index.json  —— 程序用（开书台按题材检索）
  corpus/INDEX.md    —— 人看（分类清单）

★ 打标靠关键词命中，文件名权重 3 倍于正文（正文只扫前 6000 字）。
  不追求 100% 准，够"按题材挑得出相关的"就行。
"""
import json
import os
import re
import glob

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")

# 频道：命中女频词 → 女频；否则男频（网文资料默认男频）；都没命中 → 通用
FEMALE = ["女频", "大女主", "古言", "后宫", "晋江", "言情", "霸总",
          "宅斗", "宫斗", "嫡女", "王妃", "总裁", "甜宠", "虐恋",
          "女强", "种田文", "婚恋", "cp粉"]
# 题材
GENRES = {
    "玄幻": ["玄幻", "玄幻小说", "异世", "斗气", "魂力", "魔法", "异能", "洪荒",
             "神力", "体质", "门派", "符箓", "异兽", "神器", "上古"],
    "仙侠": ["仙侠", "修仙", "修真", "炼丹", "丹药", "灵草", "飞升", "灵根",
             "仙门", "道友", "元婴", "筑基"],
    "都市": ["都市", "都市文", "商战", "职场", "娱乐圈", "神医", "兵王", "赘婿",
             "校花", "豪门", "重生都市", "装逼", "装比"],
    "古言": ["古言", "古代", "后宫", "宫斗", "宅斗", "王爷", "嫡女", "妃",
             "古代建筑", "古代服饰", "宫廷"],
    "历史": ["历史文", "架空历史", "朝代", "唐朝", "宋朝", "明朝", "清朝",
             "汉朝", "秦汉", "三国", "历史常识", "历史背景"],
    "科幻": ["科幻", "未来", "星际", "机甲", "末世", "废土", "赛博"],
    "网游": ["网游", "游戏", "副本", "公会", "虚拟游戏", "电竞"],
    "灵异": ["灵异", "鬼", "僵尸", "盗墓", "驱魔", "阴间", "鬼王", "捉鬼",
             "民间神仙", "百鬼", "亡灵"],
    "悬疑": ["悬疑", "推理", "侦探", "刑侦", "破案", "规则怪谈", "诡异"],
    "军事": ["军事", "抗战", "特种兵", "战场", "军旅"],
    "重生年代": ["年代", "重生年代", "知青", "改革开放", "八十年代", "风口",
                 "下岗", "生产队"],
    "短剧": ["短剧", "剧本", "一卡", "分镜", "短剧格式"],
    "校园": ["校园", "青春", "校草", "同桌", "高考", "大学"],
}
# 用途
USES = {
    "套路爽点": ["套路", "爽点", "爽文", "梗", "装逼", "装比", "打脸", "震惊",
                 "爆点", "高能剧情", "桥段", "必备套路", "反套路"],
    "设定素材": ["设定", "大全", "名录", "大辞典", "神器", "丹药", "灵草",
                 "阵法", "体质", "神力", "技能", "异兽", "常识", "资料",
                 "取名", "等级划分", "世界观", "修炼等级", "辞典"],
    "流程规则": ["投稿", "签约", "上架", "发书流程", "编辑联系", "作家后台",
                 "稿费", "网站选择", "签约流程"],
    "大纲结构": ["大纲", "主线", "结构", "节奏", "铺垫", "布局", "开头",
                 "把故事写长", "剧情设计", "范本"],
    "文笔": ["文笔", "描写", "修饰", "拟声词", "比喻", "台词", "金句",
             "代入感", "打斗"],
    "人物塑造": ["人物塑造", "角色设定", "反派", "人设", "如何塑造人物"],
}


def hits(text, words):
    return sum(text.count(w) for w in words)


def top(text, table, limit=None):
    sc = [(hits(text, ws), g) for g, ws in table.items()]
    sc = [(n, g) for n, g in sc if n > 0]
    sc.sort(key=lambda x: -x[0])
    if not sc:
        return [], {}
    # ★ 相对阈值：低于第一名 35% 的不算，避免"沾一个字就算"
    cut = max(2, sc[0][0] * 0.35)
    sc = [x for x in sc if x[0] >= cut]
    if limit:
        sc = sc[:limit]
    return [g for _, g in sc], dict((g, n) for n, g in sc)


def gist(text, budget=1000):
    """要点块：优先挑列表行（短、带编号），挑不出就取开头。"""
    lines = [l.strip() for l in text.split("\n")]
    items = []
    for l in lines:
        if not (4 <= len(l) <= 120):
            continue
        if re.match(r"^\s*(\d+[、.．)）]|[\(（【]?\d+|[一二三四五六七八九十]+[、．])", l) \
           or l.startswith(("·", "•", "-", "※")):
            items.append(l)
    if items and len(items) >= 3:
        out, used = [], 0
        for l in items:
            if used + len(l) > budget:
                break
            out.append(l)
            used += len(l)
        return "\n".join(out)
    body = "\n".join(l for l in lines if len(l) > 8)[:budget]
    return body


def main():
    rows = []
    for f in sorted(glob.glob(os.path.join(RAW, "*.txt"))):
        name = os.path.splitext(os.path.basename(f))[0]
        text = open(f, encoding="utf-8", errors="ignore").read()
        if len(text.strip()) < 40:
            continue
        head = text[:6000]
        # 文件名权重 3 倍
        weighted = (name + " ") * 3 + head
        # ★ 频道：文件名里带女频词才算女频；正文要命中 6 次以上才改判
        #   （只按正文算会把"常见400套路"这种通用书判成女频）
        channel = "女频" if (hits(name, FEMALE) or hits(head, FEMALE) >= 6) else "男频"
        gs, gsc = top(weighted, GENRES, limit=3)
        if not gs:
            channel = "通用"
        us, usc = top(weighted, USES, limit=2)
        rows.append({
            "file": os.path.basename(f),
            "title": name,
            "chars": len(text),
            "channel": channel,
            "genres": gs,
            "uses": us or ["其他"],
            "score": {"genre": gsc, "use": usc},
            "gist": gist(text),
        })
    rows.sort(key=lambda r: -r["chars"])
    json.dump(rows, open(os.path.join(HERE, "index.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # 人看的清单
    L = ["# 小说资料索引（E:\\小说资料 → corpus/raw）", "",
         "共 %d 篇，按「频道 / 题材 / 用途」分类。开书台会按你填的题材从这里挑资料喂给模型。" % len(rows), ""]
    for ch in ["男频", "女频", "通用"]:
        sub = [r for r in rows if r["channel"] == ch]
        if not sub:
            continue
        L.append("## %s（%d 篇）" % (ch, len(sub)))
        L.append("")
        byg = {}
        for r in sub:
            k = r["genres"][0] if r["genres"] else "未分类"
            byg.setdefault(k, []).append(r)
        for g in sorted(byg, key=lambda k: -len(byg[k])):
            L.append("### %s" % g)
            L.append("")
            for r in byg[g]:
                L.append("- **%s**（%d 字）· %s" % (
                    r["title"], r["chars"], "／".join(r["uses"])))
            L.append("")
    open(os.path.join(HERE, "INDEX.md"), "w", encoding="utf-8").write("\n".join(L))
    print("篇数", len(rows), "男频",
          sum(1 for r in rows if r["channel"] == "男频"),
          "女频", sum(1 for r in rows if r["channel"] == "女频"),
          "通用", sum(1 for r in rows if r["channel"] == "通用"))


if __name__ == "__main__":
    main()
