# FreshAir

> On a long task an AI gets narrower, not smarter. It builds on its own earlier
> conclusions, stops generating options, and cannot see this from the inside.
>
> `/freshair` opens a window: it hands the **original goal** and the **current
> state** to a model that never sat in this conversation, throws away the
> hundred turns of self-justification in between, and lets it reason from
> scratch.

```
you ──── 60 turns in ────> Claude (already anchored)
                              │
                              │  /freshair
                              ▼
                       session.jsonl
                              │
                 ┌────────────┴────────────┐
             the goal                 what exists
        (what you actually said)       (the diff)
                 └────────────┬────────────┘
                              │   ← 140 turns of agent reasoning: dropped
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
    codex exec            gemini -p           OpenRouter
  (your ChatGPT         (your Google          (pay per call)
   login, already        login, already
   signed in)            signed in)
          └───────────────────┼───────────────────┘
                              ▼
            "this isn't solving the problem you stated"
```

## Why throw the middle away

The intuition is to send the whole conversation — more context, better judgment.
**It is the opposite.**

Feed a reviewer 100 turns and it reads the reasoning *before* it forms a view.
It gets anchored on the same path, nods along, and you have paid for an echo.

Give it only the goal and the artifact and it has no path to follow. It has to
work forward from the goal on its own. That is where disagreement comes from,
and disagreement is the entire product.

Detecting drift never needed the middle anyway — only the two endpoints: what
was asked, and what exists. Locating *which turn* went wrong is a different
question, and `--mode full` is there for it.

## How this differs from the other second-opinion tools

