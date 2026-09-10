---
name: wdyt
description: Send the current session's raw transcript to a model that has not seen it, for an outside opinion. Routes through a locally installed CLI the user is already signed in to (Codex, Gemini, Claude) or OpenRouter. Use when the conversation has gone long and circular, when the same fix keeps failing, before committing to a big refactor, or whenever the user asks for a second opinion, outside eyes, a sanity check, or says things like "问问别的模型", "换个思路", "我们是不是跑偏了", "wdyt", "越聊越笨". Not for ordinary code review of a diff.
argument-hint: [what to look at]
allowed-tools: Bash(python3 *)
---

# wdyt — what do you think

Hand this session's **raw transcript** to a model that has never seen it, and
relay what it says back.

## Why the transcript, not a summary

You are the wrong narrator for this. By the time this skill is worth invoking,
you have spent dozens of turns building on your own earlier decisions — and any
summary you write will quietly preserve exactly the assumptions that need
challenging. The whole design is to route around you: the script reads the
session `.jsonl` off disk and ships it out untouched.

So: **do not pre-digest the conversation.** Do not write a "here's what we're
doing" preamble into the prompt. Just run the script.

## Running it

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/wdyt.py" --session-id "${CLAUDE_SESSION_ID}" $ARGUMENTS
```

That is the whole invocation. It finds the transcript, picks a backend the user
is already signed in to, and prints the review to stdout.

Add a focus when the user pointed at something specific — it goes in
`$ARGUMENTS`:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/wdyt.py" "这个缓存层到底值不值得"
```

### Backends

`--backend auto` (the default) prefers a CLI from a **different vendor** than
you, because a model that shares your weights shares your blind spots:

| Backend | Auth | Notes |
|---|---|---|
| `codex` | the user's existing ChatGPT login | preferred — genuinely outside |
| `gemini` | the user's existing Google login | preferred — genuinely outside |
| `openrouter` | `OPENROUTER_API_KEY` | any model, but the user pays per call |
| `claude` | the user's existing Anthropic login | last resort, same vendor as you |

When it falls back to `claude`, the script prints a warning to stderr saying so.
**Pass that warning on to the user** — a same-vendor review is a much weaker
signal than it looks, and they should know which one they got.

### Options worth knowing

| Flag | Use |
|---|---|
| `-b, --backend` | Force one: `codex`, `gemini`, `claude`, `openrouter`. |
| `-m, --model` | Model id. Repeat or comma-separate to run several in parallel. Omit on a CLI backend to use its own default. |
| `--check` | Probe every backend with a one-token prompt and report which ones actually work here. Run this first when a backend fails. |
| `--dry-run` | Print the exact payload and the exact command, call nothing. Use this if the user asks what will be sent. |
| `--thinking` | Include your thinking blocks. Useful when the question is *why* you concluded something. Off by default — it roughly doubles the payload. |
| `--budget N` | Max transcript characters (default 140000). Lower it if a backend rejects the size. |
| `--no-diff` | Skip the git diff attachment. |
| `--save PATH` | Also write the review to a file. |
| `--lang` | Force a language. By default it answers in whatever language the human used. |

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
4. If several models were asked, say where they agreed. Agreement between models
   that were never in the conversation is the strongest signal you will get.
5. Ask the user what they want to do. Do not start acting on the review
   unprompted — an outside model with partial context can confidently send you
   somewhere worse.

## If a backend fails

Run `--check`. It probes each backend with a one-token prompt and prints the
exact command line it used, which separates "not installed" from "installed but
the flags have changed upstream". Relay what it says. Do not go hunting through
the user's files for an API key.

Per-repo defaults live in `.wdyt.json` (see `.wdyt.example.json` in the repo
root). Every backend's command line is overridable there, so a changed upstream
flag is a config edit, not a code change.

## What this sends off-machine

The transcript — including tool output, file contents, and code — goes to
whichever model is asked. On a CLI backend it goes straight from this machine to
a vendor the user already has an account with; on `openrouter` it also passes
through OpenRouter. The script scrubs credential-shaped strings (API keys,
tokens, JWTs, private key blocks) first, but that is a safety net, not a
guarantee. In a repo with sensitive material, run `--dry-run` and show the user
what is going out before sending it.
