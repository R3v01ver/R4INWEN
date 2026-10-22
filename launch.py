"""开书台 · 立项生成（从 novel-pipeline 单文件版搬来，只搬"开书期"这一段）

旧工具（2026-10-11-00-19-51/novel-pipeline/index.html）是开书期的：
脑图大纲 / 立项台（钩子·书名）/ 卡片表 / 章纲表 / 伏笔账。
这里只接"生成能力"：立项表单 → 模型出钩子/书名/章纲 → 一键落盘成 books/<slug>/。
写作期的事三栏台（webui.py）已经干了，不重复造。

★ 为什么 prompts 长这样（都是从旧工具原样搬的，别随手"优化"）：
  - 钩子要"能拍成具体画面"——感慨和抽象总结拍不出来。
  - 书名按「题材＋金手指＋爽点」，爽点必须落在"对方具体丢了什么"上。
  - 章纲四字段用全角「｜」分隔，章节名 8–18 字只写爆点。
"""

import json
import os
import re
from datetime import datetime
from typing import List

import llm
import forge

HERE = os.path.dirname(os.path.abspath(__file__))
BOOKS = os.path.join(HERE, "books")

# ★ 虚字：两个字都是虚字的片段（「他的」「在于」）不算复读——
#   章节名里反复出现「世子」「公司」这种实词才叫目录像复读机。
_FUNC = set("的他我你她它了呢吧啊是有个不都也很就又还再把被从到和与对着过会能要得地为于之以所让向")

# ---------------------------------------------------------------------------
# 立项表单 → system prompt（旧工具 systemText() 的 Python 版）
# ---------------------------------------------------------------------------


def system_text(book: dict, corpus: str = "") -> str:
    rules = book.get("rules") or []
    rules_block = ("【金手指规则（不可违背，也不可改）】\n- " + "\n- ".join(rules)
                   if rules else "【金手指规则】暂无。")
    corpus_block = ("\n" + corpus + "\n" if corpus else "")
    return "\n".join([
        "你是一个长篇网文的立项与结构编辑，服务对象是番茄小说等平台的连载作者。",
        "",
        "【本书设定】",
        "题材：" + (book.get("genre") or "—"),
        "频道：" + (book.get("mf") or "—"),
        "目标字数：" + str(book.get("words") or "—"),
        "阅读标签：" + (book.get("read_tags") or "—"),
        "内容标签：" + (book.get("content_tags") or "—"),
        "作者想写：" + (book.get("brief") or "—"),
        "一句话钩子：" + (book.get("hook") or "—"),
        "主角：" + (book.get("protagonist") or "—"),
        "感情线：" + (book.get("romance") or "—"),
        "",
        rules_block,
        corpus_block,
        "【硬性要求】",
        "- 禁止使用：仿佛、似乎、宛如。",
        "- 不推进剧情的细节一律删。",
        "- 不许把金手指做成读数/面板/倒计时/余额这类可量化排序。",
        "- 章节名只写爆点，每条 8–18 字，别省字。",
        # ★★ 安全前提（他点名骂过：写出「入侵政府服务器」这种话是要命的）
        "★★★ 安全前提（这条比上面所有要求都重要，写了就要重写）：",
        "- 小说里可以写黑客、攻防、渗透这类题材，但**必须写明那是虚拟的**：",
        "  虚拟机／模拟环境／内网靶场／攻防演练／红蓝对抗演习／游戏副本／公司自己的授权测试。",
        "- ★ 绝不许写成对**真实机构**下手：政府、公安、税务、银行、军队、医院、",
        "  电力电网、铁路航空、学校、电视台、运营商——一个字都不许出现在这类动作的对象上。",
        "  要写成「某集团内网演练平台」「模拟的城市电网沙盘」这种说不出口是哪家的地方。",
        "- ★ 不许写可照做的具体手法：不写漏洞编号、利用链、命令、工具用法、端口与服务名组合。",
        "  写结果（屏幕跳出一行字）和人的反应（对面的人摔了杯子），不写怎么进去的。",
        "- 判据：这句话被人单独截图发出来，会不会被当成真的在攻击谁？会＝重写。",
    ])


# ---------------------------------------------------------------------------
# 三类生成的 user prompt
# ---------------------------------------------------------------------------

HOOK_USER = (
    "给 5 条一句话钩子，每条一行。\n"
    "每条 30~50 字，必须包含一个具体动作或具体数字。\n"
    "不要概括剧情，要让人想知道后面发生了什么。\n"
)

TITLE_USER = (
    "给 6 个书名，每条一行。\n"
    "15 字上下，题材、金手指、爽点各露一点。\n"
    "男频可以长、可以用问号；女频短、有意象。\n"
)

# ★ 男女频的吸量逻辑不一样——这是榜单上的差异，不是我编的。
MF_TITLE = {
    "女频": (
        "★ 女频吃**情绪**：读者代入的是处境和关系，不是力量。\n"
        "  【一个受委屈的身份】＋【一句撂挑子的话】＋【对方的下场】，最有效。\n"
        "  优先用：不干了、和离、退婚、替嫁、重生、全家跪着、渣男、首辅、王妃、\n"
        "  团宠、虐渣、闪婚。情绪要写在脸上：她不伺候了、全家跪着求她、这婚我不结了。\n"
        "  例：替嫁当天，病弱世子自己站起来了\n"
    ),
    "男频": (
        "★ 男频吃**爽点密度**：要给出极端化的结果承诺，别含蓄。\n"
        "  【一个具体的处境或数字】＋【极端结果】，最常见也最有效。\n"
        "  优先用：全场、全县、全城、全宗、苟到、长生、无敌、开局、给我跪下、\n"
        "  傻眼了、一步登天。数量、范围、次数要具体（全县每人、每天一块、第九十九次）。\n"
        "  例：觉醒那天，全城都在等我死\n"
    ),
}
INTRO_USER = (
    "给 3 个简介候选，各一段。\n"
    "每段 150~250 字，第一句就必须是钩子。\n"
    "不要写设定说明，写读者会想看的那个场景。\n"
)

# ★ 重出专用：上一批六条全一个模子时用。把"不许雷同"换成"各写一个侧面"，
#   模型对"各写一个侧面"这种局部要求守得住，对"不许重复"这种全局要求守不住。
SIDE_USER = (
    "\n★★ 上一批因为六条全是一个模子，整批作废。这次**必须**按下面格式写：\n"
    "  侧面｜书名\n"
    "  侧面只能从这六个里选，六条各用一个，不许重复：\n"
    "  动作／身份／代价／台词／数字／失物\n"
    "  动作＝书名里写那个动作本身；身份＝写主角是什么人；代价＝写我丢了什么；\n"
    "  台词＝一句敢喊出来的狠话；数字＝一个具体的数；失物＝写对方丢了什么。\n"
    "  例：\n"
    "  数字｜第九十九次打卡那天，我删了库\n"
    "  台词｜这杯酒我先干，你们随意\n"
    "★ 其他要求不变：**14–15 字**、要有一件具体的事、用「我」或身份、\n"
    "  不许他／她／它、不许「被××后，我反手××」这种套路句式、不许空词。\n"
    "★ 只输出六行，不要编号、不要引号、不要解释。\n"
)


# ★ 章节名：只露一角，不许把这一章写明白。书名那条规则也从这里拆出来用。
#   他点名过两次：① 不要都是四个字 ② 不要直接把本章的内容写出来。
CHAP_RULE = (
    "★★ 读者在目录里一眼扫过去只停零点几秒，标题要短句、有冲突、看出身份。\n"
    "   ★ 字数 **8–18 字**（10–18 字最好）。短于 8 字撑不起一个小钩子，\n"
    "     长于 18 字在目录里会被截断，两头都判不合格。\n"
    "★★ 头号毛病叫**事件复述**：把这一章发生了什么写完——读者看标题就知道这章讲啥，\n"
    "   还点进来干什么？要改成**情绪缺口**：给一个具体的画面／物件／数字，但不给结论。\n"
    "   反例：母亲的意外      → 母亲留下的语音只有六秒\n"
    "   反例：第八章 决斗     → 他掀开桌布，剑鞘还在滴血\n"
    "   反例：妻子的坦白      → 她说完那句，把碗摔在了地上\n"
    "★ 五种写法，十条里换着用：\n"
    "  ① 情绪缺口（一个具体的东西＋一个没回答的问题）：那行字不是他写的\n"
    "  ② 具体动作＋具体物件：他把那封信按进了账本里\n"
    "  ③ 反常识（通常是这样，这章偏是那样）：原告律师替我付了保释金\n"
    "  ④ 一句有戏的话（要有矛盾或反转，平淡的「我爱你」没用）：我等了你十年\n"
    "  ⑤ ★ 身份／关系对撞（番茄、七猫的免费流量池最吃这一条）：\n"
    "     前世的杀父仇人，此刻是她的相亲对象\n"
    "     那个救过她三次的男人，是她十年前亲手埋的\n"
    "★ 数字不许写整数——「73 小时」比「三天」真，「547 万」比「五百万」真。\n"
    "  具体数字自带画面：保险柜里的照片有七个人，四个已经死了\n"
    "  ★ 长短要有变化：三个长标题后面接一个短标题才有节奏，\n"
    "    章章一样长＝目录是一块方块字，读者一眼扫过去没有视觉重点。\n"
    "  ★★ 两种一看就是凑出来的写法，整批判废：\n"
    "     ① 七言／五言那种文绉绉的对仗句：「井边打水遇异常」「半块御牌引猜疑」。\n"
    "     ② **名词短语**：「XX中的XX」「XX的XX」「XX之夜」——\n"
    "        那是给这一章起类目名，不是钩子（「密信中的暗语」不行，\n"
    "        「信上第三行字被刮掉了」才是读者想点开的）。\n"
    "     要像人在说话。★ 上面那四个例子只是讲**口气**，不许原样端出来。\n"
    "★ ★ 不许出现**角色的人名**：目录里写人名读者一秒就划过去了。{noname}\n"
    "★★ 不许写病句，**尤其不许只写一个动宾短语**：\n"
    "   「给汤下药」——谁下的？下给谁？读都读不通。\n"
    "   改成「她端来的汤我没喝」／「那碗汤我换给了他」——谁、对谁，一眼看清。\n"
    "  ⇒ 判据：这个标题答得出**谁**和**对谁（对什么）**吗？答不出来＝残句。\n"
    "  ★ 一句里不许出现两个「我」（自己对自己＝病句）。\n"
    "  ★ 写完念一遍，念不通就重写。\n"
    "★ 不许把这一章的结果说出来（成功了／答应了／露馅了／找到了）。\n"
    "★ 不许抽象名词：决斗、阴谋、真相、对峙、风波、试探、求助、变故、相遇。\n"
    "★★★ 不许写涉黄暗示（没有商量余地）：不许用「下不了床／侍寝／春宵／初夜／\n"
    "  双修／合欢」这类词。番茄见到直接整改下架，书就没了。张力写在关系反转上。\n"
    "★ 不许写攻击真实机构（政府／公安／银行／电网／医院……）＋没有虚拟前提的内容。\n"
)

CHEAT_USER = (
    "按这本书的题材和主角，给 5 个金手指候选。\n"
    "每条一行，用全角竖线 ｜ 分三段：能力｜限制｜成长。\n"
    "每段 5~15 字，整条不超过 45 字。\n"
    "像预告片，别写成说明书。\n"
)

# ★★ 他点名：「金手指来来回回就那么几个」。
#   根因在提示词——只举了一条例子，模型就贴着那一条写，六条全是一个模子。
#   ⇒ 给它一张「能力轴」清单，每次**抽六条互不相同的轴**逼它换方向；
#     并且记住上一批用过的轴，换一批时强制避开（不然换十批还是那几个）。
CHEAT_AXES = [
    ("改", "改写一件已经发生的事：纸上写过的字、一句话、一个数字、监控里那一秒"),
    ("夺", "把别人的一样东西拿过来：运气、寿命、修为、记忆、本事"),
    ("换", "把两样东西对调：两个人的伤、两个人的处境、两件东西的位置"),
    ("挪", "把一件事推迟或者提前：一场雨、一次死亡、一封信到达的时间"),
    ("复制", "复制一件具体的东西，或者复制别人的一次出手"),
    ("预知", "看见一小段之后——但必须能靠它对外做成一件事，不许只看见不动手"),
    ("听", "听见别人听不见的：心里的念头、死人留下的话、物件上的残留"),
    ("逼", "让对方不得不做一件事：必须说实话、必须走过来、必须松开手"),
    ("控", "操控一类很具体的东西：影子、绳子、门、火、锁、别人的一只手"),
    ("记", "让人记不住他，或者反过来——让人只记得他、忘不掉他"),
    ("转", "把打到自己身上的东西转给别人：伤、病、灾、骂名"),
    ("长", "让一样东西长回来或者修好：断掉的肢体、烧掉的字、死掉的庄稼"),
    ("定", "在一小块地方里定一条规矩，在这块地方里所有人都得照着来"),
    ("借", "借来不属于自己的东西：别人的身份、别人的名字、别人十年后的本事"),
    ("藏", "把一样具体的东西藏进别人找不到的地方，之后取出来"),
    ("标", "给一样东西或一个人打上标记，之后能找到它、或者让它应验"),
]
_CHEAT_AXES_USED = {"last": []}


def _cheat_axes(n=6):
    """抽 n 条能力轴，尽量避开上一批用过的。"""
    import random
    used = set(_CHEAT_AXES_USED.get("last") or [])
    pool = [a for a in CHEAT_AXES if a[0] not in used] or list(CHEAT_AXES)
    random.shuffle(pool)
    pick = pool[:n]
    if len(pick) < n:                      # 池子不够就从用过的里补
        have = {a[0] for a in pick}
        pick += [a for a in CHEAT_AXES if a[0] not in have][:n - len(pick)]
    _CHEAT_AXES_USED["last"] = [a[0] for a in pick]
    return pick


def _axis_block(axes) -> str:
    """把能力轴写成「第 k 条走哪条轴」——比空口说「要有多样性」管用得多。"""
    s = ("\n★★★ 这一批六条必须**各走一条不同的能力轴**，按下面给的顺序写，\n"
         "  第 1 条走第 1 条轴，第 2 条走第 2 条轴……六条六个方向。\n"
         "  两条落在同一类上＝整批作废。\n"
         "  ★★ 冒号后面那句话只是**指方向**，一个字的原文都不许抄——\n"
         "     具体是谁、对谁用、在什么场合用，你自己想（照抄的一律判废）。\n"
         "  ★ 也不许把轴名（改／夺／换……）写进能力段，那是分类不是能力。\n")
    for i, (k, d) in enumerate(axes, 1):
        s += "  第%d条｜%s：%s\n" % (i, k, d)
    return s


# ★ 轴名是贴上去的标签，模型不按顺序写时就对不上。按内容里的关键词反认：
#   能力段写「对调／互换」→ 换轴，写「推迟／提前」→ 挪轴。
_AXIS_KEYS = {
    "改": ["改写", "改掉", "修改", "改一", "已经发生", "写过的字", "说过的话", "改口"],
    "夺": ["夺", "拿走", "拿过来", "抢", "分走", "抽取", "吸走", "失去", "抹掉",
          "删掉", "清空", "剥夺"],
    "换": ["对调", "互换", "交换", "对换", "置换", "换过来", "把两", "改变", "扭转"],
    "挪": ["推迟", "提前", "延后", "挪移", "改期", "挪"],
    "复制": ["复制", "复刻", "照搬", "拷贝", "拓印"],
    "预知": ["预知", "预见", "未卜", "算出", "得知", "先知道"],
    "听": ["听见", "听到", "听得见", "心声", "心里在想", "残留", "遗言", "低语"],
    "逼": ["逼", "不得不", "迫使", "强迫", "审讯"],
    "控": ["操控", "控制", "操纵", "掌控", "驱使"],
    "记": ["记不住", "忘不掉", "忘记", "记住", "记得", "铭记", "想不起", "忘掉",
          "记不起"],
    "转": ["转给", "转到", "转嫁", "反弹", "转移", "反噬", "替她", "替他", "承受",
          "扛", "挡下", "拦下", "引到"],
    "长": ["愈合", "修复", "修好", "长回来", "恢复", "治好", "重铸", "复原",
          "重生", "再生", "补全", "续上"],
    "定": ["定一条", "定下", "规矩", "规则", "立规", "立法", "这片", "范围内", "号令",
          "设定", "制订", "制定"],
    "借": ["借来", "借用", "借他", "借来的", "暂借", "借尸"],
    "藏": ["藏", "匿", "收进", "存进", "掖", "埋进"],
    "标": ["标记", "打上", "标出来", "定位", "追踪", "找到它"],
}


