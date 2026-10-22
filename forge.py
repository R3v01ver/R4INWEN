"""小说生成器（任务卡驱动 + 红线闸门）。

★ 这不是"点一下吐一百章"。那玩意儿写出来的东西正好撞在平台
  「AI 粗制滥造」那一条上，发了就是限流。

它的做法是：
  立项（书名三段齐全）→ 章纲（≤10 字爆点标题）→ 逐章任务卡 → 模型写
  → **红线自检 → P0 不过就带着问题清单打回重写**（最多三轮）
  → 过了才落盘 → 台账记下本章新增的人、地、数、物

★ 判定权在脚本手里，不在模型手里。模型只负责"写"，
  "这一章能不能发出去"由 redline.py 说了算——这跟"让模型自己判断
  写得好不好"是两回事，后者等于没有检查。

用法：
  python forge.py new    --genre 都市异能 --gimmick "能看见别人的死期" --hook 收割
  python forge.py outline
  python forge.py write 1
  python forge.py batch 1 10
  python forge.py check 3
  python forge.py ping
"""

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

# ★ 打包成 exe（PyInstaller）后，__file__ 指向临时解压目录，
#   用它当根会找不到 books/ 和 settings.json。
#   ⇒ 冻结态下一律以 exe 所在目录为根。
if getattr(sys, "frozen", False):
    HERE = os.path.dirname(sys.executable)
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import llm                                    # noqa: E402
import skills as SK                           # noqa: E402
import tracks                                 # noqa: E402
import style                                  # noqa: E402
import redline                               # noqa: E402
from rules import NEWINFO_TMPL, WRITE_RULES  # noqa: E402

BOOKS = os.path.join(HERE, "books")
STATE = os.path.join(HERE, "current.json")
MAX_RETRY = 3


# ---------------------------------------------------------------------------
# 作品目录
# ---------------------------------------------------------------------------

# ★ 书目录名＝白名单字符，跟 launch.create_book 的清洗规则对齐
#   （那边是 re.sub(r"[^\w一-龥]", "", slug)）：两边一致，
#   才不会"建的时候洗过、用的时候不洗"。
_SLUG_OK = re.compile(r"^[\w一-龥]{1,40}$")


def safe_slug(slug: str) -> str:
    """★ 路径穿越闸门。slug 会拼进 books/<slug>/chapters/...，
    以前一个校验都没有——webui 里传个 "../../.." 就能写磁盘任意位置。
    ⇒ 白名单字符 + 长度上限；不合法直接 raise，调用方转成 400。
    """
    s = (slug or "").strip()
    if not _SLUG_OK.match(s):
        raise ValueError(f"书目录名不合法：{slug!r}（只要中英文、数字、下划线）")
    return s


def book_dir(slug: str = None) -> str:
    if slug:
        s = safe_slug(slug)
        d = os.path.join(BOOKS, s)
        # 双保险：realpath 必须还在 books/ 里（防符号链接绕路）
        root = os.path.realpath(BOOKS)
        real = os.path.realpath(d)
        if real != root and not real.startswith(root + os.sep):
            raise ValueError(f"书目录跑出了 books/：{slug!r}")
        return d
    if not os.path.exists(STATE):
        raise SystemExit("还没开书。先跑：python forge.py new --genre ... --gimmick ... --hook ...")
    return json.load(open(STATE, encoding="utf-8"))["dir"]


def read(name: str, slug: str = None) -> str:
    p = os.path.join(book_dir(slug), name)
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


def write(name: str, text: str, slug: str = None) -> str:
    d = book_dir(slug)
    # ★ 文件名也过闸。内部调用拼的是 chapters/第N章 标题.md，
    #   但标题来自模型生成——一个 "../" 就能翻到书外面去。
    if (".." in name or name.startswith(("/", "\\")) or os.path.isabs(name)
            or ":" in name or name.count("/") + name.count("\\") > 1):
        raise ValueError(f"文件名不合法：{name!r}")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name)
    open(p, "w", encoding="utf-8").write(text)
    return p


# ---------------------------------------------------------------------------
# 立项
# ---------------------------------------------------------------------------

TITLE_OK = re.compile(r"^[一-龥A-Za-z0-9！？，：、·《》\[\]\s]+$")

# ★ 抽象虚词：这些词放在哪本书上都成立，所以放在书名里＝没写。
TITLE_HOLLOW = re.compile(
    r"秘密|真相|命运|宿命|契机|未来|前程|之路|传说|传人|奇迹|传奇|史诗|"
    r"不属于|不属于这个|已改变|改变了|终将|注定|逆袭|翻盘|逆天改命|"
    r"不为人知|无人知晓|隐藏的|不同的|另一方")

# ★ 文言腔：书名是广告牌，要像人喊出的一句话，不是诗词。
#   他拿一批「推崖下的我，能否复原师姐那一脚」骂过「全是病句」——
#   这批的共同点就是文言腔（欲／终／赎／能否／得偿所愿）。
TITLE_LITERARY = re.compile(
    r"能否|得偿所愿|救赎|赎(?!身)|欲(?!望|食|情)|(沉|鸣|申|昭)冤|冤$|之殇|之痛|之泪")


# ★★ 安全前提（他点名骂过：「入侵政府服务器这种话都写的出来，你要我命啊」）
#   黑客／攻防题材可以写，但**必须写明那是虚拟的**：虚拟机／模拟环境／演练／
#   演习／靶场／游戏副本。而且绝不许拿真实机构当对象。
UNSAFE_ACT = re.compile(
    r"入侵|侵入|攻破|攻入|攻陷|黑进|黑了|渗透|提权|拖库|劫持|瘫痪|爆破|"
    r"拿下.{0,4}(服务器|权限|后台|系统|数据库)|"
    r"控制.{0,3}(服务器|后台|系统)|绕过.{0,4}(认证|验证|登录)|拿到.{0,3}(权限|口令)")
UNSAFE_TARGET = re.compile(
    r"政府|政务|公安|警察|税务|国税|央行|银行|军队|军方|部队|医院|电网|电力|"
    r"铁路|航空|机场|教务|电视台|运营商|社保|医保|法院|海关|检察院")
UNSAFE_SAFEWORD = re.compile(
    r"虚拟|模拟|演练|演习|靶场|沙箱|沙盘|游戏|副本|授权|测试环境|虚构|内测")


def check_unsafe(t: str) -> List[str]:
    """安全前提检查：这条比文笔重要——被单独截图发出来像真的在攻击谁，就是事故。"""
    t = t or ""
    a = UNSAFE_ACT.search(t)
    if not a:
        return []
    # ★ 已经写明是虚拟／演练的，放行（哪怕提到了政府、电网——那是沙盘里的）
    if UNSAFE_SAFEWORD.search(t):
        return []
    tg = UNSAFE_TARGET.search(t)
    if tg:
        return ["写了「%s」＋真实机构「%s」——不许拿真实机构当攻击对象，"
                "换成虚拟的（某集团内网演练平台／模拟沙盘）" % (a.group(0), tg.group(0))]
    if not UNSAFE_SAFEWORD.search(t):
        return ["写了「%s」——必须写明是虚拟／模拟／演练环境，"
                "不写就等于在说真的攻击" % a.group(0)]
    return []


# ★★ 涉黄暗示（书名、章节名、简介共用）
#   他截图抓到过「我让世子爷下不了床」——这种词在番茄=整改下架，书直接没了。
#   判成硬伤，一条都不摆出来。只收一看就懂的那个词，不收「青楼／媚」这种
#   可能在正常语境里出现的（误伤比漏判更糟：误伤是把好书扔了）。
NSFW = re.compile(
    r"下不了床|下不来床|床笫|春宵|侍寝|初夜|破处|淫|媚药|春药|合欢|双修|"
    r"采补|采阳|补阳|房中术|娇喘|呻吟|浪叫|湿身|撩人|风骚|狐媚|滚床单|"
    r"共枕|鸳鸯戏|开房|约炮|一夜春|销魂|索求无度|夜夜呻")


def check_nsfw(t: str) -> List[str]:
    m = NSFW.search(t or "")
    if m:
        return ["涉黄暗示「%s」——番茄见到这种直接整改下架，书就没了，必须换" % m.group(0)]
    return []


# ★★ 病句检查（书名、章节名共用）
#   他点名骂过：「我要我跪着来」「我温柔抚过我」「给粥下药」——全是病句。
#   语法机器查不全，只查三种一眼就能看出来的硬伤：
#     ① 一句里两个「我」挨着 —— 自己对自己，施受关系崩了
#     ② 结尾悬空 —— 话没说完（「……我跪着来」）
#     ③ 开头悬空 —— 没有主语，谁干的不知道（「给粥下药」）
#   ★ 「了／着／过」是正常句尾助词（「灯还亮着」「搬空了」），不许判。
GRAMMAR_TAIL = set("来把被使向从到而或且与并就才还又再的地得敢")
GRAMMAR_HEAD = set("把被使给向从到而或且与并")


def grammar_bad(t: str) -> List[str]:
    """病句／成分残缺。书名和章节名都跑这一条。"""
    t = re.sub(r"\s", "", t or "")
    if len(t) < 2:
        return []
    bad = []
    # ① 两个「我」
    pos = [i for i, c in enumerate(t) if c == "我"]
    if len(pos) >= 2 and min(b - a for a, b in zip(pos, pos[1:])) <= 9:
        bad.append("病句：一句里两个「我」，自己对自己？换成对方（他／她／他们）或身份")
    # ② 结尾悬空
    if t[-1] in GRAMMAR_TAIL:
        bad.append("病句：话没说完（最后一个字是「%s」）" % t[-1])
    # ③ 开头悬空（有「我／你／他／她／它」＝已经有主语，不判）
    if t[0] in GRAMMAR_HEAD and not re.search(r"[我你他她它]", t):
        bad.append("病句：缺主语——谁做的？写清是谁")
    # ④ 「第几天／第几次」＝没写数。数要具体到第九天、第三次才抓人。
    m = re.search(r"第几|多少[个天次]|某个", t)
    if m:
        bad.append("「%s」不是数——写成具体的（第九天／第三次／第五十天）" % m.group(0))
    # ⑤ 两个「被」堆一起（「背叛被误推」）＝两个被动往一句里塞，读不通。
    if t.count("被") >= 2:
        bad.append("病句：两个「被」堆在一起，读不通——留一个，另一个改成主动")
    # ⑥ 「亲手推下悬崖」＝动作没说对谁做（「师姐被误会，我亲手推下悬崖」被骂过）。
    m = re.search(r"亲手([一-龥]{1,4})$", t)
    if m and not re.search(r"[他她它把]", m.group(1)):
        bad.append("病句：动作没说对谁做——「亲手%s」，谁承受这个动作？" % m.group(1))
    # ⑦ 「被我，」「被他。」＝「被」后面没有动词（「首辅被我，满殿长老俯首称臣」）。
    if re.search(r"被[我你他她它]\s*[，,。！？]?$|被[我你他她它][，,]", t):
        bad.append("病句：「被」后面缺了动作——被我怎么样？")
    # ⑧ 两句黏一起（实测「这笔钱如何使用？这笔资金用于哪个项目」——把两个标题拼一句）。
    #    逗号后面接半句是书名/章节名的正常写法，问叹号分号后面还有字就是黏。
    m = re.search(r"[。！？；](?=.)", t)
    if m:
        bad.append("病句：两句黏在一起了（「%s」后面还有话）——只能有一句" % m.group(0))
    return bad


