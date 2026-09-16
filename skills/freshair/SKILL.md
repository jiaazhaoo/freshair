---
name: FreshAir
description: Get an outside opinion on the current work from a model that has not seen this conversation. By default it sends only the original goal and the current state, so the reviewer cannot be anchored by the reasoning that got you here. Also read this to know when to OFFER an outside read unprompted - the same file reworked repeatedly, the same test failing twice, a decision reversed twice, or a long session with nothing verified. Routes through a locally installed CLI the user is already signed in to (Codex, Gemini, Claude) or OpenRouter. Use when the conversation has gone long and circular, when the same fix keeps failing, before committing to a big refactor, or whenever the user asks for a second opinion, outside eyes, a sanity check, or says things like "what do you think", "am I overcomplicating this", "are we off track", "freshair", "fresh air", "sanity check", "outside eyes". Also good on a long task before committing to a direction. Not for ordinary code review of a diff.
argument-hint: [what to look at]
allowed-tools: Bash(python3 *)
---

# FreshAir

Open a window. Put the work in front of a model that has never seen this
conversation, and relay what it says back.

The problem this exists for: on a long task an agent gets narrower, not
smarter. It builds on its own earlier conclusions, stops generating options, and
cannot see this from the inside — which is why it has to be a different model,
in a different context window, that never watched it get here.

## What actually gets sent, and why

**Default (`--mode fresh`): only the goal and the current state.** Every
instruction the human gave, plus the git diff — and nothing in between. All the
agent reasoning is deliberately withheld.

That withholding is the feature, not a size optimisation. Hand a reviewer the
full back-and-forth and it reads your justifications *before* it forms a view,
which anchors it on the same path you are stuck on. It agrees with you, and the
whole exercise is wasted. Given only the destination and the goal, it has to
reason forward on its own — and that is where a genuinely different answer comes
from.

**`--mode full` sends the whole transcript instead.** Use it for one specific
question: *where exactly did this go wrong?* Locating the turn that drifted needs
the turns. Anything else is better served by `fresh`.

Either way, **do not pre-digest the conversation.** Do not write a "here's what
we're doing" summary into the prompt. Your summary preserves the assumptions that
need challenging — that is the thing being routed around. The script reads the
session `.jsonl` off disk itself. Just run it.

## Offering it before you are asked

The premise is that a narrowing agent cannot see its own narrowing. So waiting
to be asked puts the decision in the hands of the one party that has already
lost the ability to make it. Watch for these instead — each is a state you can
check about yourself, not a feeling:

- You have edited the same file three or more times this session, and a recent
  edit undid or reworked an earlier one.
- The same command or test has failed twice or more, and your fixes are getting
  longer rather than shorter.
- You have reversed a decision twice ("actually, let's...").
- The user has repeated or rephrased a request because your answer missed what
  they meant.
- You are about to start something expensive to undo — a refactor across many
  files, a schema change, picking a dependency.
- Long session, and nothing has been verified against reality in a while: no
  test run, no command output, no file read back.

When one of these is true, say so in **one line** and let the user decide:

> We have rewritten this retry logic three times and it is getting longer each
> time. Want me to run `/freshair` and get an outside read before I try again?

**Suggest; do not invoke.** Running it spends the user's tokens or quota and
sends their code and tool output off the machine. That is their call, every
time. One offer is enough — if they decline or ignore it, carry on and do not
raise it again for that same issue.

## Running it

Call the script; `FA` below is just shorthand for its path:

```bash
FA="${CLAUDE_SKILL_DIR:-${CODEX_HOME:-$HOME/.codex}/skills/freshair}/scripts/freshair.py"

python3 "$FA"                                   # review this session
python3 "$FA" "is the caching layer worth it"   # with a focus
```

`CLAUDE_SKILL_DIR` is set inside Claude Code; the fallback covers Codex,
including a custom `CODEX_HOME`. In Claude Code you may also pass
`--session-id "${CLAUDE_SESSION_ID}"` if autodetection picks the wrong session.

### Arguments that are flags, not focus text

If the user's argument starts with `-`, it is an option — pass it straight
through instead of quoting it as a focus string:

| The user types | You run |
|---|---|
| `/freshair -b codex` | `python3 "$FA" -b codex` |
| `/freshair -p council why is this slow` | `python3 "$FA" -p council "why is this slow"` |
| `/freshair --mode full` | `python3 "$FA" --mode full` |
| `/freshair why is this slow` | `python3 "$FA" "why is this slow"` |

### Setup belongs in a terminal, not here

`--login`, `--set-key`, `--save-default` and `--show-config` all work through
the slash command, but **say plainly that the terminal is the better place for
them** if the user is doing setup. A slash command is a prompt to you: it costs
a turn, it is slower, and it depends on you reading it correctly. In a terminal
`freshair --login` is a program that runs immediately.

It matters most for `--login`. There it opens a browser on whichever machine the
script runs on:

- **User's own machine** — the browser is theirs. One click, done.
- **Cloud or web session** — it is not theirs, and no callback can come back.
  The script detects this and prints a URL plus instructions to paste the code
  back with `--login --code`. Relay both. This works, but it is three steps
  where the terminal on their own machine is one.

So when someone asks how to connect OpenRouter, the answer is `freshair --login`
in a terminal on their own machine. Offer the in-session path only when they
cannot get to one.

### Which session it reads

