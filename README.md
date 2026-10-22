# R4INWEN · AI 小说写作工作台

一个用 PySide6 写的桌面写作工具。不替你写，帮你把一本长篇管住。

> 只做流程和档案管理，模型能力外购（自备 API Key）。

---

## 它解决什么问题

用 AI 聊天写小说，写到第 30 章你会遇到这些：

- 设定散在几十个对话里，翻不回来
- 人物前后性格不一致，名字叫法乱
- 上一章埋的钩子忘了接
- 每章都要重新交代一遍背景

R4INWEN 把这些变成**文件**：设定、人物库、章纲、正文各存一份，每次生成自动带上。

---

## 功能

**开书台**
- 一键乱写出整套立项（题材 / 标签 / 主角 / 感情线）
- 或导入已有的 `.md` 设定
- 金手指、钩子、书名、简介各出 4~6 个候选
- 大纲（5 段）→ 细纲（每章一句）→ 章纲（每章任务 + 钩子）

**写作台**
- 三栏：AI 稿 / 对话 / 你的定稿
- 生成正文分 4 段，自动清洗（去标题、去元信息、去重复段）
- 生成后自动查红线，问题处标黄底红波浪线
- 人物库自动更新（状态、称呼、能力）
- 导入已有章节，接着往下写

**红线检查**
- 禁用词、套话、车轱辘话
- 机器味结构：结尾反转句、数字太齐、视觉多于身体、「然后」式推进、全篇零阻力

---

## 安装

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

需要 Python 3.10+。

---

## 配置

复制一份配置模板：

```bash
copy settings.example.json settings.json
```

然后填你的 API Key（DeepSeek / 豆包 / Kimi / 通义 / 智谱 都支持，走 OpenAI 兼容格式）。

```json
{
  "backend": "openai",
  "base_url": "https://api.deepseek.com",
  "model": "deepseek-chat",
  "api_key": "sk-你的key",
  "temperature": 0.9,
  "max_tokens": 8192,
  "context": 16000
}
```

启动：

```bash
python r4inwen.py
```

---

## 目录结构

```
books/<书名>/
  bible.md        设定
  characters.md   人物库
  outline.md      章纲
  skeleton.md     全书大纲
  beats.md        细纲
  chapters/       正文
  _bak/           覆盖前的旧版
corpus/
  rules.json      规则库
  train.jsonl     学习记录（可选）
```

---

## 两点说明

**关于质量**：生成质量取决于模型。7B 本地模型只能给初稿，需要你改。想要能直接发，得用更强的模型。

**关于 AI 检测**：本工具不承诺过 AI 检测。它的红线只能帮你找出结构上的机器味，最终还是要人改。

---

## 许可

MIT，见 LICENSE。
---

## 关于生成质量（重要）

本仓库是**社区版**，只附带通用的写作约束：

- `launch.py` 里的提示词只有格式／长度／语气三条约束
- `rules.py` 只有 5 条通用写作守则
- `corpus/rules.json` 是空骨架，没有任何题材规则

**高质量的提示词和题材规则库不在本仓库内。**

所以它开箱只能给初稿，需要你自己改。这不是 bug。

想提高质量，有两条路：

1. **填 `corpus/rules.json`**：按里面的结构，给每个题材写 hook / beat / trope / taboo / struct
2. **改 `launch.py`**：把 `CHEAT_USER`、`HOOK_USER`、`TITLE_USER` 这些常量换成你自己的要求

也可以在写作页点「学习」，把你改好的章节存成训练数据（`corpus/train.jsonl`），
攒够量之后用 Unsloth 做一次 QLoRA，让模型学会你的文风。
