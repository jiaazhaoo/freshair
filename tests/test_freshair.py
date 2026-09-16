"""Tests for freshair's pure logic: transcript parsing, budget fitting, redaction,
and backend resolution. No network, no subprocesses, no vendor CLIs.

    python3 -m unittest discover -s tests -v
"""

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "freshair" / "scripts" / "freshair.py"
_spec = importlib.util.spec_from_file_location("freshair", _SCRIPT)
freshair = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(freshair)


def write_transcript(records) -> Path:
    fh = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    for rec in records:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    fh.close()
    return Path(fh.name)


def user(text, **kw):
    return {"type": "user", "message": {"role": "user", "content": text}, **kw}


def assistant(blocks, **kw):
    return {"type": "assistant", "message": {"role": "assistant", "content": blocks}, **kw}


class TestProjectDir(unittest.TestCase):
    def test_slugifies_every_non_alphanumeric(self):
        got = freshair.project_dir_for(Path("/home/user/what-do-you-think"))
        self.assertEqual(got.name, "-home-user-what-do-you-think")

    def test_dots_and_underscores_are_slugified_too(self):
        got = freshair.project_dir_for(Path("/a/b.c_d"))
        self.assertEqual(got.name, "-a-b-c-d")


class TestParseTranscript(unittest.TestCase):
    def test_roles_and_ordering(self):
        path = write_transcript([
            user("build me a thing"),
            assistant([{"type": "text", "text": "on it"}]),
        ])
        turns = freshair.parse_transcript(path, keep_thinking=False)
        self.assertEqual([t["role"] for t in turns], ["user", "assistant"])
        self.assertEqual(turns[0]["text"], "build me a thing")

    def test_tool_result_turns_are_not_labelled_as_the_human(self):
        """A user record that is only tool output is the harness, not a person."""
        path = write_transcript([
            user([{"type": "tool_result", "content": "total 16\ndrwxr-xr-x"}]),
        ])
        turns = freshair.parse_transcript(path, keep_thinking=False)
        self.assertEqual(turns[0]["role"], "tool")
        self.assertIn("TOOL OUTPUT", freshair.render_turns(turns))

    def test_mixed_user_turn_stays_human(self):
        path = write_transcript([
            user([{"type": "tool_result", "content": "out"},
                  {"type": "text", "text": "and also, stop"}]),
        ])
        self.assertEqual(freshair.parse_transcript(path, False)[0]["role"], "user")

    def test_sidechain_records_are_dropped(self):
        path = write_transcript([
            user("main thread"),
            assistant([{"type": "text", "text": "subagent chatter"}], isSidechain=True),
        ])
        turns = freshair.parse_transcript(path, keep_thinking=False)
        self.assertEqual(len(turns), 1)

    def test_thinking_is_excluded_by_default_and_included_on_request(self):
        path = write_transcript([
            assistant([{"type": "thinking", "thinking": "hmm"},
                       {"type": "text", "text": "answer"}]),
        ])
        self.assertNotIn("hmm", freshair.parse_transcript(path, keep_thinking=False)[0]["text"])
        self.assertIn("hmm", freshair.parse_transcript(path, keep_thinking=True)[0]["text"])

    def test_system_reminders_are_stripped(self):
        path = write_transcript([
            user("<system-reminder>ignore me</system-reminder>"),
            user("<system-reminder>noise</system-reminder>real question"),
        ])
        turns = freshair.parse_transcript(path, keep_thinking=False)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["text"], "real question")

    def test_non_message_records_are_ignored(self):
        path = write_transcript([
            {"type": "queue-operation", "operation": "enqueue", "content": "x"},
            {"type": "summary", "summary": "y"},
            user("only this"),
        ])
        self.assertEqual(len(freshair.parse_transcript(path, False)), 1)

    def test_malformed_lines_do_not_abort_the_parse(self):
        path = write_transcript([user("first")])
        with path.open("a", encoding="utf-8") as fh:
            fh.write("{not json at all\n\n")
            fh.write(json.dumps(user("second")) + "\n")
        self.assertEqual(len(freshair.parse_transcript(path, False)), 2)

    def test_tool_use_keeps_the_meaningful_arguments(self):
        path = write_transcript([
            assistant([{"type": "tool_use", "name": "Bash",
                        "input": {"command": "ls -la", "timeout": 5000}}]),
        ])
        text = freshair.parse_transcript(path, False)[0]["text"]
        self.assertIn("[tool: Bash]", text)
        self.assertIn("ls -la", text)
        self.assertNotIn("5000", text)   # noise arguments are dropped

    def test_long_tool_results_are_capped(self):
        path = write_transcript([
            user([{"type": "tool_result", "content": "x" * 50_000}]),
        ])
        text = freshair.parse_transcript(path, False)[0]["text"]
        self.assertLess(len(text), freshair.TOOL_RESULT_CAP + 200)
        self.assertIn("elided", text)


