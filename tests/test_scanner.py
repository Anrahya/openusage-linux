"""Unit tests for Codex log scanner and replay gating."""

import json
import tempfile
import unittest
from pathlib import Path

from openusage_linux.core.pricing import ModelPricingStore
from openusage_linux.core.providers.codex.scanner import CodexLogUsageScanner
from openusage_linux.core.scan_cache import ScanCache


class TestCodexScanner(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.home_dir = Path(self.temp_dir.name)
        self.sessions_dir = self.home_dir / "sessions" / "2026" / "08" / "16"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.cache = ScanCache(cache_file=self.home_dir / "cache.json")
        self.pricing = ModelPricingStore.get_shared()
        self.scanner = CodexLogUsageScanner(
            codex_home=str(self.home_dir),
            pricing_store=self.pricing,
            cache=self.cache,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_parse_standard_session(self):
        rollout_file = self.sessions_dir / "rollout-standard.jsonl"
        lines = [
            json.dumps({"type": "session_meta", "timestamp": "2026-08-16T10:00:00Z", "payload": {"session_id": "s1"}}),
            json.dumps({"type": "turn_context", "timestamp": "2026-08-16T10:00:01Z", "payload": {"model": "gpt-5"}}),
            json.dumps({
                "type": "event_msg",
                "timestamp": "2026-08-16T10:00:05Z",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 1000,
                            "cached_input_tokens": 800,
                            "output_tokens": 200,
                            "reasoning_output_tokens": 50,
                            "total_tokens": 1250,
                        }
                    },
                },
            }),
        ]
        rollout_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        events = self.scanner.parse_file(rollout_file)
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.model, "gpt-5")
        self.assertEqual(ev.input, 1000)
        self.assertEqual(ev.cached, 800)
        self.assertEqual(ev.output, 200)
        self.assertEqual(ev.reasoning, 50)
        self.assertEqual(ev.total, 1250)

    def test_child_session_replay_gate(self):
        """Replayed parent events before live task_started should be ignored."""
        rollout_file = self.sessions_dir / "rollout-child.jsonl"
        child_creation_time = "2026-08-16T12:00:00Z"
        parent_older_timestamp = 1786878000.0  # 11:00 AM (older than 12:00 PM)
        child_live_timestamp = 1786881605.0    # 12:00:05 PM (after 12:00 PM)

        lines = [
            # Child session meta
            json.dumps({
                "type": "session_meta",
                "timestamp": child_creation_time,
                "payload": {"session_id": "child-1", "parent_session_id": "parent-1"},
            }),
            # Replayed parent turn (should be ignored)
            json.dumps({
                "type": "event_msg",
                "timestamp": "2026-08-16T11:00:01Z",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {"input_tokens": 5000, "output_tokens": 500, "total_tokens": 5500}
                    },
                },
            }),
            # Replayed task_started with older timestamp
            json.dumps({
                "type": "event_msg",
                "timestamp": "2026-08-16T11:00:00Z",
                "payload": {"type": "task_started", "started_at": parent_older_timestamp},
            }),
            # Another replayed parent turn (should still be ignored)
            json.dumps({
                "type": "event_msg",
                "timestamp": "2026-08-16T11:05:00Z",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {"input_tokens": 3000, "output_tokens": 300, "total_tokens": 3300}
                    },
                },
            }),
            # Live task_started (opens the replay gate!)
            json.dumps({
                "type": "event_msg",
                "timestamp": "2026-08-16T12:00:05Z",
                "payload": {"type": "task_started", "started_at": child_live_timestamp},
            }),
            # Live child turn (MUST be captured!)
            json.dumps({
                "type": "event_msg",
                "timestamp": "2026-08-16T12:00:10Z",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {"input_tokens": 1200, "output_tokens": 150, "total_tokens": 1350}
                    },
                },
            }),
        ]
        rollout_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        events = self.scanner.parse_file(rollout_file)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].input, 1200)
        self.assertEqual(events[0].output, 150)

    def test_multiple_codex_homes_keep_same_relative_paths(self):
        with tempfile.TemporaryDirectory() as second_home_dir:
            second_home = Path(second_home_dir)
            first_file = self.home_dir / "sessions" / "2026" / "08" / "17" / "same.jsonl"
            second_file = second_home / "sessions" / "2026" / "08" / "17" / "same.jsonl"
            first_file.parent.mkdir(parents=True, exist_ok=True)
            second_file.parent.mkdir(parents=True, exist_ok=True)
            first_file.write_text("", encoding="utf-8")
            second_file.write_text("", encoding="utf-8")

            scanner = CodexLogUsageScanner(
                codex_home=f"{self.home_dir},{second_home}",
                pricing_store=self.pricing,
                cache=self.cache,
            )

            discovered = {path.resolve() for path in scanner.discover_session_files()}
            self.assertEqual(discovered, {first_file.resolve(), second_file.resolve()})

    def test_fast_tier_resets_when_settings_change(self):
        rollout_file = self.sessions_dir / "rollout-fast.jsonl"
        lines = [
            json.dumps({"type": "session_meta", "timestamp": "2026-08-16T10:00:00Z", "payload": {"session_id": "s1"}}),
            json.dumps({
                "type": "event_msg",
                "timestamp": "2026-08-16T10:00:01Z",
                "payload": {"type": "thread_settings_applied", "service_tier": "fast"},
            }),
            json.dumps({
                "type": "event_msg",
                "timestamp": "2026-08-16T10:00:02Z",
                "payload": {
                    "type": "token_count",
                    "info": {"last_token_usage": {"input_tokens": 10, "output_tokens": 1, "total_tokens": 11}},
                },
            }),
            json.dumps({
                "type": "event_msg",
                "timestamp": "2026-08-16T10:00:03Z",
                "payload": {"type": "thread_settings_applied", "service_tier": "standard"},
            }),
            json.dumps({
                "type": "event_msg",
                "timestamp": "2026-08-16T10:00:04Z",
                "payload": {
                    "type": "token_count",
                    "info": {"last_token_usage": {"input_tokens": 20, "output_tokens": 2, "total_tokens": 22}},
                },
            }),
        ]
        rollout_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        events = self.scanner.parse_file(rollout_file)
        self.assertEqual([event.is_fast for event in events], [True, False])

    def test_aggregate_keeps_per_day_model_breakdown(self):
        from openusage_linux.core.providers.codex.scanner import TokenEvent

        history = self.scanner.aggregate([
            TokenEvent("2026-08-18T10:00:00Z", "gpt-today", 100, 0, 10, 0, 110),
            TokenEvent("2026-08-17T10:00:00Z", "gpt-yesterday", 200, 0, 20, 0, 220),
        ])
        by_date = {entry.date: entry for entry in history.series}
        self.assertEqual([model.model for model in by_date["2026-08-18"].models], ["gpt-today"])
        self.assertEqual([model.model for model in by_date["2026-08-17"].models], ["gpt-yesterday"])
        self.assertEqual(by_date["2026-08-18"].total_tokens, 110)
        self.assertEqual(by_date["2026-08-17"].total_tokens, 220)

    def test_null_token_count_info_does_not_drop_later_turns(self):
        rollout_file = self.sessions_dir / "rollout-null-info.jsonl"
        lines = [
            json.dumps({"type": "session_meta", "timestamp": "2026-09-07T10:00:00Z", "payload": {"session_id": "s1"}}),
            json.dumps({"type": "turn_context", "timestamp": "2026-09-07T10:00:01Z", "payload": {"model": "gpt-6-astra"}}),
            json.dumps({
                "type": "event_msg",
                "timestamp": "2026-09-07T10:00:02Z",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {"input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
                    },
                },
            }),
            json.dumps({
                "type": "event_msg",
                "timestamp": "2026-09-07T10:00:03Z",
                "payload": {"type": "token_count", "info": None, "rate_limits": {}},
            }),
            json.dumps({
                "type": "event_msg",
                "timestamp": "2026-09-07T10:00:04Z",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {"input_tokens": 20, "output_tokens": 2, "total_tokens": 22},
                    },
                },
            }),
        ]
        rollout_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        events = self.scanner.parse_file(rollout_file)
        self.assertEqual([event.input for event in events], [10, 20])
        self.assertEqual(events[1].model, "gpt-6-astra")

    def test_token_usage_record_is_not_double_counted_with_token_count(self):
        rollout_file = self.sessions_dir / "rollout-usage-record.jsonl"
        usage = {
            "input_tokens": 39599,
            "cached_input_tokens": 38528,
            "output_tokens": 237,
            "reasoning_output_tokens": 0,
            "total_tokens": 39836,
        }
        lines = [
            json.dumps({"type": "session_meta", "timestamp": "2026-09-07T21:00:00Z", "payload": {"session_id": "s1"}}),
            json.dumps({"type": "turn_context", "timestamp": "2026-09-07T21:00:01Z", "payload": {"model": "gpt-6-astra"}}),
            json.dumps({
                "type": "token_usage_record",
                "timestamp": "2026-09-07T21:00:02Z",
                "payload": {"usage": usage, "turn_token_usage": usage},
            }),
            json.dumps({
                "type": "event_msg",
                "timestamp": "2026-09-07T21:00:03Z",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": usage,
                        "total_token_usage": {
                            "input_tokens": 100000,
                            "cached_input_tokens": 90000,
                            "output_tokens": 300,
                            "total_tokens": 100300,
                        },
                    },
                },
            }),
        ]
        rollout_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        events = self.scanner.parse_file(rollout_file)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].input, 39599)
        self.assertEqual(events[0].output, 237)
        self.assertEqual(events[0].model, "gpt-6-astra")


if __name__ == "__main__":
    unittest.main()