def check_title(t: str, name: str = "") -> List[str]:
    """书名自检。★ 三段齐全机器查不了（要人判断），只查能查的。"""
    bad = grammar_bad(t) + check_nsfw(t) + _leak(t)
    n = len(re.sub(r"\s", "", t))
    # ★ 他点名：书名不要省字。太短在书架上没有抓手，12–15 字最好。
    if n > 15:
        bad.append(f"长度 {n} 字，最多 15 字")
    elif n < 13:
        bad.append(f"只有 {n} 字，卖点没写足——14–15 字最好，把 15 个字用满")
    if not TITLE_OK.match(t):
        bad.append("含不允许的字符（只能中英文、数字、叹号、逗号、问号、中括号、冒号）")
    # ★ 抽象虚词在哪本书上都能用＝等于没写。他点名「书名不够吸引人」，
    #   头号毛病就是拿这些词凑字数：藏着XX的秘密／命运改变／踏上XX之路。
    m = TITLE_HOLLOW.search(t)
    if m:
        bad.append("「%s」是空话，谁都能用——换成一个看得见的东西（一只鞋、第三行字、"
                   "半张纸）" % m.group(0))
    # ★ 文言腔＝另一路病句（能否／欲／赎／救赎／得偿所愿），广告牌不写诗词。
    m = TITLE_LITERARY.search(t)
    if m:
        bad.append("「%s」是文言腔——书名要像人喊出的一句话，把这个字眼换成大白话"
                   % m.group(0))
    # ★★ 它把提示词里的爆款示例原样端出来了（实测「时停起手，邪神也得给我跪下」
    #   直接进了输出）。抄示例＝没动脑子，一条都不许摆出来。
    bad += _leak(t)
    # ★ 他／她／它一律指主角（他点名：书名用「我」）。
    #   ★ 句中已经写了「我」→ 那个他／她指的是别人（「她的名字被我划掉」），放行。
    # ★ 他点名：书名里不要有角色名。爆款榜上没有一个用角色名取名的——
    #   读者不认识这个人，算法也拿人名打不出题材标签。
    #   ★ name 可以是多个写法（全名＋去掉姓的名），避免只写「玉容」漏判。
    for nm in ([name] if isinstance(name, str) else list(name or [])):
        if nm and len(nm) >= 2 and nm in t:
            bad.append("写了角色名「%s」——换成身份说法（冷宫里那个／假千金）"
                       "或直接写「我」" % nm)
    if "我" not in t and re.search(r"[他她它]", t):
        bad.append("用了他／她／它，换成「我」或身份")
    return list(dict.fromkeys(bad))


def check_cheat(t: str) -> List[str]:
    """金手指方案自检（期望「能力｜限制｜成长」）。

    ★ 头号问题不是没写限制，是**限制把能力废掉了**（能预见未来但不能改变命运）。
      这条机器查不出来，只能查"有没有写"和"是不是做成了可量化的东西"。
    """
    bad = []
    if len(t) > 70:
        bad.append("太长（三段加起来 ≤60 字）")
    if re.search(r"读数|面板|倒计时|余额|冷却|积分|进度条|百分比|属性值", t):
        bad.append("做成可量化的了")
    # ★ 他点名：金手指的成长是跟着**主角**成长的，不是跟着章节数涨的。
    if re.search(r"\d+\s*章|第[一二三四五六七八九十百千两\d]+\s*章|章后|章起|章内|章开始", t):
        bad.append("成长按章节算了（要写成主角怎样了→它才怎样）")
    # ★ 按段查，比全文扫准：玄幻那种「望气术识吉凶｜必须耗气，每天一次｜…」
    #   全文扫「能」会误报没写能力，按段看第一段就是能力。
    segs = [s.strip() for s in re.split(r"[|｜]", t) if s.strip()]
    if segs:
        # ★★ 他点名骂过：「知晓自身修为变化什么时候也能成金手指了，这有啥用啊？」
        #   只是看自己的状态＝读数，读者看着没用，主角也靠它赢不了。
        m = re.search(r"(知晓|知道|察觉|感知|看见|看到|显示|读取|看清|感受|体会)"
                      r".{0,8}(自身|自己|我的|本人|修为|境界|状态|数值|属性|等级|"
                      r"进度|心境|血量|体力|灵力|经验)", segs[0])
        if m:
            bad.append("「%s」只是看自己的状态——那是读数，不是金手指：读者看着没用，"
                       "主角也靠它赢不了。要写成能对**别人或外面的事**做成一件具体的事"
                       % m.group(0))
    if len(segs) >= 3:
        if len(segs[0]) < 3:
            bad.append("能力段太空")
        elif re.search(r"^（?待", segs[0]) or segs[0] in ("系统", "能力", "金手指"):
            bad.append("能力写成了空词（系统／能力）")
        if not re.search(r"但|不能|只能|不许|每次|每天|一天|三天|每小时|需|必须|代价|条件|"
                         r"限|除非|耗|消耗|崩溃|反噬|失效", segs[1]):
            bad.append("限制段没写代价或条件")
        # ★★ 他点名骂过：限制写成「门槛」。
        #   「必须当天知道真名」——真名那么好知道？主角第一天知道不了，
        #   这个能力一天都用不上，等于没写。
        #   ⇒ 限制只能是**用了之后付什么／多久能用一次／管多大范围**，
        #     不许是**先用一件难事去换**（真名、信物、让对方先开口）。
        m = re.search(r"必须.{0,4}知道|必须拿到|必须先|得先|要先|需先|除非先|前提是|"
                      r"只有.{0,10}才|真名|本名|姓名|名字|对方主动|对方先", segs[1])
        if m:
            bad.append("「%s」是**门槛**不是限制——主角得先搞定一件难事才用得上，"
                       "第一章根本用不了。改成代价或频率（每次吐血／一天一次）"
                       % m.group(0))
        if re.search(r"不能改变|不会影响|无效|不管用|结果不变|没有用|无法改变", segs[1]):
            bad.append("限制把能力废掉了——加上这条限制主角赢不了")
        if not re.search(r"变强|成长|提升|解锁|升级|熟练|深入|第二层|范围|进化|"
                         r"熟悉|积累|进阶|突破|后可|后能|不限|掌握|修炼|增加|减少|"
                         r"加快|提高|扩大|延伸|感应|预知|感知|加深|加强|"
                         # ★ 「越×／更×／变×」一律算成长（越提前／更及时／变粗变长——
                         #   枚举是枚举不完的，实测误报过四次，直接收一整类）
                         r"越[一-龥]|更[一-龥]|变[一-龥]|"
                         # ★ 「副作用逐渐减轻」「白天也能使用」也是成长，别误判
                         r"减轻|适应|减弱|也能|不再|逐渐|副作用", segs[2]):
            bad.append("成长段没写靠什么变强")
    else:
        if not re.search(r"能|可以|能够|让", t):
            bad.append("没写能力")
        if not re.search(r"但|不能|只能|每次|每天|一天|需|必须|代价|条件|限|耗", t):
            bad.append("没写限制")
        if len(segs) < 2:
            bad.append("没拆成能力｜限制｜成长三段")
        else:
            bad.append("缺成长段")
    return bad


# ★ 简介里不许出现的：说明性写法（讲规则／讲设定／讲对手／预告结局）
#   他点名过：简介一写完读者就知道这本书写什么，那就不点了。
INTRO_HARD = re.compile(
    r"金手指|系统|觉醒|异能|超能力|副作用|冷却|面板|属性值|"
    r"逆天改命|逆袭|翻盘|崛起|巅峰|传奇|热血|人生|命运|宿命|"
    r"仿佛|似乎|宛如|"
    r"这是一个关于|这是一个讲述|且看他|从此踏上|且看.*如何")
INTRO_SOFT = re.compile(
    r"代价|限制|规则|设定|世界观|对手是|反派是|大反派|"
    r"他要拿回|他要夺回|为了.*必须.*付出|"
    # ★ 把赢法写出来＝读者知道后面全是这一套
    r"从此|最终|终于|反杀|翻身|一雪前耻|登顶|走上人生巅峰|"
    # ★★ 谜语人结尾（番茄编辑明确打低分）：
    #   「她究竟该如何抉择」「前路通向何方」——看着像悬念，其实啥也没悬。
    r"如何抉择|何去何从|究竟该如何|前路.{0,6}何方|能否成功|"
    r"又会(如何|怎样)|是否(会|能).{0,12}[？?]|到底会怎样")


# ★★ 跨项目红线：正文里「仿佛／似乎／宛如」出现 0 次。
#   这三个词机器查得出来，就直接洗掉（删掉这个副词句子照样通顺），
#   不劳神让模型重写——重写一次它又换地方用一遍。
_FUZZY = re.compile(r"仿佛|似乎|宛如|犹如|好似")

# ★★ AI 味标记：他零容忍的那一类（「咳嗽声如狼嚎般刺耳」实测被抓到）。
#   这些不是文笔问题，是模型指纹——他一眼就能认出是机器写的。
AI_SMELL = re.compile(
    r"如[^，。！？；]{1,8}般|像[^，。！？；]{1,8}一样|映入眼帘|"
    r"(?:眼中|眼神中|目光中|眼底)满是|嘴角.{0,3}(?:勾起|扬起|泛起)|"
    r"微微一[微笑颤动抖颤抽]|闪过一丝|闪过一抹|"
    r"不由得|下意识地|暗暗.{0,2}(?:道|想|下决心)")


def wash(t: str) -> str:
    """删掉模糊比喻词。"""
    return _FUZZY.sub("", t or "")


def check_intro(t: str) -> List[str]:
    """简介自检：只查「是不是变成说明书了」，文笔好不好机器查不了。"""
    bad = check_nsfw(t) + _leak(t)
    hits = []
    for m in INTRO_HARD.finditer(t or ""):
        if m.group(0) not in hits:
            hits.append(m.group(0))
    if hits:
        bad.append("写了「%s」——简介里讲规则讲设定，读者看完就不点了"
                   % "／".join(hits[:3]))
    m2 = INTRO_SOFT.search(t or "")
    if m2:
        bad.append("写了「%s」——这是说明不是勾子，删掉换成一件事" % m2.group(0))
    # ★★ AI 味指纹：他点名过「AI 味道太浓」。「如狼嚎般」「映入眼帘」「眼中满是」
    #   这类是模型指纹，一眼就能认出是机器写的——标出来让他直接毙掉这一版。
    m3 = AI_SMELL.search(t or "")
    if m3:
        bad.append("「%s」是 AI 味的比喻／套话——换成一个只有这本书里会出现的动作"
                   % m3.group(0))
    # ★★ 结尾谜语人：番茄编辑对「她究竟该如何抉择」这类结尾一律打低分——
    #   读者还没跟主角共情，问再多也白问。要换成**有具体指向**的那一句。
    # ★ 切完最后会多一个空串（切在标点后面），必须过滤掉再取最后一句
    parts = [p for p in re.split(r"(?<=[。！？\n])", (t or "").strip()) if p.strip()]
    tail = (parts[-1] if parts else "").strip()
    if INTRO_RIDDLE.search(tail):
        bad.append("结尾是空悬念（「%s」）——读者还没共情，问了也白问。"
                   "换成一个具体指向的东西（他口袋里那张协议，签字栏空了十年）"
                   % tail[:20])
    return bad


