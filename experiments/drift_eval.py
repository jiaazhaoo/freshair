#!/usr/bin/env python3
"""
Does withholding the middle of the conversation actually help?

FreshAir is built on an untested claim: a reviewer shown the agent's reasoning
gets anchored by it and stops catching the agent's mistakes, so the reasoning
should be withheld. This measures that instead of assuming it.

Each fixture is a session with one defect deliberately planted, and — this is
the part that makes the test mean anything — the agent in the transcript argues
for that defect persuasively. If seeing the reasoning suppresses findings, this
is where it will show.

Three arms, same reviewer, same defect:

  fresh    the goal and the diff only          (what FreshAir sends today)
  full     the entire transcript               (--mode full)
  control  the entire transcript, no framing   (what you get by just asking)

    python3 experiments/drift_eval.py                 # all fixtures, 1 run each
    python3 experiments/drift_eval.py -n 3            # 3 runs, for variance
    python3 experiments/drift_eval.py -b codex        # a different reviewer

Reviewers run with no repository access, so every arm sees exactly what it was
given and nothing else.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import importlib.util
import json
import re
import statistics
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "freshair" / "scripts" / "freshair.py"
_spec = importlib.util.spec_from_file_location("freshair", _SCRIPT)
fa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fa)

ARMS = ("fresh", "full", "control")

CONTROL_SYSTEM = """\
You are reviewing a coding session. Read it and say what you think of the work.
Be specific and concrete about any problems you find.
"""


def render_transcript(fixture: dict) -> str:
    turns = []
    n = 1
    for text in fixture["goal"]:
        turns.append(f"--- turn {n} · HUMAN ---\n{text}")
        n += 1
    for role, text in fixture["turns"]:
        who = {"assistant": "AGENT", "tool": "TOOL OUTPUT", "user": "HUMAN"}[role]
        turns.append(f"--- turn {n} · {who} ---\n{text}")
        n += 1
    return "\n\n".join(turns)


def diff_block(fixture: dict) -> str:
    return ("\n===== UNCOMMITTED DIFF (the work under review) =====\n"
            + fixture["diff"] + "\n===== END DIFF =====\n")


def build(arm: str, fixture: dict) -> tuple[str, str]:
    """The exact system and user messages one arm sends."""
    if arm == "fresh":
        goal = fa.render_goal(fixture["goal"])
        claim = fixture["turns"][-1][1]
        claim_block = ("\n===== THE AGENT'S OWN ACCOUNT OF WHERE THIS STANDS =====\n"
                       "(A claim, not evidence. Check it against the diff above.)\n\n"
                       f"{claim}\n===== END ACCOUNT =====\n")
        return (fa.SYSTEM_PROMPT_FRESH.format(verify_rule=fa.VERIFY_RULE_API),
                fa.USER_TEMPLATE_FRESH.format(
                    focus_block="", goal=goal, repo_block="",
                    diff_block=diff_block(fixture), claim_block=claim_block,
                    lang="English"))

    transcript = render_transcript(fixture)
    if arm == "full":
        return (fa.SYSTEM_PROMPT_FULL.format(verify_rule=fa.VERIFY_RULE_API),
                fa.USER_TEMPLATE_FULL.format(
                    focus_block="", repo_block="", transcript=transcript,
                    diff_block=diff_block(fixture), lang="English"))

    return (CONTROL_SYSTEM,
            f"===== SESSION =====\n{transcript}\n===== END SESSION =====\n"
            f"{diff_block(fixture)}\nWhat do you think of this work?")


def caught(review: str, fixture: dict) -> bool:
    """Did the review name the planted defect?

    Every pattern must appear. Keyword matching is blunt: it can miss a review
    that describes the defect in words nobody anticipated, and it can fire on a
    passing mention. Reviews are saved so the calls can be checked by hand.
    """
    return all(re.search(p, review, re.I | re.S) for p in fixture["must_match"])


def ask(backend: str, model: str | None, system: str, user: str,
        timeout: int, backends: dict) -> dict:
    if backend == "openrouter":
        import os
        key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not key:
            return {"error": "OPENROUTER_API_KEY is not set"}
        return fa.ask_openrouter(model or fa.DEFAULT_MODELS[0], system, user,
                                 key, 0.7, timeout)
    with tempfile.TemporaryDirectory() as neutral:
        return fa.ask_cli(backend, backends[backend], model, system, user,
                          timeout, Path(neutral))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", "--runs", type=int, default=1,
                    help="runs per fixture per arm (default 1)")
    ap.add_argument("-b", "--backend", default="auto", help="reviewer backend")
    ap.add_argument("-m", "--model", default=None)
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--fixtures", default=str(Path(__file__).parent / "fixtures.json"))
    ap.add_argument("--only", help="run one fixture by name")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--save", default=None, help="directory to write every review to")
    ap.add_argument("--jobs", type=int, default=4, help="calls in flight at once")
    args = ap.parse_args()

    backends = fa.resolve_backends(fa.load_config(Path.cwd()))
    backend = fa.detect_backend(backends) if args.backend == "auto" else args.backend

    fixtures = json.loads(Path(args.fixtures).read_text(encoding="utf-8"))
    if args.only:
        fixtures = [f for f in fixtures if f["name"] == args.only]
        if not fixtures:
            sys.exit(f"no fixture named {args.only!r}")
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]

    work = [(f, arm, run) for f in fixtures for arm in arms for run in range(args.runs)]
    print(f"reviewer: {backend}" + (f" · {args.model}" if args.model else ""))
    print(f"{len(fixtures)} fixtures x {len(arms)} arms x {args.runs} run(s) "
          f"= {len(work)} calls\n", flush=True)

    save_dir = Path(args.save) if args.save else None
    if save_dir:
        save_dir.mkdir(parents=True, exist_ok=True)

    def one(item):
        fixture, arm, run = item
        system, user = build(arm, fixture)
        r = ask(backend, args.model, system, user, args.timeout, backends)
        text = r.get("text", "")
        hit = bool(text) and caught(text, fixture)
        if save_dir and text:
            (save_dir / f"{fixture['name']}.{arm}.{run}.md").write_text(text, encoding="utf-8")
        print(f"  {'HIT ' if hit else 'miss'}  {fixture['name']:<22} {arm:<8} "
              f"run {run + 1}" + (f"   ERROR: {r['error'][:60]}" if r.get("error") else ""),
              flush=True)
        return {"fixture": fixture["name"], "arm": arm, "hit": hit,
                "chars": len(user), "error": r.get("error")}

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(one, work))

    errors = [r for r in results if r["error"]]
    by_arm: dict[str, list[bool]] = defaultdict(list)
    by_cell: dict[tuple[str, str], list[bool]] = defaultdict(list)
    payload: dict[str, list[int]] = defaultdict(list)
    for r in results:
        if r["error"]:
            continue
        by_arm[r["arm"]].append(r["hit"])
        by_cell[(r["fixture"], r["arm"])].append(r["hit"])
        payload[r["arm"]].append(r["chars"])

    print("\n" + "=" * 64)
    print("Caught the planted defect")
    print("=" * 64)
    names = [f["name"] for f in fixtures]
    print(f"{'fixture':<24}" + "".join(f"{a:>12}" for a in arms))
    for name in names:
        row = f"{name:<24}"
        for arm in arms:
            hits = by_cell.get((name, arm), [])
            row += f"{(str(sum(hits)) + '/' + str(len(hits))) if hits else '-':>12}"
        print(row)

    print("-" * 64)
    row = f"{'TOTAL':<24}"
    for arm in arms:
        hits = by_arm.get(arm, [])
        rate = f"{100 * sum(hits) / len(hits):.0f}%" if hits else "-"
        row += f"{rate:>12}"
    print(row)
    row = f"{'mean payload (chars)':<24}"
    for arm in arms:
        sizes = payload.get(arm, [])
        row += f"{(f'{statistics.mean(sizes):,.0f}' if sizes else '-'):>12}"
    print(row)

    if errors:
        print(f"\n{len(errors)} call(s) failed: {errors[0]['error'][:120]}")
    print("\nKeyword detection is blunt and the sample is small. Read the saved "
          "reviews before believing any of this." if not save_dir else
          f"\nReviews written to {save_dir}/ — spot-check before believing the table.")


if __name__ == "__main__":
    main()
