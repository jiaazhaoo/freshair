#!/usr/bin/env python3
"""
wdyt — take the current Claude Code session transcript and hand it, raw, to a
model on OpenRouter that has never seen it before.

The point is that nothing in this pipeline is summarized by the agent that is
already stuck. The outside model reads what actually happened.

Stdlib only. No install step.
"""

import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_MODELS = ["openai/gpt-5.1", "google/gemini-3-pro"]

# Rough budget for the transcript we ship out, in characters.
DEFAULT_BUDGET = 140_000
TOOL_RESULT_CAP = 1_200
DIFF_CAP = 40_000

SYSTEM_PROMPT = """\
You are an outside reviewer. You were NOT part of the conversation you are about \
to read, and that is the entire reason you were called in.

The transcript below is a working session between a human and a coding agent. \
The agent has been in this conversation for a long time. It is now anchored: it \
keeps building on its own earlier decisions, treats its own assumptions as \
settled fact, and has stopped generating genuinely new options. The human called \
you because they suspect this has happened but cannot see it from inside.

You are reading the raw transcript, not a summary. Nobody has filtered it for \
you. That means the mistakes are still visible in it — an assumption that got \
made once and never questioned again, a requirement from the first message that \
quietly got dropped, a hard problem that got redefined into an easier one.

Rules:
- Judge the work, not the effort. Do not open with praise. Do not soften.
- Be specific. Point at the actual turn, decision, or line of code. "Consider \
edge cases" is worthless; "the retry loop added around turn 12 never resets \
`attempt`, so the backoff is wrong after the first failure" is worth reading.
- The agent's framing is not evidence. If the transcript takes something for \
granted, that is exactly the thing to poke.
- If they are actually on the right track, say so plainly and briefly, and spend \
your words on the sharpest remaining risk instead of manufacturing complaints.
- You cannot run anything. If a claim in the transcript needs verification, say \
what you would run to check it.
"""

USER_TEMPLATE = """\
Read the session transcript below, then answer in the structure given at the end.

{focus_block}
{repo_block}
===== BEGIN SESSION TRANSCRIPT =====
{transcript}
===== END SESSION TRANSCRIPT =====
{diff_block}
Answer in this structure, in {lang}:

## 判断 / Verdict
One line, one of: KEEP GOING / ADJUST COURSE / STOP AND RETHINK — plus one \
sentence of why.

## 跑偏了吗 / Drift
What did the human ask for in their earliest messages, and is that still what is \
being built? Quote the original ask. If it drifted, name the turn where it \
drifted.

## 没人质疑过的假设 / Unexamined assumptions
Things the transcript treats as settled that are not. For each: why it might be \
wrong, and what it would cost if it is.

## 具体问题 / Concrete problems
Bugs, gaps, broken logic, missing cases. Cite where. Ordered by how much damage \
they do.

## 更简单的做法 / The simpler path
Is there a materially simpler approach that was never considered? Is there a \
chunk of this that could just be deleted? Say so even if it means throwing away \
most of the work.

## 如果我从零开始 / If I started fresh
Two or three sentences: knowing only the original goal, how would you have \
approached this? If it is the same as what they did, say that — it is useful \
information.
"""


# --------------------------------------------------------------------------
# locating the transcript
# --------------------------------------------------------------------------

def project_dir_for(cwd: Path) -> Path:
    """Claude Code slugifies the cwd: every non-alphanumeric char becomes '-'."""
    slug = re.sub(r"[^a-zA-Z0-9]", "-", str(cwd))
    return Path.home() / ".claude" / "projects" / slug