INTRO_RIDDLE = re.compile(
    r"如何|何去何从|能否|会不会|是不是|是否|究竟|到底|抉择|该如何|"
    # ★ 「我该接受他的挑战，还是坚持自己的立场？」——还是…还是…＝二选一提问，
    #   看着像悬念，其实啥也没给（他截图骂过的「我该接受还是坚持」就是这一句）
    r"还是.{0,14}还是|该不该|是去是留|是走是留")


CHAP_SOFT = re.compile(
    r"开始|出现|决定|回忆|相遇|介绍|铺垫|交代|日常|准备|出发|到达|结束|总结|"
    r"终于|重新|往事|秘密之|身世|内心|思考|计划|"
    # ★ 四字抽象概括是章节名的头号毛病（被开除／权限觉醒／初尝甜头／危机降临）
    r"觉醒|降临|逆转|局势|危机|转机|真相|秘密|突破|升级|试炼|开局|机会|选择|"
    r"布局|算计|博弈|交锋|对峙|谈判|争斗|战斗|冲突|风波|动荡|变故|突变|剧变|"
    r"来临|归来|离开|登场|现身|意外|惊喜|震惊|困境|觉悟|领悟|成长|变化|转折|"
    r"反击|逆袭|打脸|翻身|甜头|察觉|任务|系统|"
    # ★ 七言八股的后半个词（它最爱凑成「井边打水遇异常」「半块御牌引猜疑」）
    r"遇异|异常|猜疑|机心|端倪|玄机|隐情|痕|线索|蛛丝|浮现|显露|露出|暴露|"
    r"之音|之物|之影|之谜|之称|之心|之意|之感|之约|之变|之日|之夜|之后续|"
    # ★ 「代码行背后的谜团」——「谜团／疑云／奥秘」是换个说法的空话，
    #   跟「真相／秘密」一家人，一律拦掉（他截图骂过「背后的谜团」）
    r"谜团|疑云|奥秘|悬案|真相是|背后|暗流|漩涡|棋局|棋子|代价|"
    r"提升|认可|测试|考核|晋升|签约|上任|辞职|转岗|加入|成立|组建|建立|"
    r"成功|失败|上线|优化|发布|融资|庆功|表彰|获奖|成名|走红|表态|回应|"
    # ★「权限激活」这类＝两个抽象名词拼在一起，还是概括，不是爆点
    r"激活|开启|解锁|达成|获得|得到|掌握|拥有|具备|显示|提示|通知|记录")
CHAP_ABSTRACT = re.compile(
    r"^被(开除|辞退|抛弃|拒绝|退婚|陷害|追杀|羞辱|赶出|利用)$|"
    r"^(权限|能力|系统|血脉|修为|实力|身份|记忆|真相)(激活|觉醒|提升|恢复|开启|解锁)$")


def _ngram_hits(a: str, b: str, k: int = 2) -> int:
    """a 里有多少段（连续 k 字）在 b 里出现过。

    用来抓「章节名＝把本章内容抄一遍」：中文两两字容易撞，
    所以要连撞 3 段才算复述，撞 1–2 段放行。
    """
    def clean(s):
        s = re.sub(r"[\s，。！？；：、,.!?;:|｜「」『』《》()（）\-—…·]+", "", s or "")
        return s
    a, b = clean(a), clean(b)
    grams = set(b[i:i + k] for i in range(max(len(b) - k + 1, 0)))
    hits, i = 0, 0
    while i + k <= len(a):
        if a[i:i + k] in grams:
            hits += 1
            i += k              # 命中就整段划掉，不重复计
        else:
            i += 1
    return hits


# ★★ 提示词里举的例子会被它**原样抄进输出**（玄幻章纲里冒出「密码是她的生日」
#   就是这么来的——那是我自己写在示例里的）。所以示例一律进黑名单：
#   模型没动脑子的证据，抄了就是不合格。
EXAMPLE_LEAK = (
    "密码是她的生日", "离职证明糊他脸上", "深夜十二点屏幕亮了", "账户里多出五十万",
    "三个人敲开了门", "老板跪着求我回来", "她寄来的第七封信", "他把那封信按进了",
    "原告律师替我付了保释金", "我等了你十年", "信上第三行字被刮掉了",
    "他掀开桌布", "她说完那句，把碗摔在了地上",     "母亲留下的语音只有六秒",
    # ★ 这一轮新加进提示词的示例，同样进黑名单
    "前世的杀父仇人", "相亲对象", "救过她三次", "亲手埋", "保险柜里的照片",
    "四个已经死了",
    # ★ 书名提示词里的爆款示例（实测被原样抄进输出）
    "让你管后勤", "军火库搬空", "假千金不干了", "全家跪着", "时停起手",
    "邪神也得给我跪下", "全县每人每天给我一块钱", "749收容", "三个徒弟全叛出",
    "退婚当天，我直播", "全城都在等我看笑话", "逆袭成首富", "踩在脚下",
    "屏幕一闪", "多出一行没写过的代码",
    # ★ 简介提示词里的示例句（实测两版都原样抄了「揣着那张离婚协议，签字栏空了十年」）
    "签字栏空了十年", "西装口袋里", "离婚协议，签字栏",
    # ★ 细纲／章纲提示词里的示例
    "摔玉简", "掷令牌", "被逐出宗门", "客栈老者", "云天阁",
    # ★ 上面那些整句示例，它会换个主语再抄一遍（实测「他掀开桌布」→「老板掀开桌布」）
    #   ⇒ 再按**去主语的核心片段**拦一道：只放动作＋物件，不放人名代词。
    "掀开桌布", "剑鞘还在滴血", "桌布，剑鞘", "把碗摔在了", "语音只有六秒",
    "第三行字被刮掉", "码是她", "码是他", "码是我的生日", "保释金",
)


def _leak(t: str) -> List[str]:
    for e in EXAMPLE_LEAK:
        if e in t:
            return ["抄了提示词里的示例「%s」——没动脑子，换成这本书自己的东西" % e]
    return []


# ★ 章节名的**硬伤**（抄示例／病句／字数／角色名）vs 软伤（像概括／复述）。
#   抢救重写后要比的是硬伤：只要硬伤没变多就换，否则「红字条数相同就不换」
#   会让抢救白跑一趟（实测 12 章全红、一次都没换）。
CHAP_HARD_KEYS = ("抄了提示词", "病句", "只有", "字，最多", "空", "带章号",
                  "写了角色名", "把本章内容复述")


def chap_hard(bad) -> int:
    return sum(1 for b in (bad or []) if any(k in b for k in CHAP_HARD_KEYS))


def chap_cmp(bad) -> tuple:
    """硬伤少的在前；硬伤一样再比总数。"""
    return (chap_hard(bad), len(bad or []))


CHAP_SPOIL = re.compile(
    r"答应了|认出来了|找到了|露馅了|成功了|进来了|出来了|回来啦|"
    r"我先.{0,2}一步|终于做到|圆满|搞定|拿下|赢了|输了|揭穿|承认了")


def check_chap(t: str, src: str = "", name: str = "") -> List[str]:
    """章节名自检。判据：想知道发生了什么，但猜不出发生了什么。

    ★ src 传本章的「这一步干什么」＋「章末钩子」。他点名：不要把本章内容写出来。
      标题里连着三段都跟原文撞＝那是复述，不是钩子。
    ★ name 传主角姓名：书名不许带人名，章节名同样不许。
    """
    t = re.sub(r"\s", "", t or "")
    if not t:
        return ["空"]
    bad = grammar_bad(t) + check_nsfw(t) + check_unsafe(t) + _leak(t)
    n = len(t)
    # ★★ 番茄免费平台（番茄/七猫/书旗）实测：章节标题 **10–18 字**为宜，
    #   要短句化、冲突化、身份化——读者滑目录 0.3 秒决定点不点。
    #   太短（四五个字）撑不起一个小钩子，太长（>18）目录里会被截断。
    if n > 18:
        bad.append(f"{n} 字，最多 18 字——目录里会被截断")
    elif n < 8:
        bad.append(f"只有 {n} 字——章节名是这一章的小钩子，8–18 字才撑得起来")
    if re.match(r"^第?\d+章", t):
        bad.append("带章号")
    if CHAP_SOFT.search(t) or CHAP_ABSTRACT.search(t):
        bad.append("像概括不像爆点")
    m = CHAP_SPOIL.search(t)
    if m:
        bad.append("写了「%s」——把结果说出来了，这章就不用看了" % m.group(0))
    # ★ 他点名：章节名里不要有角色名。
    for nm in ([name] if isinstance(name, str) else list(name or [])):
        if nm and len(nm) >= 2 and nm in t:
            bad.append("写了角色名「%s」——换成身份（为首的那个／前夫）或直接写「我」"
                       % nm)
    # ★ 他点名：不要直接把本章的内容写出来。连撞三段＝照抄，放行 1–2 段。
    if src and _ngram_hits(t, src) >= 3:
        bad.append("把本章内容复述了一遍——只露一角，不要把这一章说明了")
    # ★ 他／她／它可以出现，但只能指别人。
    #   标题里有「我」→ 那个他／她就是别人（她没认出我），不判；
    #   没有「我」又以他／她开头 → 大概率在写主角，提示。
    #   「它」多半是物件（它亮了＝屏幕亮了），不判。
    #   短标题（≤6 字）里这个他／她基本就是主角（他的反击／她跪了），提示；
    #   长标题多半已经交代了别人（她寄来的第七封信），不误伤。
    if re.match(r"^[他她]", t) and "我" not in t and len(t) <= 8:
        bad.append("他／她指的是主角吗？主角要写「我」或身份")
    return list(dict.fromkeys(bad))


BIBLE_TMPL = """# {title}

- 题材：{genre}
- 金手指：{gimmick}
- 爽点：{hook}
- 字数目标：{words}
- 一句话钩子：{logline}

## 金手指规则表
★ 只能往后加限制，不许改已有规则（改了＝推翻前文所有用过它的情节）。

1. 怎么触发：
2. 边界（做不到什么）：
3. 代价／反噬：
4. 谁能看穿：
5. 什么时候会失效：

## 主角三格
1. 他缺什么（要具体到一件东西）：
2. 他的一个生活细节（别人没有的）：
3. 遇到事他怎么处理（跟同场其他人不一样在哪）：

## 世界的三条规矩
1.
2.
3.

## 五阶段骨架（每段末那一刀）
1. 开局（1–30）：→ 末刀：
2. 立住（31–100）：→ 末刀：
3. 翻面（101–250）：→ 末刀：
4. 收束（251–400）：→ 末刀：
5. 终局（401–）：→ 末刀：
"""