class TestClip(unittest.TestCase):
    def test_short_text_is_untouched(self):
        self.assertEqual(freshair.clip("abc", 100), "abc")

    def test_long_text_keeps_both_ends(self):
        got = freshair.clip("A" * 100 + "B" * 100, 40)
        self.assertTrue(got.startswith("A"))
        self.assertTrue(got.endswith("B"))
        self.assertIn("elided", got)


class TestFitToBudget(unittest.TestCase):
    def make(self, n, size=100):
        return [{"role": "user" if i % 2 == 0 else "assistant",
                 "text": f"turn{i}-" + "x" * size, "ts": ""} for i in range(n)]

    def test_everything_fits(self):
        turns = self.make(4, 10)
        text, stats = freshair.fit_to_budget(turns, 100_000, 6)
        self.assertEqual(stats["elided_turns"], 0)
        self.assertEqual(stats["kept_turns"], 4)
        self.assertIn("turn0", text)
        self.assertIn("turn3", text)

    def test_over_budget_keeps_the_original_ask_and_the_recent_work(self):
        turns = self.make(60, 500)
        text, stats = freshair.fit_to_budget(turns, 6_000, head_turns=3)
        self.assertGreater(stats["elided_turns"], 0)
        # the opening — without it you cannot tell whether the work drifted
        self.assertIn("turn0-", text)
        self.assertIn("turn1-", text)
        # and the latest state of play
        self.assertIn("turn59-", text)
        # with an explicit marker rather than a silent gap
        self.assertIn("elided to fit", text)
        self.assertNotIn("turn30-", text)

    def test_head_alone_over_budget_still_keeps_the_original_ask(self):
        turns = self.make(10, 5_000)
        text, stats = freshair.fit_to_budget(turns, 1_000, head_turns=6)
        self.assertIn("turn0-", text)
        self.assertEqual(stats["kept_turns"], 6)

    def test_stats_account_for_every_turn(self):
        turns = self.make(40, 500)
        _, stats = freshair.fit_to_budget(turns, 5_000, head_turns=3)
        self.assertEqual(stats["kept_turns"] + stats["elided_turns"], stats["total_turns"])


class TestRedact(unittest.TestCase):
    def test_known_credential_shapes_are_scrubbed(self):
        for secret in [
            "sk-or-v1-abcdef0123456789abcdef0123456789",
            "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAA",
            "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
            "AKIAIOSFODNN7EXAMPLE",
            "AIzaSyD-1234567890abcdefghijklmnopqrstu",
            "xoxb-1234567890-abcdefghij",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.dBjftJeZ4CVPmB92K27uhbUJU1p1r",
        ]:
            with self.subTest(secret=secret[:12]):
                out, n = freshair.redact(f"the key is {secret} ok")
                self.assertEqual(n, 1)
                self.assertNotIn(secret, out)
                self.assertIn("REDACTED", out)

    def test_private_key_blocks_are_scrubbed_whole(self):
        blob = ("-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nlines\n"
                "-----END RSA PRIVATE KEY-----")
        out, n = freshair.redact(blob)
        self.assertEqual(n, 1)
        self.assertNotIn("MIIEow", out)

    def test_ordinary_prose_is_left_alone(self):
        for benign in ["a normal sentence with sk- in it",
                       "AKIA is a prefix",
                       "import sky from 'sky'"]:
            with self.subTest(benign=benign):
                out, n = freshair.redact(benign)
                self.assertEqual((out, n), (benign, 0))


