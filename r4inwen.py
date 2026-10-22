# -*- coding: utf-8 -*-
"""R4IN文 · 原生客户端（PySide6）

布局完全照 webui.py 里 PAGE 的原版三栏：
    顶栏   开书台 / 写作 切换 · 选书 · 选章 · 设置
    左 290   模型（下拉/温度/上下文）· 本章大纲 · 全书结构
    中 1/2   上＝AI 生成原文（只读）　下＝跟模型聊这一章（占 46%）
    右 1/2   整块修改框　底部：改稿指令 → 提交给模型／撤回／保存／查红线／导出

配色与透明度也照原版：
    --tint 底色　--pw 面板不透明度（默认 0.72）
    左栏 .72　中栏 .42　右栏 .80　正文框 .93　聊天 .88　卡片 .62

业务逻辑一行没改：forge / llm / redline / rules / style / skills / tracks / launch
"""

import argparse
import importlib.util
import json
import os
import re
import sys
import threading
import traceback

if getattr(sys, "frozen", False):
    HERE = os.path.dirname(os.path.abspath(sys.executable))
    _MEI = getattr(sys, "_MEIPASS", HERE)
else:
    HERE = os.path.dirname(os.path.abspath(__file__))

# ★ OLLAMA_MODELS 必须在最前面设：任何一处找 ollama 的代码
#   都要先命中安装目录里那一份，否则会静默用系统装的 ollama 和系统模型库。
if getattr(sys, "frozen", False):
    _OLL = os.path.join(HERE, "ollama")
    if os.path.isdir(_OLL):
        os.environ["OLLAMA_MODELS"] = os.path.join(_OLL, "models")
        os.environ["OLLAMA_HOST"] = "127.0.0.1:11434"
        # 放进 PATH 最前，让 which("ollama") / shutil.which 先找到包里的
        os.environ["PATH"] = _OLL + os.pathsep + os.environ.get("PATH", "")

def is_lite():
    """轻量版：安装目录里没有 ollama，只走云端。"""
    try:
        return (getattr(sys, "frozen", False)
                and not os.path.isdir(os.path.join(HERE, "ollama")))
    except Exception:
        return False


NOVEL_DIR = None
if getattr(sys, "frozen", False):
    # ★ 发行版：数据就在安装目录。源码已经打进 exe，
    #   不能再要求盘上有 forge.py，否则会退回到开发机路径。
    NOVEL_DIR = HERE
else:
    for _c in [HERE, r"E:\Ollama\novel", os.getcwd()]:
        if os.path.exists(os.path.join(_c, "forge.py")):
            NOVEL_DIR = _c
            break
if not NOVEL_DIR:
    sys.stderr.write("找不到 forge.py。本程序要和 forge.py 放在同一目录。\n")
    sys.exit(1)
sys.path.insert(0, NOVEL_DIR)

try:
    from PySide6.QtCore import Qt, QThread, QTimer, Signal
    from PySide6.QtGui import (
        QAction, QColor, QFont, QIcon, QKeySequence, QPainter, QPalette,
        QPixmap,
    )
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
        QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
        QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
        QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
        QSplitter, QStackedWidget, QTabWidget, QTableWidget,
        QTableWidgetItem,
        QVBoxLayout, QWidget,
    )
except ImportError:
    sys.stderr.write("缺 PySide6。先装：pip install PySide6\n")
    sys.exit(1)

import forge      # noqa: E402
import launch     # noqa: E402
import llm        # noqa: E402
import redline    # noqa: E402
import skills     # noqa: E402

_real = os.path.abspath(NOVEL_DIR)
if os.path.abspath(getattr(forge, "HERE", "")) != _real:
    forge.HERE = _real
    forge.BOOKS = os.path.join(_real, "books")
    if hasattr(forge, "STATE"):
        forge.STATE = os.path.join(_real, "current.json")
if os.path.abspath(os.path.dirname(getattr(llm, "CFG_PATH", ""))) != _real:
    llm.CFG_PATH = os.path.join(_real, "settings.json")

BOOKS = forge.BOOKS
CH_RE = re.compile(r"第(\d+)章")
NUM_RE = re.compile(r"^(\d{1,4})")
TINTS = [("#f7d8dc", "淡红"), ("#e6dcf6", "淡紫"), ("#d7e6f5", "淡蓝"),
         ("#c7edcc", "淡绿"), ("#fae3cd", "淡橙"), ("#ffffff", "素白")]

# ── 全局样式 ──────────────────────────────────────────────────────
# ★★ 不能依赖系统默认颜色：Windows 深色模式下 Qt 默认给白字，
#    而面板是白底 —— 结果就是白底白字。这里每个控件都显式写死颜色。
INK = "#222222"
GREY = "#777777"
LINE = "#cccccc"
WARN = "#a3212b"

QSS = """
QWidget {
    font-family: "Microsoft YaHei";
    font-size: 14px;
    color: %(ink)s;
    background: transparent;
}
QLabel { color: %(ink)s; background: transparent; }
QLabel[h="1"] { color: %(grey)s; font-size: 12px; background: transparent; }
QGroupBox {
    color: %(grey)s; font-size: 12px;
    border: 1px solid rgba(0,0,0,.10); border-radius: 8px;
    margin-top: 18px; padding: 12px 10px 10px 10px;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 10px; padding: 0 5px; color: %(grey)s;
}
QPushButton {
    font-size: 13px; padding: 6px 14px; text-align: center;
    color: #ffffff; background: #333333;
    border: 1px solid #333333; border-radius: 8px;
}
QPushButton:hover { background: #4a4a4a; border-color: #4a4a4a; }
QPushButton:pressed { background: #1a1a1a; }
QPushButton:disabled {
    color: #9a9a9a; background: #e6e6e3; border-color: #dcdcd8;
}
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {
    font-size: 13px; padding: 6px 10px; min-height: 20px;
    color: %(ink)s; background: #ffffff;
    border: 1px solid %(line)s; border-radius: 10px;
}
QComboBox::drop-down {
    subcontrol-origin: padding; subcontrol-position: center right;
    width: 26px; border: 0; background: transparent;
}
QComboBox::down-arrow {
    image: url("__CHEV__");
    width: 14px; height: 9px;
}
QComboBox:disabled, QLineEdit:disabled { color: #9a9a9a; background: #f4f4f2; }
QComboBox QAbstractItemView {
    color: %(ink)s; background: #ffffff;
    selection-color: #ffffff; selection-background-color: #333333;
    border: 1px solid %(line)s;
}
QPlainTextEdit {
    font-size: 14px; color: %(ink)s; background: #ffffff;
    border: 1px solid %(line)s; border-radius: 8px; padding: 10px;
}
QCheckBox { color: %(ink)s; background: transparent; }
QCheckBox::indicator {
    width: 15px; height: 15px; border: 1px solid %(line)s;
    border-radius: 3px; background: #ffffff;
}
QCheckBox::indicator:checked { background: #333333; border-color: #333333; }
QSlider::groove:horizontal { height: 4px; background: #dddddd; border-radius: 2px; }
QSlider::handle:horizontal {
    width: 14px; margin: -6px 0; border-radius: 7px; background: #333333;
}
QScrollBar:vertical { width: 11px; background: transparent; }
QScrollBar::handle:vertical {
    background: rgba(0,0,0,.22); border-radius: 5px; min-height: 30px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QStatusBar { color: %(ink)s; }
QStatusBar QLabel { color: %(ink)s; }
QMenuBar {
    color: %(ink)s; background: #ffffff;
    border-bottom: 1px solid rgba(0,0,0,.10);
}
QMenuBar::item {
    color: %(ink)s; background: transparent; padding: 5px 12px;
}
QMenuBar::item:selected { background: rgba(0,0,0,.10); color: %(ink)s; }
QMenu { color: %(ink)s; background: #ffffff; border: 1px solid %(line)s; }
QMenu::item { color: %(ink)s; padding: 6px 22px; }
QMenu::item:selected { background: #333333; color: #ffffff; }
QToolTip { color: #ffffff; background: #333333; border: 0; padding: 4px 6px; }
QDialog { background: #f6f7f9; }
""" % {"ink": INK, "grey": GREY, "line": LINE}


# 主按钮：明确写死深底白字，不靠属性选择器
PRI_QSS = ("color:#ffffff; background:#333333; border:1px solid #333333;"
           " border-radius:6px; padding:6px 14px; font-size:13px;")


BLACK_BTN = (
    "QPushButton{color:#ffffff;background:#333333;"
    "border:1px solid #333333;border-radius:8px;"
    "padding:6px 14px;font-size:13px;}"
    "QPushButton:hover{background:#4a4a4a;border-color:#4a4a4a;}"
    "QPushButton:pressed{background:#1a1a1a;}"
    "QPushButton:disabled{color:#9a9a9a;background:#e6e6e3;"
    "border-color:#dcdcd8;}")


def restyle_buttons(root):
    """把 root 下所有按钮刷成黑底白字 —— 一个都不漏。"""
    try:
        for b in root.findChildren(QPushButton):
            b.setStyleSheet(BLACK_BTN)
    except Exception:
        pass


def mkpri(btn):
    btn.setStyleSheet(PRI_QSS)
    return btn


def light_palette():
    """强制浅色调色板 —— 不看系统深色模式。"""
    p = QPalette()
    role = QPalette.ColorRole
    p.setColor(role.Window, QColor("#f6f7f9"))
    p.setColor(role.WindowText, QColor(INK))
    p.setColor(role.Base, QColor("#ffffff"))
    p.setColor(role.AlternateBase, QColor("#f2f2f0"))
    p.setColor(role.Text, QColor(INK))
    p.setColor(role.Button, QColor("#ffffff"))
    p.setColor(role.ButtonText, QColor(INK))
    p.setColor(role.BrightText, QColor("#ffffff"))
    p.setColor(role.Highlight, QColor("#333333"))
    p.setColor(role.HighlightedText, QColor("#ffffff"))
    p.setColor(role.ToolTipBase, QColor("#333333"))
    p.setColor(role.ToolTipText, QColor("#ffffff"))
    p.setColor(role.PlaceholderText, QColor("#999999"))
    dis = p.color(QPalette.ColorGroup.Disabled, role.Text)
    dis = QColor("#9a9a9a")
    p.setColor(QPalette.ColorGroup.Disabled, role.Text, dis)
    p.setColor(QPalette.ColorGroup.Disabled, role.ButtonText, dis)
    p.setColor(QPalette.ColorGroup.Disabled, role.WindowText, dis)
    return p


def bare_len(t):
    return len(re.sub(r"\s+", "", t or ""))


def list_books():
    if not os.path.isdir(BOOKS):
        return []
    return [n for n in sorted(os.listdir(BOOKS))
            if os.path.isdir(os.path.join(BOOKS, n, "chapters"))]


def list_chapters(slug):
    d = os.path.join(BOOKS, slug, "chapters")
    if not os.path.isdir(d):
        return []
    rows = []
    for fn in os.listdir(d):
        if not fn.endswith(".md") or fn.endswith(".ai.md") or fn.endswith(".draft.md"):
            continue
        m = CH_RE.search(fn)
        if m:
            n = int(m.group(1))
        else:
            m2 = NUM_RE.match(fn)
            if not m2:
                continue
            n = int(m2.group(1))
        try:
            txt = open(os.path.join(d, fn), encoding="utf-8").read()
        except Exception:
            txt = ""
        rows.append((n, fn, bare_len(txt)))
    rows.sort(key=lambda x: x[0])
    return rows


def read_doc(slug, name):
    p = os.path.join(BOOKS, slug, name)
    if not os.path.exists(p):
        return ""
    try:
        return open(p, encoding="utf-8").read()
    except Exception as e:
        return "（读不出来：%s）" % e


def rules_mod():
    """加载 corpus/pick.py（本题材写作规则库）。"""
    rp = os.path.join(NOVEL_DIR, "corpus", "pick.py")
    spec = importlib.util.spec_from_file_location("_cpick", rp)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def pick_text(it):
    if isinstance(it, str):
        return it
    if isinstance(it, dict):
        for k in ("text", "title", "hook", "intro", "txt", "value", "content", "beat"):
            v = it.get(k)
            if isinstance(v, str) and v.strip():
                return v
        ss = [v for v in it.values() if isinstance(v, str) and v.strip()]
        return max(ss, key=len) if ss else str(it)
    return str(it)


def items_of(res, *keys):
    if not isinstance(res, dict):
        return []
    for k in keys:
        v = res.get(k)
        if isinstance(v, list) and v:
            return v
    v = res.get("items")
    return v if isinstance(v, list) else []


# ══════════════════════════════════════════════════════════════════
class Worker(QThread):
    ok = Signal(object)
    fail = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            self.ok.emit(self._fn())
        except SystemExit as e:
            self.fail.emit(str(e) or "被中止")
        except Exception:
            self.fail.emit(traceback.format_exc(limit=5))


class AsyncMixin:
    """★★ 信号必须连到 QObject 的绑定方法上。

    以前写成 w.ok.connect(lambda v: on_ok(v))——lambda 没有线程归属，
    会在 **工作线程** 里直接执行，于是里面所有 setText / appendPlainText /
    setVisible 都在非主线程动界面：Qt 不允许，表现就是「点了没反应」，
    而且不抛异常，最难查。
    """

    def _w_ok(self, v):
        w = self.sender()
        cb = getattr(w, "_cb_ok", None)
        if cb:
            cb(v)

    def _w_fail(self, msg):
        w = self.sender()
        cb = getattr(w, "_cb_bad", None)
        if cb:
            cb(msg)
        else:
            QMessageBox.warning(self, "出错", msg)

    def _w_fin(self):
        w = self.sender()
        cb = getattr(w, "_cb_busy", None)
        if cb:
            cb(False)

    def run_async(self, fn, on_ok=None, on_err=None, busy=None):
        w = Worker(fn)
        self._workers.append(w)
        w._cb_ok = on_ok
        w._cb_bad = on_err
        w._cb_busy = busy
        w.ok.connect(self._w_ok)
        w.fail.connect(self._w_fail)
        if busy:
            busy(True)
            w.finished.connect(self._w_fin)
        w.start()
        return w