def cmd_new(a) -> int:
    slug = a.slug or re.sub(r"[^\w一-龥]", "", (a.title or a.genre))[:20] or "book"
    d = os.path.join(BOOKS, slug)
    os.makedirs(os.path.join(d, "chapters"), exist_ok=True)

    title = a.title
    if not title and not a.dry:
        ok, why = llm.ping()
        if ok:
            sys_p = "你是番茄小说的立项编辑。只输出书名，一行一个，不要解释。"
            usr_p = (f"题材：{a.genre}　金手指：{a.gimmick}　爽点：{a.hook}\n"
                     "按「题材＋金手指＋爽点」出 3 个书名，最多 15 字，"
                     "金手指是重点、爽点是噱头。"
                     "爽点必须落在『对方具体丢了什么』上："
                     "主角得到的东西，得是从对方身上割下来的。")
            raw = llm.complete(sys_p, usr_p, temperature=1.0)
            cands = [x.strip(" 0123456789.、-") for x in raw.splitlines() if x.strip()]
            cands = [c for c in cands if len(c) >= 6][:3]
            if cands:
                title = cands[0]
                print("书名候选：")
                for c in cands:
                    print("   " + c + ("" if not check_title(c) else "  ⚠ " + "；".join(check_title(c))))
        else:
            print(f"模型没连上（{why}），书名先空着，你自己填 bible.md")

    if title:
        bad = check_title(title)
        print(("书名：" + title) + ("" if not bad else "  ⚠ " + "；".join(bad)))
        print("★ 书名要你自己看一眼：读者看得出主角要打谁的脸吗？")

    bible = BIBLE_TMPL.format(
        title=title or "（待定）", genre=a.genre, gimmick=a.gimmick,
        hook=a.hook, words=a.words, logline=a.logline or "（待补）")
    if a.track:
        seg = tracks.fill_bible(a.track)
        if seg:
            bible += "\n" + seg
            print(f"套了赛道参数：{a.track}")
        else:
            print(f"没有「{a.track}」这个赛道。看看：python forge.py tracks")
    write("bible.md", bible, slug)
    json.dump({"dir": d, "slug": slug, "title": title or ""},
              open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n建好了：{d}")
    print("下一步：把 bible.md 里的空填上（金手指规则表最要紧），再跑 python forge.py outline")
    return 0


# ---------------------------------------------------------------------------
# 章纲
# ---------------------------------------------------------------------------

OUTLINE_HEAD = "| 章 | 标题（8–18 字，只写爆点） | 本章必办一件事 | 章末钩子 |\n|---|---|---|---|\n"


def parse_outline(text: str) -> List[Dict[str, str]]:

    # ★ 有的细纲文件里混着「卷节拍」表，第一列也是 1、2、3……
    #   直接全文件解析会把节拍当成章节号（第2章变成"71–80"那种）。
    #   规则：只要文件里有标题带「逐章」，就只解析那一段。
    _only_chapter_section = None
    try:
        _heads = list(re.finditer(r"(?m)^#{1,6}\s*(.+)$", text))
        for _n, _m in enumerate(_heads):
            if "逐章" in _m.group(1):
                _start = _m.end()
                _end = _heads[_n + 1].start() if _n + 1 < len(_heads) else len(text)
                _only_chapter_section = text[_start:_end]
                break
        if _only_chapter_section is not None:
            text = _only_chapter_section
    except Exception:
        pass

    rows = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4 or not cells[0].isdigit():
            continue
        rows.append({"n": int(cells[0]), "title": cells[1],
                     "task": cells[2], "hook": cells[3]})
    return rows


def cmd_outline(a) -> int:
    bible = read("bible.md", a.slug)
    if not bible:
        raise SystemExit("没有 bible.md，先跑 new")
    if not a.dry:
        ok, why = llm.ping()
        if not ok:
            raise SystemExit(f"模型没连上：{why}")
        sys_p = ("你是长篇网文的章纲作者。按番茄男频/女频的长连载写法，"
                 "产出前 30 章的章纲。规则：\n"
                 "- 标题只写本章最炸的那一下，每条 8–18 字（别省字），\n"
    "- 不写概括、不写意境、不写地名。\n"
                 "- 每章只办一件事，写清楚主角**做了什么**（不是受了什么）。\n"
                 "- 前 3 章必须让读者一句话说清冲突；第 5 章前主角必须赢一次。\n"
                 "- 每 3 章一个小高潮，每 10 章一个阶段结果。\n"
                 "- 章末钩子四选一：没回答的问题／没打开的门／没说出口的话／变了的数字。\n"
                 "- 只输出表格，不要解释。")
        usr_p = (f"下面是立项：\n{bible[:2500]}\n\n"
                 f"出前 {a.chapters} 章，严格按这个表头：\n{OUTLINE_HEAD}")
        raw = llm.complete(sys_p, usr_p, temperature=0.8)
        tbl = "\n".join(l for l in raw.splitlines() if l.strip().startswith("|"))
        rows = parse_outline(tbl)
        if len(rows) < 5:
            raise SystemExit("模型没吐出像样的表格，原文：\n" + raw[:600])
        bad = [r for r in rows if not (3 <= len(re.sub(r"[^\w一-龥]", "", r["title"])) <= 6)]
        p = write("outline.md", OUTLINE_HEAD + tbl + "\n", a.slug)
        print(f"章纲 {len(rows)} 章 → {p}")
        for r in bad:
            print(f"  ⚠ 第 {r['n']} 章标题「{r['title']}」超过 10 字或不像爆点，改掉")
        print("下一步：python forge.py write 1")
    else:
        print("[dry] 会拿 bible.md 去问模型出章纲表，这里跳过")
    return 0


# ---------------------------------------------------------------------------
# 写章
# ---------------------------------------------------------------------------

_CH_NUM = re.compile(r"第\s*(\d+)\s*章")


def list_chapters(slug: str = None) -> List[Tuple[int, str]]:
    """返回 [(章节号, stem), ...] 按章节号升序。

    stem ＝ 去掉 .md 后的文件名（不含 .ai.md / .draft.md）。
    支持两种命名混排：
      0001.md            → 号 = 1
      第2章 谁填了他.md   → 号 = 2
    """
    d = os.path.join(book_dir(slug), "chapters")
    if not os.path.isdir(d):
        return []
    out = []
    for f in os.listdir(d):
        if not f.endswith(".md") or f.endswith(".ai.md") or f.endswith(".draft.md"):
            continue
        stem = f[:-3]
        m = _CH_NUM.search(stem)
        if m:
            num = int(m.group(1))
        else:
            mm = re.match(r"^0*(\d+)", stem)
            num = int(mm.group(1)) if mm else 10 ** 9  # 无名数字→排最后
        out.append((num, stem))
    out.sort(key=lambda x: (x[0], x[1]))
    return out


def _split_state_block(text: str):
    """把模型附在正文末尾的东西剥掉，返回 (干净正文, 状态行列表)。

    实测模型有三种附法，都要切：
      ① 【状态变化】块            ② <!-- new: ... --> 注释
      ③ 末尾 markdown 表格（| 人 | 变化 |）
    另外它常把章节标题再写一遍（### 标题），也要删——
    这些留在正文里，读者会看到一张「人物变化表」。
    """
    import re as _re
    t = (text or "").replace("\r\n", "\n")
    got = []

    # ① 【状态变化】
    m = _re.search(r"\n?\s*【状态变化】\s*\n(.*?)\Z", t, _re.S)
    if m:
        for ln in m.group(1).splitlines():
            if "｜" in ln or "得：" in ln:
                got.append(ln.strip())
        t = t[:m.start()] + t[m.end():]

    # ② <!-- new: ... -->
    for mm in _re.finditer(r"<!--\s*(?:new|状态):.*?-->", t, _re.S):
        got.append(mm.group(0).strip())
    t = _re.sub(r"<!--\s*new:.*?-->", "", t, flags=_re.S)

    # ③ 末尾 markdown 表格
    ls = t.rstrip().splitlines()
    while ls and ls[-1].lstrip().startswith("|"):
        row = ls.pop().strip()
        if any(k in row for k in ("得：", "失：", "能力：", "关系：")):
            got.append(row)
    t = "\n".join(ls).rstrip() + "\n"

    # ③b 表格前面常留一个「### 状态变化」小标题，也删
    ls = t.rstrip().splitlines()
    while ls and ls[-1].lstrip().startswith("#") and (
            "状态变化" in ls[-1] or "人物变化" in ls[-1]):
        ls.pop()
    t = "\n".join(ls).rstrip() + "\n"

    # ④ 重复的章节标题
    ls = t.splitlines()
    if ls and ls[0].lstrip().startswith("#"):
        title = ls[0].lstrip("#").strip()
        out = [ls[0]]
        for ln in ls[1:]:
            s2 = ln.strip()
            if s2.startswith("#") and s2.lstrip("#").strip() == title:
                continue
            out.append(ln)
        t = "\n".join(out).rstrip() + "\n"

    # ③c 全文兜底：还残留的表格行、状态变化小标题，一并清掉
    #     小说正文不会以 | 开头，所以这条是安全的
    keep = []
    for ln in t.splitlines():
        s3 = ln.strip()
        if s3.startswith("|"):
            if any(k in s3 for k in ("得：", "失：", "能力：", "关系：")):
                got.append(s3)
            continue
        if s3.startswith("#") and ("状态变化" in s3 or "人物变化" in s3):
            continue
        if s3.strip("*【】[]（）() ") in ("状态变化", "人物变化"):
            continue
        # 模型爱在开头补一行「【第6章 · 标题：…】」，文件头已经有标题了
        if s3.startswith("【") and "章" in s3 and ("标题" in s3 or "·" in s3):
            continue
        if s3 in ("---", "***", "___"):
            continue
        keep.append(ln)
    t = "\n".join(keep).rstrip() + "\n"

    # ⑤ 砍掉模型附在末尾的"汇报"段：注释／章末钩子／写作手法…
    #   这些是它给自己交的作业，不是小说
    m2 = _re.search(
        r"\n[ \t]*\**[ \t]*(注释|章末钩子|写作手法|人物关系|未来悬念|"
        r"新信息|新物证|新动向|保持剧情|剧情连贯|注释清晰)", t)
    if m2:
        t = t[:m2.start()] + "\n"

    # ⑥ 防死循环：同一个短句连续重复三次以上，就砍掉重复部分。
    #   实测出现过「**注释清晰明了，不留歧义**」刷几千次刷到 token 上限，
    #   提示词管不住，只能在这里截断。
    m3 = _re.search(r"(.{12,80}?)\1{2,}", t, _re.S)
    if m3:
        t = t[:m3.start()] + "\n"
        got.append("〔已截断重复段落〕")

    # ⑦ 末段若是"自问自答"式汇报（这将如何影响…／她不知道…），整段丢掉。
    #   小说留钩子是给一个没答的问题，不是自己把问题说出来再自己说不知道。
    tail = [x for x in t.splitlines() if x.strip()]
    if len(tail) >= 3:
        lastp = tail[-1]
        marks = ("这将如何", "将如何影响", "会变成什么", "未来会怎样",
                 "她不知道", "他不知道", "她不知道。", "这意味")
        if sum(1 for k in marks if k in lastp) >= 2:
            t = "\n".join(tail[:-1]).rstrip() + "\n"
            got.append("〔已去掉末段自问自答〕")

    seen, uniq = set(), []
    for x in got:
        k = x.strip()
        if k and k not in seen:
            seen.add(k)
            uniq.append(k)
    return t, uniq

def _apply_names(slug, n, text):
    """从正文对话里抽「叫他「墨哥」」这类称呼，补进人物库的称呼记录。

    模型常漏写状态注释，称呼靠它自觉记不住；这里用代码兜一层。
    只收「叫/喊/称 + 引号包着的一小段」，宁缺勿滥。
    """
    import re as _re
    hits = set()
    pat = (r"(?:叫|喊|称|尊称|昵称)(?:他|她|了)?(?:一声|作)?"
           r"[「\"'“]([^」\"'”]{1,8})[」\"'”]")
    for m in _re.finditer(pat, text or ""):
        v = m.group(1).strip()
        if v and len(v) <= 8:
            hits.add(v)
    if not hits:
        return 0
    ch = read("characters.md", slug)
    if not ch:
        return 0
    if "## 称呼记录" not in ch:
        ch = ch.rstrip() + "\n\n## 称呼记录\n"
    lines = ch.splitlines()
    added = 0
    for h in sorted(hits):
        line = "- 第%d章：%s" % (n, h)
        if line not in lines:
            lines.append(line)
            added += 1
    if added:
        write("characters.md", "\n".join(lines) + "\n", slug)
    return added


def _apply_state(slug, n, lines):
    """把状态变化追加进人物库的「属性变更」段。"""
    if not lines:
        return
    ch = read("characters.md", slug)
    if not ch:
        return
    if "## 属性变更" not in ch:
        ch = ch.rstrip() + "\n\n## 属性变更\n"
    ck = [l for l in ch.splitlines() if not l.startswith(f"- 第{n}章｜")]
    for ln in lines:
        ck.append(f"- 第{n}章｜{ln}")
    write("characters.md", "\n".join(ck) + "\n", slug)


def prev_context(n: int, slug: str = None) -> Dict[str, Any]:
    """上一章末三行 + 台账里最近的新信息。★ 连续性的命根子。

    ★ 改用 list_chapters 按章节号找上一章——不再认死 {i:04d}.md。
      否则第 3 章起就找不到已改名的第 2 章（第2章 谁填了他.md）。
    """
    d = book_dir(slug)
    last = ""
    firsts = []
    for num, stem in list_chapters(slug):
        if num >= n:
            break
        if num < n - 3:
            continue
        p = os.path.join(d, "chapters", stem + ".md")
        if os.path.exists(p):
            txt = open(p, encoding="utf-8").read()
            paras = [x.strip() for x in txt.split("\n") if x.strip()]
            if paras:
                firsts.append(paras[0])
            if num == n - 1:
                last = "\n".join(paras[-3:])
    # ★ 人物现状：拼进 ledger 字段一起进提示词（不改 build_prompt 的接口）。
    #   只取「主角」那一段 + 变更日志尾部，整本人物库塞进去会挤爆上下文。
    ledger_txt = read("ledger.md", slug)[-1500:]
    chars = read("characters.md", slug)
    lead = chars.split("## 配角")[0].strip() if chars else ""
    if lead:
        head = lead.split("## 主角")[-1].strip()
        ledger_txt = (ledger_txt + "\n\n【人物现状（本章出场前的最新状态）】\n"
                      + head[-900:])
    return {"last_lines": last, "first_lines": firsts,
            "ledger": ledger_txt, "characters": lead[:900]}


def build_prompt(row: Dict[str, str], bible: str, ctx: Dict[str, Any],
                 slug: str = "") -> str:
    red = ""
    try:
        red = rules_of(slug)
    except Exception:
        red = ""
    # ★ 全知段不能进提示：那里写的是全书结局和真相，
    #   喂进去模型就会按全知视角写，悬疑线当场断掉
    cut = bible.find("### 世界设定")
    if cut > 0:
        bible = bible[:cut]
    head = ("【设定红线（每条都不能犯）】\n" + red + "\n\n" if red else "")
    prompt = f"""{head}【立项（不许写跑偏）】
{bible[:3000]}

【本章任务卡】
第 {row['n']} 章 · 标题：{row['title']}
本章必办的一件事：{row['task']}
章末钩子：{row['hook']}

【铁律（违反即作废）】
- 地点、人物以本章章纲和人物库为准。不许凭空多出设定里没有的人、地点、机构；
  不许让人物出现在他当前不可能在的地方。
- 人物关系以 characters.md 为准。谁和谁有仇、谁欠谁什么，按记录写，不许自行和解或结盟。
- 主角的能力边界以本章章纲为准。他能做什么、不能做什么，不许超出；越界即作废。
- 只写小说正文。绝不许把任何指令、模板、括号注释（<!-- new: --> 等）、"章末必须给东西""红线""new:"等字样写进正文——那是给你的要求，不是故事内容。

【上一章最后三行（这一章头三句必须接住或明确跳开）】
{ctx['last_lines'] or '（这是第一章，没有前文）'}

【已出现过的人、地、数（不要凭空改名改数）】
{ctx['ledger'] or '（暂无）'}

【要求（前两条是硬指标，做不到会被打回重写）】
- **去空白必须写到 1800 字以上。** 这是硬指标，宁可多写，不许收在 1600 以下。
- **"仿佛""似乎""宛如"这三个词一个都不许出现。** 想写心理就直接写动作。
- 只写正文，不要章号、不要标题、不要解释、不要代码块、不要输出任何括号注释（如 <!-- new: -->）或写作指令——那些是给你的要求，不是正文。
- 开头不许写景、不许交代世界观、不许从起床写起；从动作或对白中间切入。
- 句长要忽疏忽密：要有 4 个字的碎句，也要有 45 个字以上的长句，
  独句短段扎堆出现，不要每隔四段来一句。
- 让人早点开口：前 300 字里要有对话。
- 结尾不许总结、抒情、说教、预告下章。

{NEWINFO_TMPL}"""
    # ★ 本章所在那一卷的节拍。总览表告诉它"全书往哪走"，
    #   这一段告诉它"你现在第几步"——两个都得给，缺一个就会写跑。
    vol = ""
    if slug:
        try:
            b2 = brief_of(slug, int(row.get("n") or 0))
            # b2 = 总览表 ＋ 本卷段落。取最后一个 "## " 之后的就是本卷那一段
            idx = b2.rfind("\n## ")
            if idx >= 0:
                vol = b2[idx + 1:].strip()[:1400]
        except Exception:
            vol = ""
    if vol.strip():
        prompt += "\n\n【你所在的这一卷（照这一段写，别跳步）】\n" + vol.strip()

    # 手法库：★ 放在细纲之后——先看这一章要干什么，再看手册。
    #   顺序反了它就会背手册，不写这一章。手册是提醒，不是规则，红线照旧。
    try:
        sk = SK.render(llm.load_cfg(),
                       bible[:400] + row.get("task", "") + row.get("title", ""))
        if sk:
            prompt += "\n\n" + sk
    except Exception:
        # 静默是故意的：手法库是可选增强，挂了照常写，不该拦一章
        pass
    return prompt


def patch_banned(text: str) -> str:
    """定向修补：只改含禁用词的那几句，其余一个字不动。

    ★ 为什么不做整篇重写：让它"照着清单改一遍"，7B 会跑飞
      （实测写出 10585 字、64 个"仿佛"）。改成抽句改句——短任务它干得了，
      而且替换是脚本做的，**改没改对它说得不算，脚本说了算**。
    """
    from rules import BAN_WORDS
    hits = redline.banned_hits(text)
    if not hits:
        return text
    picks = hits[:12]
    lines = "\n".join(f"{i}|{s}" for i, s in enumerate(picks))
    sys_p = ("你是改稿编辑。下面这些句子里出现了「仿佛／似乎／宛如」，"
             "那是视角越界——作者钻进人物脑子里了。\n"
             "改法：换成看得见的动作或事实，意思不变，长度差不多。\n"
             "只输出「序号|改后的句子」，一行一条，不要解释，不要多输出别的句子。")
    out = llm.complete(sys_p, lines, temperature=0.3, max_tokens=2048)
    repl = {}
    for ln in out.splitlines():
        if "|" not in ln:
            continue
        k, v = ln.split("|", 1)
        k = re.sub(r"\D", "", k)
        if k.isdigit() and v.strip() and not any(w in v for w in BAN_WORDS):
            repl[int(k)] = v.strip().strip("「」\"'")
    if not repl:
        return text
    new = text
    for i, v in repl.items():
        if i < len(picks) and picks[i] in new:
            new = new.replace(picks[i], v, 1)
    return new


def style_pack(slug: str = None) -> Tuple[Dict[str, Any], List[str], set]:
    """读风格包（learn 生成的）。没有就返回空的三件套，流程照走。"""
    import pickle
    d = book_dir(slug)
    p = os.path.join(d, "style.pkl")
    if os.path.exists(p):
        try:
            pk = pickle.load(open(p, "rb"))
            return pk.get("stats", {}), pk.get("samples", []), pk.get("shingles", set())
        except Exception as e:
            # ★ 以前这里静默 pass——风格卡坏了看不出来，还以为是没喂语料。
            #   现在至少喊一声：按"无风格卡"继续，但让人知道。
            print(f"  （风格卡读不了，按无风格卡继续：{type(e).__name__}: {e}）")
    return {}, [], set()


def cmd_learn(a) -> int:
    """喂成品小说，量出一张风格卡。"""
    import pickle
    if not os.path.exists(a.corpus):
        raise SystemExit(f"找不到语料：{a.corpus}")
    text = style.read_corpus(a.corpus)
    if getattr(a, "rng", ""):
        s1, e1 = _range(a.rng)
        text = style.take_chapters(text, s1, e1)
        print(f"只取第 {s1}-{e1 or '末'} 章")
    if len(text) < 5000:
        raise SystemExit(f"语料只有 {len(text)} 字，太短，量不出东西")
    st = style.stats(text)
    samples = style.pick_samples(text, n=a.samples)
    name = os.path.basename(a.corpus.rstrip("\\/")) or "语料"
    card = style.build_card(st, samples, name)
    print(f"语料 {len(re.sub(r'\\s+', '', text))} 字 / {st['章数']} 章")
    print(f"  句长均值 {st['句长均值']}（p10 {st['句长p10']} / p90 {st['句长p90']}）"
          f" · CV {st['变异系数']} · 段均 {st['段均字数']}")
    print(f"  对话 {st['对话占比']}% · 独句成段 {st['独句成段占比']}%"
          f" · 章均 {st['章均字数']} 字")
    print(f"  禁用词 {st['禁用词']}")
    p = write("style.md", card, a.slug)
    pack: Dict[str, Any] = {"stats": st, "src": a.corpus}
    if getattr(a, "stats_only", False):
        # ★ 只留数字，一个字都不留。样本和 8-gram 指纹都是从原文派生的，
        #   查重能力会一起没掉（重合检测失效），但落盘的东西最干净。
        pack["samples"], pack["shingles"] = [], set()
        print("  --stats-only：只留统计数字，不落任何原文派生物（查重会失效）")
    else:
        pack["samples"] = samples
        pack["shingles"] = style.shingles(text, 8)
    pickle.dump(pack, open(os.path.join(book_dir(a.slug), "style.pkl"), "wb"))
    print(f"\n风格卡：{p}")
    print("★ 学的是节奏（句长、疏密、对话比、段落长短），不是句子。")
    print("  生成完会自动查跟语料的 4-gram 重合——那是模仿和抄袭的分界线。")
    return 0


def _range(s: str) -> Tuple[int, int]:
    """'1-10' → (1,10)；'11' → (11,0)；'' → (1,0)"""
    s = (s or "").strip()
    if not s:
        return 1, 0
    if "-" in s:
        a1, a2 = s.split("-", 1)
        return (int(a1 or 1), int(a2 or 0))
    return int(s), 0



def rules_of(slug: str) -> str:
    """设定红线。★ 没有它，模型会拿着"作者视角的全知设定"去写，
       把后面才揭的东西（真相、身份、底牌）提前抖在第二章——
       这不是它笨，是我们把剧透喂给了它。"""
    p = os.path.join(book_dir(slug), "rules.md")
    if os.path.exists(p):
        return open(p, encoding="utf-8").read().strip()
    return ""


def _brief_cfg(slug: str) -> dict:
    """当前这本书指定的大纲文件存在 brief.json。"""
    p = os.path.join(book_dir(slug), "brief.json")
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            # 静默是故意的：brief.json 坏了＝没指定大纲，走默认那一份
            pass
    return {}


def _global_file(slug: str) -> str:
    """当前大纲文件。★ 优先用人在界面上指定的那一个，
       没指定才退回"名字里带细纲的那个"。"""
    d = book_dir(slug)
    cfg = _brief_cfg(slug)
    if cfg.get("file"):
        p = os.path.join(d, cfg["file"])
        if os.path.exists(p):
            return p
    if not os.path.isdir(d):
        return ""
    for f in sorted(os.listdir(d)):
        if f.endswith(".md") and "细纲" in f:
            return os.path.join(d, f)
    return ""


def brief_of(slug: str, n: int = 0) -> str:
    """给模型看的细纲：**全书总览表 ＋ 本章所在那一卷的节拍**。

    ★ 为什么不整本塞进去：202 行的细纲全塞，模型会被淹，而且它只需要
      知道"我现在在哪一卷、这一段该干什么"。整本细纲是给人看的，
      给模型的是**切片**。
    """
    f = _global_file(slug)
    if not f:
        return ""
    txt = open(f, encoding="utf-8").read()
    out = []
    # 1) 总览表（〇 那一节）
    i = txt.find("## 〇")
    if i >= 0:
        j = txt.find("\n## ", i + 5)
        out.append(txt[i: j if j > 0 else i + 900].strip())
    # 2) 本章所在那一卷
    if n:
        # ★ 卷标题有两种写法（"## 卷二 ·…" 和 "## 一、卷一逐章…"），
        #   只认前一种会把卷一漏掉，于是卷一的章节会错读到卷二。
        heads = [(m.start(), m.group(1).strip())
                 for m in re.finditer(r"(?m)^##(.+)$", txt)
                 if "卷" in m.group(1)]
        hit = None
        for k, (pos, title) in enumerate(heads):
            end = heads[k + 1][0] if k + 1 < len(heads) else len(txt)
            seg = txt[pos:end]
            m = re.search(r"(\d+)\s*[–—-]\s*(\d+)", title) or \
                re.search(r"(\d+)\s*[–—-]\s*(\d+)", seg[:200])
            a = int(m.group(1)) if m else 0
            b = int(m.group(2)) if m else 99999
            if a <= n <= b:
                hit = seg
                break
        if not hit and heads:
            hit = txt[heads[0][0]:]
        if hit:
            # 节拍级段落（卷二及以后）整体给；卷一是逐章表，太长，
            # 只给它的目标三行（一句话／拿到／末刀）——章表由 outline 管。
            if "节拍" in hit:
                out.append(hit.strip()[:1400])
            else:
                keep = [l for l in hit.splitlines()
                        if l.startswith(("**一句话", "**拿到", "**末刀", "#"))]
                out.append("\n".join(keep[:6]).strip()[:600])
    return "\n\n".join(out)


def cmd_brief(a) -> int:
    """打印模型实际会看到的细纲切片（自检用）。"""
    print(brief_of(a.slug, a.n) or "（没找到细纲文件：书目录里要有名字带『细纲』的 md）")
    return 0


def cmd_survey(a) -> int:
    """语料普查：把一批书分别量一遍，看分布，挑靶子。"""
    rows = style.survey(a.corpus, getattr(a, 'rng', ''))
    if not rows:
        raise SystemExit(f"{a.corpus} 里没有够长的 txt/md（至少 5000 字）")
    print(f"普查 {len(rows)} 本：\n")
    print(style.survey_table(rows))
    susp = [(r["_name"], *style.ai_smell(r)) for r in rows]
    susp.sort(key=lambda x: -x[1])
    bad = [x for x in susp if x[1] >= 3]
    print("\n（AI味＝粗筛，分值来自人写/AI写的实测画像。"
          "要校准就用 python forge.py diff 拿你自己的已知稿比一次）")
    if bad:
        print("\n⚠ AI 味重的（≥3 分，别拿它们当语料）：")
        for name, sc, why in bad:
            print(f"    {name}  {sc}/5  " + "；".join(why[:3]))
    print("\n★ 别拿这批的平均值当目标——平均＝最没有风格的位置。")
    print("  挑一本当靶子。各项离中位数最近的几本（最典型）：")
    for name, d in style.typical(rows, 3):
        print(f"    {name}（偏离 {d}）")
    print("\n接着：")
    print(f'  python forge.py learn --corpus "<那一本的路径>" --slug 新书'
          + (f' --range {a.rng}' if getattr(a, "rng", "") else ""))
    return 0


def cmd_diff(a) -> int:
    """人写的 vs AI 写的，逐项对照。★ 知道差别在哪，才知道往哪改。"""
    if a.human and a.ai and os.path.abspath(a.human) != os.path.abspath(a.ai):
        th, ta = style.read_corpus(a.human), style.read_corpus(a.ai)
        nh, na = os.path.basename(a.human), os.path.basename(a.ai)
    else:
        src = a.human or a.ai
        th_full = style.read_corpus(src)
        s1, e1 = _range(a.hrange)
        s2, e2 = _range(a.arange)
        th, ta = style.take_chapters(th_full, s1, e1), style.take_chapters(th_full, s2, e2)
        nh = f"{os.path.basename(src)} {a.hrange or '全'}"
        na = f"{os.path.basename(src)} {a.arange or '全'}"
    if not th or not ta:
        raise SystemExit("两边都没切出东西，检查 --hrange / --arange 或文件路径")
    sh, sa = style.stats(th), style.stats(ta)
    print(f"人写：{nh}（{len(re.sub(chr(92) + 's+', '', th))} 字 / {sh['章数']} 章）")
    print(f"AI写：{na}（{len(re.sub(chr(92) + 's+', '', ta))} 字 / {sa['章数']} 章）")
    print()
    print(style.compare(sh, sa))
    print("\n★ 语料只喂人写的那段。拿 AI 写的当范本＝把机器指纹当标准复制一遍。")
    return 0


def cmd_write(a) -> int:
    bible = read("bible.md", a.slug)
    outline = read("outline.md", a.slug)
    if not bible or not outline:
        raise SystemExit("缺 bible.md 或 outline.md")
    rows = {r["n"]: r for r in parse_outline(outline)}
    if a.n in rows:
        row = rows[a.n]
    else:
        # ★ 缺章纲不该把整章卡死：已经有前文就接着前文写，
        #   没有前文就按全书大纲起头。硬报错会让人连一章都写不出来。
        row = {"n": a.n, "title": "第%d章" % a.n, "task": "", "hook": "",
               "_no_outline": True}
    # ★ 文件名用「第N章 标题」，不在文件名里塞斜杠等非法字符
    ch_title = str(row["title"]).replace("/", "／").replace("\\", "＼")
    ctx = prev_context(a.n, a.slug)

    if a.dry:
        print("[dry] 任务卡（不调模型）：")
        print(build_prompt(row, bible, ctx)[:1200])
        return 0

    ok, why = llm.ping()
    if not ok:
        raise SystemExit(f"模型没连上：{why}")

    # 风格包：有就照它的节奏写，没有就走通用守则
    st, samples, shingles = style_pack(a.slug)
    system = WRITE_RULES
    base = build_prompt(row, bible, ctx, a.slug)

    # ★★ 动笔前先出一版提纲（--plan）。
    #   它等价于 CoT 的"想一遍"，但有三点不同：
    #     1. 想的是**对着手上的外部记忆**想（台账、上章末三行、章纲任务），
    #        而模型内部的思考只想得到当前这一章——想不起第 30 章写了什么。
    #     2. 提纲是结构化的、短的，不会像思考过程那样把正文挤掉。
    #     3. 我可以把它**摆在正文提示里当约束**，也可以扔掉——可控。
    #   ⇒ 解决"前后矛盾"靠的是外部记忆，不是让它多想两千个 token。
    if getattr(a, "plan", False):
        print("  先想一遍（对照台账和上文）…", flush=True)
        outline = llm.complete(
            "你是长篇网文的章节统筹。你只拿到手上的材料，不许编你没有的东西。",
            f"【本章任务】{row['title']}　{row['task']}\n"
            f"【上一章最后三行】\n{ctx['last_lines'] or '（第一章）'}\n"
            f"【已经出现过的人、地、数、物（★ 只能沿用，不许改名改数）】\n"
            f"{ctx['ledger'] or '（暂无）'}\n"
            f"【本阶段骨架】\n{bible[:600]}\n\n"
            "用五行以内回答，每行一个：\n"
            "承接：上一章留下的、本章开头必须接住的那件事\n"
            "出场：本章出场的人（沿用台账里的名字，新人才另起）\n"
            "时间地点：本章发生在什么时候、什么地方\n"
            "三件事：本章必须交代的三件事\n"
            "章末：本章收在哪一下（不许总结）\n"
            "只输出这五行，不要解释。",
            temperature=0.5, max_tokens=600)
        if outline.strip():
            print("  提纲：")
            for l in outline.strip().splitlines()[:6]:
                if l.strip():
                    print("    " + l.strip()[:70])
            base += ("\n\n【本章提纲（照它写，别写跑）】\n" + outline.strip())

    if st:
        system += ("\n\n【这本书的节奏（从语料量出来的，照着做）】\n"
                   + style.translate(st) + "\n"
                   "★ 学节奏，不许照搬原句，不许用它的专属设定词和人名。\n")
        # ★ 样本默认**不**塞进提示。实测：塞了原文片段，模型直接照抄，
        #   跟语料的 4-gram 重合冲到 22.8%——那不是模仿，是抄。
        #   数字（句长、CV、对话比）是量出来的，抄不了；原文片段能抄。
        #   真要对照节奏，用 --with-samples 自己开，然后盯重合检测。
        if getattr(a, "with_samples", False) and samples:
            base += ("\n\n【节奏样本：照它的**长短和疏密**写，"
                     "一个原句都不许搬】\n" + "\n----\n".join(samples[:2]))

    text = ""

    # ★ 分段续写：7B 一次写不满 1800 字，硬要它一次写完，它会在 1300 字左右
    #   自己收尾，然后越改越短。改成一次只写一小段、接着往下续——
    #   小模型干短活才稳。段数由 --segment 控制（0＝不分段，一口气写完）。
    # 续写也要带红线（主角名字、认知边界）。只在第一段有＝等于没有
    try:
        red = rules_of(a.slug)
    except Exception:
        red = ""

    if a.segment:
        for i in range(1, a.segment + 1):
            if i == 1:
                seg_prompt = base
            else:
                seg_prompt = (f"下面是第 {a.n} 章已经写好的部分：\n\n{text}\n\n"
                              f"接着上面，继续往下写 {a.segwords} 字左右，"
                              "推进同一件事，不要重复、不要总结、不要另起一段开场白。\n"
                              # ★ 续写也必须带红线。之前没带，第二段它就把主角
                              #   改名叫"刘泽"、顺手把卷四的设定抖出来——
                              #   红线只在第一段，等于没有。
                              + (red + "\n" if red else "")
                              # ★ 试过点名"不许用 试图/尝试/再次"——结果它用得更多（9→21 次）。
                              #   这是反向强化：你提哪个词，它就更爱用哪个。
                              #   ⇒ 文笔毛病别靠提示词治，交给 redline 标出来 + 人来改。
                              + "★ 每往前写一段，就得有一件新发生的事（它做了什么／对方回了什么／"
                                "出现一个具体的数），别停在同一件事上反复想。\n")
            print(f"  第 {i}/{a.segment} 段…", flush=True)
            piece = llm.complete(system, seg_prompt,
                                 temperature=0.9, max_tokens=4096)
            text = (text + "\n\n" + piece.strip()) if text else piece.strip()
            if len(redline.bare(text)) >= a.target:
                break
        print(f"  拼完 {len(redline.bare(text))} 字", flush=True)

    best = text
    # ★ 选稿先看 P0 数，再看离目标字数多远。只看 P0 的话，
    #   它会挑中那篇跑飞到 5903 字的——硬伤少但根本不能用。
    best_p0 = (99, 99.0)
    for attempt in range(1, MAX_RETRY + 1):
        if not text:
            print(f"  第 {attempt} 遍在写…", flush=True)
            text = llm.complete(system, base, temperature=0.9,
                                max_tokens=8192)
        res = redline.check(text, ctx["first_lines"],
                            cv_min=(max(0.45, min(0.65, st["变异系数"]))
                                    if st else None),
                            corpus=shingles or None)
        p0 = [i for i in res["issues"] if i[0] == "P0"]
        print(redline.report(res))
        score = (len(p0), -len(redline.bare(text)))
        if score < best_p0:
            best, best_p0 = text, score
        if not p0:
            break

        # ★★ 整篇重写这条路对小模型不成立：实测让它"照着清单改一遍"，
        #    它写出 10585 字、64 个"仿佛" —— 跑飞了。
        #    所以：能用**脚本定向修补**解决的，绝不整篇重写。
        # ★ 修补可能一次改不干净（模型漏改、或替换没对上），所以要再来一轮。
        #   只跑一轮的实测结果：剩下 1 个"似乎"没清掉，整章就卡在 P0 上。
        patched = text
        for _ in range(2):
            nxt = patch_banned(patched)
            if nxt == patched:
                break
            patched = nxt
        if patched != text:
            text = patched
            res = redline.check(text, ctx["first_lines"],
                            cv_min=(max(0.45, min(0.65, st["变异系数"]))
                                    if st else None),
                            corpus=shingles or None)
            p0 = [i for i in res["issues"] if i[0] == "P0"]
            print("  定向修补后：")
            print(redline.report(res))
            score = (len(p0), -len(redline.bare(text)))
            if score < best_p0:
                best, best_p0 = text, score
            if not p0:
                break

        if attempt == MAX_RETRY:
            # ★ 只喊一声，不落任何文件——主文件和草稿都在循环外
            #   按同一个判定写（见下面"闸门最后一关"）。
            print("  ⚠ 还有 P0 没清掉，这一章不进主文件")
            break

        fix = "\n".join(f"- {m}：{h}" for _, m, h in p0)
        # ★ 重合超标时把"抄得最多的几句"点名给它，不然它不知道哪句犯了
        if shingles:
            hot = redline.overlap_sentences(text, shingles, 5)
            hot = [(r, s) for r, s in hot if r > 0.25]
            if hot:
                fix += "\n- 下面这几句跟语料重得太狠，逐句改写（意思可留，句子全换）：\n"
                fix += "\n".join(f"  · {s}" for _, s in hot)
        user = (f"【上一稿被拦下的硬伤，逐条改掉】\n{fix}\n\n"
                "★ 保留上一稿已经写好的段落和情节，只改上面这几处，"
                f"不要推翻重来、不要扩写。改完字数仍在 {a.target} 字左右（去空白）。\n\n"
                + base)
        print(f"  按硬伤改第 {attempt + 1} 遍…", flush=True)
        new = llm.complete(WRITE_RULES, user,
                           temperature=max(0.55, 0.9 - 0.15 * attempt),
                           max_tokens=4096)
        # ★ 跑飞保护：长度翻两倍以上是模型在重复自己，丢弃这一稿
        if len(redline.bare(new)) > 2.5 * a.target or len(redline.bare(new)) < 0.5 * a.target:
            print(f"  这一稿 {len(redline.bare(new))} 字，明显跑飞，丢弃，用修补后的那稿")
            break
        text = new

    text = best

    # ★★ 闸门最后一关——这条是产品的命根子：
    #   P0 没清零 ⇒ 只写 .draft.md，主文件一个字都不落，返回 2。
    #   以前这里无条件写主文件还 return 0，README 写的"P0 清零才准发"
    #   在代码里根本不成立，闸门退化成"事后记录"。
    #   而且 draft 和主文件同源（都取 best）——不会再出现
    #   "1191 行用 text 写草稿、1218 行用 best 写主文件，两份不一样
    #    还没说谁准"这种事。
    if not text.strip():
        print(f"\n✘ 第 {a.n} 章：模型没吐出正文，不落盘")
        return 2
    res = redline.check(text, ctx["first_lines"],
                        cv_min=(max(0.45, min(0.65, st["变异系数"]))
                                if st else None),
                        corpus=shingles or None)
    p0 = [i for i in res["issues"] if i[0] == "P0"]
    if p0:
        print(redline.report(res))
        p = write(f"chapters/第{a.n}章 {ch_title}.draft.md",
                  f"# 第{a.n}章 {ch_title}\n\n{text.strip()}\n", a.slug)
        print(f"\n✘ 第 {a.n} 章 P0 未清零（{len(p0)} 项），只存了草稿：{p}")
        print("  主文件没有落盘。人工改这份草稿，或改设定后重写。")
        return 2

    text, _state = _split_state_block(text)
    p = write(f"chapters/第{a.n}章 {ch_title}.md",
              f"# 第{a.n}章 {ch_title}\n\n{text.strip()}\n", a.slug)
    # ★ 同时留一份"AI 初稿"。你改完之后，
    #   .ai.md 和 .md 的差别＝你亲手改的那几笔＝最值钱的训练数据。
    write(f"chapters/第{a.n}章 {ch_title}.ai.md",
          f"# 第{a.n}章 {ch_title}\n\n{text.strip()}\n", a.slug)
    info = redline.newinfo(text)
    if info:
        led = read("ledger.md", a.slug)
        line = f"- 第 {a.n} 章：" + "；".join(
            f"{k}={'、'.join(v)}" for k, v in info.items())
        # ★ 重写同一章时不许追加第二行——台账里同一章出现两条，
        #   后面对账的人不知道该信哪条
        kept = [l for l in led.splitlines()
                if l.strip() and not l.startswith(f"- 第 {a.n} 章：")]
        write("ledger.md", "\n".join(["# 台账", ""] + kept + [line]) + "\n", a.slug)
    _apply_state(a.slug, a.n, _state)
    _apply_names(a.slug, a.n, text)
    # ★ 人物库记录用章纲的结构化字段，不用 newinfo——
    #   newinfo 是人／地／数／物 的词形粗筛，会把「人事系统」当地点、
    #   把「改绩效」当数字；台账能将就，人物库是长期依据，不能将就。
    ch = read("characters.md", a.slug)
    if ch:
        if "## 章节事件记录" not in ch:
            ch = ch.rstrip() + "\n\n## 章节事件记录\n"
        ck = [l for l in ch.splitlines() if not l.startswith(f"- 第{a.n}章")]
        ck.append(f"- 第{a.n}章 {ch_title}｜事：{row.get('task', '')}"
                  f"｜钩子：{row.get('hook', '')}")
        write("characters.md", "\n".join(ck) + "\n", a.slug)
    print(f"\n落盘：{p}（{len(redline.bare(text))} 字）")
    return 0


def cmd_model(a) -> int:
    """切生成用的模型。自闭环的前提：生成和训练得是同一个。"""
    if not a.name:
        cfg = llm.load_cfg()
        print(f"现在用：{cfg['model']}（{cfg['backend']}）")
        return 0
    llm.save_cfg(model=a.name)
    ok, why = llm.ping()
    print(f"切到 {a.name}：" + ("✔ " if ok else "✘ ") + why)
    if not ok:
        print(f"  ollama pull {a.name.split(':')[0]}")
    return 0


def export_pairs(slug: str, fmt: str = "dpo", only_style: bool = True,
                 out: str = "") -> Dict[str, Any]:
    """导出训练对。WebUI 的「拿去学习」就是调它。"""
    import difflib
    d = os.path.join(book_dir(slug), "chapters")
    pairs, n_ch = [], 0
    for f in sorted(os.listdir(d)):
        if not f.endswith(".md") or f.endswith(".ai.md"):
            continue
        n = f[:-3]
        ai_p = os.path.join(d, n + ".ai.md")
        if not os.path.exists(ai_p):
            continue
        ai = _paras(open(ai_p, encoding="utf-8").read())
        hu = _paras(open(os.path.join(d, f), encoding="utf-8").read())
        if not ai:
            continue
        n_ch += 1
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
                None, ai, hu).get_opcodes():
            if tag == "replace":
                for k in range(min(i2 - i1, j2 - j1)):
                    old, new = ai[i1 + k], hu[j1 + k]
                    # ★ 以前要求相似度 ≥0.65，结果整句重写（最典型的改稿）全被判成
                    #   "整段重写"丢掉——实测一句改完相似度只有 0.49。
                    #   改稿看的是**位置**：同一处换掉了，就是改了这一句。
                    if len(re.sub(r"\s+", "", old)) >= 10 and \
                       len(re.sub(r"\s+", "", new)) >= 10:
                        kind = _kind_of(old, new)
                        if only_style and kind != "style":
                            continue
                        pairs.append({"before": old, "after": new, "ch": n,
                                      "kind": kind,
                                      "prev": (hu[j1 + k - 1] if j1 + k > 0
                                               else (ai[i1 + k - 1] if i1 + k > 0 else ""))})
            elif tag == "insert":
                for k in range(j1, j2):
                    if len(re.sub(r"\s+", "", hu[k])) >= 12:
                        pairs.append({"before": "", "after": hu[k], "ch": n,
                                      "kind": "style",
                                      "prev": (hu[k - 1] if k > 0 else "")})
    out_path = out or os.path.join(book_dir(slug), f"pairs_{fmt}.jsonl")
    import json as _json
    with open(out_path, "w", encoding="utf-8") as fh:
        for p in pairs:
            ctx = p.get("prev") or "继续写"
            if fmt == "dpo" and p["before"]:
                fh.write(_json.dumps({
                    "conversations": [{"from": "human", "value": ctx}],
                    "chosen": {"from": "gpt", "value": p["after"]},
                    "rejected": {"from": "gpt", "value": p["before"]},
                }, ensure_ascii=False) + "\n")
            else:
                fh.write(_json.dumps({
                    "conversations": [{"from": "human", "value": ctx},
                                       {"from": "gpt", "value": p["after"]}],
                }, ensure_ascii=False) + "\n")
    st = sum(1 for p in pairs if p.get("kind") == "style")
    return {"chapters": n_ch, "pairs": len(pairs), "style": st,
            "path": out_path}


