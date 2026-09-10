---
name: what-do-you-think
description: Send the current session's raw transcript to a fresh model on OpenRouter for an outside opinion. Use when the conversation has gone long and circular, when the same fix keeps failing, before committing to a big refactor, or whenever the user asks for a second opinion, outside eyes, a sanity check, or says things like "问问别的模型", "换个思路", "我们是不是跑偏了", "wdyt", "越聊越笨". Not for ordinary code review of a diff.
allowed-tools: Bash
---

# what-do-you-think

Hand this session's **raw transcript** to a model that has never seen it, and
relay what it says back.

## Why the transcript, not a summary

You are the wrong narrator for this. By the time this skill is worth invoking,
you have spent dozens of turns building on your own earlier decisions — and any
summary you write will quietly preserve exactly the assumptions that need
challenging. The whole design of this skill is to route around you: the script
reads the session `.jsonl` off disk and ships it out untouched.

So: **do not pre-digest the conversation.** Do not write a "here's what we're
doing" preamble into the prompt. Just run the script.

## Running it

```bash
python3 skills/what-do-you-think/scripts/wdyt.py [focus] [options]
```

If the skill is installed globally, the script lives at
`~/.claude/skills/what-do-you-think/scripts/wdyt.py`.

The bare form is the common case — it finds this session's transcript on its own:

```bash
python3 .../wdyt.py
```

Add a focus when the user pointed at something specific:

```bash
python3 .../wdyt.py "这个缓存层到底值不值得"
python3 .../wdyt.py "is the retry logic actually correct"
```

### Options worth knowing

| Flag | Use |
|---|---|
| `-m, --model` | OpenRouter model id. Repeat or comma-separate for several — they run in parallel. Defaults come from `.wdyt.json`, else `openai/gpt-5.1` + `google/gemini-3-pro`. |
| `--dry-run` | Print the exact payload, call nothing. Use this if the user asks what will be sent. |
| `--thinking` | Include your thinking blocks. Useful when the question is *why* you concluded something. Off by default — it roughly doubles the payload. |
| `--budget N` | Max transcript characters (default 140000). Lower it if a model rejects the size. |
| `--no-diff` | Skip the git diff attachment. |
| `--save PATH` | Also write the review to a file. |
| `--lang` | Force a language for the review. By default it answers in whatever language the human used. |

## Reporting back

The review comes back as markdown on stdout. **Relay its substance — do not
quietly bury the parts that criticize your work.** That is the one failure mode
that makes this skill worthless.

1. Lead with the verdict line (KEEP GOING / ADJUST COURSE / STOP AND RETHINK).
2. Give the findings, condensed but not softened. Keep the specifics — the turn
   numbers, the file names, the concrete mechanism.
3. Then, separately and clearly marked, give **your own** response: which points
   land, which are wrong, and where the outside model lacked context that you
   have. Disagreeing is fine and often correct — it did not run the tests and
   did not read the whole repo. What is not fine is skipping a criticism because
   it stings.
4. If several models were asked, say where they agreed. Agreement between models
   that were never in the conversation is the strongest signal you will get.
5. Ask the user what they want to do. Do not start acting on the review
   unprompted — an outside model with partial context can confidently send you
   somewhere worse.

## Setup, if it is not configured yet

Needs `OPENROUTER_API_KEY` in the environment. If the script reports it missing,
tell the user to get one at https://openrouter.ai/keys and export it — do not go
hunting through their files for a key.

Model defaults can be set per-repo in `.wdyt.json`:

```json
{ "models": ["openai/gpt-5.1", "google/gemini-3-pro"], "budget": 140000 }
```

## What this sends off-machine

The transcript goes to OpenRouter and to whichever provider serves the model.
That includes tool output, file contents, and code that appeared in the
conversation. The script scrubs credential-shaped strings (API keys, tokens,
JWTs, private key blocks) before sending, but that is a safety net and not a
guarantee. In a repo with sensitive material, run `--dry-run` first and show the
user what is going out.