class TestBackends(unittest.TestCase):
    def test_model_flag_goes_before_the_trailing_stdin_sentinel(self):
        cmd = freshair.cli_command(freshair.CLI_BACKENDS["codex"], "gpt-5.1")
        self.assertEqual(cmd[-1], "-", "the stdin sentinel must stay last")
        self.assertLess(cmd.index("-m"), cmd.index("-"))

    def test_model_flag_goes_before_a_variadic_tail(self):
        cmd = freshair.cli_command(freshair.CLI_BACKENDS["claude"], "opus")
        self.assertLess(cmd.index("--model"), cmd.index("--disallowed-tools"))

    def test_no_model_means_no_model_flag(self):
        cmd = freshair.cli_command(freshair.CLI_BACKENDS["codex"], None)
        self.assertNotIn("-m", cmd)
        self.assertEqual(cmd[-1], "-")

    def test_cli_backends_are_invoked_read_only(self):
        self.assertIn("read-only", freshair.CLI_BACKENDS["codex"]["cmd"])
        claude = freshair.cli_command(freshair.CLI_BACKENDS["claude"], None)
        for tool in ("Edit", "Write", "Bash"):
            self.assertIn(tool, claude)

    def test_config_overrides_a_builtin_backend(self):
        merged = freshair.resolve_backends(
            {"backends": {"codex": {"cmd": ["codex", "exec", "--new-flag"]}}}
        )
        self.assertEqual(merged["codex"]["cmd"], ["codex", "exec", "--new-flag"])
        self.assertEqual(merged["codex"]["model_flag"], "-m")   # untouched fields survive
        self.assertEqual(freshair.CLI_BACKENDS["codex"]["cmd"][2], "--sandbox")  # no mutation

    def test_config_can_add_a_backend_freshair_has_never_heard_of(self):
        merged = freshair.resolve_backends(
            {"backends": {"llm": {"cmd": ["llm", "-m", "x"], "vendor": "simonw"}}}
        )
        self.assertIn("llm", merged)


class TestDetectBackend(unittest.TestCase):
    def detect(self, installed, key=""):
        env = dict(os.environ)
        env["OPENROUTER_API_KEY"] = key
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(freshair.shutil, "which",
                               side_effect=lambda b: f"/usr/bin/{b}" if b in installed else None):
            return freshair.detect_backend(freshair.resolve_backends({}))

    def test_prefers_a_different_vendor_over_the_host(self):
        self.assertEqual(self.detect({"codex", "claude"}), "codex")
        self.assertEqual(self.detect({"gemini", "claude"}), "gemini")

    def test_codex_outranks_gemini(self):
        self.assertEqual(self.detect({"codex", "gemini"}), "codex")

    def test_openrouter_beats_falling_back_to_the_same_vendor(self):
        self.assertEqual(self.detect({"claude"}, key="sk-or-v1-x"), "openrouter")

    def test_same_vendor_is_the_last_resort_not_a_hard_failure(self):
        self.assertEqual(self.detect({"claude"}), "claude")

    def test_nothing_installed_points_at_openrouter(self):
        self.assertEqual(self.detect(set()), "openrouter")


