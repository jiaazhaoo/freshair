# what-do-you-think

> AI 聊久了会变笨。不是模型退化，是它开始在自己的结论上盖楼——每一层都建立在上一层没人质疑过的假设上。
>
> 这个 skill 做一件事：把**当前这段对话的原始记录**，原封不动地丢给一个从没参与过的模型，让它说实话。

```
你 ──── 聊了 60 轮 ────> Claude （已经陷进去了）
                           │
                           │  /wdyt
                           ▼
                    session.jsonl（原始记录，未经转述）
                           │
                           ▼
                      OpenRouter
                     ╱          ╲
              GPT-5.1        Gemini 3 Pro
                     ╲          ╱
                    「你们跑偏了，第 23 轮开始」
```

## 为什么不是又一个 "second opinion" 工具

市面上的同类工具都少了关键一环：

| 工具 | 送出去的是什么 |
|---|---|
| [consult-llm](https://github.com/raine/consult-llm) | 你手动挑的文件 |
| [fresheyes](https://github.com/danshapiro/fresheyes) | git diff |
| [second-opinion](https://github.com/dshills/second-opinion) / [ai-council-mcp](https://github.com/0xakuti/ai-council-mcp) 等 MCP | **Claude 自己写的一段转述** |
| **what-do-you-think** | **完整的原始对话记录** |

最后一类的问题最致命：让已经跑偏的那个人去概括"我们在干嘛"，它会自动滤掉自己认为不重要的东西——而那正好就是它跑偏的地方。转述这个动作本身就把要检查的东西弄丢了。

所以这里的设计是**绕开 Claude**：脚本直接从磁盘上读 `~/.claude/projects/<项目>/<会话>.jsonl`，原样发出去。Claude 只负责执行命令和转达结果，不负责概括。

diff 只能看到"改成了什么"，对话记录能看到"为什么改成这样"——包括那句在第 23 轮被随口带过、从此再没人回头看的假设。

## 安装

```bash
git clone https://github.com/jiaazhaoo/what-do-you-think.git
cd what-do-you-think
./install.sh                    # 装到 ~/.claude/skills/（全局可用）
```

或者只在某个项目里用：

```bash
cp -r skills/what-do-you-think /你的项目/.claude/skills/
```

然后配一个 [OpenRouter](https://openrouter.ai/keys) 的 key：

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."   # 写进 ~/.zshrc 或 ~/.bashrc
```

只依赖 Python 3.9+ 标准库，没有 `pip install`。

## 用法

在 Claude Code 里，直接说人话就行：

```
问问别的模型我们这么搞对不对
换个脑子看看这段
我们是不是跑偏了
wdyt
```

也可以直接跑脚本：

```bash
# 全面审视当前会话
python3 ~/.claude/skills/what-do-you-think/scripts/wdyt.py

# 指定关注点
python3 ~/.claude/skills/what-do-you-think/scripts/wdyt.py "这个缓存层值不值得"

# 换模型 / 多模型并行
python3 ~/.claude/skills/what-do-you-think/scripts/wdyt.py -m anthropic/claude-opus-4.6 -m x-ai/grok-4

# 先看看到底要发出去什么，不调 API
python3 ~/.claude/skills/what-do-you-think/scripts/wdyt.py --dry-run
```

### 常用参数

| 参数 | 说明 |
|---|---|
| `-m, --model` | OpenRouter 模型 id，可重复或逗号分隔，多个模型并行请求 |
| `--dry-run` | 打印完整 payload，不发请求 |
| `--thinking` | 带上 Claude 的思考过程（payload 大约翻倍） |
| `--budget N` | 最多发送多少字符，默认 140000 |
| `--head-turns N` | 开头永远保留的轮数，默认 6——保住"最初要求"才看得出跑偏 |
| `--no-diff` | 不附带 git diff |
| `--save PATH` | 顺便存一份到文件 |
| `--lang` | 指定回复语言，默认跟着对话里人类用的语言走 |
| `--transcript PATH` | 审别的会话记录，不是当前这个 |

### 配置默认模型

项目根目录放 `.wdyt.json`，或者 `~/.config/wdyt/config.json`：

```json
{
  "models": ["openai/gpt-5.1", "google/gemini-3-pro"],
  "budget": 140000,
  "temperature": 0.7
}
```

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

对话记录会发给 OpenRouter 和实际提供模型的厂商，其中包含工具输出、文件内容、以及对话里出现过的代码。

脚本在发送前会清洗常见的凭据格式（OpenAI / Anthropic / OpenRouter key、GitHub token、AWS access key、Google API key、Slack token、JWT、私钥块）。**这是安全网，不是保证。** 在敏感仓库里先跑 `--dry-run` 看一眼再说。

## License

MIT