| Tool | What it sends out |
|---|---|
| [consult-llm](https://github.com/raine/consult-llm) | files you pick by hand |
| [fresheyes](https://github.com/danshapiro/fresheyes) | a git diff |
| [second-opinion](https://github.com/dshills/second-opinion) / [ai-council-mcp](https://github.com/0xakuti/ai-council-mcp) and other MCP servers | **a summary the stuck agent wrote itself** |
| **FreshAir** | **your own words as the goal, plus the diff — none of the agent's narration** |

That last row is the important one. Asking the agent that already drifted to
summarize "what we're doing" means it filters out whatever it considers
unimportant — which is precisely where it drifted. The act of summarizing
destroys the thing you wanted examined.

So the design routes around the agent: the script reads the session log off
disk itself and picks out what the human said. The agent never gets a chance to
narrate.

It reads both hosts — Claude Code (`~/.claude/projects/...`) and Codex
(`$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl`, both current
`response_item` and legacy `event_msg` records) — and reviews whichever session
for this directory was written most recently. Force one with `--from claude` or
`--from codex`.

This is also why a diff alone is not enough: a diff shows what changed, not what
was asked for. The goal has to be the human's own words, not something inferred
backwards from the code.

## No API key — it uses accounts you are already signed in to

FreshAir prefers a vendor CLI already installed and authenticated on your
machine. The request goes straight from your machine to a vendor you already pay,
with no key to copy and no third party in the path.

| Backend | Auth | Notes |
|---|---|---|
| `codex` | your ChatGPT login (Codex CLI) | preferred — a genuine outsider |
| `gemini` | your Google login (Gemini CLI) | preferred — a genuine outsider |
| `openrouter` | `OPENROUTER_API_KEY` | for picking an arbitrary model; billed per call |
| `claude` | your Anthropic login (Claude Code CLI) | last resort. **Same vendor, same weights, same blind spots** |

`--backend auto` (the default) **prefers a vendor different from the one you are
talking to**. When it does fall back to `claude` it says so on stderr, because a
same-vendor review is a much weaker signal than it looks.

Reviewers are always invoked read-only (`codex exec --sandbox read-only`,
`claude -p --disallowed-tools Edit Write NotebookEdit Bash`) and run from a
scratch directory, with the repo handed back as a readable path. They can open a
file to check a claim; they cannot change anything, and they cannot write into
the session log they are reviewing.

## Getting a genuinely independent reviewer

"A different model" is the weakest version of independence. Four things actually
separate a reviewer from the agent that is stuck, in rough order of how much
they buy you:

1. **A different context.** `--mode fresh`, the default. A reviewer that reads
   the reasoning first is anchored by it before it forms a view.
2. **A different vendor.** Different weights, different training, different
   failure modes. `--backend auto` prefers a vendor other than the host's and
   warns when it cannot find one.
3. **More than one, asked independently.** This is the big one:

   ```bash
   /freshair -b all              # every reviewer this machine can reach
   /freshair -b codex -b gemini  # or name them
   ```

   They run in parallel and never see each other's answers. **Where two
   independently land on the same problem, that is the strongest signal
   available.** Something only one raises is a lead, not a finding. Where they
   contradict each other is usually the most interesting part of the report.
4. **Nothing of the agent's narration.** `--no-claim` drops even the agent's own
   summary of where things stand, leaving the reviewer the goal and the diff and
   nothing else.

Running with one same-vendor CLI gets you (1) only. Running `-b all` on a
machine with Codex and Gemini signed in gets you all four.

## Install

One command. No clone, no git:

```bash
curl -fsSL https://raw.githubusercontent.com/jiaazhaoo/what-do-you-think/main/install.sh | bash
```

It installs three things:

- the skill in `~/.claude/skills/freshair` — invoke it with **`/freshair`** in
  Claude Code
- the same skill in `$CODEX_HOME/skills/freshair` when Codex is present —
  **`$freshair`** there, after restarting Codex
- a **`freshair`** launcher in `~/.local/bin`, for the setup commands below

If `~/.local/bin` is not on your `PATH` the installer says so and prints the
line to add. You can skip the launcher entirely — every `freshair ...` command
in this README also works as:

```bash
python3 ~/.claude/skills/freshair/scripts/freshair.py ...
```

Python 3.9+ standard library only. No `pip install`.

<details>
<summary>Other ways to install</summary>

From a clone (symlinks it, so `git pull` is the update):

```bash
git clone https://github.com/jiaazhaoo/what-do-you-think.git
cd what-do-you-think && ./install.sh
```

For a single project:

```bash
cp -r skills/freshair /your/project/.claude/skills/   # the directory name is the slash command; don't rename it
```

The repository is named `what-do-you-think` and the tool is named FreshAir. Same
thing, not a wrong link.
</details>

### Give it a real outsider

It works immediately after install — but if Claude Code is the only CLI on the
machine, that is Claude reviewing Claude: a fresh context window, but the same
weights and the same blind spots. The script warns you every time.

For a genuine outsider, install a CLI you already pay for. It reuses the existing
login, so there is no key to request and nothing extra to buy:

```bash
npm i -g @openai/codex      && codex    # sign in with ChatGPT
npm i -g @google/gemini-cli && gemini   # sign in with Google
```

Or use OpenRouter to name an arbitrary model. Connect it from inside the
session you are already in — no terminal, no PATH, nothing to paste:

```
/freshair --login
```

A browser opens, you approve, and the key goes straight to disk. Any flag works
this way: `/freshair --check`, `/freshair --show-config`, `/freshair -b codex`.
From a terminal the same thing is `freshair --login`.

In a cloud or web session the browser is not on your machine, so the callback
cannot come back. FreshAir detects that and switches to two steps by itself: it
prints a URL, and after you approve — your browser will fail to load a localhost
page, which is expected — you paste the `code=` from the address bar into
`/freshair --login --code <code>`.

It opens OpenRouter, waits on a loopback callback, and stores the key in
`~/.config/freshair/config.json` with mode 600. Nothing to copy, nothing pasted
into a shell, no key in your history — it is never displayed at all. The flow is
[OAuth PKCE](https://openrouter.ai/docs/guides/overview/auth/oauth): only a hash
of a one-time secret leaves this machine up front, and the secret itself only on
the exchange, so an intercepted callback is not enough to mint a key.

If you already have a key in hand, `freshair --set-key sk-or-v1-...` skips the
browser. Either way a key in the *repo* config is refused and says so, because
that file gets committed.

Then confirm what actually works on this machine — one real call per backend,
one token of output each:

```bash
python3 ~/.claude/skills/freshair/scripts/freshair.py --check
```

## Switching provider and models

Three levels, in increasing order of permanence.

**Per call** — flags win over everything:

```bash
/freshair -b codex                       # this one time, use Codex
/freshair -m x-ai/grok-4                 # this one time, one specific model
/freshair -b all                         # this one time, ask everyone reachable
```

**Saved as the default** — build the call you want, add `--save-default`:

```bash
freshair -b openrouter -m openai/gpt-5.1 --save-default
# Saved as the default: backend=openrouter  models=['openai/gpt-5.1']
```

After that, plain `/freshair` uses it. No JSON to write by hand.

**Saved as a named profile** — for switching between setups:

```bash
freshair -b openrouter -m x-ai/grok-4,openai/gpt-5.1,google/gemini-3-pro --save-default council
freshair -b codex --save-default quick
```

```bash
/freshair              # your default: one model, cheap
/freshair -p quick     # local Codex, no API cost
/freshair -p council   # three models in parallel, cross-vendor
```

To see what is set and where it came from:

```bash
freshair --show-config
```

```
Resolved:
  backend  openrouter
  models   openai/gpt-5.1
  api_key  sk-or-v1-abc...  [~/.config/freshair/config.json]

Profiles:
  -p council      backend=openrouter  models=x-ai/grok-4,openai/gpt-5.1,google/gemini-3-pro
  -p quick        backend=codex

Reachable right now: claude, openrouter
```

Precedence, highest first: command-line flags, then `-p` profile, then
`.freshair.json` in the repo, then `~/.config/freshair/config.json`. The
OpenRouter key is the exception — an `OPENROUTER_API_KEY` in the environment
always wins, so CI can override a stored one.

## Usage

In Claude Code:

```
/freshair
/freshair is the caching layer worth it
```

Or just say it, and Claude will reach for it: "are we off track", "am I
overcomplicating this", "get a second opinion on this".

You can also run it outside Claude Code:

```bash
F=~/.claude/skills/freshair/scripts/freshair.py

python3 $F                          # pick a backend automatically, review everything
python3 $F "is the caching worth it"  # focus on one question
python3 $F -b codex                 # force a backend
python3 $F -b openrouter -m x-ai/grok-4 -m anthropic/claude-opus-4.6   # several models in parallel
python3 $F --dry-run                # show exactly what would be sent, call nothing
```

### Options

| Flag | What it does |
|---|---|
| `--mode` | `fresh` (default: goal + current state) or `full` (whole transcript, for "which turn went wrong") |
| `--goal-turns N` | Use only the first N human instructions as the goal |
| `--no-claim` | Withhold the agent's own progress summary too, leaving only the goal and the diff |
| `-b, --backend` | `auto` (default) / `all` / `codex` / `gemini` / `claude` / `openrouter`. Repeat it to ask several independent reviewers at once |
| `-p, --profile` | Use a saved profile |
| `--save-default [NAME]` | Remember this call's `-b`/`-m` choice, as the default or as a named profile |
| `--login` | Connect OpenRouter in the browser; the key is stored, never shown |
| `--set-key KEY` | Store a key you already have, without the browser |
| `--show-config` | Print every setting, where it came from, and what is reachable |
| `--from` | `claude` or `codex` — which host's session to read. Defaults to the most recent |
| `-m, --model` | Model id; repeat or comma-separate to run several in parallel. With more than one backend these apply to `openrouter` only, since model ids are not portable between vendors |
| `--check` | Probe every backend with a one-token call and print the exact command used |
| `--dry-run` | Print the full payload and the command, send nothing |
| `--thinking` | Include the agent's thinking blocks (roughly doubles the payload) |
| `--budget N` | Max transcript characters, default 140000 |
| `--no-diff` | Don't attach the git diff |
| `--save PATH` | Also write the review to a file |
| `--lang` | Language for the review. Defaults to English |
| `--session-id` | Name the session explicitly if autodetection picks wrong |
| `--transcript PATH` | Review a different session's log |

### Configuration

Drop a `.freshair.json` in the repository root, or at
`~/.config/freshair/config.json`. Full example in
[`.freshair.example.json`](.freshair.example.json):

```json
{
  "backend": "codex",
  "budget": 140000,
  "backends": {
    "codex": { "cmd": ["codex", "exec", "--sandbox", "read-only"], "tail": ["-"] }
  }
}
```

Every CLI backend's command line is overridable there — when an upstream flag
changes, it is a config edit, not a patch.

## What it actually asks

The prompt is not "please review this". That phrasing buys nothing but polite
noise. The outside model is required to answer six specific things:

- **Verdict** — KEEP GOING / ADJUST COURSE / STOP AND RETHINK, in one line
- **Does this solve the stated problem** — hold what exists against the goal; what is missing, and what is here that was never asked for
- **How you would have approached it** — knowing only the goal, what would you reach for?
- **Unexamined assumptions** — what the design takes for granted, and what it costs if wrong
- **Concrete problems** — cited to file and line, ordered by damage
- **What can be deleted** — including "most of this"

And it is explicitly forbidden to open with praise, to write filler like
"consider edge cases", or to treat the agent's framing as evidence.

When the payload is over budget, the oldest turns are not simply dropped: the
opening instructions and the most recent work are both kept intact and the middle
is elided with a marker. Lose the original ask and you cannot judge drift.

## Privacy

The transcript — including tool output, file contents, and code that appeared in
the conversation — goes to whichever model is asked. On a CLI backend it goes
straight from your machine to a vendor you already have an account with; on
`openrouter` it also passes through OpenRouter.

Credential-shaped strings are scrubbed before sending (OpenAI / Anthropic /
OpenRouter keys, GitHub tokens, AWS access keys, Google API keys, Slack tokens,
JWTs, private key blocks). **That is a safety net, not a guarantee.** In a
repository with sensitive material, run `--dry-run` first and read what is going
out.

## Development

```bash
python3 -m unittest discover -s tests -v
```

114 tests covering transcript parsing for both hosts (Claude Code, and Codex in
both its record generations) (role labelling, sidechain filtering,
system-reminder stripping, tool-output capping), goal extraction (human
instructions only, boilerplate and harness notices rejected, retyped
instructions deduplicated), budget fitting, credential redaction, self-
contamination defences, and backend resolution. Standard library only — no
network, no subprocesses.

CI runs on Python 3.9, 3.11 and 3.13.

## License

MIT
