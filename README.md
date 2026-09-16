# FreshAir

> 长任务里的 AI 会越来越狭隘。不是模型退化，是它开始在自己的结论上盖楼——每一层都建立在上一层没人质疑过的假设上，而它在里面看不见这件事。
>
> `/freshair` 开一扇窗：把**最初的目标**和**现在的成果**交给一个从没参与过这段对话的 AI，中间那一百轮自我论证全部丢掉，让它在全新的上下文窗口里从零推一遍。

```
你 ──── 聊了 60 轮 ────> Claude （已经陷进去了）
                           │
                           │  /freshair
                           ▼
                    session.jsonl
                           │
              ┌────────────┴────────────┐
         最初的目标                 当前的成果
        （人类说的话）              （git diff）
              └────────────┬────────────┘
                           │      ← 中间 140 轮 agent 自述，扔掉
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
    codex exec        gemini -p        OpenRouter
   （你的 ChatGPT   （你的 Google      （按量付费）
     账号，已登录）    账号，已登录）
          └────────────────┼────────────────┘
                           ▼
              「这东西没在解决你说的那个问题」
```

## 为什么要扔掉中间过程

直觉上应该把完整对话发过去——信息越多判断越准。**恰恰相反。**

把 100 轮对话喂给外部模型，它会先读到那套推理，**然后才形成判断**。于是它被同一条思路锚定，点头同意，你花的钱买了一个回声。

只给「目标 + 现状」，它没有路径可循，只能自己从目标推一遍。分歧就是在这里产生的——而分歧才是你要的东西。

而且判断跑偏本来就只需要两个端点：当初要什么，现在有什么。中间过程是用来定位"哪一轮歪的"，那是另一个问题，`--mode full` 专门干这个。

## 为什么不是又一个 "second opinion" 工具

市面上的同类工具都少了关键一环：

