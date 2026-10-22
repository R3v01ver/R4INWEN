"""手法库（skill）——写正之前按需喂几段手册。

★ 为什么不整本塞进去：写作手册动辄几万字，全塞进去两个后果：
  1) 把正文挤掉（上下文就那么多）；
  2) 它开始背手册，不看这一章要干什么。
  ⇒ 只挑**跟眼前这一章最相关**的几段，且有字数预算。

★ 手册的定位（别搞反）：
  手册是"提醒"，不是"规则表"。它不能放宽红线，也不能改判定——
  红线由 redline.py 判，手册只影响它怎么写。
"""

import os
import re
from typing import Any, Dict, List

_H = re.compile(r"(?m)^#{1,6}\s+(.+)$")


def load(roots: List[str]) -> List[Dict[str, str]]:
    """扫目录里的 md，按标题切成一段一段。"""
    out: List[Dict[str, str]] = []
    for root in roots:
        root = (root or "").strip()
        if not root or not os.path.isdir(root):
            continue
        for dp, _, files in os.walk(root):
            for f in sorted(files):
                if not f.lower().endswith((".md", ".markdown")):
                    continue
                p = os.path.join(dp, f)
                try:
                    txt = open(p, encoding="utf-8").read()
                except Exception:
                    continue
                marks = [(m.start(), m.group(1).strip()) for m in _H.finditer(txt)]
                if not marks:
                    out.append({"title": f, "text": txt})
                    continue
                for i, (pos, title) in enumerate(marks):
                    end = marks[i + 1][0] if i + 1 < len(marks) else len(txt)
                    body = txt[pos:end].strip()
                    if body:
                        out.append({"title": f"{f} · {title}", "text": body})
    return out


# ★ 写正文时**不该参考**的段落：这些是立项/上架用的，塞进来只会让它
#   在写第二章的时候去琢磨书名和标签。
SKIP = ("书名", "立项", "标签", "封面", "查重", "简介", "投稿", "时间账", "字数")


def _words(t: str) -> set:
    """中文按字、英文按词。★ 别用子串匹配：'api' 会命中 'rapid'。"""
    return set(re.findall(r"[\u4e00-\u9fa5]{2}|[a-zA-Z]{3,}", (t or "").lower())

               )
def pick(chunks: List[Dict[str, str]], ctx: str, budget: int = 1800
         ) -> List[Dict[str, str]]:
    """按跟这一章的相关度挑几段，直到预算用完。"""
    if not chunks:
        return []
    kw = _words(ctx)
    if not kw:
        return chunks[:2]
    scored = []
    for c in chunks:
        if any(k in c["title"] for k in SKIP):
            continue
        w = _words(c["title"] + " " + c["text"][:400])
        hit = len(kw & w)
        if hit:
            scored.append((hit, c))
    scored.sort(key=lambda x: -x[0])
    out, used = [], 0
    for _, c in scored[:8]:
        t = c["text"]
        if used + len(t) > budget:
            t = t[:max(200, budget - used)]
        out.append({"title": c["title"], "text": t})
        used += len(t)
        if used >= budget:
            break
    return out


def render(cfg: Dict[str, Any], ctx: str) -> str:
    """给模型的手册片段。没开就返回空。"""
    if str(cfg.get("skill_on") or "0") != "1":
        return ""
    roots = [r for r in (cfg.get("skill_roots") or "").split(";") if r.strip()]
    if not roots:
        return ""
    try:
        budget = int(cfg.get("skill_chars") or 1800)
    except Exception:
        budget = 1800
    got = pick(load(roots), ctx, budget)
    if not got:
        return ""
    lines = ["【手法库节选（提醒，不是规则；红线照旧）】"]
    for g in got:
        lines.append("---- " + g["title"])
        lines.append(g["text"])
    return "\n".join(lines)
