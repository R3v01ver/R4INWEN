"""调模型。后端 ollama / OpenAI 兼容。

★ 一条纪律：连不上就说连不上，不许降级瞎写。
  写了半天发现是脚本拿假文本凑的，比直接报错糟得多。

★ 2026-10-19 修的老毛病：打开就是"加载不出模型"。
  根因两条，都不是模型的问题：
  1. ollama 不开机自启，服务没跑 ⇒ 现在由 app 自动拉起（ensure_up）。
  2. 拿不到模型列表时异常被静默吞掉 ⇒ 现在必须带着原因返回（installed_models）。
  3. 顺带：本机地址一律绕开系统代理，不然开了 Clash 会把 127.0.0.1 也送进代理。
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ★ 打包成 exe（PyInstaller）后，__file__ 指向临时解压目录，
#   用它当根会找不到 settings.json 和 books/。
#   ⇒ 冻结态下一律以 exe 所在目录为根。
if getattr(sys, "frozen", False):
    HERE = os.path.dirname(sys.executable)
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "settings.json")

# ★ 最近一次配置读/写失败的原因。load_cfg 不许再静默回默认——
#   配置坏了必须留证，而且要能到界面上（installed_models 会带出去）。
CFG_ERROR = ""

DEFAULT = {
    "backend": "ollama",                    # ollama / openai / none
    # ★★ 本机一律写 127.0.0.1，不要写 localhost。实测 Windows：
    #      localhost  → 4.20 秒
    #      127.0.0.1  → 0.10 秒    差 42 倍。
    #    原因：localhost 先解析成 ::1(IPv6)，而 ollama 只监听 IPv4，
    #    ⇒ 每次请求都先白跑一轮 IPv6 超时。这就是"半天加载不出模型"的真凶。
    "base_url": "http://127.0.0.1:11434",
    "model": "qwen2.5:7b",
    "api_key": "",
    "temperature": 0.9,
    "timeout": 900,
    "max_tokens": 4096,
}


def _bypass_proxy():
    """★ 本机地址不走代理；远端后端照旧走系统代理。

    以前是 no_proxy="*"——本机是绕开了，但用户把后端指到远端 API 时，
    代理也被一起打穿，等于悄悄改了他的网络环境（公司内网代理直接失效）。
    现在只往 no_proxy 里补本机三个名字，用户原来的设置原样保留。
    """
    for _v in ("no_proxy", "NO_PROXY"):
        cur = os.environ.get(_v, "")
        parts = [p.strip() for p in cur.split(",") if p.strip()]
        for host in ("127.0.0.1", "localhost", "::1"):
            if host not in parts:
                parts.append(host)
        os.environ[_v] = ",".join(parts)


_bypass_proxy()          # 模块一加载就生效，所有请求都沾光


def _probe(base_url: str, timeout: float = 1.0) -> bool:
    """探端口而不是发 HTTP：更快，也不受代理影响。"""
    try:
        p = urllib.parse.urlparse(base_url)
        s = socket.create_connection((p.hostname or "127.0.0.1", p.port or 11434),
                                     timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def _ollama_exe():
    """找 ollama 的绝对路径。PATH 里没有就去默认安装位置捞。"""
    exe = shutil.which("ollama")
    if exe:
        return exe
    for p in (os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe"),
              r"C:\Program Files\Ollama\ollama.exe"):
        if os.path.exists(p):
            return p
    return None


def ensure_up(cfg: dict = None, wait: float = 15.0):
    """保证本机模型服务是活的：没跑就自己把它拉起来。

    ★ 只管「ollama 后端 + 地址是本机」这一种情况，远端/OpenAI 后端不动。
    返回 (能不能用, 一句话说明)。幂等——已经跑着就直接返回。
    """
    cfg = cfg or load_cfg()
    if cfg.get("backend") != "ollama":
        # ★ 必须真的探一次。以前这里直接 return True，
        #   导致没填 Key 也显示"联通"——云端分支等于没测。
        try:
            base = (cfg.get("base_url") or "").rstrip("/")
            key = (cfg.get("api_key") or "").strip()
            if not base:
                return False, "还没填接口地址"
            if not key:
                return False, "还没填 API Key"
            _get(base + "/models", {"Authorization": "Bearer " + key}, 20)
            return True, "云端模型已联通"
        except Exception as e:
            msg = str(e)
            if "401" in msg or "403" in msg:
                return False, "云端连不上：API Key 无效或没权限"
            return False, "云端连不上：请检查 API Key 与接口地址"
    host = (urllib.parse.urlparse(cfg.get("base_url", "")).hostname or "")
    if host not in ("localhost", "127.0.0.1", "::1"):
        return True, "远端服务，不归我拉"

    if _probe(cfg["base_url"]):
        return True, "服务已在运行"

    exe = _ollama_exe()
    if not exe:
        return False, "本地模型未就绪（程序文件可能被删，请重装）"

    # ★ Windows 上拉起 ollama 最容易冒黑窗。三重保险：
    #   CREATE_NO_WINDOW（不建控制台）＋ STARTF_USESHOWWINDOW/SW_HIDE（就算建了也藏起来）。
    #   少了 startupinfo 这一段，某些机器上照样会闪一个 cmd 窗口出来。
    # ★★ 只给 CREATE_NO_WINDOW，绝不加 DETACHED_PROCESS。
    #   ollama.exe 是控制台程序：给了 DETACHED_PROCESS 它会自己另开一个控制台
    #   ⇒ 用户每次点生成就冒一个黑 cmd 窗口出来（他点名说难看）。
    _NO_WINDOW = 0x08000000
    _STARTF_USESHOWWINDOW, _SW_HIDE = 0x00000001, 0
    si = subprocess.STARTUPINFO()
    si.dwFlags |= _STARTF_USESHOWWINDOW
    si.wShowWindow = _SW_HIDE
    try:
        subprocess.Popen(
            [exe, "serve"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            startupinfo=si,
            creationflags=_NO_WINDOW, close_fds=True)
    except Exception as e:
        return False, "本地模型启动失败，请重开程序"

    end = time.time() + wait
    while time.time() < end:
        time.sleep(0.6)
        if _probe(cfg["base_url"]):
            return True, "本地模型已启动"
    return False, "本地模型启动后没有响应，请重开程序"


def installed_models(ensure: bool = True, wait: float = 15.0):
    """(模型名列表, 错误原因)。★ 不再静默吞异常——拿不到必须告诉用户为什么。"""
    cfg = load_cfg()
    if ensure:
        ok, why = ensure_up(cfg, wait=wait)
        if not ok:
            return [], (why + (f"｜{CFG_ERROR}" if CFG_ERROR else ""))
    base = cfg["base_url"].rstrip("/")
    last = ""
    # ★ 冷启动的头一次 tags 真的会超过 8 秒（要扫模型索引），
    #   给足超时并重试一次，别让用户看到"读不到模型"。
    for i in range(2):
        try:
            data = _get(base + "/api/tags", {}, 25)
            # 配置有伤（已从备份恢复／回默认）也要让界面显示出来——
            # 不然"我明明选了 7B"又成悬案
            return [m.get("name", "") for m in data.get("models", [])], CFG_ERROR
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            time.sleep(1.5)
    return [], (f"读不到模型列表：{last}"
                + (f"｜{CFG_ERROR}" if CFG_ERROR else ""))


def load_cfg() -> dict:
    """读配置。★ 坏了不再静默回默认——那是"我明明选了 7B"之谜的根源。

    坏了怎么办：① 坏文件改名留证（settings.json.broken-时间戳）
                ② 有 settings.json.bak 就从备份恢复
                ③ 都没有才回默认，且 CFG_ERROR 带上原因，
                   installed_models() 会把它送到界面上。
    """
    global CFG_ERROR
    CFG_ERROR = ""
    cfg = dict(DEFAULT)
    if os.path.exists(CFG_PATH):
        # ★ 必须 with：不 with 的话，json.load 抛异常时 traceback 还攥着
        #   文件句柄，紧接着的 os.replace 在 Windows 上必报
        #   PermissionError(WinError 32)——坏文件改不成名，留证就丢了。
        #   （实测踩中过：except 块里 os.replace 静默失败。）
        try:
            with open(CFG_PATH, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            CFG_ERROR = f"settings.json 读不了（{type(e).__name__}: {e}）"
            try:
                os.replace(CFG_PATH,
                           CFG_PATH + f".broken-{int(time.time())}")
            except OSError as ex:
                CFG_ERROR += f"；坏文件也改不成名（{ex}）"
            cfg = dict(DEFAULT)
            if os.path.exists(CFG_PATH + ".bak"):
                try:
                    with open(CFG_PATH + ".bak", encoding="utf-8") as f:
                        cfg.update(json.load(f))
                    shutil.copy2(CFG_PATH + ".bak", CFG_PATH)
                    CFG_ERROR += "；已从 settings.json.bak 恢复"
                except Exception:
                    CFG_ERROR += "；备份也读不了，回退默认值"
    # ★ 老配置里存的可能是 localhost，这里顺手纠正，不用手动改文件。
    #   （见上面 DEFAULT 的注释：localhost 在 Windows 上会慢 40 倍。）
    url = cfg.get("base_url", "")
    if "localhost" in url:
        cfg["base_url"] = url.replace("localhost", "127.0.0.1")
    return cfg


def save_cfg(**kw) -> dict:
    """★ 原子写：先写 .tmp 再 os.replace，fsync 落盘。

    以前是 json.dump(open(...))——不 close、非原子。写到一半崩，
    下次启动就是一个半份 JSON，读失败还静默回默认，
    表现是"模型悄悄变回默认值"，最难查的那种 bug。
    写失败就大声 raise，不许假装存好了。
    """
    cfg = load_cfg()
    cfg.update({k: v for k, v in kw.items() if v is not None})
    tmp = CFG_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CFG_PATH)          # 原子：要么旧要么新，没有半份
        shutil.copy2(CFG_PATH, CFG_PATH + ".bak")
    except OSError as e:
        global CFG_ERROR
        CFG_ERROR = f"settings.json 写失败：{e}"
        raise
    return cfg


def _get(url: str, headers: dict, timeout: int) -> dict:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(url: str, payload: dict, headers: dict, timeout: int) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # ★ 厂商把真正的原因写在响应体里。不读出来，用户只能看到一个
        #   "400 Bad Request"，等于什么都问不到。
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        raise RuntimeError("接口返回 %s：%s" % (e.code, (body or "")[:500]))


def _post_retry(url: str, payload: dict, headers: dict, timeout: int,
                tries: int = 2):
    """★ 连接级重试。只重试"没发出去/没连上"（拒绝、重置、DNS），

    不重试两类：读超时（服务端已经在算了，重来一遍双倍等待，
    还可能重复生成）和 HTTPError（服务端明确答复过，重试没意义）。
    """
    last = None
    for i in range(tries):
        try:
            return _post(url, payload, headers, timeout)
        except (urllib.error.HTTPError, RuntimeError):
            raise
        except (TimeoutError, socket.timeout):
            raise
        except Exception as e:
            last = e
            if i + 1 < tries:
                time.sleep(1.0 + i)
    raise last


def ping() -> tuple:
    """(通不通, 一句话说明)。分得清：连不上 / 没拉模型 / 鉴权失败。"""
    cfg = load_cfg()
    if cfg["backend"] == "none":
        return False, "后端设成了 none（不调模型）"
    ok, why = ensure_up(cfg)
    if not ok:
        return False, why
    try:
        if cfg["backend"] == "ollama":
            data = _get(cfg["base_url"].rstrip("/") + "/api/tags", {}, 10)
            names = [m.get("name", "") for m in data.get("models", [])]
            want = cfg["model"]
            # ★ 只认完整名或 :latest 别名。前缀匹配会把 qwen2.5:99b 认成已装
            ok = want in names or f"{want}:latest" in names
            if not ok and ":" not in want and f"{want}:latest" in names:
                ok = True
            if not ok:
                return False, "本地模型未就绪（模型文件缺失）"
            return True, "本地模型已就绪"
        else:
            headers = {"Authorization": f"Bearer {cfg['api_key']}"} if cfg.get("api_key") else {}
            _post(cfg["base_url"].rstrip("/") + "/v1/models", {}, headers, 15)
            return True, f"{cfg['backend']} 通"
    except urllib.error.HTTPError as e:
        return False, f"接口返回 {e.code}（多半是鉴权失败或地址不对）"
    except Exception as e:
        return False, f"连不上：{type(e).__name__}: {e}"


def _chat_url(base: str) -> str:
    """拼对话地址。不同厂商版本前缀不一样：
       DeepSeek  https://api.deepseek.com              → /v1/chat/completions
       Kimi      https://api.moonshot.cn/v1            → /chat/completions
       豆包方舟  https://ark.cn-beijing.volces.com/api/v3 → /chat/completions
       以前不分情况一律加 /v1/，导致 Kimi 和豆包都拼错。
    """
    u = (base or "").rstrip("/")
    low = u.lower()
    if low.endswith("/v1") or low.endswith("/v3") or "/api/v3" in low:
        return u + "/chat/completions"
    return u + "/v1/chat/completions"


def complete(system: str, user: str, temperature: float = None,
             max_tokens: int = None, num_ctx: int = None) -> str:
    """问一句，返回全文。

    ★ num_ctx 必须显式给：ollama 默认只开 2048 的上下文，
      而开书台生成章纲要吐 8192 token —— 上下文比输出还短，
      结果就是章纲写到一半被截断、后半截凭空消失。
      ⇒ 默认 16384（7B Q4 能吃下），调用方可覆盖。
    """
    cfg = load_cfg()
    if cfg["backend"] == "none":
        raise RuntimeError("后端是 none，调不了模型")
    temp = cfg["temperature"] if temperature is None else temperature
    cap = cfg["max_tokens"] if max_tokens is None else max_tokens
    if cfg["backend"] == "ollama":
        payload = {
            "model": cfg["model"],
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": False,
            # ★ 上下文要装得下：提示词 + 输出 + 思考余量。
            #   写死 16384 时，长设定+长上文会把正文挤掉（表现为写一半断）。
            "options": {
                "temperature": temp,
                "num_predict": cap,
                "num_ctx": min(max(
                    int(num_ctx or 0),
                    int(cfg.get("context") or 0),
                    int((len(system) + len(user)) / 1.2) + int(cap) + 3000,
                ), 32768),
            },
            "keep_alive": "30m",
        }
        # ★★ Qwen3 在这个 ollama 版本上关不掉思考，而且开关是反的，实测：
        #     think:false → 思考内容直接灌进 content（正文成"首先，用户要求…"）
        #     think:true  → 思考进 thinking 字段，content 才是干净正文
        #   关不掉（<arg_key:opensource> 软开关、预填空 think 块都试过，它照样想 800–1500 token），
        #   那就让它想、把思考单独隔开，只取 content。
        #   ⇒ 只对会思考的模型发这个字段：不会思考的模型收到它直接 400。
        if "qwen3" in cfg["model"].lower():
            payload["think"] = True
            # ★ 思考本身要吃 2000 token 左右：实测 np=300 时正文是空的，
            #   np=1500 才有 5 个字。所以给正文的额度之外，必须再加思考余量。
            payload["options"]["num_predict"] = int(cap) + 2500
        data = _post_retry(cfg["base_url"].rstrip("/") + "/api/chat", payload,
                           {}, cfg["timeout"])
        msg = data.get("message") or {}
        # ★ 有些版本会把思考也塞回 content（本项目的 Qwen3 就这样），
        #   这里再兜一道：把 <think>…</think> 整段剔掉。
        txt = msg.get("content") or ""
        txt = re.sub(r"<think>.*?</think>", "", txt, flags=re.S).strip()
        if not txt:
            # ★ 本地思考型模型（qwen3:4b 等）会把 num_predict 全花在思考上，
            #   正文就是空字符串。加大额度再来一次。
            payload["options"]["num_predict"] = max(cap * 3, 3500)
            data = _post_retry(cfg["base_url"].rstrip("/") + "/api/chat",
                               payload, {}, cfg["timeout"])
            txt = ((data.get("message") or {}).get("content") or "")
            txt = re.sub(r"<think>.*?</think>", "", txt, flags=re.S).strip()
        if not txt:
            # ★ 空正文不能当成功。以前返回 ""，界面看着像生成了，其实啥也没有。
            raise RuntimeError(
                "模型返回空正文（模型 %s，额度 %d）。多半是思考把额度吃光了，"
                "把这个模型的 max_tokens 调大再试。"
                % (cfg.get("model"), payload["options"]["num_predict"]))
        return txt

    headers = {"Authorization": f"Bearer {cfg['api_key']}"} if cfg.get("api_key") else {}
    data = _post_retry(_chat_url(cfg["base_url"]), {
        "model": cfg["model"],
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": temp,
        "max_tokens": cap,
    }, headers, cfg["timeout"])
    msg = ((data.get("choices") or [{}])[0].get("message") or {})
    txt = msg.get("content") or ""
    # ★ 思考型模型（deepseek-flash / v4-pro 等）会先想再答，
    #   额度小的时候思考把 token 全吃了，content 就是空字符串。
    #   这时加大额度重试一次，别把空字符串当成模型没回答。
    if not txt.strip() and msg.get("reasoning_content"):
        data = _post_retry(_chat_url(cfg["base_url"]), {
            "model": cfg["model"],
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": temp,
            # ★ 上限 8192：DeepSeek 单次输出最多 8192，
            #   超了服务端直接 400。之前放大到 32000 就是这个错。
            "max_tokens": min(max(cap * 2, 4096), 8192),
        }, headers, cfg["timeout"])
        txt = (((data.get("choices") or [{}])[0].get("message") or {})
               .get("content") or "")
    return txt

# ★ ping 与 ensure_up 必须说同一句话，否则「测连通」和实际调用会对不上
_ping_via_up_0 = ping


def ping():
    try:
        ok, msg = ensure_up()
    except Exception as e:
        return False, "检测失败：%s" % e
    try:
        cloud = (load_cfg() or {}).get("backend") != "ollama"
    except Exception:
        cloud = False
    if cloud:
        return ok, ("云端模型已联通" if ok else msg)
    return ok, ("本地模型已就绪" if ok else msg)