class TestLoadConfig(unittest.TestCase):
    def test_the_pre_rename_config_name_still_works(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".wdyt.json").write_text('{"backend": "gemini"}', encoding="utf-8")
            with mock.patch.object(freshair.Path, "home", return_value=Path(d)):
                self.assertEqual(freshair.load_config(Path(d))["backend"], "gemini")

    def test_repo_config_wins_over_the_user_wide_one(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".freshair.json").write_text('{"backend": "codex"}', encoding="utf-8")
            self.assertEqual(freshair.load_config(Path(d))["backend"], "codex")

    def test_missing_config_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(freshair.Path, "home", return_value=Path(d)):
                self.assertEqual(freshair.load_config(Path(d)), {})

    def test_malformed_config_is_skipped_rather_than_crashing(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".freshair.json").write_text("{oops", encoding="utf-8")
            with mock.patch.object(freshair.Path, "home", return_value=Path(d)):
                self.assertEqual(freshair.load_config(Path(d)), {})

    def test_the_shipped_example_config_is_valid(self):
        example = Path(__file__).resolve().parent.parent / ".freshair.example.json"
        json.loads(example.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestFreshMode(unittest.TestCase):
    """The default mode: only the goal and the current state go out."""

    def turns(self):
        return [
            {"role": "user", "text": "build a thing that does X", "ts": ""},
            {"role": "assistant", "text": "I'll start with approach A", "ts": ""},
            {"role": "tool", "text": "[tool result]\nok", "ts": ""},
            {"role": "assistant", "text": "[tool: Bash] {}\nA turned out hard, "
                                          "switching to B", "ts": ""},
            {"role": "user", "text": "also make it fast", "ts": ""},
            {"role": "assistant", "text": "[tool: Edit] {}\nB is done and tested",
             "ts": ""},
        ]

    def test_goal_is_the_human_instructions_and_nothing_else(self):
        goal = freshair.extract_goal(self.turns(), 0)
        self.assertIn("build a thing that does X", goal)
        self.assertIn("also make it fast", goal)
        # the agent's own reasoning is what we are trying not to ship
        self.assertNotIn("approach A", goal)
        self.assertNotIn("switching to B", goal)

    def test_goal_turns_caps_the_instructions(self):
        goal = freshair.extract_goal(self.turns(), 1)
        self.assertIn("build a thing", goal)
        self.assertNotIn("also make it fast", goal)

    def test_harness_boilerplate_is_not_a_goal(self):
        turns = [{"role": "user", "text": "Continue from where you left off.", "ts": ""},
                 {"role": "user", "text": "the real ask", "ts": ""}]
        goal = freshair.extract_goal(turns, 0)
        self.assertNotIn("Continue from where", goal)
        self.assertIn("the real ask", goal)
        self.assertEqual(goal.count("--- instruction"), 1)

    def test_goal_survives_a_session_with_no_human_turns(self):
        self.assertIn("no human instructions", freshair.extract_goal(
            [{"role": "assistant", "text": "hi", "ts": ""}], 0))

    def test_claim_is_the_latest_agent_prose_without_the_tool_log(self):
        claim = freshair.extract_claim(self.turns())
        self.assertEqual(claim, "B is done and tested")
        self.assertNotIn("[tool:", claim)

    def test_claim_skips_turns_that_are_only_tool_calls(self):
        turns = [{"role": "assistant", "text": "the real summary", "ts": ""},
                 {"role": "assistant", "text": "[tool: Bash] {}", "ts": ""}]
        self.assertEqual(freshair.extract_claim(turns), "the real summary")

    def test_no_agent_turns_means_no_claim(self):
        self.assertEqual(freshair.extract_claim([{"role": "user", "text": "x", "ts": ""}]), "")


class TestSelfContamination(unittest.TestCase):
    """A CLI backend logs its own session. None of it may come back as input."""

    def test_a_logged_payload_is_not_read_back_as_a_human_instruction(self):
        payload = freshair.SYSTEM_PROMPT_FRESH.format(verify_rule="") + "\n\nreview this"
        path = write_transcript([user("the actual goal"), user(payload)])
        turns = freshair.parse_transcript(path, keep_thinking=False)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["text"], "the actual goal")

    def test_payloads_from_versions_before_the_marker_are_also_caught(self):
        path = write_transcript([
            user("the actual goal"),
            user("You are an outside reviewer. You were NOT part of the "
                 "conversation you are about to read."),
        ])
        self.assertEqual(len(freshair.parse_transcript(path, False)), 1)

    def test_payloads_from_before_the_rename_are_still_recognised(self):
        """Sessions polluted while the tool was called wdyt are still out there."""
        path = write_transcript([
            user("the actual goal"),
            user("<!-- wdyt-review-request: generated by the wdyt tool -->\nreview this"),
        ])
        turns = freshair.parse_transcript(path, keep_thinking=False)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["text"], "the actual goal")

    def test_the_marker_travels_with_both_system_prompts(self):
        for prompt in (freshair.SYSTEM_PROMPT_FRESH, freshair.SYSTEM_PROMPT_FULL):
            self.assertIn(freshair.FRESHAIR_MARKER, prompt)

    def test_a_review_session_is_recognised_as_our_own(self):
        ours = write_transcript([user("a genuine session")])
        childs = write_transcript([user(freshair.MARKER_LINE + "review this")])
        self.assertFalse(freshair.is_freshair_child(ours))
        self.assertTrue(freshair.is_freshair_child(childs))

    def test_child_env_drops_this_session_identity_but_keeps_the_rest(self):
        with mock.patch.dict(os.environ,
                             {"CLAUDE_CODE_SESSION_ID": "abc",
                              "CLAUDE_PROJECT_DIR": "/repo",
                              "PATH": "/usr/bin",
                              "ANTHROPIC_API_KEY": "secret"},
                             clear=True):
            env = freshair.child_env()
        self.assertNotIn("CLAUDE_CODE_SESSION_ID", env)
        self.assertNotIn("CLAUDE_PROJECT_DIR", env)
        self.assertEqual(env["PATH"], "/usr/bin")
        self.assertEqual(env["ANTHROPIC_API_KEY"], "secret", "auth must survive")

    def test_repo_is_handed_back_as_a_readable_dir_where_supported(self):
        cmd = freshair.cli_command(freshair.CLI_BACKENDS["claude"], None, Path("/repo"))
        self.assertIn("--add-dir", cmd)
        self.assertEqual(cmd[cmd.index("--add-dir") + 1], "/repo")

    def test_backends_without_a_dir_flag_are_unaffected(self):
        cmd = freshair.cli_command(freshair.CLI_BACKENDS["codex"], None, Path("/repo"))
        self.assertNotIn("/repo", cmd)
        self.assertEqual(cmd[-1], "-")