class Backdrop(QWidget):
    def __init__(self):
        super().__init__()
        self._pix = None
        self._mask = 55
        self._tint = QColor("#f7d8dc")

    def set_bg(self, path):
        self._pix = QPixmap(path) if path and os.path.exists(path) else None
        self.update()

    def set_mask(self, v):
        self._mask = max(0, min(100, int(v)))
        self.update()

    def set_tint(self, css):
        c = QColor(css)
        if c.isValid():
            self._tint = c
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        r = self.rect()
        if self._pix and not self._pix.isNull():
            # ★ 不能 drawPixmap(rect, pixmap)：那会拉伸填满、比例失调。
            #   先按比例放大到盖住窗口，再居中裁切。
            sc = self._pix.scaled(
                r.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap((r.width() - sc.width()) // 2,
                         (r.height() - sc.height()) // 2, sc)
            c = QColor(self._tint)
            c.setAlpha(int(255 * self._mask / 100))
            p.fillRect(r, c)
        else:
            p.fillRect(r, QColor(self._tint))


def panel_style(alpha, radius=12):
    return ("background: rgba(255,255,255,%.3f); border-radius:%dpx;" % (alpha, radius))


class CandidateRows(QScrollArea):
    """候选：每条右边「采用 / 不采用」。"""

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.inner = QWidget()
        self.inner.setStyleSheet("background: transparent;")
        self.v = QVBoxLayout(self.inner)
        self.v.setContentsMargins(2, 2, 2, 2)
        self.v.setSpacing(6)
        self.v.addStretch(1)
        self.setWidget(self.inner)
        self._rows = []

    def clear_rows(self):
        for r in self._rows:
            r.setParent(None)
        self._rows = []

    def add_plain(self, text):
        """只读一行：显示结果用，不带采用按钮。"""
        row = QWidget()
        row.setStyleSheet("background: transparent;"
                          " border-bottom: 1px solid rgba(0,0,0,.09);")
        h = QHBoxLayout(row)
        h.setContentsMargins(8, 6, 8, 6)
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setStyleSheet("font-size:13px; line-height:1.6;")
        h.addWidget(lab, 1)
        self.v.insertWidget(self.v.count() - 1, row)
        self._rows.append(row)
        return row

    def add_row(self, text, on_adopt):
        row = QWidget()
        row.setStyleSheet("background: transparent;"
                          " border-bottom: 1px solid rgba(0,0,0,.09);")
        h = QHBoxLayout(row)
        h.setContentsMargins(8, 6, 8, 6)
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setStyleSheet("font-size:13px; line-height:1.6;")
        ok = QPushButton("采用")
        ok.setStyleSheet(BLACK_BTN)
        ok.setFixedWidth(78)
        h.addWidget(lab, 1)
        h.addWidget(ok)
        self.v.insertWidget(self.v.count() - 1, row)
        self._rows.append(row)

        state = {"on": False}

        def toggle():
            on = not state["on"]
            state["on"] = on
            if on:
                row.setStyleSheet(
                    "background: rgba(190,235,205,.92); border-radius: 6px;")
                ok.setText("已采用")
                ok.setStyleSheet(
                    "QPushButton{color:#ffffff;background:#1f7a45;"
                    "border:1px solid #1f7a45;border-radius:8px;"
                    "padding:6px 10px;font-size:13px;}")
                if on_adopt:
                    on_adopt(text, True)
            else:
                row.setStyleSheet(
                    "background: transparent; border-bottom: 1px solid rgba(0,0,0,.09);")
                ok.setText("采用")
                ok.setStyleSheet(BLACK_BTN)
                if on_adopt:
                    try:
                        on_adopt(text, False)
                    except TypeError:
                        pass

        ok.clicked.connect(toggle)


# ══════════════════════════════════════════════════════════════════
class WriterView(QWidget, AsyncMixin):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self._workers = []
        self.slug = ""
        self.chap_n = None
        self.chap_file = None
        self.dirty = False
        self._loading = False
        self._total_wc = 0
        self._cur_wc = 0
        self._last_reply = ""

        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # ── 左：290px ───────────────────────────────────────────────
        left = QWidget()
        left.setFixedWidth(290)
        left.setStyleSheet(panel_style(0.72))
        lv = QVBoxLayout(left)
        lv.setContentsMargins(12, 12, 12, 12)
        lv.setSpacing(10)

        g_model = self._card("模型配置")
        mv = QVBoxLayout(g_model)
        mv.setSpacing(6)
        self._edit_widgets = []
        self.cb_model = QComboBox()
        self.cb_model.currentIndexChanged.connect(self.on_model_changed)
        mv.addWidget(self.cb_model)
        # ★ 只在启动时查一次的话，ollama 没起来就永远只有一项。
        #   改成点开下拉时现查一次，查不到就保留原列表。
        _orig_popup = self.cb_model.showPopup

        def _popup():
            try:
                self.reload_models(keep=self.cb_model.currentData())
            except Exception:
                pass
            _orig_popup()

        self.cb_model.showPopup = _popup
        mv.addWidget(self._h("随机度"))
        self.sp_temp = QDoubleSpinBox()
        self.sp_temp.setRange(0.0, 2.0)
        self.sp_temp.setSingleStep(0.05)
        self.sp_temp.setDecimals(2)
        mv.addWidget(self.sp_temp)
        mv.addWidget(self._h("记忆字数"))
        self.sp_ctx = QSpinBox()
        self.sp_ctx.setRange(500, 20000)
        self.sp_ctx.setSingleStep(500)
        mv.addWidget(self.sp_ctx)

        # ★ 后端二选一：本地 ollama / 云端 OpenAI 兼容（DeepSeek、Kimi 等都走这条）
        mv.addWidget(self._h("模型来源"))
        self.cb_backend = QComboBox()
        self.cb_backend.addItem("本地模型（不需要联网）", "ollama")
        self.cb_backend.addItem("云端 API（需要 Key）", "openai")
        self.cb_backend.currentIndexChanged.connect(self.on_backend_changed)
        mv.addWidget(self.cb_backend)
        # ★ 选服务商自动填地址和模型名，用户只需要粘一个 Key
        self.cb_provider = QComboBox()
        for _n, _u, _m in (
                ("DeepSeek（便宜，中文好）", "https://api.deepseek.com", "deepseek-chat"),
                ("豆包（方舟，有免费额度）",
                 "https://ark.cn-beijing.volces.com/api/v3", "doubao-1-5-pro-32k-250115"),
                ("月之暗面 Kimi", "https://api.moonshot.cn/v1", "moonshot-v1-8k"),
                ("通义千问", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
                ("智谱 GLM", "https://open.bigmodel.cn/api/paas/v4", "glm-4"),
                ("OpenAI", "https://api.openai.com/v1", "gpt-4o-mini"),
                ("其他（自己填）", "", "")):
            self.cb_provider.addItem(_n, (_u, _m))
        self.cb_provider.currentIndexChanged.connect(self.on_provider_changed)
        mv.addWidget(self.cb_provider)
        self.ed_base = QLineEdit()
        self.ed_base.setPlaceholderText("接口地址，例：https://api.deepseek.com")
        mv.addWidget(self.ed_base)
        self.ed_key = QLineEdit()
        self.ed_key.setPlaceholderText("API Key（选云端才需要）")
        self.ed_key.setEchoMode(QLineEdit.EchoMode.Password)
        mv.addWidget(self.ed_key)
        self.ed_rmodel = QLineEdit()
        self.ed_rmodel.setPlaceholderText("云端模型名，例：deepseek-chat")
        mv.addWidget(self.ed_rmodel)
        # ★ 轻量版没有本地模型，直接把这一项去掉，别让买家选到死路
        if getattr(sys, "frozen", False) and not os.path.isdir(
                os.path.join(HERE, "ollama")):
            _i = self.cb_backend.findData("ollama")
            if _i >= 0:
                self.cb_backend.removeItem(_i)
            self.cb_backend.setCurrentIndex(0)
        self.on_provider_changed()          # 默认第一项，马上把地址和模型填好
        self.btn_fetch = QPushButton("从服务商拉取模型")
        self.btn_fetch.clicked.connect(self.fetch_models)
        mv.addWidget(self.btn_fetch)
        row = QHBoxLayout()
        b = QPushButton("测连通")
        b.clicked.connect(self.do_ping)
        b2 = QPushButton("保存配置")
        b2.clicked.connect(self.save_cfg_ui)
        row.addWidget(b)
        row.addWidget(b2)
        mv.addLayout(row)
        lv.addWidget(g_model)

        g_file = self._card("大纲文件")
        fv = QVBoxLayout(g_file)
        self.cb_doc = QComboBox()
        fv.addWidget(self.cb_doc)
        row2 = QHBoxLayout()
        b3 = QPushButton("打开看看")
        b3.clicked.connect(self.open_doc)
        b4 = QPushButton("刷新")
        b4.clicked.connect(self.reload_docs_list)
        row2.addWidget(b3)
        row2.addWidget(b4)
        fv.addLayout(row2)
        lv.addWidget(g_file)
        g_out = self._card("本章大纲")
        ov = QVBoxLayout(g_out)
        self.btn_save_doc = QPushButton("保存这份")
        self.btn_save_doc.setToolTip("覆盖前先把旧版放进 _bak/，改坏了能翻回来")
        self.btn_save_doc.clicked.connect(self.save_doc)
        ov.addWidget(self.btn_save_doc)
        self.txt_outline = QPlainTextEdit()
        # ★ 可改：设定是你的草稿，不是只读档案
        self.txt_outline.setReadOnly(False)
        self.txt_outline.setStyleSheet("font-size:12.5px;")
        ov.addWidget(self.txt_outline)
        lv.addWidget(g_out, 2)

        g_bib = self._card("全书结构 / 设定")
        bv = QVBoxLayout(g_bib)
        self.txt_bible = QPlainTextEdit()
        self.txt_bible.setReadOnly(True)
        self.txt_bible.setStyleSheet("font-size:12.5px;")
        bv.addWidget(self.txt_bible)
        lv.addWidget(g_bib, 3)

        # ── 中：AI 原文 + 聊天 ─────────────────────────────────────
        mid = QWidget()
        mid.setStyleSheet(panel_style(0.42))
        mv2 = QVBoxLayout(mid)
        mv2.setContentsMargins(10, 10, 10, 10)
        mv2.setSpacing(8)
        mv2.addWidget(self._h("AI 给你的稿子"))
        self.txt_ai = QPlainTextEdit()
        self.txt_ai.setReadOnly(True)
        self.txt_ai.setStyleSheet("background: rgba(255,255,255,.93); color:#444;")
        mv2.addWidget(self.txt_ai, 54)

        mv2.addWidget(self._h("跟模型聊这一章（它给建议，不改你的稿）"))
        self.chat = QPlainTextEdit()
        self.chat.setReadOnly(True)
        self.chat.setStyleSheet(
            "font-size:13px; background: rgba(255,255,255,.88);")
        mv2.addWidget(self.chat, 46)
        cbar = QHBoxLayout()
        self.ed_chat = QLineEdit()
        self.ed_chat.setPlaceholderText("问它这段哪里弱，或说「改成具体人名」…")
        self.ed_chat.returnPressed.connect(self.do_chat)
        b = mkpri(QPushButton("发送"))
        b.clicked.connect(self.do_chat)
        cbar.addWidget(self.ed_chat, 1)
        cbar.addWidget(b)
        mv2.addLayout(cbar)

        # ── 右：修改框 ─────────────────────────────────────────────
        right = QWidget()
        right.setStyleSheet(panel_style(0.80))
        rv = QVBoxLayout(right)
        rv.setContentsMargins(10, 10, 10, 10)
        rv.setSpacing(8)
        rv.addWidget(self._h("你改的这版（这块是唯一会落盘的）"))
        self.editor = QPlainTextEdit()
        self.editor.setStyleSheet("background: rgba(255,255,255,.93);")
        self.editor.textChanged.connect(self.on_edit)
        rv.addWidget(self.editor, 1)

        self.ed_ask = QLineEdit()
        self.ed_ask.setPlaceholderText("例：换成具体人名，加一个具体数字")
        self.ed_ask.returnPressed.connect(self.do_ask)
        bar = QHBoxLayout()
        _items = [("生成正文", self.generate, False),
                  ("撤回", self.do_undo, True),
                  ("保存", self.save, True),
                  ("导出", self.export, True)]
        if not is_lite():
            _items.insert(0, ("学习", self.learn_now, True))

        for text, slot, sec in _items:
            b = QPushButton(text)
            if not sec:
                mkpri(b)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch(1)
        rv.addLayout(bar)

        sc_left = QScrollArea()
        sc_left.setWidgetResizable(True)
        sc_left.setFixedWidth(306)
        sc_left.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc_left.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        sc_left.setWidget(left)
        root.addWidget(sc_left)
        root.addWidget(mid, 1)
        root.addWidget(right, 1)
        # 供 apply_appearance 统一调透明度
        self._panel_widgets = [(left, 0.72), (mid, 0.42), (right, 0.80)]
        self._edit_widgets = [(self.txt_ai, 0.93), (self.chat, 0.90),
                              (self.editor, 0.93)]

    def _card(self, title):
        g = QGroupBox(title)
        return g

    def _h(self, t):
        l = QLabel(t)
        l.setProperty("h", "1")
        return l

    def status(self, msg):
        self.win.status.showMessage(msg)

    # ── 载入 ────────────────────────────────────────────────────────
    def load_cfg_ui(self):
        """把配置灌进界面。★ 以前只在 load_book 里做，
        轻量版 books 是空的，没有书，温度/上下文就永远停在控件最小值。"""
        cfg = llm.load_cfg() or {}
        try:
            self.sp_temp.setValue(float(cfg.get("temperature") or 0.9))
            self.sp_ctx.setValue(int(cfg.get("context") or 16000))
            i = self.cb_backend.findData(cfg.get("backend") or "ollama")
            if i >= 0:
                self.cb_backend.setCurrentIndex(i)
            self.ed_key.setText(cfg.get("api_key") or "")
            if (cfg.get("backend") or "ollama") == "openai":
                self.ed_base.setText(cfg.get("base_url") or "")
                _m = cfg.get("model") or ""
                if _m:
                    self.ed_rmodel.setText(_m)
            self.on_backend_changed()
        except Exception:
            pass

    def load_book(self, slug):
        self.slug = slug
        self.reload_docs()
        self.reload_chapters()
        self.reload_models()
        cfg = llm.load_cfg() or {}
        try:
            self.sp_temp.setValue(float(cfg.get("temperature") or 0.9))
            self.sp_ctx.setValue(int(cfg.get("context") or 6000))
            i = self.cb_backend.findData(cfg.get("backend") or "ollama")
            self.cb_backend.setCurrentIndex(i if i >= 0 else 0)
            self.ed_key.setText(cfg.get("api_key") or "")
            # 本地模式下不要用配置里的 127.0.0.1 盖掉刚自动填好的厂商地址
            if (cfg.get("backend") or "ollama") == "openai":
                self.ed_base.setText(cfg.get("base_url") or "")
            _m = cfg.get("model") or ""
            if (cfg.get("backend") or "ollama") == "openai" and _m:
                self.ed_rmodel.setText(_m)
            self.on_backend_changed()
        except Exception:
            pass

    def fetch_models(self):
        """用 Key 去服务商拉可用模型，点一个填进模型名。"""
        from PySide6.QtWidgets import QMenu
        base = self.ed_base.text().strip().rstrip("/")
        key = self.ed_key.text().strip()
        if not (base and key):
            self.status("先填接口地址和 API Key")
            return
        try:
            data = llm._get(base + "/models",
                            {"Authorization": "Bearer " + key}, 20)
        except Exception as e:
            self.status("拉取失败：%s" % e)
            return
        items = data.get("data") or data.get("models") or []
        names = [str(m.get("id") or m.get("name") or "") for m in items]
        names = [x for x in names if x]
        if not names:
            self.status("服务商没返回模型列表")
            return
        menu = QMenu(self)
        for nm in names[:40]:
            act = menu.addAction(nm)
            act.triggered.connect(lambda _c=False, t=nm: self.ed_rmodel.setText(t))
        menu.exec(self.btn_fetch.mapToGlobal(self.btn_fetch.rect().bottomLeft()))
        self.status("拉到 %d 个模型，点一个填进去" % len(names))

    def on_provider_changed(self, *_):
        d = self.cb_provider.currentData() or ("", "")
        if d[0]:
            self.ed_base.setText(d[0])
        if d[1]:
            self.ed_rmodel.setText(d[1])
        # 内置厂商的地址是固定的，锁住免得改错；「其他」才放开
        self.ed_base.setReadOnly(bool(d[0]))

    def on_backend_changed(self, *_):
        # ★ 轻量版里「本地模型」被移除了，只剩一项；
        #   如果 currentData() 返回空，会误判成本地，把 Key 锁死。
        _d = self.cb_backend.currentData()
        if _d is None and self.cb_backend.count() >= 1:
            _d = self.cb_backend.itemData(0)
        cloud = (_d == "openai")
        # 只有一个选项 = 这个版本只支持这一种，永远不要禁用输入框
        if self.cb_backend.count() <= 1:
            self.ed_key.setEnabled(True)
            self.ed_base.setEnabled(True)
            self.ed_rmodel.setEnabled(True)
            self.ed_rmodel.setVisible(True)
        self.ed_key.setEnabled(cloud)
        self.ed_base.setEnabled(cloud)
        self.ed_rmodel.setEnabled(cloud)
        self.ed_rmodel.setVisible(cloud)
        if cloud and not self.ed_base.text().strip():
            self.on_provider_changed()
        # 本地：显示「本地模型」；云端：显示你要用的模型名
        self.cb_model.setVisible(not cloud)

    @staticmethod
    def _clean_key(t):
        """API Key 只该是一串 ASCII。粘错东西（比如整段文章）时要挡住，
        否则会一直"连不上"，而且没人知道为什么。"""
        t = (t or "").strip()
        if not t:
            return ""
        if len(t) > 200 or any(ord(c) > 127 or c in "\r\n\t" for c in t):
            return None          # None = 不合法
        return t

    def save_cfg_ui(self):
        try:
            m = self.cb_model.currentData()
            backend = self.cb_backend.currentData() or "ollama"
            _key = self._clean_key(self.ed_key.text())
            if _key is None:
                self.status("API Key 不像 Key（太长了或含中文），先清空再粘一次")
                return
            kw = {"temperature": self.sp_temp.value(),
                  "context": self.sp_ctx.value(),
                  "backend": backend,
                  "api_key": _key or "",
                  # ★ max_tokens 必须原样带回去：save_cfg_ui 不写它，
                  #   某些 save_cfg 实现会把它重置成默认值。
                  "max_tokens": int((llm.load_cfg() or {}).get("max_tokens")
                                    or 8192),
                  "model": (self.ed_rmodel.text().strip() or "qwen2.5:7b"),
                  # ★ 本地模式必须写死本机地址。以前无论哪个模式都从
                  #   「接口地址」框里取，你填过 DeepSeek 就一直带着它，
                  #   程序拿云端地址去探本地端口，永远报"本地模型未就绪"。
                  "base_url": ("http://127.0.0.1:11434"
                               if backend == "ollama"
                               else (self.ed_base.text().strip()
                                     or "http://127.0.0.1:11434"))}
            # ★ 只有本地模式才用下拉里的模型名。
            #   云端模式下这里一覆盖，用户填的 deepseek-flash 就变成 qwen2.5:7b，
            #   发出去必然 400。
            if m and backend != "openai":
                kw["model"] = m
            llm.save_cfg(**kw)
            # ★ 提示必须显示真正存进去的模型名。以前固定显示本地下拉那个
            #   qwen，看着像"每次保存都变成 qwen"，实际上云端名存对了。
            _shown = ((self.ed_rmodel.text().strip() or "（未填）")
                      if backend == "openai" else (m or "-"))
            self.status("配置已保存：%s · 随机度 %.2f · 记忆 %d 字"
                        % (_shown, self.sp_temp.value(), self.sp_ctx.value()))
        except Exception as e:
            self.status("保存配置失败：%s" % e)

    def reload_docs_list(self):
        self.cb_doc.clear()
        if not self.slug:
            return
        d = os.path.join(BOOKS, self.slug)
        try:
            names = [f for f in sorted(os.listdir(d))
                     if f.endswith(".md") and os.path.isfile(os.path.join(d, f))]
        except Exception:
            names = []
        for n in names:
            self.cb_doc.addItem(n, n)

    def save_doc(self):
        """把编辑框里的内容写回当前这份 .md —— 覆盖前留底。"""
        import os as _os
        import shutil as _sh
        import time as _t
        name = self.cb_doc.currentData()
        if not (self.slug and name):
            self.status("先选一份文件")
            return
        try:
            d = _os.path.join(BOOKS, self.slug)
            p = _os.path.join(d, name)
            if _os.path.exists(p):
                bk = _os.path.join(d, "_bak")
                _os.makedirs(bk, exist_ok=True)
                stem = name[:-3] if name.endswith(".md") else name
                _sh.copy2(p, _os.path.join(
                    bk, "%s.%s.md" % (stem, _t.strftime("%Y%m%d-%H%M%S"))))
            open(p, "w", encoding="utf-8").write(self.txt_outline.toPlainText())
            self.status("已保存 %s（旧版在 _bak/）" % name)
        except Exception as e:
            self.status("保存失败：%s" % e)

    def open_doc(self):
        name = self.cb_doc.currentData()
        if not (self.slug and name):
            return
        txt = read_doc(self.slug, name)
        self.txt_outline.setPlainText(txt or "（空）")
        self.status("已打开 %s（%d 字）" % (name, bare_len(txt)))

    def reload_docs(self):
        if not self.slug:
            return
        self.reload_docs_list()
        self.txt_bible.setPlainText(read_doc(self.slug, "bible.md") or "（没有 bible.md）")
        self.txt_outline.setPlainText(read_doc(self.slug, "outline.md") or "（没有 outline.md）")

    def open_learn(self):
        """学习与训练：从教程提规则 / 把稿子导成训练集。"""
        try:
            d = LearnDialog(self)
            d.exec()
        except Exception as e:
            self.status("打开学习面板失败：%s" % e)

    def import_chapter(self):
        """把已经写好的章节导入进来，后面生成正文会把它当上文。"""
        if not self.slug:
            self.status("先选一本书")
            return
        from PySide6.QtWidgets import QFileDialog
        p, _ = QFileDialog.getOpenFileName(
            self, "选一个章节文件（.md / .txt）", "",
            "文本文件 (*.md *.txt);;所有文件 (*.*)")
        if not p:
            return
        txt = None
        for enc in ("utf-8", "utf-8-sig", "gbk"):
            try:
                with open(p, "r", encoding=enc) as f:
                    txt = f.read()
                break
            except Exception:
                continue
        if txt is None:
            self.status("这个文件读不出来（编码不对）")
            return
        d = os.path.join(BOOKS, self.slug, "chapters")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception as e:
            self.status("建章节目录失败：%s" % e)
            return
        ns = []
        for f in os.listdir(d):
            m = re.match(r"第\s*(\d+)\s*章", f)
            if m:
                ns.append(int(m.group(1)))
        base = os.path.basename(p)
        m = re.search(r"第\s*(\d+)\s*章\s*(.*)", base)
        if m:
            num, tail = int(m.group(1)), m.group(2)
        else:
            num = (max(ns) + 1) if ns else 1
            tail = os.path.splitext(base)[0]
        tail = re.sub(r"\.(md|txt)$", "", (tail or "").strip(), flags=re.I)
        fn = "第%d章 %s.md" % (num, tail or "已导入")
        target = os.path.join(d, fn)
        try:
            with open(target, "w", encoding="utf-8") as f:
                f.write(txt)
        except Exception as e:
            self.status("写文件失败：%s" % e)
            return
        self.reload_chapters(keep_n=num)
        self.status("已导入：%s（%d 字）。下一章直接点「新建章节」再「生成正文」就行"
                    % (fn, len(txt)))

    def new_chapter(self):
        """真的建一个新的空章节文件，编号接着现有的往下排。"""
        if not self.slug:
            self.status("先选一本书")
            return
        d = os.path.join(BOOKS, self.slug, "chapters")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception as e:
            self.status("建章节目录失败：%s" % e)
            return
        ns = []
        for f in os.listdir(d):
            m = re.match(r"第\s*(\d+)\s*章", f)
            if m:
                ns.append(int(m.group(1)))
        n = (max(ns) + 1) if ns else 1
        p = os.path.join(d, "第%d章 未命名.md" % n)
        while os.path.exists(p):
            n += 1
            p = os.path.join(d, "第%d章 未命名.md" % n)
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write("")
        except Exception as e:
            self.status("建章节文件失败：%s" % e)
            return
        self.reload_chapters(keep_n=n)
        self.status("已新建第 %d 章（空文件，可以直接写，也可以点生成正文）" % n)

    def reload_chapters(self, keep_n=None):
        self.win.cb_ch.blockSignals(True)
        self.win.cb_ch.clear()
        rows = list_chapters(self.slug)
        self._total_wc = sum(wc for _, _, wc in rows)
        for n, fn, wc in rows:
            self.win.cb_ch.addItem("第 %d 章（%d 字）" % (n, wc), (n, fn))
        self.win.cb_ch.blockSignals(False)
        if rows:
            i = 0
            if keep_n is not None:
                for k, (n, _f, _w) in enumerate(rows):
                    if n == keep_n:
                        i = k
                        break
            self.win.cb_ch.setCurrentIndex(i)
            self.open_chapter()
        else:
            self.editor.clear()
            self.txt_ai.setPlainText("（这个目录里还没有章节）")
            self.chap_n = self.chap_file = None
            self._cur_wc = 0

    def reload_models(self, keep=None):
        """开发版列出本机所有模型（能挑 4B 调试）；
        发行版只显示「本地模型」，不暴露具体型号给买家。"""
        names = []
        try:
            base = (llm.load_cfg() or {}).get("base_url") \
                or "http://127.0.0.1:11434"
            if "127.0.0.1" in base or "localhost" in base:
                d = llm._get(base.rstrip("/") + "/api/tags", {}, 3)
                names = [m.get("name") for m in (d.get("models") or [])
                         if m.get("name")]
        except Exception:
            names = []
        # 打包后 frozen=True，但你调试时也要能挑模型：
        # 在 settings.json 里写 "dev_mode": true 就打开
        # ★ 用独立文件标记，别放 settings.json —— 那个会被「保存配置」整个覆盖
        dev = (not getattr(sys, "frozen", False)) or os.path.exists(
            os.path.join(HERE, "dev_mode.txt"))

        cfg = llm.load_cfg() or {}
        want = str(cfg.get("model") or "qwen2.5:7b")
        self.cb_model.blockSignals(True)
        self.cb_model.clear()
        if dev and not names:
            # ollama 还没起来时查不到列表，至少把配置里的模型名显示出来，
            # 不然开发版看着像"选不了模型"
            names = [want]
        if dev and names:
            for nm in sorted(names):
                self.cb_model.addItem(nm, nm)
            want = keep or want
            k = self.cb_model.findData(want)
            self.cb_model.setCurrentIndex(k if k >= 0 else 0)
        else:
            if "qwen2.5" not in want:
                want = "qwen2.5:7b"
            self.cb_model.addItem("本地模型", want)
            self.cb_model.setCurrentIndex(0)
        self.cb_model.blockSignals(False)
    def on_model_changed(self, *_):
        m = self.cb_model.currentData()
        if not m:
            return
        try:
            llm.save_cfg(model=m)
            self.status("模型已切到 %s" % m)
        except Exception as e:
            self.status("切模型失败：%s" % e)

    # ── 章节 ────────────────────────────────────────────────────────
    def open_chapter(self):
        if self.dirty:
            self.save(quiet=True)
        data = self.win.cb_ch.currentData()
        if not data:
            return
        n, fn = data
        self.chap_n, self.chap_file = n, fn
        p = os.path.join(BOOKS, self.slug, "chapters", fn)
        try:
            txt = open(p, encoding="utf-8").read()
        except Exception as e:
            QMessageBox.warning(self, "打不开", str(e))
            return
        self._loading = True
        self.editor.setPlainText(txt)
        self._loading = False
        self.dirty = False
        self._cur_wc = bare_len(txt)
        ai = fn[:-3] + ".ai.md"
        ap = os.path.join(BOOKS, self.slug, "chapters", ai)
        self.txt_ai.setPlainText(
            open(ap, encoding="utf-8").read() if os.path.exists(ap)
            else "（还没有 AI 初稿 —— 点「生成正文」出一稿）")
        self.update_status()

    def goto_delta(self, d):
        i = self.win.cb_ch.currentIndex() + d
        if 0 <= i < self.win.cb_ch.count():
            self.win.cb_ch.setCurrentIndex(i)
            self.open_chapter()

    # ── 编辑 / 保存 ─────────────────────────────────────────────────
    def on_edit(self):
        if self._loading:
            return
        self.dirty = True
        self.update_status()

    def update_status(self):
        n = bare_len(self.editor.toPlainText())
        total = max(self._total_wc - self._cur_wc + n, 0)
        mark = "● 未保存" if self.dirty else "已保存"
        self.status("第 %s 章 · 本章 %d 字 · 全书 %d 字 · %s"
                    % (self.chap_n if self.chap_n else "-", n, total, mark))

    def save(self, quiet=False):
        if not (self.slug and self.chap_file):
            return False
        p = os.path.join(BOOKS, self.slug, "chapters", self.chap_file)
        try:
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(self.editor.toPlainText())
            os.replace(tmp, p)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))
            return False
        self.dirty = False
        n = bare_len(self.editor.toPlainText())
        self._total_wc += n - self._cur_wc
        self._cur_wc = n
        self.update_status()
        if not quiet:
            self.status(self.win.status.currentMessage() + "　·　已写入磁盘")
        return True

    def autosave(self):
        if self.dirty:
            self.save(quiet=True)

    # ── 红线 ────────────────────────────────────────────────────────
    def check_redline(self):
        t = self.editor.toPlainText()
        if not t.strip():
            self.status("正文是空的")
            return
        try:
            res = redline.check(t, [], None, None)
            rep = redline.report(res)
        except Exception as e:
            self.status("红线跑挂了：%s" % e)
            return
        self.win.show_report(rep)
        self._highlight_issues(res)
        p0 = sum(1 for i in res["issues"] if i[0] == "P0")
        self.status("红线查完：P0 %d 项（清零才准发）" % p0)

    def _highlight_issues(self, res):
        """把红线报出来的词在右栏高亮，波浪线标出来，便于直接改。"""
        import re as _re
        from PySide6.QtGui import QTextCharFormat, QTextCursor, QColor
        from PySide6.QtWidgets import QTextEdit
        toks = set()
        for it in (res.get("issues") or []):
            msg = it[1] if len(it) > 1 else ""
            for m in _re.finditer(r"「([^」]{1,24})」", msg or ""):
                t = m.group(1).strip()
                if t:
                    toks.add(t)
        if not toks:
            self.editor.setExtraSelections([])
            return 0, 0
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(255, 226, 138))
        fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
        fmt.setUnderlineColor(QColor(200, 60, 60))
        text = self.editor.toPlainText()
        sels, hits = [], 0
        for t in toks:
            k = text.find(t)
            while k >= 0:
                cur = QTextCursor(self.editor.document())
                cur.setPosition(k)
                cur.setPosition(k + len(t), QTextCursor.MoveMode.KeepAnchor)
                sel = QTextEdit.ExtraSelection()
                sel.cursor = cur
                sel.format = fmt
                sels.append(sel)
                hits += 1
                k = text.find(t, k + len(t))
        self.editor.setExtraSelections(sels)
        self.status("已高亮 %d 处（%d 个词），波浪线是要改的地方" % (hits, len(toks)))
        return len(toks), hits

    def show_brief(self):
        if not (self.slug and self.chap_n):
            self.status("先选一章")
            return
        try:
            bible = forge.read("bible.md", self.slug)
            outline = forge.read("outline.md", self.slug)
            rows = {r["n"]: r for r in forge.parse_outline(outline)}
            ctx = forge.prev_context(self.chap_n, self.slug)
            card = forge.build_prompt(rows[self.chap_n], bible, ctx, self.slug)
        except Exception as e:
            self.status("组任务卡失败：%s" % e)
            return
        self.txt_ai.setPlainText("【本章任务卡（模型实际读到的）】\n\n" + card)
        self.status("任务卡 %d 字" % len(card))

    # ── 生成正文 ────────────────────────────────────────────────────
    def _body_only(self, t):
        """右栏只放正文：去掉标题行和 <!-- 状态 --> 这类元信息。
        状态已经由 forge 写进人物库了，留在稿子里是噪音。"""
        t = re.sub(r"<!--.*?-->", "", t or "", flags=re.S)
        t = re.sub(r"(?m)^\s*#{1,6}\s*第\s*\d+\s*章[^\n]*$", "", t)
        t = re.sub(r"(?m)^\s*#{1,6}\s*$", "", t)
        t = re.sub(r"\n{3,}", "\n\n", t)
        return t.strip()

    def learn_now(self):
        """点一下就把右栏这篇（你改过的）收进训练集，不弹窗。"""
        if not (getattr(self, "slug", "") and getattr(self, "chap_n", 0)):
            self.status("先选一本书和一章，再点学习")
            return
        txt = (self.editor.toPlainText() or "").strip()
        if not txt:
            self.status("右栏是空的，没什么可学")
            return
        body = re.sub(r"(?m)^#{1,6}\s*.*$", "", txt)
        body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        nc = len(re.findall(r"[\u4e00-\u9fa5]", body))
        if nc < 200:
            self.status("这篇只有 %d 个汉字，太短了，学不出东西" % nc)
            return
        ins = "按《%s》的风格写第 %d 章的正文。约 %d 字。" % (self.slug, self.chap_n, nc)
        path = os.path.join(NOVEL_DIR, "corpus", "train.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"instruction": ins, "output": body},
                                   ensure_ascii=False) + "\n")
            total = sum(1 for _ in open(path, encoding="utf-8"))
        except Exception as e:
            self.status("写入训练集失败：%s" % e)
            return
        self.status("已学习第 %d 章（%d 汉字）· 累计 %d 条 → corpus\\train.jsonl"
                    % (self.chap_n, nc, total))

    def generate(self):
        if not (self.slug and self.chap_n):
            self.status("先选一章")
            return
        if self.dirty and not self.save(quiet=True):
            return
        self.save_cfg_ui()          # ★ 先把界面配置落盘，避免用了旧后端
        n, slug = self.chap_n, self.slug
        cfg = llm.load_cfg() or {}
        temp = self.sp_temp.value()
        self.status("正在生成第 %d 章…（可能要几分钟）" % n)

        def job():
            llm.save_cfg(temperature=temp)
            a = argparse.Namespace(n=n, slug=slug, dry=False, segment=4,
                                   segwords=550, target=1800,
                                   with_samples=True, plan=False)
            # ★ 把 forge 的 print 抓下来：exe 是 --windowed，没有控制台，
            #   那些「第 i/4 段」「拼完 N 字」全看不见，字数不够就没法定位。
            import io as _io
            import contextlib as _ctx
            buf = _io.StringIO()
            with _ctx.redirect_stdout(buf):
                rc = forge.cmd_write(a)
            return (rc, buf.getvalue())

        def bad(m):
            last = (m or "").strip().splitlines()[-1] if (m or "").strip() else "未知错误"
            self.status("生成失败：" + last)
            try:
                self.chat.appendPlainText("【生成失败】" + (m or last)[-800:])
            except Exception:
                pass

        def done(r):
            rc, out = r if isinstance(r, tuple) else (r, "")
            for ln in (out or "").splitlines():
                t = ln.strip()
                if t:
                    try:
                        self.chat.appendPlainText("〔forge〕" + t[:200])
                    except Exception:
                        pass
            self._after_generate(rc)

        self.run_async(job, on_ok=done, on_err=bad)

    def _after_generate(self, rc):
        msg = ("生成完成" if rc == 0 else
               "没过闸门（返回 %s）—— 只写了 .draft.md，主文件没动" % rc)
        self.status(msg)
        # ★ 结果也写进聊天框：状态栏会变，聊天框不会，能翻回去看
        try:
            self.chat.appendPlainText("【结果】" + msg)
        except Exception:
            pass
        self.reload_chapters(keep_n=self.chap_n)

    # ── 聊天 / 提交改稿 ────────────────────────────────────────────
    def do_chat(self):
        self.save_cfg_ui()      # ★ 聊天也必须先把界面配置落盘，
                                #   否则会用旧的模型名（比如本地 qwen）
        q = self.ed_chat.text().strip()
        if not q:
            self.status("先在下面输入框里写点什么")
            return
        if not (self.slug and self.chap_n):
            self.status("先选一本书和一章，才能发")
            return
        self.ed_chat.clear()
        ctx = self.editor.toPlainText()[:6000]
        self.chat.appendPlainText("\n【我】" + q)
        self.status("模型在答…")
        temp = self.sp_temp.value()

        def job():
            return llm.complete(
                "你在跟这本书的作者改稿。只回答问题、给具体改法，"
                "不整篇重写，不复述原文。",
                "【本章原文】\n%s\n\n【他问】%s" % (ctx, q),
                temperature=temp, max_tokens=2000)

        def ok(r):
            self._last_reply = r or ""
            self.chat.appendPlainText("\n【模型】" + (r or "（空回复）"))
            self.status("答完了（%d 字）" % bare_len(r))

        self.run_async(job, on_ok=ok,
                       on_err=lambda m: self.status("聊天失败：%s" % m.splitlines()[-1]))

    def do_ask(self):
        inst = self.ed_ask.text().strip()
        if not inst:
            self.status("先写改稿指令")
            return
        if not (self.slug and self.chap_n):
            self.status("先选一章")
            return
        body = self.editor.toPlainText()
        temp = self.sp_temp.value()
        self.status("提交给模型…")

        def job():
            return llm.complete(
                "按指令改稿。只输出改后的完整正文，不许解释、不许列点、"
                "不许加前后缀，保持分段。",
                "【指令】%s\n\n【原文】\n%s\n\n【改后全文】" % (inst, body),
                temperature=temp, max_tokens=8192)

        def ok(r):
            if not r or not r.strip():
                self.status("模型返回空")
                return
            self._last_reply = r
            if QMessageBox.question(
                    self, "覆盖你的稿？",
                    "模型改完 %d 字，替换编辑框？（可 Ctrl+Z 撤销）" % bare_len(r),
                    QMessageBox.StandardButton.Ok
                    | QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Ok:
                _clean_r = self._body_only(r)
                self.editor.setPlainText(_clean_r)
                # 生成完立刻自检并在正文里高亮，不用再点按钮
                try:
                    _res = redline.check(_clean_r, [], None, None)
                    self._highlight_issues(_res)
                except Exception:
                    pass
                self.status("已替换（未保存）")

        self.run_async(job, on_ok=ok,
                       on_err=lambda m: self.status("改稿失败：%s" % m.splitlines()[-1]))

    def do_undo(self):
        if self._last_reply:
            self.editor.setPlainText(self._last_reply)
            self.status("已把模型那版放回编辑框")
        else:
            self.editor.undo()

    def export(self):
        out = self.win.tmp_dir()
        self.status("正在导出…")
        self.run_async(lambda: forge.export_pairs(self.slug),
                       on_ok=lambda r: self.status("导出完成：%s" % (r,)))

    def do_ping(self):
        self.run_async(llm.ping,
                       on_ok=lambda r: self.status(
                           "连通：" + ("OK " if r[0] else "FAILED ") + str(r[1])))


# ══════════════════════════════════════════════════════════════════
FIELDS = [("genre", "题材"), ("mf", "男频/女频"), ("words", "篇幅"),
          ("read_tags", "读者标签"), ("content_tags", "内容标签"),
          ("brief", "一句话故事"), ("protagonist", "主角"),
          ("romance", "感情线")]


class LaunchView(QWidget, AsyncMixin):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self._workers = []
        self.book = {}
        self.rules = []
        self.skel = []
        self.beats = []
        self.rows = []
        self._adopt_kind = ""
        self._chain = []
        self._groups = []

        # 四个内联候选（挂在各自字段下面）
        self.cand = {}
        for key in ("cheat", "hook", "title", "intro", "skel", "beats", "rows"):
            w = CandidateRows()
            w.setVisible(False)
            # 简介是整段文字，给足高度；其余保持紧凑
            w.setMaximumHeight(360 if key == "intro" else 190)
            self.cand[key] = w

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent;")
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        cols = QHBoxLayout(inner)
        cols.setContentsMargins(16, 16, 16, 16)
        cols.setSpacing(14)
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        # ══ 左：① 立项信息 ══
        g1 = QGroupBox("① 立项信息")
        v1 = QVBoxLayout(g1)
        v1.setSpacing(7)
        self.btn_all = mkpri(QPushButton("一键乱写"))
        self.btn_all.clicked.connect(self.do_roll_all)
        r0 = QHBoxLayout()
        r0.addWidget(self.btn_all)
        hint = QLabel("一个字都不用填")
        hint.setProperty("h", "1")
        r0.addWidget(hint)
        r0.addStretch(1)
        v1.addLayout(r0)

        self.f = {}
        for key, label in (("genre", "题材"), ("words", "目标字数"),
                           ("read_tags", "阅读标签"), ("content_tags", "内容标签"),
                           ("brief", "想写的一句话"), ("protagonist", "主角"),
                           ("romance", "感情线")):
            v1.addWidget(QLabel(label))
            e = QLineEdit()
            self.f[key] = e
            v1.addWidget(e)
        self.f["brief"].setPlaceholderText("例：一个被开除的程序员，决定把公司的账本翻过来")
        self.f["protagonist"].setPlaceholderText("例：25 岁程序员，最在乎妹妹，缺钱")
        self.f["romance"].setPlaceholderText("例：她护了他三年，他到最后才知道")

        v1.addWidget(QLabel("频道"))
        self.cb_mf = QComboBox()
        self.cb_mf.addItem("男频", "男频")
        self.cb_mf.addItem("女频", "女频")
        v1.addWidget(self.cb_mf)

        v1.addWidget(QLabel("金手指规则"))
        r1 = QHBoxLayout()
        b = QPushButton("AI 生成")
        b.clicked.connect(self.do_cheat)
        b2 = QPushButton("换一批")
        b2.clicked.connect(self.do_cheat)
        r1.addWidget(b)
        r1.addWidget(b2)
        r1.addStretch(1)
        v1.addLayout(r1)
        self.e_cheat = QLineEdit()
        self.e_cheat.setPlaceholderText("自己写一条：例——只能看穿，不能修改规则本身")
        r2 = QHBoxLayout()
        r2.addWidget(self.e_cheat, 1)
        b3 = QPushButton("加一条")
        b3.clicked.connect(self.add_cheat_line)
        r2.addWidget(b3)
        v1.addLayout(r2)
        v1.addWidget(self.cand["cheat"])
        v1.addWidget(self.lbl_picked())
        v1.addStretch(1)

        # ══ 右：② 生成 ══
        g2 = QGroupBox("③ 结构")
        v2 = QVBoxLayout(g2)
        v2.setSpacing(7)
        b_rule = QPushButton("查看／补写本题材规则")
        b_rule.clicked.connect(self.view_genre_rules)
        v2.addWidget(b_rule)
        self.t_rule = QPlainTextEdit()
        self.t_rule.setReadOnly(True)
        self.t_rule.setMaximumHeight(90)
        self.t_rule.setVisible(False)
        v2.addWidget(self.t_rule)
        self.t_rule_own = QPlainTextEdit()
        self.t_rule_own.setMaximumHeight(60)
        self.t_rule_own.setVisible(False)
        v2.addWidget(self.t_rule_own)
        b_rs = QPushButton("保存我补写的")
        b_rs.clicked.connect(self.save_genre_rules)
        b_rs.setVisible(False)
        self._b_save_rule = b_rs
        v2.addWidget(b_rs)

        gA = QGroupBox("② 生成")
        vA = QVBoxLayout(gA)
        vA.setSpacing(7)

        # 钩子
        vA.addWidget(QLabel("一句话钩子"))
        r = QHBoxLayout()
        b = QPushButton("生成钩子")
        b.clicked.connect(self.do_hook)
        b2 = QPushButton("换一批")
        b2.clicked.connect(self.do_hook)
        r.addWidget(b)
        r.addWidget(b2)
        r.addStretch(1)
        vA.addLayout(r)
        vA.addWidget(QLabel("当前钩子"))
        self.e_hook = QLineEdit()
        self.e_hook.setPlaceholderText("例：他把离职证明折成纸飞机，扔进了前台的垃圾桶")
        vA.addWidget(self.e_hook)
        vA.addWidget(self.cand["hook"])

        # 书名
        vA.addWidget(QLabel("书名"))
        r = QHBoxLayout()
        b = QPushButton("AI 生成书名")
        b.clicked.connect(self.do_title)
        b2 = QPushButton("换一批")
        b2.clicked.connect(self.do_title)
        r.addWidget(b)
        r.addWidget(b2)
        r.addStretch(1)
        vA.addLayout(r)
        vA.addWidget(QLabel("当前书名"))
        r = QHBoxLayout()
        self.e_title = QLineEdit()
        self.e_title.setPlaceholderText("点上面任意一条「采用」，也可以直接在这里写")
        b3 = QPushButton("清空重挑")
        b3.clicked.connect(lambda: self.e_title.clear())
        r.addWidget(self.e_title, 1)
        r.addWidget(b3)
        vA.addLayout(r)
        vA.addWidget(self.cand["title"])

        # 简介
        vA.addWidget(QLabel("简介"))
        r = QHBoxLayout()
        b = QPushButton("生成简介")
        b.clicked.connect(self.do_intro)
        b2 = QPushButton("换一批")
        b2.clicked.connect(self.do_intro)
        r.addWidget(b)
        r.addWidget(b2)
        r.addStretch(1)
        vA.addLayout(r)
        vA.addWidget(QLabel("当前简介"))
        self.e_intro = QPlainTextEdit()
        self.e_intro.setMaximumHeight(90)
        self.e_intro.setPlaceholderText(
            "点「生成简介」出 3 版挑一版，也可以自己写。读者在书架页看到的就是这段")
        vA.addWidget(self.e_intro)
        vA.addWidget(self.cand["intro"])

        # 大纲
        v2.addWidget(QLabel("大纲（全书 5 段）"))
        r = QHBoxLayout()
        b = QPushButton("生成大纲")
        b.clicked.connect(self.do_skel)
        r.addWidget(b)
        h = QLabel("简介定完再生成——细纲和章纲都长在它身上")
        h.setProperty("h", "1")
        r.addWidget(h, 1)
        v2.addLayout(r)
        v2.addWidget(self.cand["skel"])

        # 细纲
        v2.addWidget(QLabel("细纲（逐章一句话）"))
        r = QHBoxLayout()
        r.addWidget(QLabel("从"))
        self.sp_b1 = QSpinBox(); self.sp_b1.setRange(1, 4000); self.sp_b1.setValue(1)
        r.addWidget(self.sp_b1)
        r.addWidget(QLabel("到"))
        self.sp_b2 = QSpinBox(); self.sp_b2.setRange(1, 4000); self.sp_b2.setValue(30)
        r.addWidget(self.sp_b2)
        r.addWidget(QLabel("章"))
        b = QPushButton("生成细纲")
        b.clicked.connect(self.do_beats)
        r.addWidget(b)
        r.addStretch(1)
        v2.addLayout(r)
        h = QLabel("按大纲的段切着写，写到后面才不会换人换题材")
        h.setProperty("h", "1")
        v2.addWidget(h)
        v2.addWidget(self.cand["beats"])

        # 章纲
        v2.addWidget(QLabel("章纲"))
        r = QHBoxLayout()
        r.addWidget(QLabel("从"))
        self.sp_o1 = QSpinBox(); self.sp_o1.setRange(1, 4000); self.sp_o1.setValue(1)
        r.addWidget(self.sp_o1)
        r.addWidget(QLabel("到"))
        self.sp_o2 = QSpinBox(); self.sp_o2.setRange(1, 4000); self.sp_o2.setValue(10)
        r.addWidget(self.sp_o2)
        r.addWidget(QLabel("章"))
        b = QPushButton("生成章纲")
        b.clicked.connect(self.do_outline)
        b2 = QPushButton("重写章节名")
        b2.clicked.connect(self.do_chap)
        r.addWidget(b)
        r.addWidget(b2)
        r.addStretch(1)
        v2.addLayout(r)
        h = QLabel("有细纲时章纲只照细纲扩写；没有也能出，但更容易跑偏。")
        h.setProperty("h", "1")
        v2.addWidget(h)
        self.e_tone = QLineEdit()
        self.e_tone.setPlaceholderText("例：卷一只写公司内，不出城")
        v2.addWidget(self.e_tone)
        v2.addWidget(self.cand["rows"])
        v2.addStretch(1)

        # ══ 底：③ 落盘 ══
        g3 = QGroupBox("④ 落盘")
        v3 = QVBoxLayout(g3)
        v3.addWidget(QLabel("新书目录名"))
        r = QHBoxLayout()
        self.e_slug = QLineEdit()
        self.e_slug.setPlaceholderText("英文或中文，别用符号")
        self.btn_create = mkpri(QPushButton("建书落盘"))
        self.btn_create.clicked.connect(self.do_create)
        self.btn_imp = QPushButton("导入已有设定")
        self.btn_imp.setToolTip("读一份「第X、…」结构的设定 md，抽出人物和技能直接建书")
        self.btn_imp.clicked.connect(self.do_import)
        v3.addWidget(self.btn_imp)   # ★ 之前漏了这行，按钮建了没进布局
        r.addWidget(self.e_slug, 1)
        r.addWidget(self.btn_create)
        v3.addLayout(r)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(120)
        v3.addWidget(self.log)

        self._groups = [g1, gA, g2, g3]
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(g1)
        lv.addWidget(gA)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(g2)
        rv.addWidget(g3)
        cols.addWidget(left, 5)
        cols.addWidget(right, 5)

    def lbl_picked(self):
        if not hasattr(self, "_lbl_picked"):
            self._lbl_picked = QLabel("已选金手指：0 条")
            self._lbl_picked.setProperty("h", "1")
        return self._lbl_picked

    def add_cheat_line(self):
        t = self.e_cheat.text().strip()
        if not t:
            return
        if t not in self.rules:
            self.rules.append(t)
        self.lbl_picked().setText("已选金手指：%d 条" % len(self.rules))
        self.e_cheat.clear()
        self.log.appendPlainText("加了一条金手指：" + t[:50])

    def status(self, msg):
        self.win.status.showMessage(msg)

    def _book(self):
        b = dict(self.book)
        for k, e in self.f.items():
            b[k] = e.text().strip()
        b["mf"] = self.cb_mf.currentData()
        b["hook"] = self.e_hook.text().strip()
        b["title"] = self.e_title.text().strip()
        b["intro"] = self.e_intro.toPlainText().strip()
        b["rules"] = list(self.rules)
        return b

    def _busy(self, on):
        # ★ 只禁用建书；一键乱写不要跟着变灰，否则点别的按钮时
        #   它闪一下，看起来像又点了一次一键乱写。
        self.btn_create.setEnabled(not on)

    def _need_model(self):
        """★ 模型不通就别生成。以前 gen_roll 会静默退回纯随机，
        界面看起来像成功，其实是模板拼的——这是在骗用户。"""
        ok, msg = (False, "")
        try:
            ok, msg = llm.ping()
        except Exception as e:
            ok, msg = False, str(e)
        if not ok:
            self.status("模型没连通：%s" % msg)
            try:
                self.log.appendPlainText("!!! 模型没连通：%s" % msg)
            except Exception:
                pass
        return ok

    def _sync_cfg(self):
        """★ 生成前先把界面上的模型配置存进去。
        不存的话，填了 Key 但还在用旧配置（本地模式），
        模型调用失败会悄悄退回纯随机，主角和感情线就是空的。"""
        try:
            self.win.writer.save_cfg_ui()
        except Exception:
            pass

    def _gen(self, fn, mode, handler):
        self._sync_cfg()
        if not self._need_model():
            return
        self.status("模型在出 %s…" % mode)
        self.log.appendPlainText(">>> %s" % mode)

        def bad(m):
            self.log.appendPlainText("!!! 失败：%s" % m)
            self.status("失败：%s" % (m.splitlines()[-1] if m else ""))

        self.run_async(fn, on_ok=handler, on_err=bad, busy=self._busy)

    def do_roll_all(self):
        self._chain = ["roll"]        # 只乱写立项；其余是线性链，必须等采用
        self.log.appendPlainText("======== 一键乱写 ========")
        self._chain_step()

    def _fill_from_roll(self, r):
        if not isinstance(r, dict) or not r.get("ok"):
            return
        b = r.get("book") or {}
        for k, e in self.f.items():
            if b.get(k) not in (None, ""):
                e.setText(str(b[k]))
        if b.get("mf"):
            i = self.cb_mf.findData(str(b["mf"]))
            if i >= 0:
                self.cb_mf.setCurrentIndex(i)
        self.book = b
        self.log.appendPlainText("[1/5] 掷出：%s / %s / %s" % (
            b.get("genre", ""), b.get("mf", ""), b.get("words", "")))

    def _chain_gen(self, fn, mode, handler):
        self._sync_cfg()
        if not self._need_model():
            return
        self.status("一键乱写中：%s…" % mode)

        def bad(m):
            self.log.appendPlainText("!!! %s 失败" % mode)
            self._chain_step()

        self.run_async(fn, on_ok=handler, on_err=bad, busy=self._busy)

    def _chain_step(self):
        if not self._chain:
            self.status("一键乱写完成 —— 逐条点「采用」，再填目录名建书")
            self.log.appendPlainText("======== 完成 ========")
            return
        step = self._chain.pop(0)
        if step == "roll":
            def ok(r):
                try:
                    self._fill_from_roll(r)
                except Exception as e:
                    self.log.appendPlainText("!!! 填表出错：%r" % (e,))
                self._chain_step()

            self._chain_gen(
                lambda: launch.gen_roll(use_llm=True, with_cheat=False),
                "随机立项", ok)

        elif step == "cheat":
            def ok(r):
                ts = ([pick_text(x) for x in items_of(r, "items", "cheats")]
                      if isinstance(r, dict) else [])
                if ts:
                    self._show(ts, "金手指候选")
                    self._adopt_kind = "金手指候选"
                    self._adopt(ts[0])
                self._chain_step()
            self._chain_gen(lambda: launch.gen_cheat(self._book()), "金手指", ok)
        elif step == "hook":
            def ok(r):
                its = items_of(r, "items", "hooks") if isinstance(r, dict) else []
                if its:
                    self.e_hook.setText(pick_text(its[0]))
                    self._show([pick_text(x) for x in its], "钩子候选")
                self._chain_step()
            self._chain_gen(lambda: launch.gen_hook(self._book()), "钩子", ok)
        elif step == "title":
            def ok(r):
                its = items_of(r, "items", "titles") if isinstance(r, dict) else []
                if its:
                    self.e_title.setText(pick_text(its[0]))
                    self._show([pick_text(x) for x in its], "书名候选")
                self._chain_step()
            self._chain_gen(lambda: launch.gen_title(
                self._book(), None, True, self.e_hook.text().strip()), "书名", ok)
        elif step == "intro":
            def ok(r):
                its = items_of(r, "items", "intros") if isinstance(r, dict) else []
                if its:
                    self.e_intro.setPlainText(pick_text(its[0]))
                self._chain_step()
            self._chain_gen(lambda: launch.gen_intro(
                self._book(), None, True, self.e_title.text().strip(),
                self.e_hook.text().strip()), "简介", ok)
        else:
            self._chain_step()

    def do_cheat(self):
        def ok(r):
            ts = ([pick_text(x) for x in items_of(r, "items", "cheats")]
                  if isinstance(r, dict) else [])
            self._show(ts, "金手指候选")
            for w in (r.get("warn") or [] if isinstance(r, dict) else []):
                self.log.appendPlainText("安全前提：%s" % w)
        self._gen(lambda: launch.gen_cheat(self._book()), "金手指", ok)

    def do_hook(self):
        self._gen(lambda: launch.gen_hook(self._book()), "钩子",
                  lambda r: self._show([pick_text(x) for x in
                                        items_of(r, "items", "hooks")], "钩子候选"))

    def do_title(self):
        self._gen(lambda: launch.gen_title(
            self._book(), None, True, self.e_hook.text().strip()), "书名",
                  lambda r: self._show([pick_text(x) for x in
                                        items_of(r, "items", "titles")], "书名候选"))

    def do_intro(self):
        self._gen(lambda: launch.gen_intro(
            self._book(), None, True, self.e_title.text().strip(),
            self.e_hook.text().strip()), "简介",
                  lambda r: self._show([pick_text(x) for x in
                                        items_of(r, "items", "intros")], "简介候选"))

    def _show(self, texts, label):
        key = ("cheat" if "金手指" in label else
               "hook" if "钩子" in label else
               "title" if "书名" in label else "intro")
        self._adopt_kind = label
        rows = self.cand[key]
        rows.clear_rows()
        for t in texts:
            rows.add_row(t, self._adopt)
        rows.setVisible(bool(texts))
        self.log.appendPlainText("  %s：%d 条" % (label, len(texts)))
        self.status("%s 出了 %d 条" % (label, len(texts)))

    def _adopt(self, t, on=True):
        if not on:
            if t in self.rules:
                self.rules.remove(t)
                self.lbl_picked().setText("已选金手指：%d 条" % len(self.rules))
            self.log.appendPlainText("取消采用")
            return
        k = self._adopt_kind
        if "金手指" in k:
            if t not in self.rules:
                self.rules.append(t)
            self.e_cheat.setText(t)
            self.lbl_picked().setText("已选金手指：%d 条" % len(self.rules))
        elif "钩子" in k:
            self.e_hook.setText(t)
        elif "书名" in k:
            self.e_title.setText(t)
        elif "简介" in k:
            self.e_intro.setPlainText(t)
        else:
            return
        self.log.appendPlainText("  ← 已采用")

    def _setting_text(self):
        """读当前目录名对应的 bible.md（导入的书才有），给大纲/章纲当骨架。"""
        import os as _os
        slug = self.e_slug.text().strip()
        if not slug:
            return ""
        for base in (getattr(launch, "BOOKS", ""), None):
            if not base:
                continue
            p = _os.path.join(base, slug, "bible.md")
            if _os.path.exists(p):
                return open(p, encoding="utf-8").read()[:6000]
        return ""

    def _book_with_setting(self):
        b = self._book()
        st = self._setting_text()
        if st:
            b["setting"] = st
        return b

    def do_skel(self):
        def ok(r):
            sk = [x for x in items_of(r, "skel", "items", "rows") if isinstance(x, dict)]
            self.skel = sk
            self._show_struct("skel", [
                "%s｜%s｜%s｜%s" % (x.get("name", ""), x.get("span", ""),
                                    x.get("what", ""), x.get("knife", ""))
                for x in sk])
            self.log.appendPlainText("大纲：%d 段" % len(sk))
        self._gen(lambda: launch.gen_skeleton(
            self._book_with_setting(), self.e_title.text().strip(),
            self.e_intro.toPlainText().strip()), "大纲", ok)

    def do_beats(self):
        sk = list(self.skel)
        a, b = self.sp_b1.value(), self.sp_b2.value()

        def ok(r):
            bt = [x for x in items_of(r, "beats", "items", "rows") if isinstance(x, dict)]
            self.beats = bt
            self._show_struct("beats", [
                "第%s章｜%s" % (x.get("n", ""), x.get("beat", "")) for x in bt])
            self.log.appendPlainText("细纲：%d 条" % len(bt))
        self._gen(lambda: launch.gen_beats(
            self._book_with_setting(), sk, a, b, True,
            self.e_title.text().strip(), self.e_intro.toPlainText().strip()), "细纲", ok)

    def do_outline(self):
        bt, sk = list(self.beats), list(self.skel)
        a, b = self.sp_o1.value(), self.sp_o2.value()
        tone = self.e_tone.text().strip()

        def ok(r):
            rw = [x for x in items_of(r, "rows", "items") if isinstance(x, dict)]
            if rw:
                self.rows = rw
            self._show_struct("rows", [self._row_text(x) for x in self.rows])
            self.log.appendPlainText("章纲：%d 行" % len(self.rows))
        self._gen(lambda: launch.gen_outline(self._book_with_setting(), a, b, tone, True, bt, sk),
                  "章纲", ok)

    def do_chap(self):
        rows = list(self.rows)

        def ok(r):
            rw = [x for x in items_of(r, "rows", "items") if isinstance(x, dict)]
            if rw:
                self.rows = rw
            self._show_struct("rows", [self._row_text(x) for x in self.rows])
            self.log.appendPlainText("章名：%d 行" % len(self.rows))
        self._gen(lambda: launch.gen_chap_titles(self._book(), rows), "章名", ok)

    def _row_text(self, x):
        return "第%s章　%s\n　　%s　｜　钩子：%s" % (
            x.get("n", ""), x.get("title", ""),
            x.get("task", ""), x.get("hook", ""))

    def _show_struct(self, key, lines):
        rows = self.cand[key]
        rows.clear_rows()
        for t in lines:
            rows.add_plain(t)          # 只显示，不给采用按钮
        rows.setVisible(bool(lines))
        name = {"skel": "大纲", "beats": "细纲"}.get(key, "章纲")
        self.status("%s：%d 条" % (name, len(lines)))

    def fill_table(self, *_):
        which = self.cb_tbl.currentData()
        cols = {"skel": ["阶段", "章", "要什么／被谁挡", "段末那一刀"],
                "beats": ["章", "这一章的一句话"],
                "rows": ["章", "标题", "本章必办一件事", "章末钩子"]}[which]
        keys = {"skel": ["name", "span", "what", "knife"],
                "beats": ["n", "beat"],
                "rows": ["n", "title", "task", "hook"]}[which]
        data = {"skel": self.skel, "beats": self.beats, "rows": self.rows}[which]
        self.tbl.clear()
        self.tbl.setColumnCount(len(cols))
        self.tbl.setHorizontalHeaderLabels(cols)
        self.tbl.setRowCount(len(data))
        for r, it in enumerate(data):
            for c, k in enumerate(keys):
                self.tbl.setItem(r, c, QTableWidgetItem(str(it.get(k, ""))))
        self.tbl.resizeColumnsToContents()

    def view_genre_rules(self):
        genre = self.f["genre"].text().strip()
        mf = self.cb_mf.currentData() or ""
        show = not self.t_rule.isVisible()
        self.t_rule.setVisible(show)
        self.t_rule_own.setVisible(show)
        self._b_save_rule.setVisible(show)
        if not show:
            return
        try:
            m = rules_mod()
            rr = m.load_rules()
            keys = m._genre_keys({"genre": genre, "mf": mf}, rr)
            lines = []
            for k in keys:
                lines.append("【%s】" % k)
                for cat, items in (rr.get(k) or {}).items():
                    lines.append("  <%s>" % cat)
                    for x in items:
                        lines.append("    · " + str(x))
            self.t_rule.setPlainText("\n".join(lines) or "（这个题材还没有规则）")
            gs = m.norm_genre(genre)
            gkey = gs[0] if gs else "__全部__"
            try:
                p = os.path.join(NOVEL_DIR, "corpus", "custom.json")
                own = (json.load(open(p, encoding="utf-8")) or {}).get(gkey) or {}
            except Exception:
                own = {}
            flat = []
            for _c, items in own.items():
                flat += list(items)
            self.t_rule_own.setPlainText("\n".join(flat))
            self.status("本题材规则：%d 组，你补写 %d 条" % (len(keys), len(flat)))
        except Exception as e:
            self.status("读规则库失败：%s" % e)

    def save_genre_rules(self):
        genre = self.f["genre"].text().strip()
        try:
            lines = [l for l in self.t_rule_own.toPlainText().splitlines() if l.strip()]
            rules_mod().save_custom(genre, "own", lines)
            self.status("已保存 %d 条到「%s」" % (len(lines), genre or "全部"))
        except Exception as e:
            self.status("保存失败：%s" % e)

    def do_import(self):
        """导入已有设定：解析 → 填表 → 直接建书（bible/rules/characters）。"""
        import re as _re
        from PySide6.QtWidgets import QFileDialog
        p, _ = QFileDialog.getOpenFileName(
            self, "选设定文件", "", "Markdown / 文本 (*.md *.txt);;所有文件 (*)")
        if not p:
            return
        try:
            imp = launch.import_setting(p)
        except Exception as e:
            self.status("读设定失败：%s" % e)
            return
        secs = imp.get("sections") or {}
        for k, v in secs.items():
            body = v.split("\n", 1)[-1].strip()
            if "体材" in k or "题材" in k:
                self.f["genre"].setText(body.split("\n")[0][:20])
            elif "内容简介" in k:
                self.e_intro.setPlainText(body)
            elif "角色设定" in k:
                mm = _re.search(
                    r"姓名[:：]\s*([^\s　]+).*?年龄[:：]\s*(\d+).*?性格[:：]\s*([^\n]+)",
                    body)
                if mm:
                    self.f["protagonist"].setText(
                        "%s／%s／%s" % (mm.group(1), mm.group(2),
                                        mm.group(3).strip()[:40]))
            elif "情节设定" in k:
                self.f["brief"].setText(body.split("\n")[0][:120])
            elif "预计字数" in k:
                m = _re.search(r"(\d+)\s*万", body)
                if m:
                    self.f["words"].setText(str(int(m.group(1)) * 10000))
        t = (imp.get("title") or "").strip()
        if t:
            self.e_title.setText(t)
        slug = _re.sub(r"[^\w\u4e00-\u9fa5]", "", t)[:20] or "导入书"
        self.e_slug.setText(slug)
        try:
            r = launch.write_setting(imp, slug)
        except Exception as e:
            self.status("写设定失败：%s" % e)
            return
        self.log.appendPlainText(
            "导入 %s：人物 %d 个，技能 %d 个"
            % (slug, r.get("chars", 0), r.get("skills", 0)))
        self.status("已导入并建书：%s（去写作页点「补建章节」）" % slug)
        self.win.reload_books(select=slug)

    def do_create(self):
        slug = self.e_slug.text().strip()
        if not slug:
            # ★ 采用书名之后不该再逼他手打目录名：直接用书名当目录
            t = self.e_title.text().strip()
            slug = re.sub(r"[^\w\u4e00-\u9fa5]", "", t)[:20]
            self.e_slug.setText(slug)
        if not slug:
            self.status("先采用一个书名，或填一下目录名")
            return
        b = self._book()
        rows, skel, beats = list(self.rows), list(self.skel), list(self.beats)
        hook = self.e_hook.text().strip()
        title = self.e_title.text().strip()
        intro = self.e_intro.toPlainText().strip()

        def job():
            import os as _os
            d = _os.path.join(getattr(launch, "BOOKS", ""), slug)
            if _os.path.isdir(d):
                # 书已存在 → 更新（不动 bible / characters，旧版进 _bak）
                return launch.update_book(slug, rows, skel, beats)
            return launch.create_book(slug, b, hook, title, rows, intro, skel, beats)

        def ok(r):
            if not isinstance(r, dict) or not r.get("ok"):
                err = r.get("err") if isinstance(r, dict) else r
                self.log.appendPlainText("!!! 建书失败：%s" % err)
                return
            if r.get("updated"):
                self.log.appendPlainText(
                    "OK 更新了：books/%s（章纲 %s 章，补建 %s 章，旧版在 _bak/）"
                    % (r.get("slug"), r.get("chapters"), r.get("made", 0)))
            else:
                self.log.appendPlainText("OK 建好了：books/%s（%s 章）"
                                         % (r.get("slug"), r.get("chapters")))
            self.status("建好了：books/%s" % r.get("slug"))
            self.win.reload_books(select=r.get("slug"))

        self.run_async(job, on_ok=ok,
                       on_err=lambda m: self.status("建书失败：%s" % m.splitlines()[-1]),
                       busy=self._busy)


class SettingsDialog(QDialog, AsyncMixin):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self._workers = []
        self.setWindowTitle("设置")
        self.resize(560, 720)
        lay = QVBoxLayout(self)

        lay.addWidget(self._h("外观"))
        row = QHBoxLayout()
        self.lbl_bg = QLabel("背景图：无")
        b1 = QPushButton("选择图片")
        b1.clicked.connect(self.pick_bg)
        b2 = QPushButton("清除背景")
        b2.setProperty("sec", "1")
        b2.clicked.connect(self.clear_bg)
        row.addWidget(self.lbl_bg, 1)
        row.addWidget(b1)
        row.addWidget(b2)
        lay.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("遮罩"))
        self.sl = QSlider(Qt.Orientation.Horizontal)
        self.sl.setRange(0, 100)
        self.sl.setValue(int((llm.load_cfg() or {}).get("bg_mask", 55)))
        self.sl.valueChanged.connect(self.on_mask)
        row.addWidget(self.sl, 1)
        self.lbl_mask = QLabel("%d%%" % self.sl.value())
        row.addWidget(self.lbl_mask)
        lay.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("面板透明度"))
        self.sl_pw = QSlider(Qt.Orientation.Horizontal)
        self.sl_pw.setRange(0, 100)
        self.sl_pw.setValue(int(float((llm.load_cfg() or {}).get("pw") or 0.72) * 100))
        self.sl_pw.valueChanged.connect(self.on_pw)
        row.addWidget(self.sl_pw, 1)
        self.lbl_pw = QLabel("%d%%" % self.sl_pw.value())
        row.addWidget(self.lbl_pw)
        lay.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("配色"))
        for css, name in TINTS:
            b = QPushButton(name)
            b.setStyleSheet("background:%s; color:#333; border-color:#999;" % css)
            b.clicked.connect(lambda _c=False, cc=css: self.set_tint(cc))
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        lay.addSpacing(10)
        lay.addWidget(self._h("手法库"))
        row = QHBoxLayout()
        self.e_roots = QLineEdit((llm.load_cfg() or {}).get("skill_roots") or "")
        self.e_roots.setPlaceholderText("放 md 的目录，多个用 ; 隔开")
        row.addWidget(self.e_roots, 1)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.cb_on = QCheckBox("启用")
        self.cb_on.setChecked(str((llm.load_cfg() or {}).get("skill_on") or "0")
                              in ("1", "true", "on"))
        self.sp_chars = QSpinBox()
        self.sp_chars.setRange(200, 20000)
        self.sp_chars.setSingleStep(100)
        self.sp_chars.setValue(int((llm.load_cfg() or {}).get("skill_chars") or 1800))
        b3 = QPushButton("保存")
        b3.clicked.connect(self.save_skills)
        b4 = QPushButton("试挑")
        b4.setProperty("sec", "1")
        b4.clicked.connect(self.preview_skills)
        row.addWidget(self.cb_on)
        row.addWidget(QLabel("字数预算"))
        row.addWidget(self.sp_chars)
        row.addWidget(b3)
        row.addWidget(b4)
        row.addStretch(1)
        lay.addLayout(row)

        lay.addSpacing(10)
        lay.addWidget(self._h("规则库"))
        row = QHBoxLayout()
        self.e_genre = QLineEdit()
        self.e_genre.setPlaceholderText("题材，如 都市异能")
        self.e_mf = QLineEdit()
        self.e_mf.setPlaceholderText("男频 / 女频")
        b5 = QPushButton("查看")
        b5.setProperty("sec", "1")
        b5.clicked.connect(self.view_rules)
        row.addWidget(self.e_genre, 1)
        row.addWidget(self.e_mf, 1)
        row.addWidget(b5)
        lay.addLayout(row)
        self.t_rules = QPlainTextEdit()
        self.t_rules.setReadOnly(True)
        self.t_rules.setStyleSheet("font-size:12.5px;")
        lay.addWidget(self.t_rules, 1)
        lay.addWidget(QLabel("我补写的（一行一条）"))
        self.t_own = QPlainTextEdit()
        self.t_own.setStyleSheet("font-size:13px;")
        lay.addWidget(self.t_own, 1)
        b6 = QPushButton("保存我补写的")
        b6.clicked.connect(self.save_rules)
        lay.addWidget(b6)

        b_close = QPushButton("关闭")
        b_close.setProperty("sec", "1")
        b_close.clicked.connect(self.accept)
        lay.addWidget(b_close)
        self.refresh_bg_label()

    def _h(self, t):
        l = QLabel(t)
        l.setStyleSheet("font-size:13px; color:#777; font-weight:500;")
        return l

    def status(self, msg):
        self.win.status.showMessage(msg)

    def refresh_bg_label(self):
        p = (llm.load_cfg() or {}).get("bg_path") or ""
        self.lbl_bg.setText("背景图：" + (os.path.basename(p) if p else "无"))

    def pick_bg(self):
        p, _f = QFileDialog.getOpenFileName(
            self, "选一张背景图", "", "图片 (*.jpg *.jpeg *.png *.bmp *.webp)")
        if not p:
            return
        llm.save_cfg(bg_path=p)
        self.win.apply_appearance()
        self.refresh_bg_label()

    def clear_bg(self):
        llm.save_cfg(bg_path="__none__")     # 哨兵：明确表示不要背景
        self.win.apply_appearance()
        self.refresh_bg_label()
        self.status("背景已清除")

    def on_pw(self, v):
        self.lbl_pw.setText("%d%%" % v)
        llm.save_cfg(pw=round(v / 100.0, 2))
        self.win.apply_appearance()

    def on_mask(self, v):
        self.lbl_mask.setText("%d%%" % v)
        llm.save_cfg(bg_mask=v)
        self.win.apply_appearance()

    def set_tint(self, css):
        llm.save_cfg(tint=css)
        self.win.apply_appearance()

    def save_skills(self):
        llm.save_cfg(skill_roots=self.e_roots.text().strip(),
                     skill_on="1" if self.cb_on.isChecked() else "0",
                     skill_chars=self.sp_chars.value())
        self.status("手法库已保存")

    def preview_skills(self):
        roots = [r for r in self.e_roots.text().split(";") if r.strip()]
        budget = self.sp_chars.value()
        ctx = self.win.writer.editor.toPlainText()[:3000]

        def job():
            ch = skills.load(roots)
            return ch, skills.pick(ch, ctx, budget)

        def ok(r):
            ch, got = r
            self.t_rules.setPlainText("共 %d 段，挑到 %d 段：\n\n%s" % (
                len(ch), len(got),
                "\n".join("· " + g.get("title", "") for g in got) or "（没挑到）"))
        self.run_async(job, on_ok=ok)

    def _rules_mod(self):
        rp = os.path.join(NOVEL_DIR, "corpus", "pick.py")
        spec = importlib.util.spec_from_file_location("_cpick", rp)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def view_rules(self):
        try:
            m = self._rules_mod()
            book = {"genre": self.e_genre.text().strip(),
                    "mf": self.e_mf.text().strip()}
            rr = m.load_rules()
            keys = m._genre_keys(book, rr)
            lines = []
            for k in keys:
                lines.append("【%s】" % k)
                for c, its in (rr.get(k) or {}).items():
                    lines.append("  <%s>" % c)
                    for x in its:
                        lines.append("    · " + str(x))
            self.t_rules.setPlainText("\n".join(lines) or "（这个题材还没有规则）")
            gs = m.norm_genre(book["genre"])
            gkey = gs[0] if gs else "__全部__"
            try:
                p = os.path.join(NOVEL_DIR, "corpus", "custom.json")
                own = (json.load(open(p, encoding="utf-8")) or {}).get(gkey) or {}
            except Exception:
                own = {}
            flat = []
            for _c, its in own.items():
                flat += list(its)
            self.t_own.setPlainText("\n".join(flat))
        except Exception as e:
            self.status("读规则库失败：%s" % e)

    def save_rules(self):
        try:
            m = self._rules_mod()
            lines = [l for l in self.t_own.toPlainText().splitlines() if l.strip()]
            m.save_custom(self.e_genre.text().strip(), "own", lines)
            self.status("已保存 %d 条" % len(lines))
            self.view_rules()
        except Exception as e:
            self.status("保存失败：%s" % e)