def cmd_export(a) -> int:
    """导出训练对。跟 WebUI 的「拿去学习」是同一个函数，不再各写一份。"""
    r = export_pairs(a.slug, a.fmt, bool(getattr(a, "only_style", False)),
                     getattr(a, "out", "") or "")
    print(f"从 {r['chapters']} 章抽出 {r['pairs']} 组（改味 {r['style']}） → {r['path']}")
    return 0


def _paras(text: str) -> List[str]:
    body = re.sub(r"^#.*$", "", text, flags=re.M)
    return [p.strip() for p in body.split("\n") if p.strip()]


# 模糊指代。它们在原文里出现、在你改后的句子里消失＝你把话说具体了
FUZZY = ("那个人", "那东西", "什么东西", "在那儿", "某种", "这个人",
         "什么东西", "一个东西", "某个人")


def _kind_of(old: str, new: str) -> str:
    """这一笔改的是**味道**还是**错误**。

    ★ 为什么必须分开：你改 AI 稿时手上有两种动作——
        · 改味：把"那个人"换成真名字、补一个具体细节、把短句抻长
        · 改错：修事实、顺逻辑、删它自己重复的话
      训模型要的是**改味**。把纠错混进去，它学到的是"少犯错"，
      而文风一点没变——等于你改了半天白改。
    """
    lo, ln = len(re.sub(r"\s+", "", old)), len(re.sub(r"\s+", "", new))
    # 补了东西（多半是补了具体细节）
    if ln - lo >= 12:
        return "style"
    # 把模糊指代换成了具体的
    if any(f in old for f in FUZZY) and not any(f in new for f in FUZZY):
        return "style"
    # 把短句抻成了长句
    if ln >= 40 and lo < 25:
        return "style"
    return "fix"