class TestGoalHygiene(unittest.TestCase):
    """What counts as an instruction, and what is just session noise."""

    def goal(self, texts, cap=0):
        turns = [{"role": "user", "text": t, "ts": ""} for t in texts]
        return freshair.goal_instructions(turns, cap)

    def test_harness_notices_are_not_instructions(self):
        kept = self.goal(["[Request interrupted by user]", "the real ask"])
        self.assertEqual(kept, ["the real ask"])

    def test_a_bracketed_line_inside_a_real_message_is_kept(self):
        msg = "[note] this is still a genuine instruction with content"
        self.assertEqual(self.goal([msg]), [msg])

    def test_a_retyped_instruction_supersedes_the_fragment_it_extends(self):
        kept = self.goal([
            "add the thing to the project",
            "[Request interrupted by user]",
            "add the thing to the project, and call it FreshAir",
        ])
        self.assertEqual(kept, ["add the thing to the project, and call it FreshAir"])

    def test_two_genuinely_different_asks_both_survive(self):
        kept = self.goal(["make it fast", "make it correct"])
        self.assertEqual(len(kept), 2)

    def test_the_reported_count_matches_what_is_actually_sent(self):
        turns = [{"role": "user", "text": t, "ts": ""} for t in
                 ["real one", "Continue from where you left off.",
                  "[Request interrupted by user]", "real two"]]
        kept = freshair.goal_instructions(turns, 0)
        rendered = freshair.extract_goal(turns, 0)
        self.assertEqual(len(kept), rendered.count("--- instruction "))
        self.assertEqual(len(kept), 2)