# ══════════════════════════════════════════════════════════════════
# 学习面板要用到的控件（主界面没用到，单独导一次）
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                               QCheckBox, QTextEdit)
from PySide6.QtCore import QTimer


class LearnDialog(QDialog):
    """学习与训练：从教程提规则 / 把自己的稿子导出成训练集。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("学习与训练")
        self.resize(760, 560)
        lay = QVBoxLayout(self)

        row1 = QHBoxLayout()
        b1 = QPushButton("① 选教程 txt，提炼成规则")
        b1.clicked.connect(self.distill)
        row1.addWidget(b1)
        b2 = QPushButton("② 把我的稿子导出成训练集")
        b2.clicked.connect(self.export_train)
        row1.addWidget(b2)
        b3 = QPushButton("打开 corpus 目录")
        b3.clicked.connect(self.open_corpus)
        row1.addWidget(b3)
        row1.addStretch(1)
        b0 = QPushButton("清空日志")
        b0.clicked.connect(lambda: self.log.clear())
        row1.addWidget(b0)
        lay.addLayout(row1)

        lay.addWidget(self._h("说明"))
        tip = QLabel(
            "① 把你下的写作教程 .txt 丢进去，它会依次跑 "
            "extract → classify → distill，把规则提炼进 corpus/rules.json。\n"
            "    勾选「用本地模型归纳」会更准，但慢（10–20 分钟），且需要本地模型在跑。\n"
            "② 把 books 里你改好的终稿（不含 .ai.md 原稿）导成 jsonl，"
            "格式为 instruction/output，可直接喂给微调脚本训练 4B。")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        self.chk_llm = QCheckBox("用本地模型归纳（慢，更准）")
        lay.addWidget(self.chk_llm)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        lay.addWidget(self.log, 1)

        self._q = []
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._drain)
        self._timer.start(400)

    def _h(self, t):
        lb = QLabel(t)
        lb.setStyleSheet("color:#333;font-weight:600;")
        return lb

    def _drain(self):
        if not self._q:
            return
        while self._q:
            self.log.append(self._q.pop(0))

    def _run(self, args, cwd):
        import subprocess
        self._q.append("$ " + " ".join(args))

        def job():
            try:
                p = subprocess.Popen(
                    args, cwd=cwd, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                for raw in p.stdout:
                    line = raw.decode("utf-8", "replace").rstrip()
                    if line:
                        self._q.append(line)
                p.wait()
                self._q.append("—— 结束，返回码 %s ——" % p.returncode)
            except Exception as e:
                self._q.append("!!! 失败：%s" % e)

        threading.Thread(target=job, daemon=True).start()

    def open_corpus(self):
        d = os.path.join(NOVEL_DIR, "corpus")
        try:
            os.startfile(d)
        except Exception as e:
            self.log.append("打不开：%s" % e)

    def distill(self):
        from PySide6.QtWidgets import QFileDialog
        files, _ = QFileDialog.getOpenFileNames(
            self, "选写作教程（可多选）", "", "文本 (*.txt *.md)")
        if not files:
            return
        raw = os.path.join(NOVEL_DIR, "corpus", "raw")
        os.makedirs(raw, exist_ok=True)
        for f in files:
            try:
                shutil.copy2(f, raw)
                self.log.append("已放入 raw：%s" % os.path.basename(f))
            except Exception as e:
                self.log.append("复制失败 %s：%s" % (f, e))
        py = sys.executable
        cwd = os.path.join(NOVEL_DIR, "corpus")
        self._run([py, "extract.py"], cwd)
        self._run([py, "classify.py"], cwd)
        args = [py, "distill.py"]
        if self.chk_llm.isChecked():
            args.append("--llm")
        self._run(args, cwd)
        self.log.append("★ 三步跑完后，看 corpus/rules.json 是否变大。备份在 rules.json.bak。")

    def export_train(self):
        from PySide6.QtWidgets import QFileDialog
        root = BOOKS
        out, _ = QFileDialog.getSaveFileName(
            self, "训练集存哪", os.path.join(NOVEL_DIR, "corpus", "train.jsonl"),
            "JSONL (*.jsonl)")
        if not out:
            return
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        n = 0
        books = 0
        with open(out, "w", encoding="utf-8") as fo:
            for slug in sorted(os.listdir(root)) if os.path.isdir(root) else []:
                d = os.path.join(root, slug, "chapters")
                if not os.path.isdir(d):
                    continue
                books += 1
                for fn in sorted(os.listdir(d)):
                    if not fn.endswith(".md") or fn.endswith(".ai.md"):
                        continue
                    try:
                        txt = open(os.path.join(d, fn), encoding="utf-8").read()
                    except Exception:
                        continue
                    body = re.sub(r"(?m)^#.*$", "", txt)
                    body = re.sub(r"<!--.*?-->", "", body, flags=re.S).strip()
                    if len(body) < 400:
                        continue
                    tm = re.search(r"第\s*(\d+)\s*章\s*(.*)", fn)
                    num = int(tm.group(1)) if tm else 0
                    title = (tm.group(2) if tm else "").strip() or ("第%d章" % num)
                    ins = ("按《%s》的风格和第 %d 章的上下文，写「%s」这一章的正文。"
                           "约 %d 字。" % (slug, num, title, len(body)))
                    fo.write(json.dumps({"instruction": ins, "output": body},
                                        ensure_ascii=False) + "\n")
                    n += 1
        self.log.append("导出完成：%s" % out)
        self.log.append("共 %d 章（来自 %d 本书）" % (n, books))
        if n == 0:
            self.log.append("★ 一章都没导到：检查 books 里有没有 .md 正文（.ai.md 会被排除）")
        else:
            self.log.append("★ 400 字以下的短章已被跳过；想更准可以再人工筛一遍。")


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("R4IN文 · 客户端")
        self.setMinimumSize(1000, 640)
        # ★ 不能写死 1440x920：屏幕缩放 125% 时逻辑宽 1440 = 物理 1800，
        #   会超出屏幕导致右栏被裁。这里按可用区域自适应。
        try:
            scr = QApplication.primaryScreen().availableGeometry()
            w = min(1440, scr.width() - 80)
            h = min(920, scr.height() - 80)
            self.resize(w, h)
            self.move(scr.x() + (scr.width() - w) // 2,
                      scr.y() + (scr.height() - h) // 2)
        except Exception:
            self.resize(1280, 820)

        self.back = Backdrop()
        root = QVBoxLayout(self.back)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ── 顶栏 ────────────────────────────────────────────────────
        head = QWidget()
        head.setFixedHeight(50)
        head.setStyleSheet(panel_style(0.72, 14))
        hv = QHBoxLayout(head)
        hv.setContentsMargins(16, 8, 16, 8)
        hv.setSpacing(14)
        self.btn_launch = QPushButton("开书台")
        self.btn_launch.setProperty("sec", "1")
        self.btn_launch.clicked.connect(lambda: self.switch_view(0))
        self.btn_write = QPushButton("写作")
        self.btn_write.clicked.connect(lambda: self.switch_view(1))
        hv.addWidget(self.btn_launch)
        hv.addWidget(self.btn_write)
        hv.addSpacing(12)
        lb_book = QLabel("书名")
        lb_book.setStyleSheet("padding: 0 8px 0 4px; font-size:14px;")
        hv.addWidget(lb_book)
        self.cb_book = QComboBox()
        self.cb_book.setMinimumWidth(170)
        self.cb_book.setMinimumHeight(32)
        self.cb_book.currentIndexChanged.connect(self.on_book)
        hv.addWidget(self.cb_book)
        self.btn_del_book = QPushButton("删除")
        self.btn_del_book.clicked.connect(self.del_book)
        hv.addWidget(self.btn_del_book)
        lb_ch = QLabel("章节")
        lb_ch.setStyleSheet("padding: 0 8px 0 4px; font-size:14px;")
        hv.addWidget(lb_ch)
        self.cb_ch = QComboBox()
        self.cb_ch.setMinimumWidth(260)
        self.cb_ch.setMinimumHeight(32)
        self.cb_ch.currentIndexChanged.connect(self.on_chapter)
        hv.addWidget(self.cb_ch)
        self.btn_del_ch = QPushButton("删除")
        self.btn_del_ch.clicked.connect(self.del_chapter)
        hv.addWidget(self.btn_del_ch)
        self.btn_fix_ch = QPushButton("补建章节")
        self.btn_fix_ch.setToolTip("按 outline.md 的章纲，把缺的章节文件补齐")
        self.btn_fix_ch.clicked.connect(self.fix_chapters)
        hv.addWidget(self.btn_fix_ch)
        # 导入/新建章节放在顶部，跟"补建章节"是一类操作
        # 顶栏比写作页先建，self.writer 还不存在，必须点到时才取
        for _t, _fn in (("导入章节", lambda: self.writer.import_chapter()),
                        ("新建章节", lambda: self.writer.new_chapter())):
            _b = QPushButton(_t)
            _b.clicked.connect(_fn)
            hv.addWidget(_b)
        hv.addStretch(1)
        b = QPushButton("设置")
        b.setProperty("sec", "1")
        b.clicked.connect(self.open_settings)
        hv.addWidget(b)
        root.addWidget(head)

        # ── 两个视图 ────────────────────────────────────────────────
        self.writer = WriterView(self)
        self.launchv = LaunchView(self)
        self.stack = QStackedWidget()
        self.stack.setStyleSheet("background: transparent;")
        self.stack.addWidget(self.launchv)
        self.stack.addWidget(self.writer)
        root.addWidget(self.stack, 1)
        self.setCentralWidget(self.back)

        self.status = self.statusBar()
        self.status.setStyleSheet("QStatusBar{background:rgba(255,255,255,.72);}")
        # 面板透明度统一管理：主窗口顶栏 + 写作页三栏
        # (控件, 基础不透明度, 是否 QGroupBox)
        self._styled = [(head, 0.72, False)]
        self._styled += [(w, b, False)
                         for w, b in getattr(self.writer, "_panel_widgets", [])]
        self._styled += [(g, 1.0, True)
                         for g in getattr(self.launchv, "_groups", [])]
        self._styled += [(w, 1.0, "text") for w in (self.writer.txt_ai, self.writer.chat, self.writer.editor, self.writer.txt_outline, self.writer.txt_bible, self.launchv.t_rule, self.launchv.t_rule_own, self.launchv.log)]
        self.status.showMessage("就绪")

        self._menu()
        self.timer = QTimer(self)
        self.timer.setInterval(60_000)
        self.timer.timeout.connect(self.writer.autosave)
        self.timer.start()

        self.apply_appearance()
        restyle_buttons(self)
        # ★ 建书用的 launch.BOOKS 必须和列表用的 forge.BOOKS 是同一个目录。
        #   不设的话，发行版里书会建到开发机路径，写作台永远看不到。
        try:
            launch.BOOKS = BOOKS
            if NOVEL_DIR:
                launch.HERE = NOVEL_DIR
            for _mod in (launch, forge):
                try:
                    _mod.BOOKS = BOOKS
                except Exception:
                    pass
        except Exception:
            pass

        # ★ 启动就按配置初始化界面（温度/上下文/后端/Key），
        #   不能等用户打开一本书才载入。
        try:
            self.writer.load_cfg_ui()
        except Exception:
            pass

        self._patch_ollama_spawn()
        self.reload_books()
        self.switch_view(1)

        # 有任务在跑时，状态栏显示「AI 生成中 · N 秒」
        self._busy_sec = 0
        self._tick = QTimer(self)
        self._tick.setInterval(500)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()

    def _workers_running(self):
        out = []
        for pane in (getattr(self, "writer", None), getattr(self, "launchv", None)):
            for w in getattr(pane, "_workers", []) if pane else []:
                if w.isRunning():
                    out.append(w)
        return out

    def _on_tick(self):
        run = self._workers_running()
        if run:
            self._busy_sec += 0.5
            sec = int(self._busy_sec)
            if sec >= 240:
                msg = ("已等 %d 秒 —— 很可能卡住了。可以点「撤回」或关掉重来" % sec)
            elif sec >= 120:
                msg = "AI 生成中… %d 秒（已超过两分钟，慢但还在跑）" % sec
            else:
                msg = "AI 生成中… %d 秒（模型在跑，别关窗口）" % sec
            self.status.showMessage(msg)
            # ★ 每 10 秒往聊天框写一行。光是状态栏数字在跳没说服力，
            #   要有一条不断变长的记录，用户才敢确定不是在死等。
            if sec and sec % 10 == 0 and sec != getattr(self, "_tick_mark", -1):
                self._tick_mark = sec
                try:
                    self.writer.chat.appendPlainText(
                        "〔第 %d 秒〕模型还在跑，未返回…" % sec)
                except Exception:
                    pass
        elif self._busy_sec:
            self._busy_sec = 0.0
            # ★ 这里以前写「这一轮结束了」，会把「生成完成」「没过闸门（返回 2）」
            #   这类真正要告诉用户的结果冲掉。只清计数器，不动状态栏。

    # ── 顶栏行为 ────────────────────────────────────────────────────
    def switch_view(self, i):
        self.stack.setCurrentIndex(i)
        if i == 0:
            mkpri(self.btn_launch)
            self.btn_write.setStyleSheet("")
        else:
            mkpri(self.btn_write)
            self.btn_launch.setStyleSheet("")

    def _default_book(self):
        """优先 current.json 记的那本；否则选章节最多的那本。"""
        bs = list_books()
        if not bs:
            return ""
        try:
            st = os.path.join(NOVEL_DIR, "current.json")
            d = json.load(open(st, encoding="utf-8")).get("dir") or ""
            b = os.path.basename(d.rstrip("\\/"))
            if b in bs:
                return b
        except Exception:
            pass
        return max(bs, key=lambda x: len(list_chapters(x)))

    def reload_books(self, select=None):
        cur = (select or self.writer.slug
               or (self.cb_book.currentData() if self.cb_book.count() else "")
               or self._default_book())
        self.cb_book.blockSignals(True)
        self.cb_book.clear()
        for b in list_books():
            self.cb_book.addItem(b, b)
        self.cb_book.blockSignals(False)
        i = self.cb_book.findData(cur) if cur else 0
        self.cb_book.setCurrentIndex(max(i, 0))
        self.on_book()

    def on_book(self, *_):
        slug = self.cb_book.currentData()
        if slug:
            self.writer.load_book(slug)

    def on_chapter(self, *_):
        self.writer.open_chapter()

    def _trash(self, *parts):
        d = os.path.join(BOOKS, *parts)
        os.makedirs(d, exist_ok=True)
        return d

    def del_book(self):
        slug = self.cb_book.currentData()
        if not slug:
            return
        if QMessageBox.question(
                self, "删除这本书？",
                "把 books/%s 移到 books/_trash/ 里（不是真删，可以手动找回）。" % slug,
                QMessageBox.StandardButton.Ok
                | QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Ok:
            return
        try:
            import shutil
            t = self._trash("_trash")
            dst = os.path.join(t, slug)
            if os.path.exists(dst):
                shutil.rmtree(dst, ignore_errors=True)
            shutil.move(os.path.join(BOOKS, slug), dst)
            self.status.showMessage("已移到 books/_trash/%s" % slug)
            self.writer.slug = ""
            self.writer.editor.clear()
            self.writer.txt_ai.clear()
            self.writer.chat.clear()
            self.cb_ch.clear()
            self.reload_books(select=self.cb_book.currentData())
        except Exception as e:
            QMessageBox.critical(self, "删除失败", str(e))

    def fix_chapters(self):
        """按 outline.md 补建缺的章节文件（不删书、不碰其他文件）。"""
        import re as _re
        slug = self.cb_book.currentData()
        if not slug:
            self.status.showMessage("先选一本书")
            return
        d = os.path.join(BOOKS, slug)
        op = os.path.join(d, "outline.md")
        if not os.path.exists(op):
            self.status.showMessage("这本书没有 outline.md，先用开书台建书")
            return
        cd = os.path.join(d, "chapters")
        os.makedirs(cd, exist_ok=True)
        made = 0
        for line in open(op, encoding="utf-8"):
            m = _re.match(r"\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|", line)
            if not m:
                continue
            num, title = m.group(1), m.group(2).strip()
            title = title.replace("/", "／").replace("\\", "＼")
            fp = os.path.join(cd, "第%s章 %s.md" % (num, title))
            if not os.path.exists(fp):
                open(fp, "w", encoding="utf-8").write(
                    "# 第%s章 %s\n\n" % (num, title))
                made += 1
        self.status.showMessage("补建了 %d 章" % made)
        self.writer.reload_chapters()

    def del_chapter(self):
        slug = self.writer.slug
        data = self.cb_ch.currentData()
        if not (slug and data):
            return
        n, fn = data
        if QMessageBox.question(
                self, "删除第 %d 章？" % n,
                "把这一章移到 chapters/_trash/ 里（不是真删）。",
                QMessageBox.StandardButton.Ok
                | QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Ok:
            return
        try:
            import shutil
            src = os.path.join(BOOKS, slug, "chapters")
            dst = os.path.join(src, "_trash")
            os.makedirs(dst, exist_ok=True)
            for name in (fn, fn[:-3] + ".ai.md"):
                p = os.path.join(src, name)
                if os.path.exists(p):
                    shutil.move(p, os.path.join(dst, name))
            self.status.showMessage("第 %d 章已移到 chapters/_trash/" % n)
            self.writer.reload_chapters()
        except Exception as e:
            QMessageBox.critical(self, "删除失败", str(e))

    def open_settings(self):
        d = SettingsDialog(self)
        restyle_buttons(d)
        d.exec()

    def show_report(self, text):
        QMessageBox.information(self, "红线报告", text[:4000])

    def tmp_dir(self):
        return os.environ.get("TEMP", ".")

    def _menu(self):
        mb = self.menuBar()
        m = mb.addMenu("文件")
        for text, slot, sc in (
                ("保存", lambda: self.writer.save(), QKeySequence.StandardKey.Save),
                ("退出", self.close, QKeySequence.StandardKey.Quit)):
            a = QAction(text, self)
            a.setShortcut(sc)
            a.triggered.connect(slot)
            m.addAction(a)
        m = mb.addMenu("章节")
        for text, d, sc in (("上一章", -1, "Ctrl+PageUp"),
                            ("下一章", 1, "Ctrl+PageDown")):
            a = QAction(text, self)
            a.setShortcut(sc)
            a.triggered.connect(lambda _c=False, dd=d: self.writer.goto_delta(dd))
            m.addAction(a)
        m = mb.addMenu("工具")
        _tools = [("查红线", self.writer.check_redline, "Ctrl+Shift+Z")]
        if not is_lite():
            _tools.append(("学习与训练", self.open_learn, ""))
        for text, slot, sc in _tools:
            a = QAction(text, self)
            if sc:
                a.setShortcut(sc)
            a.triggered.connect(slot)
            m.addAction(a)

    def open_learn(self):
        """工具 → 学习与训练：教程提规则 / 导出训练集。"""
        try:
            d = LearnDialog(self)
            d.exec()
        except Exception as e:
            self.statusBar().showMessage("打开失败：%s" % e)

    # ── 外观 ────────────────────────────────────────────────────────
    def apply_appearance(self):
        cfg = llm.load_cfg() or {}
        path = cfg.get("bg_path") or ""
        if path == "__none__":
            path = ""
        elif not path:
            # 原版 webui 把背景存在 bg.img；没设过 bg_path 就直接用它。
            # bg_path == "__none__" 表示用户主动清除了，不再回退。
            legacy = os.path.join(NOVEL_DIR, "bg.img")
            if os.path.exists(legacy):
                path = legacy
        try:
            mask = int(cfg.get("bg_mask") if cfg.get("bg_mask") is not None else 55)
        except Exception:
            mask = 55
        self.back.set_bg(path)
        self.back.set_mask(mask)
        self.back.set_tint(cfg.get("tint") or "#f7d8dc")
        try:
            pw = float(cfg.get("pw") if cfg.get("pw") is not None else 0.72)
        except Exception:
            pw = 0.72
        pw = max(0.0, min(1.0, pw))
        for w, base, is_group in getattr(self, "_styled", []):
            a = max(0.0, min(1.0, base * pw))
            if is_group == 'text':
                w.setStyleSheet(
                    'background: rgba(255,255,255,%.3f);'
                    ' border:1px solid rgba(0,0,0,.16); border-radius:8px;'
                    ' padding:10px; color:#222222; font-size:14px;' % a)
            elif is_group:
                w.setStyleSheet(
                    "QGroupBox{background: rgba(255,255,255,%.3f);"
                    " border:1px solid rgba(0,0,0,.10); border-radius:8px;"
                    " margin-top:18px; padding:12px 10px 10px 10px;}"
                    "QGroupBox::title{color:#4a4a4a; font-weight:600;"
                    " font-size:13px;}" % a)
            else:
                w.setStyleSheet(
                    "background: rgba(255,255,255,%.3f); border-radius:12px;" % a)
        for w, base in getattr(self, "_panels", []):
            w.setStyleSheet(panel_style(min(1.0, base * pw / 0.72)))
        self.back.update()
        self.repaint()

    # ── 禁止反复拉 ollama（否则闪 cmd 窗口）────────────────────────
    def _patch_ollama_spawn(self):
        def once():
            try:
                llm.ensure_up()
            except Exception:
                pass
            # 预热：第一次调用要把 7B 模型加载进显存（30-60 秒），
            # 提前在后台做掉，一键乱写就不会卡在那儿不动。
            try:
                llm.complete('只回一个字。', '你好', temperature=0.1,
                             max_tokens=8)
            except Exception:
                pass

            def quiet(cfg=None, wait=15.0):
                """★ 必须分辨后端：云端去请求 /models，本地才探端口。
                以前这里只会探 base_url，云端是 https 必然失败，
                于是云端模式也弹「本地模型未就绪」。"""
                try:
                    c = cfg or llm.load_cfg()
                except Exception as e:
                    return False, "读配置失败：%s" % e
                cloud = (c.get("backend") or "ollama") != "ollama"
                try:
                    if cloud:
                        base = (c.get("base_url") or "").rstrip("/")
                        key = (c.get("api_key") or "").strip()
                        if not base:
                            return False, "还没填接口地址"
                        if not key:
                            return False, "还没填 API Key"
                        llm._get(base + "/models",
                                 {"Authorization": "Bearer " + key}, 20)
                        return True, "云端模型已联通"
                    if llm._probe(c["base_url"]):
                        return True, "本地模型已就绪"
                    return False, "本地模型未就绪，请重开程序"
                except Exception as e:
                    m = str(e)
                    if cloud:
                        if "401" in m or "403" in m:
                            return False, "云端连不上：API Key 无效或没权限"
                        return False, "云端连不上：请检查 API Key 与接口地址"
                    return False, "本地模型未就绪：%s" % m

            llm.ensure_up = quiet

        threading.Thread(target=once, daemon=True).start()

    def closeEvent(self, ev):
        if self.writer.dirty:
            r = QMessageBox.question(
                self, "还有没保存的改动", "先保存再退出？",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel)
            if r == QMessageBox.StandardButton.Cancel:
                ev.ignore()
                return
            if r == QMessageBox.StandardButton.Save and not self.writer.save(quiet=True):
                ev.ignore()
                return
        ev.accept()


# ══════════════════════════════════════════════════════════════════
def _main_switch_view(self, i):
    self.stack.setCurrentIndex(i)
    if i == 0:
        mkpri(self.btn_launch)
        self.btn_write.setStyleSheet("")
    else:
        mkpri(self.btn_write)
        self.btn_launch.setStyleSheet("")


Main.switch_view = _main_switch_view


def selftest():
    lines = ["frozen      = %s" % getattr(sys, "frozen", False),
             "NOVEL_DIR   = %s" % NOVEL_DIR,
             "BOOKS 存在  = %s" % os.path.isdir(BOOKS),
             "书目录      = %s" % (list_books(),)]
    for b in list_books()[:3]:
        r = list_chapters(b)
        lines.append("  [%s] %d 章" % (b, len(r)))
    try:
        c = llm.load_cfg()
        lines.append("模型        = %s / %s" % (c.get("model"), c.get("backend")))
    except Exception as e:
        lines.append("模型读失败  = %r" % (e,))
    for name, fn in (("红线", lambda: redline.check("甲乙丙丁。", [], None, None)),
                     ("开书台", lambda: launch.gen_roll(use_llm=False, with_cheat=False)),
                     ("手法库", lambda: skills.load([])),
                     ("规则库", lambda: os.path.exists(
                         os.path.join(NOVEL_DIR, "corpus", "pick.py")) or 1 / 0)):
        try:
            fn()
            lines.append("%-12s= OK" % name)
        except Exception as e:
            lines.append("%-12s= FAIL %r" % (name, e))
    out = "\n".join(lines)
    try:
        print(out)
    except Exception:
        pass
    try:
        with open(os.path.join(os.environ.get("TEMP", "."), "r4in_selftest.log"),
                  "w", encoding="utf-8") as f:
            f.write(out + "\n")
    except Exception:
        pass
    return 0


_OLLAMA_PROC = [None]


def _boot_bundled_ollama():
    """用安装目录里的 ollama + models 起本地服务（买家不用装 Ollama）。"""
    import socket
    import subprocess
    d = os.path.join(HERE, "ollama")
    exe = os.path.join(d, "ollama.exe")
    if not os.path.exists(exe):
        return False                      # 没捆绑就交给系统里那个
    try:
        sk = socket.create_connection(("127.0.0.1", 11434), timeout=0.5)
        sk.close()
        return True                       # 已经在跑，别重复起
    except Exception:
        pass
    env = dict(os.environ)
    env["OLLAMA_MODELS"] = os.path.join(d, "models")
    env["OLLAMA_HOST"] = "127.0.0.1:11434"
    env.pop("OLLAMA_GPU_OVERHEAD", None)
    try:
        _OLLAMA_PROC[0] = subprocess.Popen(
            [exe, "serve"], env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def main():
    # ★ 自测入口：R4INWEN.exe --apitest
    #   冻结后没有控制台，结果写到 %TEMP%\r4in_api.log
    if "--apitest" in sys.argv:
        import traceback
        log = os.path.join(os.environ.get("TEMP", "."), "r4in_api.log")
        out = []
        try:
            import llm as _L
            c = _L.load_cfg() or {}
            out.append("backend=%s base=%s model=%s key=%s"
                       % (c.get("backend"), c.get("base_url"),
                          c.get("model"), "有" if c.get("api_key") else "无"))
            out.append("ping=%r" % (_L.ping(),))
            r = _L.complete("只回三个字。", "你好", max_tokens=16)
            out.append("complete=%r" % (r[:40],))
        except Exception:
            out.append("EXC:\n" + traceback.format_exc())
        open(log, "w", encoding="utf-8").write("\n".join(out))
        return

    # ★ 任务栏图标：Windows 靠 AppUserModelID 认这是哪个程序。
    #   不设的话，哪怕 setWindowIcon 了，任务栏也只显示默认图标（或 python 的图标）。
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "R4IN.Novel.Client.1")
    except Exception:
        pass

    # ★ 安装目录里没有 ollama → 这是轻量版 → 默认走云端，
    #   否则买家第一次点测连通会看到「本地模型未就绪」，以为程序坏了
    try:
        if (getattr(sys, "frozen", False)
                and not os.path.isdir(os.path.join(HERE, "ollama"))):
            if (llm.load_cfg() or {}).get("backend") != "openai":
                llm.save_cfg(backend="openai")
    except Exception:
        pass

    # ★ 只有本地模式才拉 ollama；云端模式别占内存
    try:
        _bk = (llm.load_cfg() or {}).get("backend") or "ollama"
        if _bk == "ollama":
            _boot_bundled_ollama()
    except Exception:
        pass
    import atexit
    def _kill_ollama():
        p = _OLLAMA_PROC[0]
        if p and p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass
    atexit.register(_kill_ollama)

    if "--selftest" in sys.argv:
        sys.exit(selftest())
    # ★ Fusion + 显式浅色调色板：不管系统是深色还是浅色，界面都是浅底深字
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(light_palette())
    app.setFont(QFont("Microsoft YaHei", 10))
    # 任务栏 + 窗口左上角图标
    for cand in (os.path.join(getattr(sys, "_MEIPASS", ""), "app.ico"),
                 os.path.join(HERE, "_internal", "app.ico"),
                 os.path.join(HERE, "app.ico"),
                 os.path.join(NOVEL_DIR, "app.ico")):
        if cand and os.path.exists(cand):
            app.setWindowIcon(QIcon(cand))
            break
    _chev = ""
    for cand in (os.path.join(getattr(sys, "_MEIPASS", ""), "chev.png"),
                 os.path.join(HERE, "_internal", "chev.png"),
                 os.path.join(HERE, "chev.png")):
        if cand and os.path.exists(cand):
            _chev = cand.replace("\\", "/")
            break
    app.setStyleSheet(QSS.replace("__CHEV__", _chev))
    app.setApplicationName("R4IN文")
    w = Main()
    w.setWindowIcon(app.windowIcon())
    if "--view" in sys.argv:
        try:
            v = sys.argv[sys.argv.index("--view") + 1]
            w.switch_view(0 if v.startswith("l") else 1)
        except Exception:
            pass
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

# ══════════════════════════════════════════════════════════════