def _sim(a: str, b: str) -> float:
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio()


def cmd_check(a) -> int:
    d = book_dir(a.slug)
    stem = None
    for num, s in list_chapters(a.slug):
        if num == a.n:
            stem = s
            break
    if not stem:
        raise SystemExit(f"没有第 {a.n} 章（已有："
                         + "、".join(s for _, s in list_chapters(a.slug)) + "）")
    p = os.path.join(d, "chapters", stem + ".md")
    ctx = prev_context(a.n, a.slug)
    _, _, sh = style_pack(a.slug)
    res = redline.check(open(p, encoding="utf-8").read(), ctx["first_lines"],
                        corpus=sh or None)
    print(f"第 {a.n} 章（{stem}）")
    print(redline.report(res))
    return 0


def cmd_batch(a) -> int:
    for n in range(a.start, a.end + 1):
        print(f"\n===== 第 {n} 章 =====")
        try:
            rc = cmd_write(argparse.Namespace(n=n, slug=a.slug, dry=a.dry,
                                              segment=a.segment, segwords=a.segwords,
                                              target=a.target,
                                              with_samples=getattr(a, 'with_samples', False),
                                              plan=getattr(a, 'plan', False)))
        except SystemExit as e:
            print(f"  停：{e}")
            return 1
        except (ValueError, RuntimeError) as e:
            # 路径不合法、后端是 none 这类——带原因停，别硬闯下一章
            print(f"  停：{e}")
            return 1
        # ★★ 闸门要真闸：P0 没清零就地停，不许带着硬伤写第二章。
        #   以前这里根本不看返回值——第一章没过，第二章照写，
        #   一批二十章全带伤，"P0 清零才准发"整条链是断的。
        if rc != 0:
            print(f"  第 {n} 章没过闸门（rc={rc}），batch 停在这里。"
                  "清掉硬伤或改设定后，从这一章重跑。")
            return rc
    return 0