| 工具 | 送出去的是什么 |
|---|---|
| [consult-llm](https://github.com/raine/consult-llm) | 你手动挑的文件 |
| [fresheyes](https://github.com/danshapiro/fresheyes) | git diff |
| [second-opinion](https://github.com/dshills/second-opinion) / [ai-council-mcp](https://github.com/0xakuti/ai-council-mcp) 等 MCP | **Claude 自己写的一段转述** |
| **FreshAir** | **人类原话说的目标 + 当前 diff，agent 的自述一句不发** |

最后一类的问题最致命：让已经跑偏的那个人去概括"我们在干嘛"，它会自动滤掉自己认为不重要的东西——而那正好就是它跑偏的地方。转述这个动作本身就把要检查的东西弄丢了。

所以这里的设计是**绕开 Claude**：脚本自己从磁盘上读 `~/.claude/projects/<项目>/<会话>.jsonl`，自己挑出人类说过的话当目标。Claude 只负责执行命令和转达结果，从头到尾没机会"概括"任何东西。

跟 fresheyes 那种只发 diff 的也不一样：diff 只说明"改成了什么"，说不清"当初到底要什么"。目标必须是人类的原话，不能是从代码反推出来的。

## 不用申请 key，用你已经登录的账号

优先走**本机已装好、已登录**的厂商 CLI。请求从你的机器直接发给你本来就在付费的厂商，不经过任何第三方，也不用复制粘贴任何 key。

| 后端 | 认证方式 | 说明 |
|---|---|---|
| `codex` | 你的 ChatGPT 登录（Codex CLI） | 首选——真正的局外人 |
| `gemini` | 你的 Google 登录（Gemini CLI） | 首选——真正的局外人 |
| `openrouter` | `OPENROUTER_API_KEY` | 想指定任意模型时用，按量计费 |
| `claude` | 你的 Anthropic 登录（Claude Code CLI） | 兜底。**同厂同权重，盲点也是同一套** |

`--backend auto`（默认）**优先挑跟你不同厂商的**。真落到 `claude` 时会在 stderr 打一条警告说明——同厂互审是这个工具最弱的形态，你有权知道自己拿到的是哪一种。

CLI 后端一律以只读方式调起（`codex exec --sandbox read-only`、`claude -p --disallowed-tools Edit Write NotebookEdit Bash`），审阅者能翻代码核实，但改不了任何东西。

各家 CLI 的 flag 会变。所以别信文档，直接实测——每个后端发一个只回一个 token 的探针：

```bash
python3 ~/.claude/skills/freshair/scripts/freshair.py --check
```

```
Probing backends (a real call each, one token of output):
  ✓ codex       codex exec --sandbox read-only --ask-for-approval never -   (3.1s)
  – gemini      not installed (gemini not on PATH)
  ✓ claude      claude -p --disallowed-tools Edit Write NotebookEdit Bash   (4.3s)
  ✗ openrouter — OPENROUTER_API_KEY is not set
```

失败时会打出完整命令行，照着改 `.freshair.json` 里的 `backends` 就行，不用动代码。

## 安装

一条命令，不用 clone，不用 git：

```bash
curl -fsSL https://raw.githubusercontent.com/jiaazhaoo/what-do-you-think/main/install.sh | bash
```

装到 `~/.claude/skills/freshair`，所有项目通用。装完在任何 Claude Code 会话里敲 `/freshair`。

只依赖 Python 3.9+ 标准库，没有 `pip install`。

<details>
<summary>其他装法</summary>

从 clone 装（会用软链，`git pull` 就等于更新）：

```bash
git clone https://github.com/jiaazhaoo/what-do-you-think.git
cd what-do-you-think && ./install.sh
```

只给某一个项目用：

```bash
cp -r skills/freshair /你的项目/.claude/skills/   # 目录名决定斜杠命令，别改
```

仓库名是 `what-do-you-think`，工具名是 FreshAir——同一个东西，不是拿错了地址。
</details>

### 配一个"局外人"后端

装完就能用——但如果这台机器上只有 Claude Code，那是**同厂互审**（新的上下文窗口，同一套权重和盲点），脚本每次都会警告你。

想要真正的局外人，装一个你**本来就在付费**的 CLI 就行，用已有账号登录，不用申请 key、不用额外花钱：

```bash
npm i -g @openai/codex      && codex    # 用 ChatGPT 账号登录
npm i -g @google/gemini-cli && gemini   # 用 Google 账号登录
```

或者配 [OpenRouter](https://openrouter.ai/keys)（想指定任意模型时用）：`export OPENROUTER_API_KEY=...`

装完实测一下，每个后端发一个 token 的探针：

```bash
python3 ~/.claude/skills/freshair/scripts/freshair.py --check
```

## 用法

在 Claude Code 里敲斜杠：

```
/freshair
/freshair 这个缓存层到底值不值得
```

或者直接说人话，Claude 会自己调起来：

```
问问别的模型我们这么搞对不对
换个脑子看看这段
我们是不是跑偏了
```

也可以脱离 Claude Code 直接跑：

```bash
W=~/.claude/skills/freshair/scripts/freshair.py

python3 $W                              # 自动挑后端，全面审视
python3 $W "这个缓存层值不值得"           # 指定关注点
python3 $W -b codex                     # 强制走 ChatGPT
python3 $W -b openrouter -m x-ai/grok-4 -m anthropic/claude-opus-4.6   # 多模型并行
python3 $W --dry-run                    # 只看要发出去什么，不发请求
```

### 常用参数

| 参数 | 说明 |
|---|---|
| `--mode` | `fresh`（默认，只发目标+现状）/ `full`（发完整对话，用于定位"哪一轮歪的"） |
| `--goal-turns N` | 只取前 N 条人类指令当目标 |
| `--no-claim` | 连 agent 自己的进度总结也不发，只留目标和 diff |
| `-b, --backend` | `auto`（默认）/ `codex` / `gemini` / `claude` / `openrouter` |
| `-m, --model` | 模型 id，可重复或逗号分隔，多个并行。CLI 后端不填就用它自己的默认模型 |
| `--dry-run` | 打印完整 payload **和将要执行的命令**，不发请求 |
| `--thinking` | 带上 Claude 的思考过程（payload 大约翻倍） |
| `--budget N` | 最多发送多少字符，默认 140000 |
| `--head-turns N` | 开头永远保留的轮数，默认 6——保住"最初要求"才看得出跑偏 |
| `--no-diff` | 不附带 git diff |
| `--save PATH` | 顺便存一份到文件 |
| `--lang` | 指定回复语言，默认跟着对话里人类用的语言走 |
| `--check` | 逐个实测后端是否真的能用，打印实际执行的命令 |
| `--session-id` | 手动指定会话 id（自动识别选错时用） |
| `--transcript PATH` | 审别的会话记录，不是当前这个 |

### 配置

项目根目录放 `.freshair.json`，或 `~/.config/freshair/config.json`。完整示例见 [`.freshair.example.json`](.freshair.example.json)：

```json
{
  "backend": "codex",
  "budget": 140000,
  "backends": {
    "codex": { "cmd": ["codex", "exec", "--sandbox", "read-only"], "tail": ["-"] }
  }
}
```

每个 CLI 后端的命令行都可以在这里覆盖——上游改了 flag，改配置就行，不用动代码。

## 它到底问了什么

发出去的 prompt 不是"帮我 review 一下"——那种问法只会换来一堆礼貌的废话。外部模型被要求回答六件具体的事：

- **判断** — KEEP GOING / ADJUST COURSE / STOP AND RETHINK，一句话说清
- **跑偏了吗** — 引用最初那条需求，对比现在在造的东西，指出是哪一轮开始歪的
- **没人质疑过的假设** — 对话里被当成既定事实、但其实不是的东西
- **具体问题** — 按破坏力排序，必须指到具体位置
- **更简单的做法** — 包括"这一整块可以删掉"这种答案
- **如果我从零开始** — 只知道最初目标的话，它会怎么做

同时明确禁止：开场恭维、"注意边界情况"这类空话、把 Claude 的说法当证据。

超预算时不是简单砍掉旧的：开头几轮（最初的需求）和最近几轮（当前的成果）都完整保留，中间部分省略并标注。丢了最初的需求，就没法判断跑偏。

## 隐私

对话记录（含工具输出、文件内容、出现过的代码）会发给被问的那个模型。走 CLI 后端时从你的机器直达厂商；走 `openrouter` 时还会多经过 OpenRouter 一道。

脚本在发送前会清洗常见的凭据格式（OpenAI / Anthropic / OpenRouter key、GitHub token、AWS access key、Google API key、Slack token、JWT、私钥块）。**这是安全网，不是保证。** 在敏感仓库里先跑 `--dry-run` 看一眼再说。

## 开发

```bash
python3 -m unittest discover -s tests -v
```

36 个测试，覆盖 transcript 解析（角色标注、sidechain 过滤、system-reminder 剥离、工具输出截断）、预算裁剪（头尾保留、中段省略、极端情况）、凭据脱敏（正例与误伤）、后端解析（参数顺序、只读 flag、配置覆盖、优先级）。纯 stdlib，不联网、不起子进程。

CI 在 Python 3.9 / 3.11 / 3.13 上跑。

## License

MIT