def find_transcript(explicit: str | None, cwd: Path) -> Path:
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            die(f"transcript not found: {p}")
        return p

    pdir = project_dir_for(cwd)
    if not pdir.is_dir():
        die(
            f"no session directory for {cwd} (looked in {pdir}).\n"
            "Pass --transcript /path/to/session.jsonl explicitly."
        )

    session_id = (
        os.environ.get("CLAUDE_SESSION_ID")
        or os.environ.get("CLAUDE_CODE_SESSION_ID")
    )
    if session_id:
        candidate = pdir / f"{session_id}.jsonl"
        if candidate.is_file():
            return candidate

    # Fall back to the most recently written transcript in this project — in a
    # live session that is the one being appended to right now.
    files = sorted(pdir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        die(f"no .jsonl transcripts in {pdir}")
    return files[0]


# --------------------------------------------------------------------------
# parsing the transcript
# --------------------------------------------------------------------------

def clip(text: str, cap: int) -> str:
    text = text.rstrip()
    if len(text) <= cap:
        return text
    head = text[: cap // 2]
    tail = text[-cap // 2 :]
    return f"{head}\n… [{len(text) - cap} chars elided] …\n{tail}"


def block_text(block) -> str:
    """Flatten one content block into readable text."""
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return ""

    btype = block.get("type")

    if btype == "text":
        return block.get("text", "")

    if btype == "thinking":
        return ""  # handled by the caller, which knows whether to keep it

    if btype == "tool_use":
        name = block.get("name", "tool")
        args = block.get("input", {})
        if isinstance(args, dict):
            # Show the arguments that carry meaning, skip the bulk payloads.
            interesting = {}
            for key in ("command", "file_path", "pattern", "path", "url",
                        "prompt", "description", "query", "old_string"):
                if key in args:
                    interesting[key] = clip(str(args[key]), 400)
            rendered = json.dumps(interesting, ensure_ascii=False) if interesting else ""
        else:
            rendered = clip(str(args), 400)
        return f"[tool: {name}] {rendered}"

    if btype == "tool_result":
        content = block.get("content", "")
        if isinstance(content, list):
            content = "\n".join(block_text(c) for c in content)
        text = clip(str(content), TOOL_RESULT_CAP)
        marker = " (error)" if block.get("is_error") else ""
        return f"[tool result{marker}]\n{text}"

    if btype == "image":
        return "[image]"

    return ""


def parse_transcript(path: Path, keep_thinking: bool) -> list[dict]:
    """Return a list of {role, text, ts} turns, oldest first."""
    turns: list[dict] = []

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            if rec.get("type") not in ("user", "assistant"):
                continue
            if rec.get("isSidechain"):
                continue  # subagent chatter, not the main thread

            msg = rec.get("message") or {}
            role = msg.get("role") or rec.get("type")
            content = msg.get("content")

            # A "user" record that is nothing but tool results is the harness
            # feeding output back, not the human speaking. Label it honestly so
            # the outside reader can tell who actually said what.
            if role == "user" and isinstance(content, list) and content and all(
                isinstance(b, dict) and b.get("type") in ("tool_result", "image")
                for b in content
            ):
                role = "tool"

            parts: list[str] = []
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "thinking":
                        if keep_thinking:
                            think = block.get("thinking", "")
                            if think.strip():
                                parts.append(f"[thinking] {think}")
                        continue
                    rendered = block_text(block)
                    if rendered.strip():
                        parts.append(rendered)

            text = "\n".join(p for p in parts if p.strip()).strip()
            if not text:
                continue

            # Claude Code's own injected reminders are noise to an outside reader.
            if role in ("user", "tool") and text.startswith("<system-reminder>") and text.endswith("</system-reminder>"):
                continue
            text = re.sub(r"<system-reminder>.*?</system-reminder>", "", text, flags=re.S).strip()
            if not text:
                continue

            turns.append({"role": role, "text": text, "ts": rec.get("timestamp", "")})

    return turns


# --------------------------------------------------------------------------
# shaping it to fit
# --------------------------------------------------------------------------

def render_turns(turns: list[dict]) -> str:
    out = []
    for i, t in enumerate(turns, 1):
        who = {"user": "HUMAN", "assistant": "AGENT"}.get(t["role"], "TOOL OUTPUT")
        out.append(f"--- turn {i} · {who} ---\n{t['text']}")
    return "\n\n".join(out)


def fit_to_budget(turns: list[dict], budget: int, head_turns: int) -> tuple[str, dict]:
    """
    Keep the opening turns (the original goal — the single most important thing
    for spotting drift) and as many recent turns as fit. Elide the middle.
    """
    stats = {"total_turns": len(turns), "kept_turns": len(turns), "elided_turns": 0}

    full = render_turns(turns)
    if len(full) <= budget:
        return full, stats

    head = turns[:head_turns]
    head_text = render_turns(head)
    if len(head_text) > budget:
        # Pathological case: the opening turns alone blow the budget. Keep the
        # original ask visible rather than dropping it.
        stats["kept_turns"] = len(head)
        stats["elided_turns"] = len(turns) - len(head)
        return clip(head_text, budget), stats

    tail: list[dict] = []
    used = len(head_text)
    for t in reversed(turns[head_turns:]):
        chunk = len(t["text"]) + 40
        if used + chunk > budget:
            break
        tail.append(t)
        used += chunk
    tail.reverse()

    elided = len(turns) - len(head) - len(tail)
    stats["kept_turns"] = len(head) + len(tail)
    stats["elided_turns"] = elided

    gap = (
        f"\n\n--- [{elided} turns from the middle of the session were elided to fit; "
        f"the opening and the most recent work are both intact] ---\n\n"
    )
    return head_text + gap + render_turns(tail), stats


SECRET_PATTERNS = [
    (re.compile(r"sk-or-v1-[A-Za-z0-9]{16,}"), "sk-or-v1-***REDACTED***"),
    (re.compile(r"sk-ant-[A-Za-z0-9\-_]{16,}"), "sk-ant-***REDACTED***"),
    (re.compile(r"sk-proj-[A-Za-z0-9\-_]{16,}"), "sk-proj-***REDACTED***"),
    (re.compile(r"\bsk-[A-Za-z0-9]{32,}"), "sk-***REDACTED***"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "gh*_***REDACTED***"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AKIA***REDACTED***"),
    (re.compile(r"AIza[0-9A-Za-z\-_]{30,}"), "AIza***REDACTED***"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"), "xox*-***REDACTED***"),
    (re.compile(r"ey[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"), "***JWT-REDACTED***"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "***PRIVATE-KEY-REDACTED***"),
]


def redact(text: str) -> tuple[str, int]:
    count = 0
    for pattern, replacement in SECRET_PATTERNS:
        text, n = pattern.subn(replacement, text)
        count += n
    return text, count


# --------------------------------------------------------------------------
# repo context
# --------------------------------------------------------------------------

def git(args: list[str], cwd: Path) -> str:
    try:
        r = subprocess.run(
            ["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=20
        )
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def repo_context(cwd: Path) -> tuple[str, str]:
    """Returns (summary_block, diff_block)."""
    if git(["rev-parse", "--is-inside-work-tree"], cwd).strip() != "true":
        return "", ""

    branch = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd).strip()
    status = git(["status", "--short"], cwd).strip()
    log = git(["log", "--oneline", "-8"], cwd).strip()

    summary = "===== REPO STATE =====\n"
    if branch:
        summary += f"branch: {branch}\n"
    if log:
        summary += f"\nrecent commits:\n{log}\n"
    if status:
        summary += f"\nworking tree:\n{status}\n"
    summary += "======================\n\n"

    diff = git(["diff", "HEAD"], cwd)
    if not diff.strip():
        diff = git(["diff"], cwd)
    if not diff.strip():
        return summary, ""

    diff_block = (
        "\n===== UNCOMMITTED DIFF (the work under review) =====\n"
        + clip(diff, DIFF_CAP)
        + "\n===== END DIFF =====\n"
    )
    return summary, diff_block


# --------------------------------------------------------------------------
# calling OpenRouter
# --------------------------------------------------------------------------

def ask_model(model: str, system: str, user: str, api_key: str,
              temperature: float, timeout: int) -> dict:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
    }
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/jiaazhaoo/what-do-you-think",
            "X-Title": "what-do-you-think",
        },
        method="POST",
    )

    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:600]
        return {"model": model, "error": f"HTTP {e.code}: {detail}"}
    except Exception as e:  # noqa: BLE001 — surface anything the network does
        return {"model": model, "error": f"{type(e).__name__}: {e}"}

    choices = body.get("choices") or []
    if not choices:
        return {"model": model, "error": f"no choices in response: {json.dumps(body)[:600]}"}

    return {
        "model": model,
        "text": (choices[0].get("message") or {}).get("content", "").strip(),
        "usage": body.get("usage") or {},
        "seconds": round(time.time() - started, 1),
    }


# --------------------------------------------------------------------------

def die(msg: str) -> None:
    print(f"wdyt: {msg}", file=sys.stderr)
    sys.exit(1)


def load_config(cwd: Path) -> dict:
    for path in (cwd / ".wdyt.json",
                 Path.home() / ".config" / "wdyt" / "config.json"):
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                print(f"wdyt: ignoring malformed config at {path}", file=sys.stderr)
    return {}


def main() -> None:
    cwd = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()
    config = load_config(cwd)

    ap = argparse.ArgumentParser(
        prog="wdyt",
        description="Send this session's transcript to a fresh model on OpenRouter.",
    )
    ap.add_argument("focus", nargs="*",
                    help="what to look at, e.g. 'is the caching layer worth it'")
    ap.add_argument("-m", "--model", action="append", default=[],
                    help="OpenRouter model id; repeat or comma-separate for several")
    ap.add_argument("--transcript", help="path to a session .jsonl (default: this session)")
    ap.add_argument("--budget", type=int,
                    default=int(config.get("budget", DEFAULT_BUDGET)),
                    help=f"max transcript characters to send (default {DEFAULT_BUDGET})")
    ap.add_argument("--head-turns", type=int, default=int(config.get("head_turns", 6)),
                    help="opening turns always kept, so drift stays visible (default 6)")
    ap.add_argument("--thinking", action="store_true",
                    help="include the agent's thinking blocks")
    ap.add_argument("--no-diff", action="store_true", help="do not attach the git diff")
    ap.add_argument("--no-redact", action="store_true",
                    help="skip the credential scrub (not recommended)")
    ap.add_argument("--lang", default=config.get("lang", "the language the human used in the transcript"),
                    help="language for the review")
    ap.add_argument("--temperature", type=float, default=float(config.get("temperature", 0.7)))
    ap.add_argument("--timeout", type=int, default=int(config.get("timeout", 300)))
    ap.add_argument("--dry-run", action="store_true",
                    help="print exactly what would be sent, call nothing")
    ap.add_argument("--save", metavar="PATH", help="also write the review to this file")
    args = ap.parse_args()

    models: list[str] = []
    for entry in (args.model or config.get("models") or DEFAULT_MODELS):
        models.extend(m.strip() for m in entry.split(",") if m.strip())

    path = find_transcript(args.transcript, cwd)
    turns = parse_transcript(path, keep_thinking=args.thinking)
    if not turns:
        die(f"no readable turns in {path}")

    transcript, stats = fit_to_budget(turns, args.budget, args.head_turns)

    repo_block, diff_block = ("", "")
    if not args.no_diff:
        repo_block, diff_block = repo_context(cwd)

    focus_block = ""
    focus = " ".join(args.focus).strip()
    if focus:
        focus_block = f"The human specifically wants your read on: {focus}\n\n"

    user_msg = USER_TEMPLATE.format(
        focus_block=focus_block,
        repo_block=repo_block,
        transcript=transcript,
        diff_block=diff_block,
        lang=args.lang,
    )

    redacted_count = 0
    if not args.no_redact:
        user_msg, redacted_count = redact(user_msg)

    header = (
        f"transcript: {path}\n"
        f"turns: {stats['kept_turns']}/{stats['total_turns']} kept"
        + (f", {stats['elided_turns']} elided from the middle" if stats["elided_turns"] else "")
        + f"\npayload: {len(user_msg):,} chars (~{len(user_msg)//4:,} tokens)\n"
        f"models: {', '.join(models)}\n"
        + (f"redacted: {redacted_count} credential-shaped strings\n" if redacted_count else "")
    )

    if args.dry_run:
        print(header)
        print("=" * 70)
        print("SYSTEM:\n" + SYSTEM_PROMPT)
        print("=" * 70)
        print("USER:\n" + user_msg)
        return

    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        die("OPENROUTER_API_KEY is not set. Get a key at https://openrouter.ai/keys")

    print(header, file=sys.stderr)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(models))) as pool:
        results = list(pool.map(
            lambda m: ask_model(m, SYSTEM_PROMPT, user_msg, api_key,
                                args.temperature, args.timeout),
            models,
        ))

    chunks = [f"# 外部意见 / Outside opinion\n\n_{datetime.now(timezone.utc).astimezone():%Y-%m-%d %H:%M}_\n"]
    failures = 0
    for r in results:
        if r.get("error"):
            failures += 1
            chunks.append(f"\n---\n\n## ❌ {r['model']}\n\n```\n{r['error']}\n```\n")
            continue
        usage = r.get("usage") or {}
        meta = f"{r['seconds']}s"
        if usage.get("total_tokens"):
            meta += f" · {usage['total_tokens']:,} tokens"
        chunks.append(f"\n---\n\n## 🔎 {r['model']}\n\n_{meta}_\n\n{r['text']}\n")

    out = "".join(chunks)
    print(out)

    if args.save:
        Path(args.save).expanduser().write_text(out, encoding="utf-8")
        print(f"\nsaved to {args.save}", file=sys.stderr)

    if failures == len(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