class TestCurrentStateOnACleanTree(unittest.TestCase):
    """A committed change is still the work under review."""

    def repo(self, stack):
        import subprocess as sp
        d = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        run = lambda *a: sp.run(["git", *a], cwd=d, capture_output=True, check=True)
        run("init", "-q", "-b", "main")
        run("config", "user.email", "t@t"); run("config", "user.name", "t")
        (d / "f.txt").write_text("one\n")
        run("add", "-A"); run("commit", "-qm", "first")
        return d, run

    def test_a_committed_branch_still_produces_a_diff(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            d, run = self.repo(stack)
            run("checkout", "-qb", "feature")
            (d / "f.txt").write_text("one\ntwo\n")
            run("add", "-A"); run("commit", "-qm", "second")

            _, diff = freshair.repo_context(d)
            self.assertIn("+two", diff, "a clean tree must not mean an empty review")
            self.assertIn("THIS BRANCH CHANGED", diff)

    def test_uncommitted_work_still_wins(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            d, run = self.repo(stack)
            run("checkout", "-qb", "feature")
            (d / "f.txt").write_text("one\nuncommitted\n")

            _, diff = freshair.repo_context(d)
            self.assertIn("+uncommitted", diff)
            self.assertIn("UNCOMMITTED", diff)

    def test_nothing_to_show_is_not_a_crash(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            d, _ = self.repo(stack)
            summary, diff = freshair.repo_context(d)
            self.assertIn("branch: main", summary)
            self.assertEqual(diff, "")

    def test_outside_a_repo_returns_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(freshair.repo_context(Path(d)), ("", ""))


def write_codex_rollout(home: Path, cwd: str, records) -> Path:
    """A rollout file shaped the way Codex writes them."""
    day = home / "sessions" / "2026" / "09" / "16"
    day.mkdir(parents=True, exist_ok=True)
    path = day / "rollout-2026-09-16T10-00-00-abc123.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "type": "session_meta",
            "payload": {"id": "abc123", "cwd": cwd, "cli_version": "0.150.0"},
        }) + "\n")
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path


def codex_msg(role, text):
    kind = "input_text" if role == "user" else "output_text"
    return {"type": "response_item",
            "payload": {"type": "message", "role": role,
                        "content": [{"type": kind, "text": text}]}}