def _match_axis(text: str, axes) -> str:
    """按内容把这条金手指归到某条能力轴上；认不出来就返回空（不硬贴）。"""
    t = text or ""
    best, hits = "", 0
    for k, _d in axes:
        n = sum(1 for w in _AXIS_KEYS.get(k, []) if w in t)
        if n > hits:
            best, hits = k, n
    return best

OUTLINE_USER = (
    "给本章的章纲。每章一行，用 ｜ 分四段：\n"
    "章号｜标题｜本章必办一件事｜章末钩子\n"
    "标题 3~6 字，只写炸的那一下；必办一件事只写一件；"
    "钩子是留给读者的疑问，不是总结。\n"
)

# ★ 全书大纲：简介之后、细纲之前的那一步（他点名要加的：简介→大纲→细纲→章纲→章节名）
SKELETON_USER = (
    "给全书大纲 5 段。每段一行，用 ｜ 分四段：\n"
    "段名｜章节范围｜这一段发生什么｜段末那一刀\n"
    "每段 30~60 字。\n"
)

# ★ 细纲：把大纲的一段拆成逐章一句话（章纲之前的那一步）
BEAT_USER = (
    "这本书第 {frm} 章到第 {to} 章的细纲。这一段属于「{stage}」。\n"
    "这一段的大纲：{line}\n"
    "★★ 逐章写一行，一共 {n} 行。格式严格为：章号｜这一章的一句话\n"
    "  「这一章的一句话」＝谁、要什么、被谁挡、结果如何。25–45 字，只写一件东西。\n"
    "★ 不要写正文、不要写对话、不要解释、不要前后说明。\n"
    "★★ 连贯：第 N 章开头必须接住第 N-1 章结尾没解决的那件事，不许跳、不许另开一条线。\n"
    "★★★ 推进（最容易犯的错）：第 N 章必须出现**一件第 N-1 章没有的事**——\n"
    "  一个新人物、一个新物件、一个新信息、一个新麻烦，四选一。\n"
    "  ★ 同一件事换个说法连写三章＝整批作废\n"
    "    （「护令牌」「抗令牌」「令牌誓死不弃」是同一件事，不是三章）。\n"
    "  ★ 每章一句话里要有**结果**，别停在「他决心……」——决心不是事。\n"
    "  推进的节奏照下面这三行学（只学节奏，不许抄它的内容）：\n"
    "    1｜他把令牌扔进火里，转头下山，身后没有一个人追出来\n"
    "    2｜山脚客栈里，有人盯住他腰上那道疤，问他是谁\n"
    "    3｜那道疤是三年前留下的，而当年送他上山的就是这个人\n"
    "★★ 不许漂移：主角是{sex}，题材是{genre}——\n"
    "  从头到尾不许换人、换性别、换题材、换世界观，\n"
    "  不许把别的书的东西带进来（本书没有的设定、物件、职业、时代一律不许出现）。\n"
    "{prev}"
)


def _book_block(book: dict) -> str:
    """★ 钩子之外，想写的一句话／主角／感情线也都是已定的。

    他撞过一回：想写「配角逆袭成女帝」（主角萧澜，女的），
    出来的书名简介却跑成另一套人马。根因：下游生成只盯着钩子，
    主角、感情线躺在 system prompt 里，user prompt 不点名模型就当没看见。
    """
    brief = (book.get("brief") or "").strip()
    prot = (book.get("protagonist") or "").strip()
    rom = (book.get("romance") or "").strip()
    lines = [("想写的一句话", brief), ("主角", prot), ("感情线", rom)]
    lines = ["%s：%s" % (k, v) for k, v in lines if v]
    if not lines:
        return ""
    sex = ""
    if re.search(r"她|女", prot) or re.search(r"女帝|娘娘|公主|嫡女|皇妹|妃", brief + rom):
        sex = ("  ★ 主角是女性：写主角一律用「我」或她的身份，不许把她写成男的、\n"
               "  不许换成另一个人、不许改成别的职业。她／皇后这类字只能指别人。\n")
    elif re.search(r"他", prot):
        sex = ("  ★ 主角是男性：不许把他写成女的、不许换成另一个人或别的职业。\n")
    setting = (book.get("setting") or "").strip()
    tail = ""
    if setting:
        # ★ 导入的书有完整设定文档。它是骨架，不是参考——
        #   模型要在这上面长血肉，不许另起一套人物和世界。
        tail = ("\n★★ 这本书已经有完整设定，写在下面。\n"
                "   你接下来写的所有东西都要长在它上面：人物用这里面的人名和性格，\n"
                "   地点用这里面的地点，能力只能用这里写明的那些。\n"
                "   另起一套人物、地点、能力，就是废稿。\n"
                "【既有设定】\n" + setting[:6000] + "\n")
    return ("\n★ 下面这些已经定了，你写的一切都要长在它们身上，跑了就是废稿：\n"
            + "".join("  · %s\n" % l for l in lines) + sex + tail)


def _hook_block(hook: str) -> str:
    """★ 书名要吃钩子：书名是那一个动作的标题，不是另起一个故事。"""
    hook = (hook or "").strip()
    if not hook:
        return ""
    return ("\n★ 这本书的一句话钩子已经定了：「%s」\n"
            "  书名要让读者觉得**就是这一句的书名**：同一个处境、\n"
            "  同一个没回答的问题。写到别的处境上去＝跑题，判废。\n"
            "  ★★ 但围着同一件事，十条要换十个不同的侧面：\n"
            "     主角此刻的身份／对方丢了的东西／一个时间点（第几夜、第几天）／\n"
            "     一句狠话／一个数字或一个物件／最坏的那种可能／有人以为他已经完了。\n"
            "  ★★ 死法就在这：**同一个物件不许在两条里都出现**——\n"
            "     钩子里那个东西（离职证明、那口井、那只耳坠）在十条里只许用一次，\n"
            "     出现第二次的那条判废。这是现在最常见的死法：十条全围着一件东西写。\n"
            "★ 钩子里的「他」是叙述视角，搬进书名时必须换成「我」或身份\n"
            "  （「他砸碎手机」不合格，「我砸碎那部手机」合格）——这条高于钩子的原话。"
            % hook)


def _lcs(a: str, b: str) -> str:
    """最长公共连续子串。用来抓「我反手」「被…后」这种共用骨架（≥3 字才算）。"""
    if not a or not b:
        return ""
    prev = [0] * (len(b) + 1)
    best, at = 0, 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best, at = cur[j], i
        prev = cur
    return a[at - best:at] if best >= 3 else ""


def _title_block(title: str, hook: str = "") -> str:
    """★ 简介要吃书名（顺带吃钩子）：简介得让书名站得住，但不能解释书名。"""
    ps = []
    title = (title or "").strip()
    hook = (hook or "").strip()
    if title:
        ps.append("★ 这本书的书名已经定了：**《%s》**。\n"
                  "  简介要让读者相信这个书名：书名里写的那个动作／那个反差，\n"
                  "  在简介里要有对应的**画面**。但不许复述书名，也不许解释书名是什么意思。"
                  % title)
    if hook:
        ps.append("★ 这本书的一句话钩子：「%s」。\n"
                  "  简介沿着这个钩子往下写，不许写成另一个故事。" % hook)
    return ("\n" + "\n".join(ps) + "\n") if ps else ""


def _avoid(prev) -> str:
    prev = [p for p in (prev or []) if p]
    if not prev:
        return ""
    return "\n\n请避开以下已出过的，不要重复或近似：\n- " + "\n- ".join(prev)


# ★ 模型输出里常混进 emoji／几何符号（实测出过"他一剑封喉，📐竟在敌人临死前笑出了声"）。
#   一律按字符区间剔掉，只留文字和正常标点。
_STRIP = re.compile("["
                    "\U0001F000-\U0001FAFF"      # emoji、象形符号
                    "\U0001F900-\U0001F9FF"
                    "\U00002600-\U000027BF"      # 杂项符号、dingbats
                    "\U0001F1E6-\U0001F1FF"      # 国旗
                    "\u2190-\u21FF"              # 箭头
                    "\u2200-\u22FF"              # 数学符号
                    "\u2300-\u23FF"              # 技术符号
                    "\u2460-\u24FF"              # 带圈数字
                    "\u25A0-\u27BF"              # 几何图形
                    "\u2B00-\u2BFF"              # 杂项箭头
                    "\uFE00-\uFE0F"              # 变体选择符
                    "\u200D\u20E3"               # 连字、键帽
                    "]+")


def _clean(s: str) -> str:
    s = _STRIP.sub("", s or "")
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


def parse_lines(raw: str):
    out = []
    for r in (raw or "").split("\n"):
        s = _clean(r)
        s = re.sub(r"^\s*\d+[.、)．]\s*", "", s)
        s = re.sub(r"^[\s\"'“”「」『』【】\[\]>*\-]+", "", s)
        s = re.sub(r"[\s\"'“”「」『』【】\[\]]+$", "", s).strip()
        if len(s) >= 4:
            out.append(s)
    return out


def parse_rows(raw: str):
    out = []
    for line in (raw or "").split("\n"):
        s = line.strip()
        # ★ 它爱插一行 markdown 小标题（「### 章节名候选」「**第1章**」）——
        #   不剥掉的话那行没有竖线会被跳过还好，怕的是它把标题和内容写同一行
        s = re.sub(r"^[#>*\-\s]+", "", s)
        if not s or ("｜" not in s and "|" not in s):
            continue
        parts = [_clean(x) for x in re.split(r"[|｜]", s)]
        m = re.match(r"^\s*(\d+)", parts[0])
        if not m:
            continue
        # ★ 模型常常自己重复一遍章号（「1｜1｜章节名｜…」），剥掉，
        #   否则章节名里带个「1｜」，看着像坏了。
        t = re.sub(r"^\s*\d+\s*[.、)．｜|]\s*", "", parts[1] if len(parts) > 1 else "")
        # ★ 章节名自己带 markdown 残渣（「**章节名**」「`章节名`」）也要剥
        t = re.sub(r"^[*`#\s\"'“”「」『』【】\[\]]+|[*`#\s\"'“”」』】\]）]+$", "", t)
        task = parts[2] if len(parts) > 2 else ""
        hook = parts[3] if len(parts) > 3 else ""
        if len(parts) > 4 and not hook:
            hook = parts[4]
        out.append({"n": int(m.group(1)), "title": t, "task": task, "hook": hook})
    return out


# ---------------------------------------------------------------------------
# 三个生成入口（模型调不通时把提示词带回去，让人能复制到别处）
# ---------------------------------------------------------------------------

def _corpus(book, task="outline", use=True):
    """取本题材的写法规则（蒸馏过的，不是原文）。坏了就当没有，不能拖垮生成。

    ★ task 决定取哪几类规则：钩子要开篇+爽点，章纲要结构+爽点+桥段+毒点。
      不是每次都把同一坨资料塞进去。
    """
    if not use:
        return "", []
    try:
        import importlib.util
        p = os.path.join(HERE, "corpus", "pick.py")
        spec = importlib.util.spec_from_file_location("_cpick", p)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m.rules_for(book, task)
    except Exception:
        return "", []


def gen_hook(book: dict, avoid=None, use_corpus=True):
    ctx, picked = _corpus(book, "hook", use_corpus)
    sys_p = system_text(book, ctx)
    usr_p = HOOK_USER + _avoid(avoid)
    try:
        raw = llm.complete(sys_p, usr_p, temperature=1.0, max_tokens=4096)
    except Exception as e:
        return {"ok": False, "err": str(e), "prompt": sys_p + "\n\n" + usr_p}
    items = parse_lines(raw)
    if not items:
        return {"ok": False, "err": "解析不出条目", "raw": raw}
    return {"ok": True, "items": items[:8], "picked": picked,
            "warn": _unsafe(items)}


def _norm_title(t: str) -> str:
    """★ 模型十次有八次出半角标点，书名因此全被标红。
    归一成中文是程序该干的活，不该让人手动去改标点。"""
    t = (t.replace(",", "，").replace(";", "；")
          .replace("?", "？").replace("!", "！").replace(":", "："))
    return re.sub(r"\s+", "", t)


def _fix_pronoun(t: str) -> str:
    """书名里的「他／她」几乎总是在写主角，程序替他改掉，别让人手动改。

    ★ 判据：以他／她开头、句中没有「我」→ 这个他／她就是主角，换成「我」。
      句中已有「我」→ 那个他／她指的是别人（「她的名字被我划掉」），保留。
    """
    # ★ 判据：**整句里没有「我」** → 这里的他／她就是主角，一律换成「我」。
    #   句子里已经有「我」了 → 那个他／她指的是别人（「她的名字被我划掉」），保留。
    #   女频设定里主角通篇写「她」，模型照抄出来十有八九是这个情况。
    t = (t or "").strip()
    if not t or "我" in t:
        return t
    if re.search(r"[他她]", t):
        return re.sub(r"[他她]", "我", t)
    return t


def _parse_titles(raw: str, name: str = ""):
    """书名候选。兼容两种输出：纯书名，或「侧面｜书名」（重出时才要求带侧面）。"""
    out = []
    for line in parse_lines(raw):
        t = _fix_pronoun(_norm_title(line))
        side = ""
        m = re.match(r"^(动作|身份|代价|台词|数字|失物)\s*[｜|]\s*(.+)$", t)
        if m:
            side, t = m.group(1), _fix_pronoun(_norm_title(m.group(2)))
        if t:
            out.append({"title": t, "side": side,
                        "bad": forge.check_title(t, name)})
    return out


# ★ 7B 写不满 15 字（它自然长度就是 11 字）。重写没用，重出也没用——
#   实测把已经写好的那条**加长**，它守得住。所以第二阶段：加料，不重写。
WIDEN_USER = (
    "请把下面这条书名加长到 14–15 字。书名：\n「{t}」\n"
    "★ 原来那句话一个字都不许删，也不许再加第二个逗号——加完必须还是一句话。\n"
    "★ 加料只加在一处：**逗号前面那半句**，给它加处境（什么时候／被谁／第几次／在哪）。\n"
    "   例：第三杯毒酒，我一饮而尽 → 被逼饮下第三杯毒酒当晚，我一饮而尽\n"
    "★ 不许加抽象名词（秘密、命运、契机、真相、之路），不许加句号，\n"
    "   不许改成「被××后，我反手××」这种句式。\n"
    "★ 给 3 个加长后的版本，每条一行，不要编号、不要引号、不要解释。\n"
)


FIX_USER = (
    "下面这几条书名不合格，逐条改到合格。格式：`编号｜毛病｜书名`\n"
    "{body}\n"
    "★ 只改毛病指出的那一处，**其余一个字都不许动**——\n"
    "  改完必须还是同一个书名、同一件事、同一个结果，只是把毛病去掉了。\n"
    "★ 毛病说「太短」的：给**逗号前面那半句**加处境（什么时候／第几次／在哪／被谁），\n"
    "  不许再加第二个逗号，不许加抽象名词（秘密、命运、契机、真相、之路）。\n"
    "★ 毛病说「空话」的：把那个词换成一个看得见的东西（一只脚、第九封信、一碗汤）。\n"
    "★ 毛病说「他／她／它」的：把那个字换成「我」。\n"
    "★ 毛病说「文言腔」的：把那个字眼换成大白话（欲→要，赎→还，能否→直接给结果），\n"
    "  改完要像人喊出的一句话，不许像古装剧台词。\n"
    "★ 毛病说「病句」的：把句子理通——主语只有一个、动词带着对象、结尾说完整。\n"
    "★ 改完必须同时满足：12–15 字；用「我」或身份；不许空话；不许角色名。\n"
    "★ 输出格式：`编号｜改好的书名`，一行一条，编号顺序照原样，\n"
    "  只输出这几行，不要编号以外的内容、不要解释。\n"
)


WIDEN_CHAP_USER = (
    "有一条章节名太短了，撑不起钩子：\n"
    "  现在的：「{t}」\n"
    "  这一章写的是：{src}\n"
    "★ 把它加长到 **8–18 字**：补上一个具体的物件、一个数字、或者一个身份，\n"
    "  让它变成读者想点开的一句话。不许改掉原来的意思，不许加抽象名词。\n"
    "  例：「屏幕一闪」→「屏幕一闪，多出一行没写过的代码」\n"
    "★ 只给 3 个候选，每条一行，不要编号、不要引号、不要解释。\n"
)