def cmd_ping(a) -> int:
    ok, why = llm.ping()
    print(("通：" if ok else "不通：") + why)
    cfg = llm.load_cfg()
    print(f"配置：{cfg['backend']} @ {cfg['base_url']} · {cfg['model']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="小说生成器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new", help="立项")
    p.add_argument("--genre", required=True)
    p.add_argument("--gimmick", required=True)
    p.add_argument("--hook", required=True)
    p.add_argument("--title", default="")
    p.add_argument("--logline", default="")
    p.add_argument("--words", default="60万")
    p.add_argument("--slug", default="")
    p.add_argument("--track", default="", help="赛道：见 python forge.py tracks")
    p.add_argument("--dry", action="store_true")
    p.set_defaults(fn=cmd_new)

    p = sub.add_parser("learn", help="喂成品小说，量出风格卡")
    p.add_argument("--corpus", required=True, help="成品 txt 或目录")
    p.add_argument("--samples", type=int, default=3)
    p.add_argument("--range", dest="rng", default="",
                   help='只取这几章，如 1-10（★ AI 写的章不许当语料）')
    p.add_argument("--stats-only", action="store_true",
                   help="只留统计数字，不落样本和 8-gram 指纹（查重会失效）")
    p.add_argument("--slug", default="")
    p.set_defaults(fn=cmd_learn)

    p = sub.add_parser("tracks", help="番茄赛道参数：学结构，不学句子")
    p.set_defaults(fn=lambda a: (print(tracks.list_tracks()),
                                 print("\n通用：" + "；".join(f"{k} {v}" for k, v in tracks.COMMON.items())),
                                 0)[-1])

    p = sub.add_parser("survey", help="语料普查：一批书分别量，挑一本当靶子")
    p.add_argument("--corpus", required=True, help="放 txt 的目录")
    p.add_argument("--range", dest="rng", default="", help="每本只看这几章，如 1-10")
    p.set_defaults(fn=cmd_survey)

    p = sub.add_parser("diff", help="人写的 vs AI 写的，量出差别")
    p.add_argument("--human", default="", help="人写的稿子（或同一本书 + --hrange）")
    p.add_argument("--ai", default="", help="AI 写的稿子")
    p.add_argument("--hrange", default="1-10", help="人写的章范围，如 1-10")
    p.add_argument("--arange", default="11-0", help="AI 写的章范围，如 11-0（0＝到结尾）")
    p.set_defaults(fn=cmd_diff)

    p = sub.add_parser("outline", help="出章纲")
    p.add_argument("--chapters", type=int, default=30)
    p.add_argument("--slug", default="")
    p.add_argument("--dry", action="store_true")
    p.set_defaults(fn=cmd_outline)

    p = sub.add_parser("write", help="写一章")
    p.add_argument("n", type=int)
    p.add_argument("--slug", default="")
    p.add_argument("--dry", action="store_true")
    p.add_argument("--segment", type=int, default=4,
                   help="分几段续写（0＝一口气写完；小模型用 3–5）")
    p.add_argument("--segwords", type=int, default=550)
    p.add_argument("--target", type=int, default=1800)
    p.add_argument("--with-samples", action="store_true",
                   help="把语料原文片段塞进提示（有抄袭风险，默认关）")
    p.add_argument("--plan", action="store_true",
                   help="动笔前先出一版提纲：对照台账和上文想一遍（防前后矛盾）")
    p.set_defaults(fn=cmd_write)

    p = sub.add_parser("batch", help="连续写")
    p.add_argument("start", type=int)
    p.add_argument("end", type=int)
    p.add_argument("--slug", default="")
    p.add_argument("--dry", action="store_true")
    p.add_argument("--segment", type=int, default=4)
    p.add_argument("--segwords", type=int, default=550)
    p.add_argument("--target", type=int, default=1800)
    p.add_argument("--with-samples", action="store_true")
    p.add_argument("--plan", action="store_true")
    p.set_defaults(fn=cmd_batch)

    p = sub.add_parser("export", help="把「AI初稿→你改后」抽成训练对")
    p.add_argument("--fmt", default="dpo", help="dpo / sft")
    p.add_argument("--out", default="", help="输出 jsonl（默认 books/<书>/pairs_<fmt>.jsonl）")
    p.add_argument("--only-style", action="store_true",
                   help="只导改了味道的，丢掉改错的（纠错不教文风）")
    p.add_argument("--slug", default="")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("brief", help="看模型实际读到的细纲切片")
    p.add_argument("--slug", default="")
    p.add_argument("--n", type=int, default=0)
    p.set_defaults(fn=cmd_brief)

    p = sub.add_parser("check", help="只查红线")
    p.add_argument("n", type=int)
    p.add_argument("--slug", default="")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("model", help="切到哪个模型")
    p.add_argument("name", nargs="?", default="", help="如 qwen3:4b；不给就显示当前")
    p.set_defaults(fn=cmd_model)

    p = sub.add_parser("ping", help="看模型通不通")
    p.set_defaults(fn=cmd_ping)

    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