class TestCodexSessions(unittest.TestCase):
    """Codex files sessions differently from Claude Code, in two generations."""

    def test_current_format_messages_are_read(self):
        with tempfile.TemporaryDirectory() as home:
            path = write_codex_rollout(Path(home), "/repo", [
                codex_msg("user", "build the thing"),
                codex_msg("assistant", "starting on it"),
            ])
            turns = freshair.parse_codex(path, keep_thinking=False)
            self.assertEqual([(t["role"], t["text"]) for t in turns],
                             [("user", "build the thing"),
                              ("assistant", "starting on it")])

    def test_tool_calls_and_their_output_are_labelled(self):
        with tempfile.TemporaryDirectory() as home:
            path = write_codex_rollout(Path(home), "/repo", [
                codex_msg("user", "list the files"),
                {"type": "response_item", "payload": {
                    "type": "function_call", "name": "shell",
                    "arguments": '{"command":"ls -la"}'}},
                {"type": "response_item", "payload": {
                    "type": "function_call_output", "output": "total 16"}},
            ])
            turns = freshair.parse_codex(path, keep_thinking=False)
            self.assertIn("[tool: shell]", turns[1]["text"])
            self.assertIn("ls -la", turns[1]["text"])
            self.assertEqual(turns[2]["role"], "tool")
            self.assertIn("total 16", turns[2]["text"])

    def test_tool_records_alone_do_not_outrank_a_real_legacy_conversation(self):
        """A response_item stream with no messages is not a conversation."""
        with tempfile.TemporaryDirectory() as home:
            path = write_codex_rollout(Path(home), "/repo", [
                {"type": "response_item", "payload": {
                    "type": "function_call", "name": "shell", "arguments": "{}"}},
                {"type": "event_msg", "payload": {
                    "type": "user_message", "message": "the real ask"}},
            ])
            turns = freshair.parse_codex(path, keep_thinking=False)
            self.assertEqual([t["text"] for t in turns], ["the real ask"])

    def test_developer_scaffolding_is_not_the_conversation(self):
        with tempfile.TemporaryDirectory() as home:
            path = write_codex_rollout(Path(home), "/repo", [
                codex_msg("developer", "system scaffolding"),
                codex_msg("user", "the real ask"),
            ])
            turns = freshair.parse_codex(path, keep_thinking=False)
            self.assertEqual(len(turns), 1)
            self.assertEqual(turns[0]["text"], "the real ask")

    def test_reasoning_follows_the_thinking_flag(self):
        with tempfile.TemporaryDirectory() as home:
            path = write_codex_rollout(Path(home), "/repo", [
                {"type": "response_item", "payload": {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "hmm"}]}},
                codex_msg("assistant", "answer"),
            ])
            self.assertEqual(len(freshair.parse_codex(path, False)), 1)
            self.assertIn("hmm", freshair.parse_codex(path, True)[0]["text"])

    def test_legacy_event_format_still_parses(self):
        with tempfile.TemporaryDirectory() as home:
            path = write_codex_rollout(Path(home), "/repo", [
                {"type": "event_msg", "payload": {
                    "type": "user_message", "message": "old style ask"}},
                {"type": "event_msg", "payload": {
                    "type": "agent_message", "message": "old style reply"}},
            ])
            turns = freshair.parse_codex(path, keep_thinking=False)
            self.assertEqual([t["role"] for t in turns], ["user", "assistant"])
            self.assertEqual(turns[0]["text"], "old style ask")

    def test_a_file_with_both_generations_does_not_double_count(self):
        with tempfile.TemporaryDirectory() as home:
            path = write_codex_rollout(Path(home), "/repo", [
                {"type": "event_msg", "payload": {
                    "type": "user_message", "message": "the ask"}},
                codex_msg("user", "the ask"),
            ])
            turns = freshair.parse_codex(path, keep_thinking=False)
            self.assertEqual(len(turns), 1, "newer stream must win outright")

    def test_our_own_review_requests_are_filtered_here_too(self):
        with tempfile.TemporaryDirectory() as home:
            path = write_codex_rollout(Path(home), "/repo", [
                codex_msg("user", "the real goal"),
                codex_msg("user", freshair.MARKER_LINE + "review this"),
            ])
            turns = freshair.parse_codex(path, keep_thinking=False)
            self.assertEqual(len(turns), 1)