TRIM_USER = (
    "下面这几条书名都超了 15 字（书架上最多 15 字，多一个都放不下）：\n"
    "{body}\n"
    "★ 逐条压到 **14–15 字**（含标点）。只许删字，**不许另起一句写**。\n"
    "★ 删的顺序：先删抽象名词（秘密／真相／命运／未来／之路），再删修饰语\n"
    "  （竟然／突然／忽然／狠狠），最后删重复的意思。\n"
    "  **具体的东西一个都不许删**（是谁、什么时候、哪个物件、什么结果）。\n"
    "★ 压完还要是「前后两半句、中间一个逗号」的结构。\n"
    "★ 输出格式：`编号｜压好的书名`，一行一条，编号顺序照原样，\n"
    "  只输出这几行，不要编号以外的内容、不要解释。\n"
)


def _trim_batch(book: dict, ts, name: str = ""):
    """★ 一次把几条超字书名全压回去。

    它写书名十有八九写超（实测十条里六条是 16–23 字），一条一条问＝干等半分钟。
    所以一次把整批塞进去，一次调用改完。
    """
    ts = [t for t in ts if t]
    if not ts:
        return {}
    body = "\n".join("%s｜%s" % (i + 1, t) for i, t in enumerate(ts))
    try:
        raw = llm.complete(system_text(book, ""), TRIM_USER.format(body=body),
                           temperature=0.8, max_tokens=600)
    except Exception:
        return {}
    got = {}
    for line in (raw or "").splitlines():
        m = re.match(r"^\s*(\d+)\s*[｜|.、)．:：]\s*(.+)$", line)
        if not m:
            continue
        i = int(m.group(1)) - 1
        if not (0 <= i < len(ts)) or i in got:
            continue
        s = _fix_pronoun(_norm_title(_clean(m.group(2))))
        # ★ 压之前矛盾必须 still valid：原话的核心物件还在、长度合法了
        if (not s or len(s) > 15 or forge.check_title(s, name)
                or len(s) < len(ts[i]) - 9):
            continue
        got[i] = s
    return got


def _hard_bad(b: str) -> bool:
    """判罚分两级：硬伤（必须扔）／软伤（只是不够好，可以摆出来让人自己判断）。

    ★ 没有这条分级，凑不够 5 条的时候要么空列表、要么拿废稿凑数——两种都不能接受。
    """
    # ★ 病句也是硬伤：他点名骂过「我要我跪着来」「我温柔抚过我」——
    #   这种摆出来就是丢人，宁可少给一条也不许显示。文言腔（能否／欲／赎）同罪。
    #   ★ 涉黄暗示同罪：他截图那条「我让世子爷下不了床」——番茄看到这种书名
    #     直接整改下架，书就没了。宁可少给一条，不许摆出来。
    return any(k in b for k in ("最多 15 字", "角色名", "他／她／它", "不允许的字符",
                                "病句", "文言腔", "涉黄"))


def _fix_batch(book: dict, pairs, name: str = ""):
    """★ 一次把几条「有毛病但不是超字」的书名全改好。

    pairs = [(书名, 毛病)]。它写书名十有八九要差一点（太短／空话／写了他她），
    一条一条问＝干等半分钟；把整批连毛病一起塞进去，一次调用改完。
    ★ 关键是**把毛病写出来**——只说"改好"它不知道改哪儿（章节名那套验证过）。
    """
    pairs = [(t, r) for t, r in pairs if t and r]
    if not pairs:
        return {}
    body = "\n".join("%s｜%s｜%s" % (i + 1, r, t) for i, (t, r) in enumerate(pairs))
    try:
        raw = llm.complete(system_text(book, ""), FIX_USER.format(body=body),
                           temperature=0.85, max_tokens=900)
    except Exception:
        return {}
    got = {}
    for line in (raw or "").splitlines():
        m = re.match(r"^\s*(\d+)\s*[｜|.、)．:：]\s*(.+)$", line)
        if not m:
            continue
        i = int(m.group(1)) - 1
        if not (0 <= i < len(pairs)) or i in got:
            continue
        s = _fix_pronoun(_norm_title(_clean(m.group(2))))
        # ★ 改完还得是原来那条：跟原话至少还有 4 个字是连着的，否则是另起了一句
        # ★★ _lcs 返回的是**子串**不是长度（这里写错过一次：拿字符串跟 4 比
        #   直接 TypeError，整批修补白跑，界面上就表现为"十条全扔"）。
        if not s or len(s) > 15 or len(_lcs(s, pairs[i][0])) < 4:
            continue
        if any(_hard_bad(z) for z in forge.check_title(s, name)):
            continue
        got[i] = s
    return got


def _widen_title(book: dict, t: str, name: str = "") -> str:
    """把一条短书名加长到 14–15 字。加不动就原样退回（宁可短，不许写坏）。"""
    n0 = len(re.sub(r"\s", "", t or ""))
    if n0 >= 14 or not t:
        return t
    try:
        raw = llm.complete(system_text(book), WIDEN_USER.format(t=t),
                           temperature=1.0, max_tokens=400)
    except Exception:
        return t
    best, bestn = t, n0
    for line in (raw or "").splitlines():
        s = _norm_title(_clean(line))
        # ★ 加长的前提是「原话没丢」——它最爱顺手把后半句改掉。
        if not s or len(s) < n0 or t[:3] not in s:
            continue
        if forge.check_title(s, name):
            continue
        if len(s) > bestn:
            best, bestn = s, len(s)
    return best


def _unsafe(items) -> List[str]:
    """★ 安全前提检查：命中就在界面上弹红条。

    这条比文笔重要得多——「入侵政府服务器」被单独截图发出去是要命的。
    写了虚拟／模拟／演练就放行；写了真实机构或压根没提前提，就得报出来。
    """
    out = []
    for t in items:
        out += forge.check_unsafe(t or "")
    return list(dict.fromkeys(out))[:3]


def _prot_name(book: dict) -> str:
    """从主角设定里抠出人名（「萧澜 / 15岁 / 身处冷宫」→「萧澜」）。

    ★ 他点名：书名和章节名里不许出现角色名。爆款榜上确实没有用人名取名的——
      读者不认识这个人，算法也拿人名打不出标签。
    """
    p = (book.get("protagonist") or "").strip()
    if not p:
        return ""
    # ★「／」全角斜杠也是常见分隔（云蘅／19岁），不切就抠不出人名
    head = re.split(r"[\s/／｜|，,、。（(:：]", p)[0].strip()
    head = re.sub(r"[\d岁]", "", head)
    # 名字是 2–3 个纯中文字；「冷宫里的那个」「被开除的程序员」不是名字
    if 2 <= len(head) <= 3 and re.fullmatch(r"[一-龥]{2,3}", head):
        return head
    return ""


def _prot_names(book: dict) -> list:
    """★ 主角姓名的**所有写法**：全名 ＋ 去掉姓的名。

    只查全名会漏：主角「沈玉容」，书名写「玉容替嫁」照样是角色名，
    读者一样不认识、算法一样打不上标签。
    """
    nm = _prot_name(book)
    if not nm:
        return []
    out = [nm]
    if len(nm) == 3:
        out.append(nm[1:])          # 去掉姓，剩名字那两个字
    return out


def _noname(name: str) -> str:
    """把「不许写人名」写进 prompt——点着名字说，比说「不要用人名」管用。"""
    if not name:
        return ""
    return ("这本书的主角叫「%s」，书名里出现这两个字＝失败。"
            "换个身份说法（冷宫里那个／假千金），或者直接写「我」。\n  " % name)


def _rank_titles(items):
    """给候选排个好坏：先不判废的，再挑把 15 字用满的。

    ★ 书好不好看书名占一半，7B 一次给的十条参差不齐。
      交给它自己挑它会把最套路那条排第一（「开局××」），所以由程序挑。
    """
    return sorted(items, key=lambda x: (1 if x["bad"] else 0,
                                        -len(re.sub(r"\s", "", x["title"]))))


def _dedup_titles(pool, ban=None):
    """★ 判「六条是不是一个模子」——他点名过两次，光说"不许雷同"模型守不住。

    三道判据：① 前四个字一样 ② 与已选中的某条有 ≥3 字的公共片段
    （「我砸碎手机」「我反手」这种共用骨架就是这么抓出来的）
    ③ 侧面重复（重出批里两条都标了「动作」）。
    抓到的不入选，并把那个片段记进 ban，重出时禁掉。
    """
    out, rest, seen, used, sides = [], [], set(), [], set()
    ban = list(ban or [])
    for x in pool:
        t = re.sub(r"\s", "", x["title"])
        # ★ 完全一样的两条＝同一条书。后面那条直接丢掉，连「雷同」的灰字都不该有
        #   （实测两批合起来时出现过两条一字不差还占一个位置）。
        if t in used:
            continue
        head, frag, why = t[:4], "", ""
        if head in seen:
            why = "句式和上面一条雷同"
        else:
            for u in used:
                c = _lcs(t, u)
                if len(c) >= 3:
                    why, frag = "句式和上面一条雷同（都用了「%s」）" % c, c
                    break
        if not why and x.get("side"):
            if x["side"] in sides:
                why = "侧面和上面一条重复（都是「%s」）" % x["side"]
            else:
                sides.add(x["side"])
        if why:
            x["bad"] = (x["bad"] or []) + [why]
            rest.append(x)
            ban.append(frag or head)
        else:
            seen.add(head)
            used.append(t)
            out.append(x)
    return out, rest, ban


def gen_title(book: dict, avoid=None, use_corpus=True, hook="",
              _retry=0, _ban=None):
    """★ 超 15 字的书名默认不显示：先扔掉重出一批，实在出不够才带着原因显示。

    他点名过两件事：① 超 15 字还照样列出来 ② 六条全是「我改了××」一个模子。
    所以这里两道关：不合公式的扔掉重来，开头撞车的也扔掉重来（把撞车的开头禁掉）。

    ★ 流程是串起来的：钩子定了 → 书名跟着钩子走（hook 传进来）。
    """
    ctx, picked = _corpus(book, "title", use_corpus)
    sys_p = system_text(book, ctx)
    nm = _prot_names(book)
    # ★ 男女频吸量逻辑不同，缺口也要跟着变
    usr_p = (TITLE_USER.format(noname=_noname(nm),
                               mf=MF_TITLE.get(book.get("mf", ""), ""))
             + _book_block(book) + _hook_block(hook))
    # ★ 重出＝上一批撞车了。这时换打法：强制它六条各写一个侧面
    #   （光说"不许雷同"它守不住，说"各写一个侧面"它守得住）。
    if _retry >= 1:
        usr_p += SIDE_USER
        usr_p += ("\n★ 上一批太短的整批判了废：这次每条必须 14–15 字，"
                  "写完自己数字数，少于 13 字就是失败。\n")
    usr_p += _avoid(avoid)
    if _ban:
        usr_p += ("\n★ 这些开头／片段已经用过了，一条都不许再出现（用了就是失败）："
                  + "／".join(_ban))
    try:
        # ★ 重出＝上一批句式撞车了，越重出越要放开（否则它照着原样再来一遍）
        raw = llm.complete(sys_p, usr_p, temperature=min(1.0 + 0.1 * _retry, 1.25),
                           max_tokens=4096)
    except Exception as e:
        return {"ok": False, "err": str(e), "prompt": sys_p + "\n\n" + usr_p}
    items = _parse_titles(raw, nm)
    if not items:
        return {"ok": False, "err": "解析不出书名", "raw": raw}
    # ★ 一次让它出 10 条，程序在这里排序挑最好的：先不判红，再按字数用满程度降序。
    pool = _rank_titles(items)
    # ★ 模型十有八九写不到 15 字，十条全因为「太短」红掉＝白等一轮重出
    #   （重出还是短）。所以字数**不足**的时候容忍，只留其它判罚。
    #   ★ 注意别把「超字」也容忍了——「长度 22 字」也是含「字」的。
    if pool and all(x["bad"] for x in items) and all(
            b.startswith("只有") for x in items for b in x["bad"]):
        pool = _rank_titles([dict(x, bad=[b for b in x["bad"]
                                          if not b.startswith("只有")])
                             for x in items])
    out, rest, ban = _dedup_titles(pool, _ban)
    # ★ 合格的不够就再出一批，两批合起来挑最好的（至少凑够 4 条，别让人没得挑）。
    if len([x for x in out if not x["bad"]]) < 4 and _retry < 2:
        sub = gen_title(book, (avoid or []) + [x["title"] for x in items],
                        use_corpus, hook, _retry + 1, sorted(set(ban)))
        if sub.get("ok"):
            cand = []
            for x in out + list(sub["items"]):
                y = dict(x)
                y["bad"] = [b for b in (y.get("bad") or [])
                            if "雷同" not in b and "侧面" not in b]
                cand.append(y)
            cand.sort(key=lambda x: 0 if not x["bad"] else 1)
            out, rest, _ = _dedup_titles(cand, _ban)
    # ★★ 淘汰之前先救。**必须先改再淘汰**——写超的书名挂着红字，
    #   先淘汰就永远修不到了（我第一版就写反了，十条全扔）。
    #   重写它写不好，改长度它守得住（跟简介续写同一个道理）。
    #   ★ 两条路各一次调用：**超字的整批压**，**其它毛病的整批改**，
    #     一条一条问＝人在界面上干等半分钟。
    cand = out[:]

    def _len(x):
        return len(re.sub(r"\s", "", x["title"]))

    if not _retry:
        longs = [x for x in cand if 15 < _len(x) <= 24]
        if longs:
            got = _trim_batch(book, [x["title"] for x in longs], nm)
            for i, x in enumerate(longs):
                if i in got:
                    x["title"] = got[i]
                    x["bad"] = forge.check_title(got[i], nm)
        bads = [x for x in cand if x["bad"]][:8]
        if bads:
            got = _fix_batch(book, [(x["title"], "；".join(x["bad"])) for x in bads], nm)
            for i, x in enumerate(bads):
                if i in got:
                    x["title"] = got[i]
                    x["bad"] = forge.check_title(got[i], nm)
    # ★★ 最后才淘汰：不合格的一条都不许摆出来
    #   （他点名过两次「你自己标了不合格，为什么还给我看」）。
    final = [x for x in cand if not x["bad"]]
    # ★ 兜底：他要一次看到 5 条（「只显示一条，想换书名也不行」）。
    #   合格的凑不够 5 条时，把**只是短了一点**（11–12 字、其余都合格）的放进来，
    #   标一句淡灰的提示，不再判废——宁可给他 5 条稍短的，也不给 2 条。
    if len(final) < 5:
        # ★ 连「句式雷同」被淘汰的那批也算进来：雷同比空着强，人会自己挑
        for x in (cand + rest):
            if x in final or _len(x) > 15 or _len(x) < 10:
                continue
            if x["bad"] and not any(_hard_bad(b) for b in x["bad"]):
                if "14–15 字最抓人" not in (x["bad"][0] or ""):
                    x["bad"] = ["%s（14–15 字最抓人，可以手动加长）" % x["bad"][0]]
                final.append(x)
            if len(final) >= 5:
                break
    # ★ 还是不够 5 条就再出一批，两批合起来挑（宁可多等几秒）。
    #   ★ 只挡**完全一样**的：女频那批书名都围着同一个物件写，
    #     按公共片段去重会把重出来的全挡掉（实测白等一轮还是 3 条）。
    if len(final) < 5 and _retry < 2:
        sub = gen_title(book, (avoid or []) + [x["title"] for x in items],
                        use_corpus, hook, _retry + 1, sorted(set(ban)))
        if sub.get("ok"):
            for y in sub["items"]:
                if len(final) >= 6:
                    break
                if all(y["title"] != z["title"] for z in final):
                    final.append(dict(y))
    # ★★ 最后一关：一条都挑不出来时绝不给空列表（界面空白＝功能坏了）。
    #   挑判罚最少的三条摆出来，原因用淡灰小字标着，人自己判断要不要用。
    if not final:
        final = [dict(x) for x in sorted(
            cand + rest, key=lambda x: (len(x["bad"]), -_len(x)))[:3]]
    final.sort(key=lambda x: (1 if x["bad"] else 0, -_len(x)))
    final = final[:6]
    # ★ total＝这一轮一共生成了多少条候选。界面拿它说清楚「从多少条里挑的」，
    #   别只报扔掉多少——他上次看到「10 条全扔掉」以为程序坏了。
    return {"ok": True, "items": final, "picked": picked, "total": len(items),
            "warn": _unsafe([x["title"] for x in final])}


