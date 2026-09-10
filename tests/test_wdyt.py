"""Tests for wdyt's pure logic: transcript parsing, budget fitting, redaction,
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

_SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "wdyt" / "scripts" / "wdyt.py"
_spec = importlib.util.spec_from_file_location("wdyt", _SCRIPT)
wdyt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wdyt)


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
        got = wdyt.project_dir_for(Path("/home/user/what-do-you-think"))
        self.assertEqual(got.name, "-home-user-what-do-you-think")

    def test_dots_and_underscores_are_slugified_too(self):
        got = wdyt.project_dir_for(Path("/a/b.c_d"))
        self.assertEqual(got.name, "-a-b-c-d")


class TestParseTranscript(unittest.TestCase):
    def test_roles_and_ordering(self):
        path = write_transcript([
            user("build me a thing"),
            assistant([{"type": "text", "text": "on it"}]),
        ])
        turns = wdyt.parse_transcript(path, keep_thinking=False)
        self.assertEqual([t["role"] for t in turns], ["user", "assistant"])
        self.assertEqual(turns[0]["text"], "build me a thing")

    def test_tool_result_turns_are_not_labelled_as_the_human(self):
        """A user record that is only tool output is the harness, not a person."""
        path = write_transcript([
            user([{"type": "tool_result", "content": "total 16\ndrwxr-xr-x"}]),
        ])
        turns = wdyt.parse_transcript(path, keep_thinking=False)
        self.assertEqual(turns[0]["role"], "tool")
        self.assertIn("TOOL OUTPUT", wdyt.render_turns(turns))

    def test_mixed_user_turn_stays_human(self):
        path = write_transcript([
            user([{"type": "tool_result", "content": "out"},
                  {"type": "text", "text": "and also, stop"}]),
        ])
        self.assertEqual(wdyt.parse_transcript(path, False)[0]["role"], "user")

    def test_sidechain_records_are_dropped(self):
        path = write_transcript([
            user("main thread"),
            assistant([{"type": "text", "text": "subagent chatter"}], isSidechain=True),
        ])
        turns = wdyt.parse_transcript(path, keep_thinking=False)
        self.assertEqual(len(turns), 1)

    def test_thinking_is_excluded_by_default_and_included_on_request(self):
        path = write_transcript([
            assistant([{"type": "thinking", "thinking": "hmm"},
                       {"type": "text", "text": "answer"}]),
        ])
        self.assertNotIn("hmm", wdyt.parse_transcript(path, keep_thinking=False)[0]["text"])
        self.assertIn("hmm", wdyt.parse_transcript(path, keep_thinking=True)[0]["text"])

    def test_system_reminders_are_stripped(self):
        path = write_transcript([
            user("<system-reminder>ignore me</system-reminder>"),
            user("<system-reminder>noise</system-reminder>real question"),
        ])
        turns = wdyt.parse_transcript(path, keep_thinking=False)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["text"], "real question")

    def test_non_message_records_are_ignored(self):
        path = write_transcript([
            {"type": "queue-operation", "operation": "enqueue", "content": "x"},
            {"type": "summary", "summary": "y"},
            user("only this"),
        ])
        self.assertEqual(len(wdyt.parse_transcript(path, False)), 1)

    def test_malformed_lines_do_not_abort_the_parse(self):
        path = write_transcript([user("first")])
        with path.open("a", encoding="utf-8") as fh:
            fh.write("{not json at all\n\n")
            fh.write(json.dumps(user("second")) + "\n")
        self.assertEqual(len(wdyt.parse_transcript(path, False)), 2)

    def test_tool_use_keeps_the_meaningful_arguments(self):
        path = write_transcript([
            assistant([{"type": "tool_use", "name": "Bash",
                        "input": {"command": "ls -la", "timeout": 5000}}]),
        ])
        text = wdyt.parse_transcript(path, False)[0]["text"]
        self.assertIn("[tool: Bash]", text)
        self.assertIn("ls -la", text)
        self.assertNotIn("5000", text)   # noise arguments are dropped

    def test_long_tool_results_are_capped(self):
        path = write_transcript([
            user([{"type": "tool_result", "content": "x" * 50_000}]),
        ])
        text = wdyt.parse_transcript(path, False)[0]["text"]
        self.assertLess(len(text), wdyt.TOOL_RESULT_CAP + 200)
        self.assertIn("elided", text)


class TestClip(unittest.TestCase):
    def test_short_text_is_untouched(self):
        self.assertEqual(wdyt.clip("abc", 100), "abc")

    def test_long_text_keeps_both_ends(self):
        got = wdyt.clip("A" * 100 + "B" * 100, 40)
        self.assertTrue(got.startswith("A"))
        self.assertTrue(got.endswith("B"))
        self.assertIn("elided", got)


class TestFitToBudget(unittest.TestCase):
    def make(self, n, size=100):
        return [{"role": "user" if i % 2 == 0 else "assistant",
                 "text": f"turn{i}-" + "x" * size, "ts": ""} for i in range(n)]

    def test_everything_fits(self):
        turns = self.make(4, 10)
        text, stats = wdyt.fit_to_budget(turns, 100_000, 6)
        self.assertEqual(stats["elided_turns"], 0)
        self.assertEqual(stats["kept_turns"], 4)
        self.assertIn("turn0", text)
        self.assertIn("turn3", text)

    def test_over_budget_keeps_the_original_ask_and_the_recent_work(self):
        turns = self.make(60, 500)
        text, stats = wdyt.fit_to_budget(turns, 6_000, head_turns=3)
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
        text, stats = wdyt.fit_to_budget(turns, 1_000, head_turns=6)
        self.assertIn("turn0-", text)
        self.assertEqual(stats["kept_turns"], 6)

    def test_stats_account_for_every_turn(self):
        turns = self.make(40, 500)
        _, stats = wdyt.fit_to_budget(turns, 5_000, head_turns=3)
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
                out, n = wdyt.redact(f"the key is {secret} ok")
                self.assertEqual(n, 1)
                self.assertNotIn(secret, out)
                self.assertIn("REDACTED", out)

    def test_private_key_blocks_are_scrubbed_whole(self):
        blob = ("-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nlines\n"
                "-----END RSA PRIVATE KEY-----")
        out, n = wdyt.redact(blob)
        self.assertEqual(n, 1)
        self.assertNotIn("MIIEow", out)

    def test_ordinary_prose_is_left_alone(self):
        for benign in ["a normal sentence with sk- in it",
                       "AKIA is a prefix",
                       "import sky from 'sky'"]:
            with self.subTest(benign=benign):
                out, n = wdyt.redact(benign)
                self.assertEqual((out, n), (benign, 0))


class TestBackends(unittest.TestCase):
    def test_model_flag_goes_before_the_trailing_stdin_sentinel(self):
        cmd = wdyt.cli_command(wdyt.CLI_BACKENDS["codex"], "gpt-5.1")
        self.assertEqual(cmd[-1], "-", "the stdin sentinel must stay last")
        self.assertLess(cmd.index("-m"), cmd.index("-"))

    def test_model_flag_goes_before_a_variadic_tail(self):
        cmd = wdyt.cli_command(wdyt.CLI_BACKENDS["claude"], "opus")
        self.assertLess(cmd.index("--model"), cmd.index("--disallowed-tools"))

    def test_no_model_means_no_model_flag(self):
        cmd = wdyt.cli_command(wdyt.CLI_BACKENDS["codex"], None)
        self.assertNotIn("-m", cmd)
        self.assertEqual(cmd[-1], "-")

    def test_cli_backends_are_invoked_read_only(self):
        self.assertIn("read-only", wdyt.CLI_BACKENDS["codex"]["cmd"])
        claude = wdyt.cli_command(wdyt.CLI_BACKENDS["claude"], None)
        for tool in ("Edit", "Write", "Bash"):
            self.assertIn(tool, claude)

    def test_config_overrides_a_builtin_backend(self):
        merged = wdyt.resolve_backends(
            {"backends": {"codex": {"cmd": ["codex", "exec", "--new-flag"]}}}
        )
        self.assertEqual(merged["codex"]["cmd"], ["codex", "exec", "--new-flag"])
        self.assertEqual(merged["codex"]["model_flag"], "-m")   # untouched fields survive
        self.assertEqual(wdyt.CLI_BACKENDS["codex"]["cmd"][2], "--sandbox")  # no mutation

    def test_config_can_add_a_backend_wdyt_has_never_heard_of(self):
        merged = wdyt.resolve_backends(
            {"backends": {"llm": {"cmd": ["llm", "-m", "x"], "vendor": "simonw"}}}
        )
        self.assertIn("llm", merged)


class TestDetectBackend(unittest.TestCase):
    def detect(self, installed, key=""):
        env = dict(os.environ)
        env["OPENROUTER_API_KEY"] = key
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(wdyt.shutil, "which",
                               side_effect=lambda b: f"/usr/bin/{b}" if b in installed else None):
            return wdyt.detect_backend(wdyt.resolve_backends({}))

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
    def test_repo_config_wins_over_the_user_wide_one(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".wdyt.json").write_text('{"backend": "codex"}', encoding="utf-8")
            self.assertEqual(wdyt.load_config(Path(d))["backend"], "codex")

    def test_missing_config_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(wdyt.Path, "home", return_value=Path(d)):
                self.assertEqual(wdyt.load_config(Path(d)), {})

    def test_malformed_config_is_skipped_rather_than_crashing(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".wdyt.json").write_text("{oops", encoding="utf-8")
            with mock.patch.object(wdyt.Path, "home", return_value=Path(d)):
                self.assertEqual(wdyt.load_config(Path(d)), {})

    def test_the_shipped_example_config_is_valid(self):
        example = Path(__file__).resolve().parent.parent / ".wdyt.example.json"
        json.loads(example.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