class TestCodexDiscovery(unittest.TestCase):
    def test_a_review_we_spawned_is_never_offered_back(self):
        with tempfile.TemporaryDirectory() as home:
            write_codex_rollout(Path(home), "/repo",
                                [codex_msg("user", freshair.MARKER_LINE + "review this")])
            with mock.patch.dict(os.environ, {"CODEX_HOME": home}):
                self.assertEqual(freshair.codex_transcripts(Path("/repo")), [])

    def test_a_trailing_slash_does_not_lose_the_session(self):
        with tempfile.TemporaryDirectory() as real:
            with tempfile.TemporaryDirectory() as home:
                write_codex_rollout(Path(home), real + os.sep, [codex_msg("user", "x")])
                with mock.patch.dict(os.environ, {"CODEX_HOME": home}):
                    self.assertEqual(len(freshair.codex_transcripts(Path(real))), 1)

    def test_only_sessions_from_this_project_are_offered(self):
        with tempfile.TemporaryDirectory() as home:
            write_codex_rollout(Path(home), "/somewhere/else", [codex_msg("user", "x")])
            with mock.patch.dict(os.environ, {"CODEX_HOME": home}):
                self.assertEqual(freshair.codex_transcripts(Path("/repo")), [])

    def test_a_session_started_at_the_repo_root_covers_a_subdirectory(self):
        with tempfile.TemporaryDirectory() as home:
            write_codex_rollout(Path(home), "/repo", [codex_msg("user", "x")])
            with mock.patch.dict(os.environ, {"CODEX_HOME": home}):
                found = freshair.codex_transcripts(Path("/repo/src/deep"))
            self.assertEqual(len(found), 1)

    def test_codex_home_env_var_is_respected(self):
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {"CODEX_HOME": home}):
                self.assertEqual(freshair.codex_home(), Path(home))

    def test_explicit_transcript_is_recognised_as_codex_by_its_name(self):
        with tempfile.TemporaryDirectory() as home:
            path = write_codex_rollout(Path(home), "/repo", [codex_msg("user", "x")])
            _, source = freshair.find_transcript(str(path), Path("/repo"))
            self.assertEqual(source, "codex")

    def test_an_unknown_source_is_rejected(self):
        with self.assertRaises(SystemExit):
            freshair.find_transcript(None, Path("/repo"), None, "cursor")


class TestSeveralReviewers(unittest.TestCase):
    """Independent reviewers that never saw each other's answers."""

    def reachable(self, installed, key=""):
        env = dict(os.environ); env["OPENROUTER_API_KEY"] = key
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(freshair.shutil, "which",
                               side_effect=lambda b: f"/usr/bin/{b}" if b in installed else None):
            return freshair.reachable_backends(freshair.resolve_backends({}))

    def test_every_reachable_reviewer_is_offered(self):
        self.assertEqual(self.reachable({"codex", "gemini", "claude"}, key="sk-or-v1-x"),
                         ["codex", "gemini", "claude", "openrouter"])

    def test_outsiders_come_first(self):
        got = self.reachable({"claude", "gemini"})
        self.assertLess(got.index("gemini"), got.index("claude"))

    def test_openrouter_needs_its_key_to_count(self):
        self.assertNotIn("openrouter", self.reachable({"claude"}))
        self.assertIn("openrouter", self.reachable({"claude"}, key="sk-or-v1-x"))

    def test_nothing_reachable_is_an_empty_list_not_a_crash(self):
        self.assertEqual(self.reachable(set()), [])


class TestOutputIsNotReadBack(unittest.TestCase):
    """A printed review can land in the session as captured tool output."""

    def test_a_previous_review_is_not_mistaken_for_conversation(self):
        report = f"<!-- {freshair.OUTPUT_MARKER} -->\n# Outside opinion\n\n## Verdict\nADJUST COURSE"
        self.assertTrue(freshair.is_own_payload(report))

    def test_it_is_filtered_out_of_both_hosts(self):
        report = f"<!-- {freshair.OUTPUT_MARKER} -->\n# Outside opinion"
        claude = write_transcript([user("the goal"), user(report)])
        self.assertEqual(len(freshair.parse_transcript(claude, False)), 1)

        with tempfile.TemporaryDirectory() as home:
            codex = write_codex_rollout(Path(home), "/repo", [
                codex_msg("user", "the goal"), codex_msg("user", report)])
            self.assertEqual(len(freshair.parse_codex(codex, False)), 1)

    def test_ordinary_prose_is_not_mistaken_for_ours(self):
        self.assertFalse(freshair.is_own_payload("here is my outside opinion on this"))