def parse_cheats(raw: str):
    """金手指候选：期望「能力｜限制｜成长」三段，模型不守格式就整行收下。"""
    out = []
    for line in (raw or "").split("\n"):
        s = _clean(line)
        s = re.sub(r"^\s*\d+[.、)．]\s*", "", s)
        # ★ 它爱写 markdown 小标题（「### 能看见别人心里在想什么」），不剥掉就是残行
        s = re.sub(r"^[#*\s\"'“”「」『』【】\[\]>*\-]+", "", s)
        s = re.sub(r"^(能力|限制|成长|代价)\s*[：:]\s*", "", s)
        if not s:
            continue
        # ★★ 它把「第1条｜长：…」这个壳原样抄出来当过能力段（实测 6 条全是壳）。
        #   ★ 只剥壳、不扔内容：是不是照抄轴说明，由后面的重合度检测判
        #     （整批抄 ≥2 条 → 重出；单条重合 ≥8 字 → 标红）。以前这里凭
        #     「剩余部分有没有竖线」扔行，结果它把三段拆成三行写时，能力段
        #     被扔、后面的「｜限制」没东西可并——六条黏成一条巨型残废。
        #   ★ new＝这一行带「第N条」前缀：它是**新的一条**，后面的分支
        #     不许把它并回上一条（实测六条全黏成一条就是这么来的）。
        new = False
        s2 = re.sub(r"^\s*第?\s*\d+\s*条?\s*[｜|]\s*", "", s)
        if s2 != s:
            new, s = True, s2
        s = re.sub(r"^(改|夺|换|挪|复制|预知|听|逼|控|记|转|长|定|借|藏|标)"
                   r"\s*[：:]\s*", "", s)
        # ★★ 它把一条拆成三行写（「｜必须隔天…」单独一行）——不并回去就是三条残废
        if re.match(r"^\s*[|｜]", s):
            if out:
                out[-1] = out[-1] + s
                continue
            # 上一条被剥壳剥掉了，这一行就是新条的开头：把竖线剃掉另起一条
            s = s.lstrip("|｜ ")
        if "｜" in s or "|" in s:
            parts = [p for p in (_clean(x) for x in re.split(r"[|｜]", s)) if p]
            # ★ 它爱在能力段尾巴上挂个冒号（「能改一行代码：｜…」）——段内冒号一律剃掉
            parts = [re.sub(r"[：:]\s*$", "", p) for p in parts]
            if len(parts) >= 2:
                out.append("｜".join(parts[:3]))
                continue
        # ★★ 没有竖线的行是两种：上一条被它拆断的后半截（实测它把
        #   「能力／限制／成长」写成三行，竖线全忘了），或者一句开场白。
        #   前者拿竖线并回去（并完就是条完整的），后者丢掉。
        if re.match(r"^(以下|这是|根据|好的|当然|方案|候选|说明|注意|提示|"
                    r"下面是|为您|为您设计)", s):
            continue
        # ★ 带「第N条」前缀的＝新的一条，哪怕这一行没有竖线也不许并回上一条
        if new:
            if len(s) >= 6:
                out.append(s)
            continue
        if out and len(s) >= 4:
            out[-1] = out[-1] + "｜" + s
            continue
        if len(s) >= 6:
            out.append(s)
    return out[:8]


def gen_cheat(book: dict, avoid=None, use_corpus=True, _retry=0, _feedback="",
              axes=None):
    """金手指：AI 出候选（能力｜限制｜成长），程序先查一遍，人选、人改。

    ★ axes＝能力轴。换一批时把上一批的轴传回来避开，否则六条还是那几个方向。
    """
    ctx, picked = _corpus(book, "cheat", use_corpus)
    sys_p = system_text(book, ctx)
    axes = axes or _cheat_axes(6)
    usr_p = CHEAT_USER + _axis_block(axes) + _avoid(avoid)
    if _feedback:
        usr_p += ("\n★ 上一批被判废的原因，这一批一条都不许再犯：\n" + _feedback)
    try:
        raw = llm.complete(sys_p, usr_p, temperature=1.0, max_tokens=4096)
    except Exception as e:
        return {"ok": False, "err": str(e), "prompt": sys_p + "\n\n" + usr_p}
    # ★★ 它会把能力轴那段说明**整段抄出来**（实测 6 条轴原样端上前，一条真的都没写）。
    #   ⇒ 抄了两条以上这一批就是废的，带 feedback 重出一批——不重出的话解析出来
    #     的全是「只能在夜晚使用，一天一次」这种残段，界面直接空一片。
    flat = re.sub(r"\s", "", raw or "")
    echo = sum(1 for _k, d in axes
               if len(re.sub(r"\s", "", d)) >= 10
               and re.sub(r"\s", "", d) in flat)
    if echo >= 2 and _retry < 2:
        return gen_cheat(book, avoid, use_corpus, _retry + 1,
                         "你把能力轴的说明原样抄了 %d 条——那是方向不是答案，"
                         "照着它写谁、在什么场合、对谁用。" % echo, axes=axes)
    items = [{"text": t, "bad": forge.check_cheat(t)}
             for t in parse_cheats(raw)]
    # ★★ 它把能力轴那句说明原样端出来当过能力段（实测「藏：把一枚硬币藏进…」）。
    #   解析那关只挡得住带冒号的整行，挡不住它把说明揉进能力段——这里再按重合度拦一次。
    for x in items:
        seg = re.split(r"[|｜]", x["text"])[0]
        for k, d in axes:
            if len(_common_sub(re.sub(r"\s", "", seg),
                               re.sub(r"\s", "", d))) >= 8:
                x["bad"] = list(x["bad"]) + [
                    "能力段照抄了「%s」那条轴的说明——换成这本书里的人、场合、对象" % k]
                break
    if not items:
        # ★ 一条都没解析出来（它把指引抄一遍就收，或者只吐了两行）——
        #   以前这里直接 FAIL，界面空一片。改成带 feedback 再要一批。
        if _retry < 2:
            return gen_cheat(book, avoid, use_corpus, _retry + 1,
                             "你一条都没写出来（或者只抄了指引）。按「能力｜限制｜成长」"
                             "写 6 条，每条一行，直接开始写。", axes=axes)
        return {"ok": False, "err": "解析不出条目", "raw": raw}
    # ★ 合格的太少就再出一批，把判废原因原样甩给它（比空口说"写好点"管用）。
    #   他点名骂过「限制写成门槛，第一章根本用不了」，这种一条都不该摆在最前面。
    if _retry < 1 and len([x for x in items if not x["bad"]]) < 3:
        why = "\n".join("- " + "；".join(x["bad"]) for x in items if x["bad"])
        sub = gen_cheat(book, (avoid or []) + [x["text"] for x in items],
                        use_corpus, _retry=1, _feedback=why, axes=axes)
        if sub.get("ok"):
            have = {x["text"] for x in items}
            items += [y for y in sub["items"] if y["text"] not in have]
        # ★ 两批撞出重样的很常见，去重后再排（同一行摆两遍＝功能坏了）
        seen, uniq = set(), []
        for x in items:
            k = re.sub(r"\s", "", x["text"])
            if k and k not in seen:
                seen.add(k)
                uniq.append(x)
        items = uniq
    # ★★ 他点名：「空的生成出来干嘛」。三段不全的、空段的，一条都不摆出来。
    def _full(x):
        return (len([p for p in re.split(r"[|｜]", x["text"]) if p.strip()]) >= 3
                and len(re.sub(r"\s", "", x["text"])) >= 12)
    good = [x for x in items if _full(x)]
    keep = {id(x) for x in good}
    # ★ 全被过滤掉＝界面空一片，比给残次品更糟。合格的不够就把两段的也捞回来
    #   （只捞不带空段的），并且**如实标上缺哪一段**。
    if len([x for x in good if not x["bad"]]) < 3:
        for x in items:
            if id(x) in keep:
                continue
            if len(re.sub(r"\s", "", x["text"])) < 12:
                continue
            segs = [p for p in re.split(r"[|｜]", x["text"]) if p.strip()]
            if len(segs) < 3:
                x["bad"] = list(x["bad"]) + ["「能力｜限制｜成长」三段缺了一段，补齐"]
            good.append(x)
    # ★ 还是一条都没有（模型整批没按格式写）——宁可摆原样让他自己改，也不给空面板
    items = good or items
    # 合格在前，不合格的靠后并带上原因（不是直接丢掉，他要能看见模型错在哪）
    items.sort(key=lambda x: 0 if not x["bad"] else 1)
    # ★ 他点名：一次显示 5 条就够了。
    for x in items[:6]:
        # ★ 轴标签必须**按内容认**，不能按下标硬贴——模型十次有八次不按给的顺序写，
        #   硬贴就出现「[记] 能对调伤痛」这种标签和内容对不上的丢人场面。
        x["axis"] = _match_axis(x["text"], axes)
    return {"ok": True, "items": items[:5], "picked": picked,
            "axes": [a[0] for a in axes],
            "warn": _unsafe([x.get("text", "") for x in items])}


def _gaps(part, frm, to):
    """这一批里缺了哪几章号。"""
    have = set()
    for r in (part or []):
        try:
            have.add(int(r.get("n")))
        except (TypeError, ValueError):
            # 静默是故意的：模型偶尔吐非数字章号，那一行当没给，
            # 由后面的补章逻辑再要一遍
            pass
    return [i for i in range(frm, to + 1) if i not in have]


def _runs(nums):
    """[1,2,3, 7,8] → [(1,3),(7,8)]：把缺的章号聚成连续段，一次补一段。"""
    out = []
    for i in (nums or []):
        if out and i == out[-1][1] + 1:
            out[-1][1] = i
        else:
            out.append([i, i])
    return [(a, b) for a, b in out]


def _outline_part(book, frm, to, tone, sys_p, bmap, prev=""):
    """一批（≤8 章）章纲。★ 细纲行 + 上一批结尾 + 一致性锚，三样一起塞进去。"""
    n = to - frm + 1
    usr_p = (OUTLINE_USER.format(frm=frm, to=to, n=n,
                                 noname=_noname(_prot_name(book)))
             + _book_block(book) + _consist_block(book))
    bs = [(i, bmap[i]) for i in range(frm, to + 1) if bmap.get(i)]
    if bs:
        usr_p += ("\n★★ 这几章的细纲已经定了，你只能把它**扩写**成四个字段，"
                  "不许改细纲的意思、不许跳过任何一章：\n")
        usr_p += "".join("  第%d章｜%s\n" % (i, s) for i, s in bs)
    if prev:
        usr_p += ("\n★ 上一批最后一章停在这里，第 %d 章开头必须接住它：%s\n"
                  % (frm, prev))
    if tone:
        usr_p += "\n额外要求：" + tone
    try:
        raw = llm.complete(sys_p, usr_p, temperature=0.8, max_tokens=4096)
    except Exception:
        return []
    return [r for r in parse_rows(raw) if frm <= int(r.get("n") or 0) <= to]


def gen_skeleton(book: dict, title: str = "", intro: str = "",
                 use_corpus=True):
    """全书大纲（简介→大纲）。5 段，段末各一刀。"""
    ctx, picked = _corpus(book, "outline", use_corpus)
    sys_p = system_text(book, ctx)
    usr_p = (SKELETON_USER + _book_block(book) + _consist_block(book)
             + _title_block(title or book.get("title", "")))
    if intro:
        usr_p += "\n★ 简介（大纲必须长在它身上，不许另起一个故事）：\n" + intro[:600]
    def _sk_parse(txt):
        out = []
        for line in (txt or "").splitlines():
            s = _clean(line)
            s = re.sub(r"^\s*\d+[.、)．]\s*", "", s)
            s = re.sub(r"^[\s\"'“”「」『』【】\[\]>*\-]+", "", s)
            if "｜" not in s and "|" not in s:
                continue
            p = [x.strip() for x in re.split(r"[｜|]", s) if x.strip()]
            if len(p) < 3:
                continue
            out.append({"name": p[0], "span": p[1] if len(p) > 1 else "",
                        "what": p[2] if len(p) > 2 else "",
                        "knife": p[3] if len(p) > 3 else "",
                        "text": "｜".join(p[:4])})
        return out

    # ★ 思考型模型（deepseek-flash 等）会把额度先花在思考上，
    #   2048 时正文常被截断，竖线格式就凑不齐——看起来是"随机失败"。
    #   加额度，并在解析不出时用更明确的格式要求再要一次。
    raw, items = "", []
    for _try in range(2):
        _u = usr_p if _try == 0 else (
            usr_p + "\n\n★ 上一版格式不对。这次每一段必须写成整整一行，"
                    "用全角竖线 ｜ 分成四截：段名｜章节范围｜这一段发生什么｜段末那一刀。"
                    "一行都不要少，也不要换成表格或分行。")
        try:
            raw = llm.complete(sys_p, _u, temperature=0.8, max_tokens=4096)
        except Exception as e:
            return {"ok": False, "err": str(e), "prompt": sys_p + "\n\n" + usr_p}
        items = _sk_parse(raw)
        if items:
            break
    if not items:
        return {"ok": False,
                "err": "解析不出大纲。模型原文开头：%s"
                       % re.sub(r"\s+", " ", (raw or ""))[:150],
                "raw": raw}
    return {"ok": True, "items": items[:5], "picked": picked,
            "warn": _unsafe([x["text"] for x in items])}


def _span_of(s: str):
    """从「1–30」「31~100」「401 以后」里抠出章区间。"""
    m = re.search(r"(\d+)\s*[–—\-~至到]\s*(\d*)", s or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2) or 10 ** 6)


def _skel_segs(skel, frm, to):
    """把大纲的 5 段和 [frm,to] 求交，得到要写细纲的几段。"""
    out = []
    for s in (skel or []):
        sp = _span_of(s.get("span", ""))
        if not sp:
            continue
        a, b = max(sp[0], frm), min(sp[1], to)
        if a <= b:
            out.append((a, b, s.get("name", ""),
                        (s.get("what", "") + "｜" + s.get("knife", ""))))
    if not out:
        out = [(frm, to, "", "")]
    return out


def _beats_part(book, frm, to, stage, line, sys_p, prev=""):
    """一段细纲（≤20 章），逐章一句话。"""
    n = to - frm + 1
    sex = _sex_of(book) or "（未指定，按主角设定写）"
    usr_p = (BEAT_USER.format(frm=frm, to=to, n=n, stage=stage or "这一段",
                              line=line or "（按本书主线写）", sex=sex,
                              genre=book.get("genre") or "—",
                              prev=("★ 上一批最后一章是：%s，第 %d 章要接住它。\n"
                                    % (prev, frm)) if prev else "")
             + _book_block(book) + _consist_block(book, stage))
    try:
        raw = llm.complete(sys_p, usr_p, temperature=0.8, max_tokens=4096)
    except Exception:
        return []
    rows, i = [], frm
    for ln in (raw or "").splitlines():
        s = _clean(ln)
        s = re.sub(r"^\s*第?\s*(\d+)\s*章?\s*[｜|.、)．:：]\s*", "", s)
        s = re.sub(r"^[\s\"'“”「」『』【】\[\]>*\-]+", "", s)
        if "｜" in s or "|" in s:
            s = re.split(r"[｜|]", s, 1)[1]
        s = s.strip()
        if not s or len(s) < 6:
            continue
        rows.append({"n": i, "beat": s})
        i += 1
        if i > to:
            break
    return rows


def gen_beats(book: dict, skel=None, frm: int = 1, to: int = 30,
              use_corpus=True, title: str = "", intro: str = ""):
    """细纲（大纲→细纲）：按大纲的段切分，每段逐章一句话。"""
    if to < frm:
        frm, to = to, frm
    if to - frm > 200:
        to = frm + 200
    ctx, picked = _corpus(book, "outline", use_corpus)
    sys_p = system_text(book, ctx)
    out, prev = [], ""
    for a, b, stage, line in _skel_segs(skel, frm, to):
        for x, y in _chunks(a, b, 20):
            got = _beats_part(book, x, y, stage, line, sys_p, prev)
            if got:
                out += got
                prev = got[-1].get("beat", "")
    if not out:
        return {"ok": False, "err": "细纲没生成出来（模型没按「章号｜一句话」输出）"}
    out.sort(key=lambda r: r["n"])
    return {"ok": True, "rows": out, "picked": picked,
            "warn": _unsafe([r["beat"] for r in out])}


def _common_sub(a: str, b: str) -> str:
    """a、b 的**最长公共子串**（不是子序列——「屏幕亮起」必须连续撞上才算）。

    ★ 用途：抓章节名的「半重名」。完全重名好办，麻烦的是这种共用一个词儿的：
      目录里三处「屏幕亮起」，读者一眼就看出这书在原地打转。
    """
    a, b = a or "", b or ""
    best = ""
    for i in range(len(a)):
        for j in range(len(b)):
            k = 0
            while (i + k < len(a) and j + k < len(b)
                   and a[i + k] == b[j + k]):
                k += 1
            if k > len(best):
                best = a[i:i + k]
    return best