It reviews the most recently written session for this directory, from either
host: Claude Code (`~/.claude/projects/...`) or Codex
(`$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl`). Force one with
`--from claude` or `--from codex`.

### Backends

`--backend auto` (the default) prefers a CLI from a **different vendor** than
you, because a model that shares your weights shares your blind spots:

| Backend | Auth | Notes |
|---|---|---|
| `codex` | the user's existing ChatGPT login | preferred — genuinely outside |
| `gemini` | the user's existing Google login | preferred — genuinely outside |
| `openrouter` | `freshair --login`, or `OPENROUTER_API_KEY` | any model, but the user pays per call |
| `claude` | the user's existing Anthropic login | last resort, same vendor as you |

When every reviewer is the same vendor as you, the script says so — both on
stderr and quoted at the top of the report itself, so it reaches the user
whether or not you relay it. Escaping the context is the main mechanism and it
does most of the work; a different vendor is an increment on top. Report which
one they got, without implying the result is worthless — it is not.

### Options worth knowing

| Flag | Use |
|---|---|
| `--mode` | `fresh` (default) or `full` — see above. |
| `--goal-turns N` | Use only the first N human instructions as the goal. Handy when a long session has accumulated steering that muddies the original ask. |
| `--no-claim` | Drop the agent's own account of where things stand, leaving the reviewer nothing but the goal and the diff. |
| `-b, --backend` | `codex`, `gemini`, `claude`, `openrouter`. Repeat it, or pass `all`, to ask several independent reviewers at once. |
| `-p, --profile` | A setup the user saved. `--show-config` lists them. |
| `--from` | `claude` or `codex` — which host's session to read. Defaults to the most recent. |
| `-m, --model` | Model id. Repeat or comma-separate to run several in parallel. Omit on a CLI backend to use its own default. |
| `--check` | Probe every backend with a one-token prompt and report which ones actually work here. Run this first when a backend fails. |
| `--dry-run` | Print the exact payload and the exact command, call nothing. Use this if the user asks what will be sent. |
| `--thinking` | Include your thinking blocks. Useful when the question is *why* you concluded something. Off by default — it roughly doubles the payload. |
| `--budget N` | Max transcript characters (default 140000). Lower it if a backend rejects the size. |
| `--no-diff` | Skip the git diff attachment. |
| `--save PATH` | Also write the review to a file. |
| `--lang` | Language for the review. Defaults to English. |

## Reporting back

The review comes back as markdown on stdout. **Relay its substance — do not
quietly bury the parts that criticize your work.** That is the one failure mode
that makes this skill worthless.

1. Lead with the verdict line (KEEP GOING / ADJUST COURSE / STOP AND RETHINK).
2. Give the findings, condensed but not softened. Keep the specifics — the turn
   numbers, the file names, the concrete mechanism.
3. Then, separately and clearly marked, give **your own** response: which points
   land, which are wrong, and where the outside model lacked context that you
   have. Disagreeing is fine and often correct. What is not fine is skipping a
   criticism because it stings.
4. If several reviewers were asked, **lead with where they independently
   agreed**. They never saw each other's answers, so convergence is the
   strongest signal available. Something only one of them raised is a lead, not
   a finding — say which it was. Where they contradict each other, that
   disagreement is usually the most interesting part of the whole report.
5. Ask the user what they want to do. Do not start acting on the review
   unprompted — an outside model with partial context can confidently send you
   somewhere worse.

## If a backend fails

Run `--check`. It probes each backend with a one-token prompt and prints the
exact command line it used, which separates "not installed" from "installed but
the flags have changed upstream". Relay what it says. Do not go hunting through
the user's files for an API key.

If the user wants a different provider or model set, do not hand-edit JSON for
them — the script writes its own config:

These are written as `freshair`, the launcher `install.sh` puts in
`~/.local/bin`. **The user does not need it** — `/freshair --login` and friends
reach the same flags from inside the session, with no PATH involved. If they do
report `command not found` in a terminal, point them back at the slash command
rather than debugging their shell.

```bash
freshair --login                                         # connect OpenRouter in a browser
freshair --set-key sk-or-v1-...                          # or store a key you already have
freshair -b openrouter -m openai/gpt-5.1 --save-default  # make it the default
freshair -b all --save-default council                   # save it as -p council
freshair --show-config                                   # what is set, and from where
```

Per-repo overrides still live in `.freshair.json`, including each backend's
command line, so a changed upstream flag is a config edit rather than a patch.
A key there is refused — it would be committed.

## Where the reviewer runs

CLI backends are started from a scratch directory, not from the repo — an agent
CLI files its session log under a path derived from its working directory, and
started inside the repo the reviewer's own prompt lands in the transcript we
read, making each review the input to the next one. The repo is handed back as a
readable directory where the backend supports it, so the reviewer can still open
a file to check a claim. It cannot write: `codex exec --sandbox read-only`,
`claude -p --disallowed-tools Edit Write NotebookEdit Bash`.

## What this sends off-machine

The transcript — including tool output, file contents, and code — goes to
whichever model is asked. On a CLI backend it goes straight from this machine to
a vendor the user already has an account with; on `openrouter` it also passes
through OpenRouter. The script scrubs credential-shaped strings (API keys,
tokens, JWTs, private key blocks) first, but that is a safety net, not a
guarantee. In a repo with sensitive material, run `--dry-run` and show the user
what is going out before sending it.