def _chunks(frm: int, to: int, size: int):
    """把 [frm,to] 切成一段一段。★ 一次让它写 30 章，写到后面就换人换题材。"""
    a = frm
    while a <= to:
        yield a, min(a + size - 1, to)
        a += size


def _sex_of(book: dict) -> str:
    p = (book.get("protagonist") or "") + (book.get("brief") or "") + \
        (book.get("romance") or "")
    if re.search(r"她|女帝|女主|嫡女|王妃|皇后|姑娘|庶女|千金", p):
        return "女"
    if re.search(r"他|男主|少爷|世子|小子|公子", p):
        return "男"
    return ""


def _consist_block(book: dict, stage: str = "") -> str:
    """★★ 一致性锚：每一批都重新钉一遍，否则写到第 20 章人就换了。

    他点名骂过：「前面还是男主，后面就变成女的了；前面还是修仙，后面变成生日密码」。
    根因是长序列生成——模型写飞了。所以不靠一次说清，靠**每批重复钉一次**。
    """
    sex = _sex_of(book)
    lines = [
        "★★★ 这本书已经定死的东西，从第一个字到最后一个字都不许变：",
        "  · 题材：%s" % (book.get("genre") or "—"),
        "  · 主角：%s" % (book.get("protagonist") or "—"),
    ]
    if sex:
        same = "他" if sex == "男" else "她"
        other = "她" if sex == "男" else "他"
        lines.append("  · ★ 主角是%s性：全文只能写「%s」，出现「%s」指主角就是废稿"
                     % (sex, same, other))
    lines += [
        "  · ★ 不许换人、换性别、换题材、换世界观——写着写着变了个人＝整批作废。",
        "  · ★ 不许把别的书的东西带进来：本书没有的设定、物件、职业、时代一律不许出现",
        "    （修仙书里不许冒出密码、生日、办公室；都市书里不许冒出灵根、宗门、法器）。",
    ]
    if stage:
        lines.append("  · 现在写的是「%s」这一段，只写这一段的事，不许跳到后面去。" % stage)
    return "\n".join(lines) + "\n"


def gen_outline(book: dict, frm: int, to: int, tone: str = "", use_corpus=True,
                beats=None, skel=None):
    """章纲。★ 分批生成（每批 8 章），每批重新钉一次设定，并接住上一批的结尾。

    beats＝细纲（[{n, beat}]），有细纲时章纲只能照细纲扩写，不许另起一套。
    """
    if to < frm or to - frm > 60:
        return {"ok": False, "err": "章范围不对（止章不能比起章小，一次最多 60 章）"}
    ctx, picked = _corpus(book, "outline", use_corpus)
    sys_p = system_text(book, ctx)
    bmap = {}
    for b in (beats or []):
        try:
            bmap[int(b.get("n"))] = (b.get("beat") or "").strip()
        except (TypeError, ValueError):
            # 静默是故意的：细纲里章号不是数字的行＝坏行，跳过；
            # 少一条细纲不会让章纲停，只是那一章少了依据
            pass
    rows, prev, err = [], "", ""
    for a, b in _chunks(frm, to, 8):
        part = _outline_part(book, a, b, tone, sys_p, bmap, prev)
        # ★★ 他点名：「生成章纲那里为什么还差两章」。
        #   模型一次要它写 8 章，它经常只给 6 行——少写的那两章就这么没了。
        #   ⇒ 数一下缺哪几章，把缺的**单独再要一遍**（一次只补一两章，它就给得齐）。
        for _ in range(2):
            miss = _gaps(part, a, b)
            if not miss:
                break
            for c, d in _runs(miss):
                sub = _outline_part(book, c, d, tone, sys_p, bmap, prev)
                if sub:
                    part += [r for r in sub
                             if c <= int(r.get("n") or 0) <= d]
        if not part:
            err = err or ("第 %d–%d 章没生成出来。模型原文开头：%s"
                          % (a, b, re.sub(r"\s+", " ", (raw or ""))[:150]))
            continue
        rows += part
        part.sort(key=lambda r: r.get("n", 0))
        prev = part[-1].get("hook", "")
    if not rows:
        return {"ok": False, "err": err or "解析不出章表（模型没按「｜」格式输出）"}
    rows.sort(key=lambda r: r.get("n", 0))
    # 章节名不合公式的当场标出来（他点名：章节名质量很低）
    nm = _prot_names(book)
    seen_t = []
    for r in rows:
        # ★ src＝本章要干什么＋章末钩子：标题跟它连撞三段＝把内容复述了一遍
        r["tbad"] = forge.check_chap(
            r.get("title", ""), "%s %s" % (r.get("task", ""), r.get("hook", "")),
            nm)
        # ★★ 他点名：「两章的名字都是一样的」。完全重名已经拦了，
        #   但「屏幕亮起权限请求」和「权限条目骤变，屏幕亮起」这种**半重**的更阴——
        #   目录一眼扫过去三处一样的字眼，读者以为这书在原地打转。
        #   ⇒ 两条标题共用一个 4 字以上的串，就把后面那条标出来重写。
        t = re.sub(r"\s", "", r.get("title", ""))
        for n0, t0 in seen_t:
            c = _common_sub(t, t0)
            if len(c) >= 4:
                r["tbad"] = list(r["tbad"]) + [
                    "和第 %s 章的「%s」重了一半（都用了「%s」）——换个写法"
                    % (n0, t0, c)]
                break
        else:
            seen_t.append((r.get("n"), t))
    # ★ 复读检测：「权限」这种 2 字实词在六条里反复出现，两两共用不足 4 字，
    #   上面那道拦不住——单独再过一遍，命中的标出来交给后面的单条抢救。
    _mark_repeat(rows)
    # ★ 章节名七言八股是它的老毛病。★ 只要有一条红字就整批重写一遍
    #   （不是红一半才重写——留一条八股在目录里，读者一眼就看见了），
    #   重写那关里还会对剩下的红字逐条单独救。
    nbad = sum(1 for r in rows if r["tbad"])
    if rows and nbad:
        fix = gen_chap_titles(book, rows, use_corpus)
        if fix.get("ok") and fix.get("rows"):
            # 只换章节名有改善的行，内容（task/hook）一律不动
            for i, r in enumerate(rows):
                s = fix["rows"][i] if i < len(fix["rows"]) else None
                if s and s.get("title") and s["title"] != r["title"] \
                        and forge.chap_cmp(s["tbad"]) <= forge.chap_cmp(r["tbad"]):
                    r["title"] = s["title"]
                    r["tbad"] = s["tbad"]
    return {"ok": True, "rows": rows, "miss": to - frm + 1 - len(rows),
            "picked": picked,
            "warn": _unsafe(["%s %s %s" % (r.get("title", ""), r.get("task", ""),
                                           r.get("hook", "")) for r in rows])}


# ---------------------------------------------------------------------------
# 一键乱写：第一步全部随机（题材/频道/字数/标签/想写什么/主角/感情线）
# ---------------------------------------------------------------------------

GENRES = {
    "男频": ["玄幻", "仙侠", "都市", "都市脑洞", "科幻", "历史", "游戏", "悬疑", "军事"],
    "女频": ["古言", "现言", "幻言", "宅斗", "宫斗", "婚恋", "种田"],
}
WORDS = [600000, 800000, 1000000, 1200000, 1500000, 2000000, 3000000]
READ_TAGS = ["爽文", "脑洞", "轻松", "热血", "悬疑", "重生", "无敌流", "慢热",
             "群像", "硬核", "甜宠", "虐恋", "复仇", "种田", "马甲", "日常"]
CONTENT_TAGS = ["打脸", "升级", "复仇", "逆袭", "鉴宝", "医术", "黑客", "直播",
                "重生", "契约", "掉马", "读心", "预知", "炼丹", "签到", "卡牌",
                "副本", "宫斗", "宅斗", "追妻", "破案", "逃亡", "开店", "种田"]

# ★ 感情线：给方法，不给现成情境（题库存多少条都会重样）
ROMANCE_HOW = (
    "感情：不要套现成情境，按这三步现造一句。\n"
    "  一、两个人各自的算盘：她想要什么、他想要什么，这两件事必须对不上；\n"
    "  二、一件具体的事把它捅破：一个动作、一样东西、或一个确切的数字，"
    "不是形容词；\n"
    "  三、最后一句落到她判断的转变上，而他自己还不知道她变了。\n"
    "  句式禁止：谁护着谁、他到最后才明白、默默地、一直守护。\n"
)

ROMANCE_MALE_NOTE = (
    "  说明：番茄男频的 19 个分类——西方奇幻／东方仙侠／都市修真／都市高武／"
    "历史古代／战神赘婿／悬疑脑洞／抗战谍战／游戏体育——没有一个属于感情类。\n"
    "  读者来这里主要是看打斗、升级、赚钱、探险，感情线是调味不是主食，\n"
    "  写得少不要紧，写多了反而拖节奏。但一段好的感情可以当打脸的道具用，\n"
    "  所以最好是能当众发生、能被别人看见的那种。\n"
)

ROMANCE_FEMALE_NOTE = (
    "  说明：番茄女频 18 个分类里有 5 个直接就是感情类——玄幻言情／青春甜宠／"
    "职场婚恋／豪门总裁／民国言情，剩下的宫斗宅斗、种田、年代也大多靠关系推进。\n"
    "  读者要的就是情感本身，而且比男频细得多：一个停顿、一次没说出口的话、\n"
    "  一个没递出去的东西，她们都看得见。男性读者更理性，要的是结果和爽点；\n"
    "  女性读者更感性，要的是过程中那些没说破的东西。所以这一段不能一句带过，\n"
    "  要写出层次。\n"
)

ROMANCE_MALE_OPTIONAL = ""


def _romance_how(mf: str) -> str:
    if (mf or "").startswith("女"):
        return ROMANCE_HOW + ROMANCE_FEMALE_NOTE
    return ROMANCE_HOW + ROMANCE_MALE_NOTE + ROMANCE_MALE_OPTIONAL


ROLL_USER = (
    "随便给我一个能写一百多万字的{genre}（{mf}）故事胚子。\n"
    "★ 严格照下面三行输出，行首必须是我给的这三个词，不要加粗、不要编号、"
    "不要多写任何别的行：\n"
    "想写：主角是谁、他身上发生了什么变化、他要往哪儿去。一句话。\n"
    "主角：姓名／年纪／处境／他最在乎什么／他缺什么。\n"
    "★ 主角性别按频道定，这是读者代入问题，不是偏好问题：\n"
    "  女频的读者是女性，她要代入的是女主——所以女频主角必须是女性；写成男的，她第一句就读不进去，直接划走。\n"
    "  男频的读者是男性，主角通常是男性。\n"
    "  姓名要像那个性别：女频不要用男性名，反之亦然。\n"
    "感情：{rom}\n"
    "★ 不要写成大纲，不要写世界观设定，就这三行。"
)


_ROLL_KEY = r"^\s*(想写|想写的|故事|主角|主人公|感情|感情线)\s*[:：]\s*(.+)$"
_ROLL_MAP = {"想写": "brief", "想写的": "brief", "故事": "brief",
             "主角": "protagonist", "主人公": "protagonist",
             "感情": "romance", "感情线": "romance"}


def _roll_parse(raw: str) -> dict:
    """模型常常不守格式（加粗、换词、只输出主角那行），所以要两级：先认标签，再猜。"""
    got = {}
    lines = [re.sub(r"^[\s*#>`\-•]+", "", _clean(l))
             for l in (raw or "").splitlines()]
    rest = []
    for s in lines:
        if not s:
            continue
        m = re.match(_ROLL_KEY, s)
        if m:
            got.setdefault(_ROLL_MAP[m.group(1)], m.group(2).strip())
        else:
            rest.append(s)
    # 兜底：按内容猜（模型经常只肯写主角那一行）
    if "protagonist" not in got:
        c = [x for x in rest if re.search(r"姓名|主角|岁|身份|职业|／", x)]
        if c:
            got["protagonist"] = max(c, key=len)
            rest.remove(got["protagonist"])
    if "romance" not in got:
        c = [x for x in rest if re.search(r"感情|爱情|喜欢|护着|暗恋|婚", x)]
        if c:
            got["romance"] = max(c, key=len)
            rest.remove(got["romance"])
    if "brief" not in got and rest:
        got["brief"] = max(rest, key=len)
    return got


CHAP_USER = (
    "这是一本书的章纲，章节名写得太直白（会被标红），全部重写。\n"
    "只输出新的章节名，一行一个，按下面的顺序，不要章号、不要引号、不要解释，"
    "一共 {n} 行。\n"
    + CHAP_RULE +
    "★ 必须照着每章「这一步干什么」和「章末钩子」写，不许脱离内容自己编；\n"
    "  但**不许用它们的原词**——要用另外一件只露一角的细节、一句话、一个数"
    "去勾同一件事。\n"
    "★ 可以用他／她／它，但只能指别人，不能指主角（主角写「我」或写身份）。\n"
    "★ 禁止这些词（写了＝像概括不像钩子）：开始、出现、决定、回忆、相遇、介绍、\n"
    "  铺垫、交代、日常、准备、出发、到达、结束、总结、重新、终于、觉醒、降临、\n"
    "  逆转、危机、转机、真相、秘密、往事、突破、升级、试炼、开局、机会、选择、\n"
    "  布局、博弈、交锋、对峙、提升、认可、测试。\n"
    "  ★★ 尤其不许套「××（名词）＋的＋××」这种概括结构\n"
    "  （不该响的铃铛 可以；铃铛的秘密 不行——后者是给这一章起类目名）。\n"
    "\n{rows}"
)


def gen_chap_titles(book: dict, rows, use_corpus=True, _retry=0, _feedback=""):
    """只重写章节名。章纲内容不动——章节名是钩子，值得单独出一遍。

    ★ 7B 光说"8–18 字"不听，实测十次九次全给四字概括。所以两道：
      第一轮出来后标红超过三分之一，就把**每条的判废原因**逐条写回去重写一轮
      （跟简介续写同一个道理：给它看具体的错，比给它讲要求管用）。
    """
    rows = [r for r in (rows or []) if r.get("n")]
    if not rows:
        return {"ok": False, "err": "还没有章纲"}
    ctx, picked = _corpus(book, "outline", use_corpus)
    sys_p = system_text(book, ctx)
    nm = _prot_names(book)
    body = "\n".join("%s｜%s｜%s" % (r.get("n"), r.get("task", ""), r.get("hook", ""))
                     for r in rows)
    usr_p = (CHAP_USER.format(n=len(rows), rows=body,
                              noname=_noname(_prot_name(book)))
                + _book_block(book))
    if _feedback:
        usr_p += ("\n★★ 上一批的章节名被程序判废了，判废原因如下，照着一条条改：\n"
                  + _feedback + "\n")
    try:
        raw = llm.complete(sys_p, usr_p, temperature=1.0, max_tokens=4096)
    except Exception as e:
        return {"ok": False, "err": str(e), "prompt": sys_p + "\n\n" + usr_p}
    # ★ 模型常常把输入的行格式照抄回来：「1｜新的章节名」。剥掉，别让章号进书名位。
    names = [re.sub(r"^\s*\d+\s*[.、)．｜|]\s*", "", _clean(x))
             for x in raw.splitlines()]
    names = [re.sub(r"^[\s\"'“”「」『』【】\[\]>*\-]+", "", x).strip()
             for x in names]
    names = [re.sub(r"\s*[\d]+\s*$", "", x).strip() for x in names]
    # ★ 上限要跟章节名规则对齐（8–18 字），写死 12 会把 13–18 字的合格标题全丢掉
    names = [x for x in names if 1 <= len(x) <= 20]
    if len(names) < len(rows) // 2:
        return {"ok": False, "err": "模型没按行输出章节名", "raw": raw}

    def _apply(ns):
        out = []
        for i, r in enumerate(rows):
            # ★ 模型偶尔把输入的行格式照抄回来（「1｜新的章节名｜…」），竖线要切掉
            t = (ns[i] if i < len(ns) else r.get("title", "") or "")
            t = re.split(r"[|｜]", t)[0].strip()
            r = dict(r)
            r["title"] = t
            # ★ src＝本章要干什么＋章末钩子：标题跟它连撞三段＝复述，判红
            r["tbad"] = forge.check_chap(
                t, "%s %s" % (r.get("task", ""), r.get("hook", "")), nm)
            out.append(r)
        # ★ 复读检测（「权限」×6）：单条查不出来，要整批一起看
        _mark_repeat(out)
        return out

    out = _apply(names)
    nbad = sum(1 for r in out if r["tbad"])
    if nbad > len(rows) // 3 and not _retry:
        fb = "\n".join("  第%d章「%s」——%s" % (r["n"], r["title"], "；".join(r["tbad"]))
                       for r in out if r["tbad"])
        sub = gen_chap_titles(book, rows, use_corpus, _retry=1, _feedback=fb)
        if sub.get("ok"):
            for i, r in enumerate(out):
                s = sub["rows"][i] if i < len(sub["rows"]) else None
                # 两轮里挑**硬伤**少的那条；硬伤一样但换了字也换过去
                # （旧的要是抄了示例，留着就是丢人）
                if s and s.get("title") and s["title"] != r["title"] \
                        and forge.chap_cmp(s["tbad"]) <= forge.chap_cmp(r["tbad"]):
                    out[i] = s
    # ★★ 最后剩下的那几条，一条一条单独救：单次只求一个，给它看判废原因，
    #    命中率远高于整批重写（他就是嫌不合格的还摆在那儿）。
    _patch_chaps(book, out, rows, nm)
    # ★ 最后一关：两章不许同名（他点名：目录里两章一个名字，读者以为点错了）
    _dedup_chap_names(book, out, rows, nm)
    return {"ok": True, "rows": out, "picked": picked,
            "warn": _unsafe([r.get("title", "") for r in out])}


def _mark_repeat(out):
    """★ 一个实词在三条以上的章节名里反复出现＝目录在复读（实测一批里「权限」占了六条）。

    两两之间只共用 2 字，「共用 4 字以上」那道拦不住，可目录一眼扫过去就是坏掉的
    复读机。⇒ 按 2 字片段跨批统计，3 条以上撞同一个实词，那几条全标出来交给
    单条抢救去改。两个字都是虚字的（「他的」「在于」）不算——那不叫复读。
    """
    titles = [re.sub(r"\s", "", r.get("title") or "") for r in out]
    if sum(1 for t in titles if len(t) >= 2) < 3:
        return
    cnt, per = {}, []
    for t in titles:
        bs = {t[i:i + 2] for i in range(len(t) - 1)
              if not (t[i] in _FUNC and t[i + 1] in _FUNC)}
        per.append(bs)
        for b in bs:
            cnt[b] = cnt.get(b, 0) + 1
    rep = {b for b, c in cnt.items() if c >= 3}
    if not rep:
        return
    for r, t, bs in zip(out, titles, per):
        if not (bs & rep):
            continue
        # 把重叠的重复片段拼回长词（屏幕／幕亮／亮起 → 屏幕亮起）
        best, i = "", 0
        while i < len(t) - 1:
            if t[i:i + 2] in rep:
                j = i + 2
                while j < len(t) and t[j - 1:j + 1] in rep:
                    j += 1
                if j - i > len(best):
                    best = t[i:j]
                i = j
            else:
                i += 1
        if best:
            r["tbad"] = list(r.get("tbad") or []) + [
                "「%s」这个词一批里出现了 %d 次——目录像复读机，换一个说法"
                % (best, cnt.get(best[:2], cnt.get(best, 3)))]


def _dedup_chap_names(book, out, rows, nm=""):
    """★ 两章同名＝目录废了（他点名过）。重名的那条单独重写，带上「跟第几章撞了」。"""
    seen = {}
    for i, r in enumerate(out):
        t = re.sub(r"\s", "", r.get("title") or "")
        if not t:
            continue
        if t in seen:
            why = "和第 %s 章重名了——两章一个名字，读者以为点错了" % seen[t]
            src = "%s %s" % (rows[i].get("task", ""), rows[i].get("hook", ""))
            # ★ 重名本身就是不合格（tbad 是空的），所以不能拿「比原来判罚少」当条件
            #   ——那样一个候选都换不上（我第一版就写错在这）。只要不重名就换。
            best = None
            for got in _rewrite_one_chap(book, rows[i], r["title"], why, nm):
                g = re.sub(r"\s", "", got or "")
                if not g or g in seen:
                    continue
                bad = forge.check_chap(got, src, nm)
                if best is None or len(bad) < len(best[1]):
                    best = (got, bad)
                if not bad:
                    break
            if best:
                r["title"], r["tbad"] = best
            t = re.sub(r"\s", "", r["title"])
        seen.setdefault(t, r.get("n"))


def _patch_chaps(book, out, rows, nm="", limit=10, rounds=2):
    """给还挂着红字的章节名单点重写。每行一次调用，程序从候选里挑最合格的。

    ★ 改之前：它给了 3 个候选，我却只用第一条——命中率低得离谱，目录里一片红字
      （「给汤下药」「危机降临」就是这么留下来的）。程序挑，跟书名一个道理：
      它自己会把最套路那条排第一个。
    ★ rounds=2：一轮没救回来再救一轮（一条一次调用，几秒的事，目录干净值这个价）。
    """
    for _ in range(rounds):
        todo = [i for i, r in enumerate(out) if r["tbad"]][:limit]
        if not todo:
            break
        for i in todo:
            r = out[i]
            src = "%s %s" % (rows[i].get("task", ""), rows[i].get("hook", ""))
            best = (r["title"], list(r["tbad"]))
            for got in _rewrite_one_chap(book, rows[i], r["title"],
                                         "；".join(r["tbad"]), nm):
                bad = forge.check_chap(got, src, nm)
                if forge.chap_cmp(bad) < forge.chap_cmp(best[1]):
                    best = (got, bad)
                if not bad:
                    break
            r["title"], r["tbad"] = best
        # ★★ 重写后的 tbad 是**单条**检查的结果，看不到批次——复读标记被清掉了，
        #   可新标题照样带着「权限」（实测四轮全这么溜过去的）。每轮结束重标一次，
        #   下一轮接着救；标记还在才继续进 todo。
        _mark_repeat(out)
    # ★ 抢救完还是太短（它顽固地给四字概括）：单独加长一次。
    #   番茄目录里 8–18 字才有钩子，四五个字读者一眼扫过去没感觉。
    for r in out:
        t = re.sub(r"\s", "", r.get("title") or "")
        if r["tbad"] and len(t) < 8:
            src = ""
            for x in (rows or []):
                if x.get("n") == r.get("n"):
                    src = "%s %s" % (x.get("task", ""), x.get("hook", ""))
                    break
            got = _widen_chap(book, r["title"], src, nm)
            if got and len(re.sub(r"\s", "", got)) > len(t):
                bad = forge.check_chap(got, src, nm)
                if forge.chap_cmp(bad) < forge.chap_cmp(r["tbad"]):
                    r["title"], r["tbad"] = got, bad
    # 加长也一样是单条检查，收尾再标一次复读（标了没人救也不显示，但别假装没有）
    _mark_repeat(out)


def _widen_chap(book, t, src="", nm="") -> str:
    """把一条短章节名加长到 8–18 字。加不动就退回原样。"""
    try:
        raw = llm.complete(system_text(book), WIDEN_CHAP_USER.format(
            t=t, src=src or "（未知）"), temperature=1.0, max_tokens=300)
    except Exception:
        return ""
    best, n0 = "", len(re.sub(r"\s", "", t or ""))
    for line in (raw or "").splitlines():
        s = re.sub(r"^\s*\d+\s*[.、)．｜|]\s*", "", _clean(line))
        s = re.sub(r"^[\s\"'“”「」『』【】\[\]>*#\-]+", "", s).strip()
        s = re.split(r"[|｜]", s)[0].strip()
        if not s or t[:2] not in s:
            continue
        if forge.check_chap(s, src, nm):
            continue
        if len(re.sub(r"\s", "", s)) > n0:
            return s
    return best


def _rewrite_one_chap(book, row, old, why, nm="") -> List[str]:
    """单独救一个章节名：给 3 个候选，**全部返回**，由程序挑合格的那个。"""
    usr = (
        "这一本书的第 %s 章，现在的章节名是「%s」。\n"
        "★ 它被判废了，原因：%s\n"
        "★ 这一章写的是：%s\n"
        "★ 章末停在：%s\n"
        "★ 重写章节名：**只给这件事的一个切口，不要给出这一章的答案**。\n"
        "  给一个具体的物件／数字／一句台词就停，不许把这一章发生了什么说完。\n"
        "  8–18 字，不许文绉绉的对仗句，不许抽象名词，不许出现角色的人名。%s\n"
        "★ 给 3 个候选，每条一行，不要编号、不要引号、不要解释。\n"
        % (row.get("n"), old, why, row.get("task", ""), row.get("hook", ""),
           ("特别注意：不许出现「%s」。" % nm) if nm else "")
    )
    try:
        raw = llm.complete(system_text(book, ""), usr, temperature=1.0,
                           max_tokens=300)
    except Exception:
        return []
    outs, seen = [], set()
    for line in (raw or "").splitlines():
        t = re.sub(r"^\s*\d+\s*[.、)．｜|]\s*", "", _clean(line))
        t = re.sub(r"^[\s\"'“”「」『』【】\[\]>*\-]+", "", t).strip()
        if t and t not in seen:
            seen.add(t)
            outs.append(t)
    return outs


_VER_MARK = re.compile(
    r"^\s*[*\s#]*(?:简介|版本|方案)?\s*[第]?\s*([1-9１-９一二三四五六七八九])\s*"
    r"[版篇个．.、)】：:｜|]?\s*[*\s]*$")


def _split_blocks(raw):
    """简介：按「第N版」标记分版；版内空行是**分段**，不是分版。

    ★★ 上一版的逻辑是「空行＝换下一版」，那是建立在"每版写成一整段"上的。
      现在简介改成番茄三段式（一版三段），空行变成了段内分隔，
      再按空行分版就会把三版切碎、或者把三版粘成一版。
      ⇒ 改成分版看「第N版」标记，空行只负责分段。
    ★ 模型不守规矩没写标记时退化成按段落数均分成三版。
    """
    txt = re.sub(r"\r\n?", "\n", raw or "")
    groups, paras, cur, marks, blk = [], [], [], 0, 0
    tag = []          # 每一版属于第几「块」——它爱把整批原样重复输出两三遍

    def endpara():
        if cur:
            p = "".join(cur).strip()
            if p:
                paras.append(p)
            cur.clear()

    def endver():
        endpara()
        if paras:
            groups.append(list(paras))
            tag.append(blk)
        del paras[:]

    for line in txt.split("\n"):
        s = line.strip()
        # 分隔线（--- ／ *** ／ ===）＝换下一批
        if re.fullmatch(r"[-=*·—_·]{3,}", s):
            endver()
            blk += 1
            continue
        t = s.strip("*").strip()
        t = re.sub(r"^[\s\"'“”「」『』【】\[\]>#\-•·]+", "", t)
        # ★★ 它把我提示词里的段名（「中段：」「末段：」「头一截：」）当正文抄了出来
        #    （实测三版里两版带头）——标签剃掉，后面的内容是真内容，留着。
        t = re.sub(r"^(头一截|第一截|中间那一截|中间那截|中段|第二截|"
                   r"最后一截|末段|第三截|开放式)\s*[：:]\s*", "", t)
        # ★ 版标记单独一行（第1版／**第2版**／简介三）→ 换下一版
        m = _VER_MARK.match(t)
        if m and len(t) <= 12:
            if paras or cur:
                endver()
            marks += 1
            continue
        if not t:
            # ★ 空行＝换段（三段式简介里一版三段，靠空行隔开）
            endpara()
            continue
        # ★ 模型常把 prompt 里的分步说明当成正文抄出来——整行丢掉
        if re.match(r"^\s*(第\s*[一二三四五六七八九十\d]+\s*[步点·、]|"
                    r"[一二三四五六七八九十]\s*[、．]\s*"
                    r"(开场|开局|开篇|反差|难处|钩子|简介|要写的|绝对不许))", t):
            continue
        if re.search(r"能拍成画面|不得少于|一步都不得|读者必须|别总结|没回答的问题|"
                     r"告诉读者信息|勾着读者|勾子|说明书|戛然而止|概括句|"
                     r"不许写|不许交代|不许解释|不许复述|不许总结|"
                     r"加分|黄金结构|推荐页|开放式", t):
            continue
        # 短行且没有句读＝小标题，不是正文
        if len(t) <= 14 and not re.search(r"[。，！？；]", t):
            if re.search(r"简介|版|方案|第", t):
                endver()
                marks += 1
            continue
        cur.append(t)
    endver()

    # ★ 退化：版标记没写（7B 十次有八次不写）。
    #   那就按**字数**把段落重新归成版——番茄一版 250–350 字，
    #   攒够 150 字就切一刀，这样切出来的一版正好是三段。
    if marks < 2:
        ps = []
        for g in groups:
            ps += g
        groups = _regroup(ps)
        tag = [0] * len(groups)
    # ★★ 它爱把整批**原样重复输出两三遍**（实测一批吐 9 版，前三版是最短的草稿）。
    #   ⇒ 超过 3 版时只认**最后一块**的（那是它改完的定稿），再去掉跨块重样的。
    if len(groups) > 3:
        keep = max(tag) if tag else 0
        last = [g for g, b in zip(groups, tag) if b == keep]
        head = [g for g, b in zip(groups, tag) if b != keep]
        groups = last + head
    vers = [_force_paras("\n\n".join(_dedup_paras(g)[:3])) for g in groups]
    # ★ 跨版去重：同一版被改写了两三遍，只留最长的那一版（其它是它的草稿）
    uniq, keys = [], []
    for v in vers:
        k = re.sub(r"\s", "", v)
        hit = -1
        for i, k0 in enumerate(keys):
            if len(_lcs(k, k0)) >= 20:
                hit = i
                break
        if hit < 0:
            uniq.append(v)
            keys.append(k)
        elif len(k) > len(keys[hit]):
            uniq[hit], keys[hit] = v, k
    vers = uniq
    out = [v for v in vers if len(re.sub(r"\s", "", v)) >= 60]
    # ★ 一版都没切出来（模型给了连贯一大段）→ 整段当一版
    if not out and vers:
        out = [vers[0]]
    return out[:3]


def _dedup_paras(ps):
    """★ 它经常把刚写完的一段换个说法再写一遍（实测同一段出现两次）。"""
    out = []
    for p in ps or []:
        a = re.sub(r"\s", "", p)
        dup = False
        for q in out:
            b = re.sub(r"\s", "", q)
            if len(_lcs(a, b)) >= 24:      # 24 字以上重合＝抄自己
                dup = True
                break
        if not dup:
            out.append(p)
    return out


def _force_paras(t, n=3):
    """★ 一版必须三截。它经常不分段（一大段糊到底），或者只分两截——
    番茄推荐页只露前 50–80 字，版式是硬要求，不能指望模型自觉。

    ⇒ 段落不够 n 截时，挑**最长的那一截**按句子再切开（切口落在句号上），
      一直切到够数。切不动的（整截就一句）就停，不硬凑。
    """
    t = (t or "").strip()
    if not t:
        return t
    # ★ 它把我提示词里的段名（「中段：」「末段：」）当正文抄了出来——实测三版里两版带头，
    #   而且不一定在行首（「…不再脆弱。末段：他今日的病情」这种也要剃）。
    t = re.sub(r"(^|(?<=[。！？\n]))\s*(头一截|第一截|中间那一截|中间那截|中段|第二截|"
               r"最后一截|末段|第三截|开放式)\s*[：:]\s*", r"\1", t)
    paras = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
    if len(paras) >= n:
        return t
    while len(paras) < n:
        # 挑字数最多、且至少有句子的那一截来切
        best, blen = -1, 0
        for i, p in enumerate(paras):
            # ★ 只按 。！？ 切——把 」』 也当句尾会切出孤零零一个引号
            sents = [s for s in re.split(r"(?<=[。！？])", p) if s.strip()]
            if len(sents) >= 2 and len(re.sub(r"\s", "", p)) > blen:
                best, blen = i, len(re.sub(r"\s", "", p))
        if best < 0:
            break
        p = paras[best]
        sents = [s for s in re.split(r"(?<=[。！？])", p) if s.strip()]
        # ★ 中文的收尾标点在句号**后面**（「……快进府去。”」），按句号切会把 ” 剩成
        #   下一句的头，切完出现一段以一个引号开头。⇒ 句首的收尾标点一律并回上一句；
        #   并完只剩标点的碎块直接丢掉。
        fixed = [sents[0]] if sents else []
        for s in sents[1:]:
            m = re.match(r"^[”’」』）】》〉]+", s)
            if m:
                fixed[-1] += m.group(0)
                s = s[m.end():]
                if not s.strip():
                    continue
            fixed.append(s)
        sents = fixed
        # ★ 切口选在「两半字数最接近」的那个句界上，且必须两半都非空——
        #   旧逻辑是「累计过半就切」，句子一长就切在最后一句上，后半截是空的，白切。
        lens = [len(re.sub(r"\s", "", s)) for s in sents]
        total = sum(lens)
        cut, acc, gap = 1, 0, total
        for k in range(len(sents) - 1):
            acc += lens[k]
            if abs(acc * 2 - total) < gap:
                gap, cut = abs(acc * 2 - total), k + 1
        paras[best:best + 1] = ["".join(sents[:cut]), "".join(sents[cut:])]
    return "\n\n".join(x.strip() for x in paras if x.strip())


def _regroup(ps, n=3):
    """按字数把段落归成 n 个版：攒够 150 字切一刀，多余的并进最后一版。"""
    groups, cur, size = [], [], 0
    for p in ps or []:
        cur.append(p)
        size += len(re.sub(r"\s", "", p))
        if size >= 150 and len(groups) < n - 1:
            groups.append(cur)
            cur, size = [], 0
    if cur:
        if groups:
            groups[-1] = groups[-1] + cur
        else:
            groups.append(cur)
    return groups or [list(ps or [])]


def _fix_intro_parse(raw):
    """把「第N版」＋下面那段原文切出来，**保留编号**。

    ★ 不能直接用 _split_blocks——它会把「第N版」当版标记剥掉，编号丢了就对不回原版。
    """
    out, cur_idx, cur = [], None, []
    for line in (raw or "").split("\n"):
        s = line.strip()
        m = re.match(r"^第\s*([一二三四五六七八九十\d]+)\s*版", s)
        if m:
            if cur_idx is not None and cur:
                out.append((cur_idx, "\n".join(cur).strip()))
            g = m.group(1)
            cur_idx = (int(g) - 1) if g.isdigit() else "一二三四五六七八九十".index(g)
            cur = []
            # 它有时把「｜毛病：…｜原文：」抄在同一行，后面才是正文
            tail = re.sub(r"^.*?原文\s*[：:]\s*", "", s[m.end():])
            if tail:
                cur.append(tail)
            continue
        if cur_idx is not None:
            cur.append(line)
    if cur_idx is not None and cur:
        out.append((cur_idx, "\n".join(cur).strip()))
    return out


def _fix_intro(book, items, title, hook) -> list:
    """简介批改：把带质量问题的几版一次给它，只许改毛病、不许重写。

    ★ 用途：三版都犯同一个毛病（实测三版全写「眼中闪过一丝」——AI 味的套话）。
      整批重出它还是这个味（温度高、它记不住），逐版指着毛病改才管用。
    """
    if not any(x.get("bad") for x in items):
        return items
    # ★ 编号按它在 items 里的位置（不是按毛病条的序号），回来才能对回原版
    body = "\n".join(
        "第%d版｜毛病：%s｜原文：\n%s" % (i + 1, "；".join(x["bad"]), x["text"])
        for i, x in enumerate(items) if x.get("bad"))
    usr = ("下面是这本书几版简介，程序判出了毛病（AI 套话／说明书句子）。\n"
           "★ 每一版**只改判出来的那几句**，其余原样不动——不许重写、不许换个故事、\n"
           "   不许加长或缩短，字数保持原样。\n"
           "★ 输出格式：每版先写「第N版」单独一行，下面紧跟改好的那一版全文"
           "（保留原来的分段，空行留着）。\n"
           "★ 改的原则：把套话换成一个**具体的动作或物件**（「眼中闪过一丝感激」→\n"
           "   他把她的手攥紧了一寸）。简介只写画面，不写评价。\n"
           "★ 不许用仿佛／似乎／宛如，不许解释设定，不许写主角怎么赢。\n"
           "%s\n%s" % (_title_block(title, hook), body))
    try:
        raw = llm.complete(system_text(book), usr, temperature=0.7,
                           max_tokens=3200)
    except Exception:
        return items
    fixed = list(items)
    for idx, g in _fix_intro_parse(raw):
        if not (0 <= idx < len(fixed)):
            continue
        old = fixed[idx]
        g = forge.wash(g)
        # ★ 改完必须还像一版简介（三段、字数没跑飞），否则宁可留原样
        if abs(len(re.sub(r"\s", "", g)) - old["n"]) > max(60, old["n"] // 3):
            continue
        keep = [b for b in old["bad"] if "太像" in b or "抄了钩子" in b]
        fixed[idx] = {"text": _force_paras(g),
                      "n": len(re.sub(r"\s", "", g)), "bad": keep}
    return fixed


def _extend_intro(book, text, title, hook, need) -> str:
    """简介写短了：让它接着最后一句往下补，不许重写、不许另起一个故事。"""
    usr = ("下面是这本书的一版简介，三段没写足，字数不够（要 250–350 字）。\n"
           "★ 接着它的**最后一句**往下续写 %d 字左右。\n"
           "  不要重写前面已经写过的，不要另起一个故事，不要加小标题，\n"
           "  直接输出**续写的那部分**，不要重复最后一句。\n"
           "★ 续写按番茄三段式往下补：如果还停在「身份处境＋冲突」就补\n"
           "  「人设反差＋他手里那张底牌＋他要办的那件事」；已经写到那儿了，\n"
           "  就补最后那个**开放式钩子**（一个具体的指向，不许写「她该如何抉择」）。\n"
           "★ 不许写主角怎么赢，不许解释设定和规则，不许用仿佛／似乎／宛如。\n"
           "%s\n已有部分：\n%s"
           % (max(int(need), 120), _title_block(title, hook), text))
    try:
        # ★ 它一续就收不住（实测让你补 200 字，它写 400 多），
        #   所以按要补的字数卡 max_tokens，让它写不完就断在那儿。
        raw = llm.complete(system_text(book), usr, temperature=1.0,
                           max_tokens=max(200, int(max(int(need), 120) * 2)))
    except Exception:
        return ""
    add = re.sub(r"^(续写[：: ]*|接上文[：: ]*|接着写[：: ]*)", "",
                 _clean(raw)).strip()
    # ★ 它爱把 prompt 里的要求原句抄进正文（「一个更大的麻烦浮出水面」）
    parts = re.split(r"(?<=[。！？])", add)
    add = "".join(p for p in parts
                  if not re.search(r"更大的麻烦浮出|没回答的问题|说不清的事|开放式|"
                                   r"人设反差|底牌|三段式|"
                                   r"不许|不要重写|不要另起|续写|250|350", p)).strip()
    if len(re.sub(r"\s", "", add)) < 60:
        return ""
    # ★ 它常把已有的最后一句重抄一遍再往下写，裁掉重合的那一截
    tail = text[-120:]
    cut = 0
    for k in range(min(60, len(add)), 11, -1):
        if add[:k] in tail:
            cut = k
            break
    if cut:
        add = add[cut:]
    # ★★ 续写必须**接在最后一段尾巴上**，不许另起一段——
    #   番茄三段式一版就三段，拼出一个第四段＝版式又散了（实测它就是会自己空行）。
    add = re.sub(r"\s*\n+\s*", "", add)
    return add if len(re.sub(r"\s", "", add)) >= 60 else ""


def gen_intro(book: dict, avoid=None, use_corpus=True, title="", hook="",
              _retry=0, _short=0):
    """简介：读者在书架页看到的唯一一段字。AI 出 3 版，人选、人改。

    ★ 流程是串起来的：钩子 → 书名 → 简介。简介吃已经定下来的书名和钩子，
      否则三样东西各说各的，最后拼不成一本书。
    ★ 简介不是说明书：写完它不能让读者"已经知道这本书讲什么"，
      那他就不点了。所以金手指的机制、对手是谁、主角怎么赢，一律不许写。
    ★ 三版都写不够 450 字就重出一次，并把字数原样骂回去（7B 对具体的数有反应）。
    """
    ctx, picked = _corpus(book, "intro", use_corpus)
    sys_p = system_text(book, ctx)
    usr_p = INTRO_USER + _book_block(book) + _title_block(title, hook) + _avoid(avoid)
    if _short:
        usr_p += ("\n★★ 上一版每版只有 %d 字，太短了，整批作废。\n"
                  "  这次每版必须写到 250–350 字，一版里的**三截**一截都不能少：\n"
                  "  头一截身份处境＋冲突写足 → 中段人设反差＋底牌＋他要办的事 → "
                  "末段开放式钩子。\n"
                  "  不要一句话带过，把每个动作拆成谁在哪儿、做了什么、对方什么反应。\n"
                  % _short)
    try:
        raw = llm.complete(sys_p, usr_p, temperature=1.0, max_tokens=3200)
    except Exception as e:
        return {"ok": False, "err": str(e), "prompt": sys_p + "\n\n" + usr_p}
    items = _split_blocks(raw)
    if not items:
        return {"ok": False, "err": "解析不出简介", "raw": raw}
    out, prev = [], []
    for t in items[:3]:
        t = forge.wash(t)          # ★ 仿佛／似乎／宛如 直接洗掉，不重写
        n = len(re.sub(r"\s", "", t))
        # ★ 7B 实测写到 300 字上下就收（要 450 它写不动，重出一次也一样）。
        #   所以 300 字以上不判废，只提示差多少。
        bad = [] if 200 <= n <= 400 else ["%d 字，要 250–350 字" % n]
        bad += forge.check_intro(t)
        # ★ 三版吃同一个书名，模型最爱把一段话换词写三遍。用公共片段抓出来。
        for p in prev:
            c = _lcs(re.sub(r"\s", "", t), p)
            if len(c) >= 12:
                bad.append("和上一版太像（都写了「%s…」，要换个开场）" % c[:12])
                break
        prev.append(re.sub(r"\s", "", t))
        # ★ 钩子原话被抄进简介＝白写（那句话是要印在书名旁边的）
        if hook and len(hook) >= 8 and len(_lcs(re.sub(r"\s", "", t),
                                               re.sub(r"\s", "", hook))) >= 8:
            bad.append("抄了钩子的原话，要换成那个动作发生当时的画面")
        out.append({"text": t, "n": n, "bad": bad})
    # ★★ 它实测每版只写 60–90 字（把「一版三截」理解成「一版一截」了）。
    #   先把字数原样骂回去重出一批——**必须赶在补写前面**：
    #   补写会把它顶到 300 字，「全部不到 200 字」这个重出条件就永远不成立了。
    if _retry < 1 and out and all(x["n"] < 200 for x in out):
        sub = gen_intro(book, avoid, use_corpus, title, hook, _retry + 1,
                        max(x["n"] for x in out))
        if sub.get("ok") and sub.get("items"):
            out = sorted(out + sub["items"], key=lambda x: -x["n"])[:3]
    # ★ 7B 写简介总是写到一半就收。别整批重出（重出还是短），
    #   让它**接着自己那版往下补**——续写比重写靠谱，前面写好的也不浪费。
    # ★ 补写对每一批都做（换一批回来的那条同样可能太短）
    for x in out:
        if x["n"] < 300:
            # ★ 让它补 X 字，它实际会写 2X（实测），所以只报一半的量。
            add = _extend_intro(book, x["text"], title, hook,
                                max(min((300 - x["n"]) // 2, 120), 40))
            if not add:
                continue
            x["text"] = forge.wash(x["text"].rstrip() + add)
            # ★ 它一续就收不住（实测让补 60 字写 150），超 400 字按句号砍回线内
            if len(re.sub(r"\s", "", x["text"])) > 400:
                body, keep = x["text"], ""
                for p in re.split(r"(?<=[。！？])", body):
                    if len(re.sub(r"\s", "", keep + p)) > 380:
                        break
                    keep += p
                x["text"] = keep.rstrip() or body[:380]
            # ★★ 补写是拼在最后一截尾巴上的，版式不能散：
            #   原版只有一截（短稿）时补完还是一长条，重新按三截切一遍。
            x["text"] = _force_paras(x["text"])
            x["n"] = len(re.sub(r"\s", "", x["text"]))
            keep_same = [b for b in x["bad"] if "太像" in b or "抄了钩子" in b]
            x["bad"] = ([] if 200 <= x["n"] <= 400
                        else ["%d 字，要 250–350 字" % x["n"]]) + \
                       forge.check_intro(x["text"]) + keep_same
    # ★★ 三版同犯一个毛病（实测三版全是「眼中闪过一丝」）＝AI 套话没治住。
    #   指着毛病批一次：只许改那几句，不许重写。判据还有字数/太像的也一起带过去。
    if not _retry:
        out = _fix_intro(book, out, title, hook)
        for x in out:
            # ★ 批改它爱顺手写长（实测改出 491 字），超 400 按句号砍回线内
            if len(re.sub(r"\s", "", x["text"])) > 400:
                body, keep = x["text"], ""
                for p in re.split(r"(?<=[。！？])", body):
                    if len(re.sub(r"\s", "", keep + p)) > 380:
                        break
                    keep += p
                x["text"] = keep.rstrip() or body[:380]
                x["text"] = _force_paras(x["text"])
                x["n"] = len(re.sub(r"\s", "", x["text"]))
            keep = [b for b in x["bad"] if "太像" in b or "抄了钩子" in b]
            x["bad"] = ([] if 200 <= x["n"] <= 400
                        else ["%d 字，要 250–350 字" % x["n"]]) + \
                       forge.check_intro(x["text"]) + keep
    # ★ 三版被粘成一版（它不写「第N版」标记时经常这样）＝他没得挑。
    #   再要一次，把两批里互不相同的凑够三版。
    if _retry < 1 and len(out) < 2:
        sub = gen_intro(book, (avoid or []) + [x["text"] for x in out],
                        use_corpus, title, hook, _retry + 1)
        if sub.get("ok") and sub.get("items"):
            for x in sub["items"]:
                if all(len(_lcs(re.sub(r"\s", "", x["text"]),
                               re.sub(r"\s", "", y["text"]))) < 20 for y in out):
                    out.append(x)
            out = out[:3]
    return {"ok": True, "items": out, "picked": picked,
            "warn": _unsafe([x.get("text", "") for x in out])}


def gen_roll(use_llm=True, with_cheat=True):
    """一键乱写：他不填一个字，程序替他掷骰子。模型调不通就退化成纯随机。"""
    import random
    mf = random.choice(["男频", "女频"])
    genre = random.choice(GENRES[mf])
    words = random.choice(WORDS)
    rtags = "／".join(random.sample(READ_TAGS, 3))
    ctags = "／".join(random.sample(CONTENT_TAGS, 3))
    book = {"genre": genre, "mf": mf, "words": words,
            "read_tags": rtags, "content_tags": ctags,
            "brief": "", "protagonist": "", "romance": ""}
    err = ""
    if use_llm:
        try:
            raw = llm.complete(
                "你是网文策划，出题要野、要具体，别出套路模板。只按指定三行输出。",
                ROLL_USER.format(genre=genre, mf=mf, rom=_romance_how(mf)),
                temperature=1.1, max_tokens=600)
            for k, v in _roll_parse(raw).items():
                if v:
                    book[k] = v
        except Exception as e:
            # ★ 退化成纯随机是设计如此（骰子照扔），但别再静默——
            #   界面必须知道这一把不是模型出的题
            err = f"模型没答（{type(e).__name__}），这一把是纯随机"
    if not book["brief"]:
        book["brief"] = "一个在%s里%s的人，从最底下往上走" % (genre, random.choice(CONTENT_TAGS))
    if not book["protagonist"]:
        book["protagonist"] = "（待填）"
    if not book["romance"]:
        book["romance"] = "（待填）"
    # ★ 一键乱写＝一步都不用填，金手指也得跟着随机出来，顺手挑一条合格的塞进去
    out = {"ok": True, "book": book, "cheats": [], "cheat": ""}
    if err:
        out["err"] = err          # 前端 note 出来：这一把是随机不是模型
    if not with_cheat:
        return out
    try:
        cr = gen_cheat(book, use_corpus=True)
        items = cr.get("items") or []
        out["cheats"] = items
        # ★ 一键乱写＝随机，那就真随机：合格的里面随手掷一个，
        #   不是永远挑第一条（那样每次点都出同一种味道的金手指）。
        #   一条合格的都没有时，至少先把「成长按章节算」这种硬伤的挑掉再掷。
        ok1 = [x for x in items if not x.get("bad")]
        if not ok1:
            ok1 = [x for x in items
                   if not any("按章节" in b for b in (x.get("bad") or []))]
        pool = ok1 or items
        if pool:
            one = random.choice(pool)
            out["cheat"] = one["text"]
            # 前端要把它当候选列出来（带校验结果），所以整条也带回去
            out["cheatItem"] = one
    except Exception as e:
        # 静默是故意的：金手指没掷出来不影响开书（cheats 空着，
        # 前端照常显示，人去选）。但错误本身带走，别丢。
        out["cheatErr"] = f"{type(e).__name__}: {e}"
    return out


# ---------------------------------------------------------------------------
# 落盘：建成一本书的目录（bible / outline / rules / chapters）
# ---------------------------------------------------------------------------

BIBLE_TMPL = """# 《{title}》

> 立项时间：{date} · 目录名：{slug}
> 状态：开书台生成，简介与细纲待补。

## 一、基本信息

- 题材：{genre}
- 频道：{mf}
- 目标字数：{words}
- 阅读标签：{read_tags}
- 内容标签：{content_tags}

## 二、一句话钩子

{hook}

## 三、作者想写什么

{brief}

## 四、主角

{protagonist}

## 五、感情线

{romance}

## 六、金手指规则表（不可违背，也不可改）

{rules}

## 七、简介

{intro}
"""

RULES_TMPL = """# 设定红线（写每一章之前必读，违反即作废）

★ 这份是给模型看的。写正文时它会整份拼进 prompt，所以只写"现在就必须守住的"，
   把后面才揭的东西写在"本书禁词"里——提前写＝悬疑线当场断掉。

## 一、通用红线（每本书都一样）

- 禁止使用：仿佛、似乎、宛如。（AI 味头号来源，见到就删）
- 不推进剧情的细节一律删：换手、抹袖子、抖衣襟上的雨、远处过一辆车……
  判据：把这句整句删掉，后面剧情还走得通吗？走得通＝填充，删。
- 不许让读者算数：金手指不做成身上挂读数/悬浮面板/倒计时/余额/榜单。
- 强弱写在身体和处境上（他穿多少/敢不敢还嘴/赔不赔得起），不写在等级名上。
- 句长要有方差；"有的……有的……"见到就改成交锋或删。
- 静态观察改成一次交锋：嘴问出来的有对手，有对手就有信息。

## 二、本书禁词（卷末才揭的，现在一个都不许出现）

（填。例：虚拟空间/意识/培养舱/自愈/影子/模拟……）

## 三、本书专属红线

（填。例：主角此刻不知道XX；同一个人不是分身……）
"""


CHARACTERS_TMPL = """# 人物库 ·《{title}》

> 用途：写任何一章之前，先看这里「章内出场人物」的当前状态。
> 规矩：这一份只追加，不覆盖旧值——心态、实力、关系变了，
> 就把新值写上去，并在「变更日志」里记下是第几章、因为哪件事。
> 为什么：绝大多数穿帮不是文笔问题，是状态没记住——
> 他上一章没钱这一章随手掏钱，上一章在东城这一章突然在西城。

## 主角

| 项 | 当前值 | 最后更新 |
|---|---|---|
| 姓名／年纪／性别 | {protagonist} | 第1章 |
| 性格 | （上面那一栏里的性格词与证明动作） | 第1章 |
| 理想／想要什么 | （填：具体到能失去的东西） | 第1章 |
| 当前想法 | （填：他此刻最想要什么、最怕什么） | 第1章 |
| 实力等级 | （填） | 第1章 |
| 所属势力 | （填） | 第1章 |
| 身上带着 | （填） | 第1章 |
| 能力（已开发用法） | （一开始只有一个核心，随剧情长出新用法；每长出一种就记一条：第几章、什么场合、怎么用出来的） | 第1章 |
| 性别 | （填；可因剧情／能力／法宝而改变，改了记在下方变更日志） | 第1章 |
| 称呼 | （本名／外号／敌人怎么叫／朋友怎么叫／不同时期怎么叫，全记下来；同一个人在不同人嘴里叫法可以不同） | 第1章 |
| 年纪 | （填；会随时间推进变，也会因穿越／能力／法宝变，变了记变更日志） | 第1章 |
| 最近做的事 | （填：这一章他做了什么，一句话） | 第1章 |
| 当前所在地 | （填） | 第1章 |

### 变更日志（只追加）
- 第1章：立项落定。

## 配角（出场一个加一个）

<!-- 每个配角都用上面那张表的格式；没有就空着 -->

## 人物关系

| 谁 | 对谁 | 关系 | 从哪章起 |
|---|---|---|---|
| （填） | （填） | （填） | （填） |

## 势力与等级

| 势力 | 立场 | 主要人物 | 备注 |
|---|---|---|---|
| （填） | （填） | （填） | （填） |
"""



def create_book(slug: str, book: dict, hook: str, title: str, rows,
                intro: str = "", skel=None, beats=None) -> dict:
    slug = re.sub(r"[^\w一-龥]", "", (slug or ""))[:20]
    if not slug:
        return {"ok": False, "err": "目录名空了（用英文或中文，别用符号）"}
    d = os.path.join(BOOKS, slug)
    if os.path.exists(d):
        return {"ok": False, "err": f"已有同名目录：{slug}"}
    os.makedirs(os.path.join(d, "chapters"), exist_ok=True)

    title = (title or "").strip() or "（待定）"
    rules = book.get("rules") or []
    bible = BIBLE_TMPL.format(
        title=title, date=datetime.now().strftime("%Y-%m-%d"), slug=slug,
        genre=book.get("genre") or "—", mf=book.get("mf") or "—",
        words=book.get("words") or "—",
        read_tags=book.get("read_tags") or "—",
        content_tags=book.get("content_tags") or "—",
        hook=(hook or "").strip() or "（待补）",
        brief=book.get("brief") or "（待补）",
        intro=(intro or "").strip() or "（待补）",
        protagonist=book.get("protagonist") or "（待补）",
        romance=book.get("romance") or "（待补）",
        rules="\n".join("- " + r for r in rules) if rules else "（待补，最要紧的一项）",
    )
    open(os.path.join(d, "bible.md"), "w", encoding="utf-8").write(bible)
    open(os.path.join(d, "rules.md"), "w", encoding="utf-8").write(RULES_TMPL)
    # ★ 人物库：立项时就落盘，后面每章在此基础上追加
    open(os.path.join(d, "characters.md"), "w", encoding="utf-8").write(
        CHARACTERS_TMPL.format(title=title,
                               protagonist=book.get("protagonist") or "（待补）"))

    lines = ["| 章 | 标题 | 本章必办一件事 | 章末钩子 |", "|---|---|---|---|"]
    for r in sorted(rows, key=lambda x: x.get("n", 0)):
        lines.append("| %s | %s | %s | %s |" % (
            r.get("n", ""), r.get("title", ""), r.get("task", ""), r.get("hook", "")))
    open(os.path.join(d, "outline.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    # ★ 建书就把章节文件建出来。否则写作页的章节下拉是空的，
    #   没有章节就点不了「生成正文」，只会提示「先选一章」。
    for r in sorted(rows, key=lambda x: x.get("n", 0)):
        _nm = str(r.get("title", "")).replace("/", "／").replace("\\", "＼")
        _fp = os.path.join(d, "chapters", "第%s章 %s.md" % (r.get("n", ""), _nm))
        if not os.path.exists(_fp):
            open(_fp, "w", encoding="utf-8").write(
                "# 第%s章 %s\n\n" % (r.get("n", ""), _nm))
    # ★ 大纲、细纲一起落盘：后面写正文要按它们走，不能只靠章纲
    if skel:
        ls = ["| 阶段 | 章 | 要什么／被谁挡 | 段末那一刀 |", "|---|---|---|---|"]
        for s in skel:
            ls.append("| %s | %s | %s | %s |" % (s.get("name", ""), s.get("span", ""),
                                                 s.get("what", ""), s.get("knife", "")))
        open(os.path.join(d, "skeleton.md"), "w",
             encoding="utf-8").write("\n".join(ls) + "\n")
    if beats:
        lb = ["# 细纲（逐章一句话）", ""]
        for r in sorted(beats, key=lambda x: x.get("n", 0)):
            lb.append("- 第%s章｜%s" % (r.get("n", ""), r.get("beat", "")))
        open(os.path.join(d, "beats.md"), "w",
             encoding="utf-8").write("\n".join(lb) + "\n")
    return {"ok": True, "slug": slug, "dir": d, "chapters": len(rows)}


# ══════════════════════════════════════════════════════════════
# ★ 导入已有设定：把一份「第X、…」结构的 .md 拆成程序要的东西。
#   不走模型——文档本身足够规整，正则就能切准，零幻觉。
import re as _re_imp

_IMP_SEC = _re_imp.compile(r"^##\s*第[一二三四五六七八九十]+、\s*(.+?)\s*$", _re_imp.M)
_IMP_CHAR = _re_imp.compile(
    r"姓名[:：]\s*(?P<name>.+?)\s*[　\s]+性别[:：]\s*(?P<sex>.+?)\s*[　\s]+"
    r"年龄[:：]\s*(?P<age>\d+)\s*[　\s]+性格[:：]\s*(?P<char>.+?)\s*"
    r"(?:[　\s]+语言风格[:：]\s*(?P<voice>.+))?$", _re_imp.M)


def import_setting(path: str) -> dict:
    """读一份设定 md，返回 {sections, chars, title}。不写文件、不调模型。"""
    raw = open(path, encoding="utf-8").read().replace("\r\n", "\n")
    title = ""
    m = _re_imp.search(r"^#\s*《(.+?)》", raw, _re_imp.M)
    if m:
        title = m.group(1).strip()
    # 按「## 第X、」切段
    marks = [(m.start(), m.group(1).strip()) for m in _IMP_SEC.finditer(raw)]
    secs = {}
    for k, (pos, name) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(raw)
        secs[name] = raw[pos:end].strip()
    # 人物：只从「角色设定」那段里抽
    chars = []
    for name, body in secs.items():
        if "角色" in name:
            for m in _IMP_CHAR.finditer(body):
                d = m.groupdict()
                chars.append({
                    "name": (d.get("name") or "").replace("**", "").strip(" 　"),
                    "sex": (d.get("sex") or "").strip(),
                    "age": (d.get("age") or "").strip(),
                    "char": (d.get("char") or "").strip(),
                    "voice": (d.get("voice") or "").strip(),
                })
    # 技能/道具：这几条就是金手指，必须进 rules（不可违背）
    skills = []
    for name, body in secs.items():
        if "技能" in name:
            for m in _re_imp.finditer(r"###\s*\d+\.\s*技能名称[:：]\s*(.+)", body):
                head = m.group(1).strip()
                rest = body[m.end():]
                nxt = _re_imp.search(r"\n###\s*\d+\.", rest)
                detail = (rest[:nxt.start()] if nxt else rest).strip()
                skills.append({"title": head, "detail": detail})
            break
    return {"title": title, "sections": secs, "chars": chars,
            "skills": skills, "raw": raw}


def write_setting(imp: dict, slug: str) -> dict:
    """把导入结果落盘：bible.md（原文只读区 + 摘要）与 characters.md。"""
    import os as _os
    d = _os.path.join(BOOKS, slug)
    _os.makedirs(_os.path.join(d, "chapters"), exist_ok=True)
    # bible.md：摘要在上，原文在下（原文只读，改坏了能翻回来）
    parts = ["# 《%s》· 设定（导入）" % (imp.get("title") or slug), ""]
    for name, body in (imp.get("sections") or {}).items():
        parts.append("## " + name)
        parts.append(body.split("\n", 1)[-1].strip())
        parts.append("")
    parts += ["---", "", "## 原始设定（只读，导入时原样保存）", "", imp.get("raw", "")]
    open(_os.path.join(d, "bible.md"), "w", encoding="utf-8").write(
        "\n".join(parts))
    # characters.md
    cl = ["# 人物库 ·《%s》" % (imp.get("title") or slug), "",
          "## 主角", "", "| 项 | 当前值 | 最后更新 |", "|---|---|---|"]
    for c in (imp.get("chars") or [])[:2]:
        cl.append("| 姓名／年纪／性别 | %s／%s／%s | 第1章 |"
                  % (c["name"], c["age"], c["sex"]))
        cl.append("| 性格 | %s | 第1章 |" % (c["char"] or "—"))
        cl.append("| 语言风格 | %s | 第1章 |" % (c["voice"] or "—"))
    cl += ["", "## 配角", "", "| 姓名 | 年纪 | 性别 | 性格 | 语言风格 |",
           "|---|---|---|---|---|"]
    for c in (imp.get("chars") or [])[2:]:
        cl.append("| %s | %s | %s | %s | %s |"
                  % (c["name"], c["age"], c["sex"], c["char"], c["voice"] or "—"))
    cl += ["", "## 变更日志（只追加）", "- 第1章：导入既有设定。", ""]
    open(_os.path.join(d, "characters.md"), "w", encoding="utf-8").write(
        "\n".join(cl))
    # rules.md：把技能设定当成本书不可违背的金手指规则
    sk = imp.get("skills") or []
    rl = ["# 《%s》· 本书规则（导入）" % (imp.get("title") or slug), "",
          "> 这些是从原始设定里抽出来的能力约束。写正文时不可违背，" ,
          "> 也不许自己加设定里没有的能力。", "", "## 技能／金手指", ""]
    for x in sk:
        rl.append("### " + x["title"])
        rl.append("")
        rl.append(x["detail"][:1200])
        rl.append("")
    open(_os.path.join(d, "rules.md"), "w", encoding="utf-8").write("\n".join(rl))
    return {"ok": True, "dir": d, "chars": len(imp.get("chars") or []),
            "skills": len(sk),
            "sections": list((imp.get("sections") or {}).keys())}


def update_book(slug: str, rows, skel=None, beats=None) -> dict:
    """更新已存在的书：只覆盖大纲/骨架/细纲/章节。

    ★ 不碰 bible.md 和 characters.md——那是你的原始设定和人物库，
      迭代时被覆盖就找不回来了。被覆盖的三份先放进 _bak/。
    """
    import shutil as _sh
    d = os.path.join(BOOKS, slug)
    if not os.path.isdir(d):
        return {"ok": False, "err": "这本书不存在"}
    bk = os.path.join(d, "_bak")
    os.makedirs(bk, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for fn in ("outline.md", "skeleton.md", "beats.md"):
        p0 = os.path.join(d, fn)
        if os.path.exists(p0):
            _sh.copy2(p0, os.path.join(bk, "%s.%s.md" % (fn[:-3], stamp)))
    lines = ["| 章 | 标题 | 本章必办一件事 | 章末钩子 |", "|---|---|---|---|"]
    for r in sorted(rows, key=lambda x: x.get("n", 0)):
        lines.append("| %s | %s | %s | %s |" % (
            r.get("n", ""), r.get("title", ""), r.get("task", ""), r.get("hook", "")))
    open(os.path.join(d, "outline.md"), "w", encoding="utf-8").write(
        "\n".join(lines) + "\n")
    if skel:
        ls = ["| 阶段 | 章 | 要什么／被谁挡 | 段末那一刀 |", "|---|---|---|---|"]
        for x in skel:
            ls.append("| %s | %s | %s | %s |" % (
                x.get("name", ""), x.get("span", ""),
                x.get("what", ""), x.get("knife", "")))
        open(os.path.join(d, "skeleton.md"), "w", encoding="utf-8").write(
            "\n".join(ls) + "\n")
    if beats:
        lb = ["# 细纲（逐章一句话）", ""]
        for x in sorted(beats, key=lambda y: y.get("n", 0)):
            lb.append("- 第%s章｜%s" % (x.get("n", ""), x.get("beat", "")))
        open(os.path.join(d, "beats.md"), "w", encoding="utf-8").write(
            "\n".join(lb) + "\n")
    # 补建缺的章节文件
    made = 0
    cd = os.path.join(d, "chapters")
    os.makedirs(cd, exist_ok=True)
    for r in sorted(rows, key=lambda x: x.get("n", 0)):
        nm = str(r.get("title", "")).replace("/", "／").replace("\\", "＼")
        fp = os.path.join(cd, "第%s章 %s.md" % (r.get("n", ""), nm))
        if not os.path.exists(fp):
            open(fp, "w", encoding="utf-8").write(
                "# 第%s章 %s\n\n" % (r.get("n", ""), nm))
            made += 1
    return {"ok": True, "slug": slug, "dir": d, "chapters": len(rows),
            "updated": True, "made": made}


# ══════════════════════════════════════════════════════════════
# ★ 迭代用：generate 金手指时把「已有的那几条」带上，让它在此基础上换写法，
#   而不是另起一套跟你设定无关的能力。
_gen_cheat_before = gen_cheat


def gen_cheat(book, *a, **k):
    b = dict(book or {})
    rs = b.get("rules") or []
    if rs:
        head = ("\n\n【本书已有的金手指（必须遵守）】\n"
                + "\n".join("- " + str(x) for x in rs)
                + "\n★ 不要照抄上面这几条，也不许与它们冲突。"
                  "新候选要能在它们旁边成立，或者写成它们的另一种用法。\n")
        b["setting"] = (b.get("setting") or "") + head
    return _gen_cheat_before(b, *a, **k)

