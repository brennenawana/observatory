from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
import time
import unittest
from importlib.metadata import distribution
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import observatory
from observatory.contract import Event, ObservatoryError
from observatory.hermes_plugin import HermesMetadataRecorder, register


class FakeContext:
    def __init__(
        self, settings: dict | None = None, data_dir: Path | None = None
    ) -> None:
        self.settings = settings or {}
        self.hooks: dict[str, Any] = {}
        self.commands: dict[str, Any] = {}
        self.unload_callbacks: list[Any] = []
        self.state = SimpleNamespace(data_dir=data_dir or Path.cwd() / ".plugin-data")

    def get_config(self, key: str, default=None):
        return self.settings.get(key, default)

    def register_hook(self, name: str, callback) -> None:
        self.hooks[name] = callback

    def register_command(self, name: str, handler, **_kwargs) -> None:
        self.commands[name] = handler

    def on_unload(self, callback) -> None:
        self.unload_callbacks.append(callback)


class HermesMetadataRecorderTests(unittest.TestCase):
    def make_recorder(self, **kwargs: Any) -> HermesMetadataRecorder:
        recorder = HermesMetadataRecorder(**kwargs)
        self.addCleanup(recorder.close)
        return recorder

    def test_queue_overflow_marks_recording_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = self.make_recorder(
                data_dir=Path(tmp) / "plugin-data" / "observatory",
                process_start_id="proc-start",
                process_id=123,
                queue_size=1,
            )
            entered = threading.Event()
            release = threading.Event()
            original_append = recorder.ledger.append

            def blocking_append(event):
                entered.set()
                release.wait(timeout=2)
                original_append(event)

            recorder.ledger.append = blocking_append
            recorder.observe("on_session_start", session_id="session-1")
            self.assertTrue(entered.wait(timeout=1))
            recorder.observe("pre_llm_call", session_id="session-1")
            recorder.observe("pre_api_request", session_id="session-1")

            status = recorder.status()
            self.assertFalse(status["complete"])
            self.assertEqual(status["dropped_events"], 1)
            release.set()
            recorder.close(timeout=2)

    def test_close_can_retry_after_a_full_queue_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = self.make_recorder(
                data_dir=Path(tmp) / "plugin-data" / "observatory",
                process_start_id="proc-start",
                process_id=123,
                queue_size=1,
            )
            entered = threading.Event()
            release = threading.Event()
            original_append = recorder.ledger.append

            def blocking_append(event):
                entered.set()
                release.wait(timeout=2)
                original_append(event)

            recorder.ledger.append = blocking_append
            recorder.observe("on_session_start", session_id="session-1")
            self.assertTrue(entered.wait(timeout=1))
            recorder.observe("pre_llm_call", session_id="session-1")

            self.assertFalse(recorder.close(timeout=0.01))
            self.assertTrue(recorder._worker.is_alive())
            release.set()
            self.assertTrue(recorder.close(timeout=2))
            self.assertFalse(recorder._worker.is_alive())

    def test_observe_does_not_wait_for_ledger_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = self.make_recorder(
                data_dir=Path(tmp) / "plugin-data" / "observatory",
                process_start_id="proc-start",
                process_id=123,
            )
            entered = threading.Event()
            release = threading.Event()
            original_append = recorder.ledger.append

            def blocking_append(event):
                entered.set()
                release.wait(timeout=2)
                original_append(event)

            recorder.ledger.append = blocking_append

            started = time.monotonic()
            recorder.observe(
                "post_tool_call", tool_name="terminal", args={"command": "git status"}
            )
            elapsed = time.monotonic() - started

            self.assertLess(elapsed, 0.1)
            self.assertTrue(entered.wait(timeout=1))
            release.set()
            self.assertTrue(recorder.flush(timeout=2))
            self.assertEqual(
                next(iter(recorder.read_events())).kind, "hermes.tool.finish"
            )
            recorder.close(timeout=2)

    def test_tool_finish_writes_metadata_without_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = self.make_recorder(
                data_dir=Path(tmp) / "plugin-data" / "observatory",
                process_start_id="proc-start",
                process_id=123,
            )

            recorder.emit(
                "post_tool_call",
                tool_name="terminal",
                args={
                    "command": "curl -H 'Authorization: secret-value' https://example.test"
                },
                session_id="session-1",
                task_id="task-1",
                turn_id="turn-1",
                api_request_id="request-1",
                tool_call_id="tool-1",
                status="ok",
            )

            rows = list(recorder.read_events())
            self.assertEqual(len(rows), 1)
            event = rows[0]
            self.assertIsInstance(event, Event)
            self.assertEqual(event.kind, "hermes.tool.finish")
            self.assertNotEqual(event.subject, "session-1")
            self.assertEqual(len(event.subject), 64)
            self.assertEqual(event.grade, "native")
            self.assertEqual(event.source, "hermes-observatory")
            self.assertNotIn("tool_name", event.data)
            self.assertEqual(len(event.data["tool_name_hash"]), 64)
            self.assertEqual(event.data["argument_item_count"], 1)
            self.assertEqual(event.data["status"], "ok")
            self.assertNotIn("args", event.data)
            self.assertNotIn(b"secret-value", recorder.path.read_bytes())
            self.assertEqual(event.data["event_schema"], "hermes-observatory.v1")
            self.assertEqual(event.data["capture_policy"], "metadata")
            self.assertEqual(event.data["process_id"], 123)
            self.assertEqual(event.data["process_start_id"], "proc-start")
            self.assertEqual(event.data["process_sequence"], 1)
            self.assertTrue(event.data["event_id"])

    def test_approval_choices_match_the_hermes_hook_contract(self) -> None:
        choices = {
            "once",
            "session",
            "always",
            "notify_failed",
            "smart_approve",
            "smart_deny",
            "transport_busy",
            "transport_error",
            "transport_interrupted",
            "transport_invalid",
            "transport_stale",
            "transport_timeout",
        }
        with tempfile.TemporaryDirectory() as tmp:
            recorder = self.make_recorder(
                data_dir=Path(tmp) / "plugin-data" / "observatory",
                process_start_id="proc-start",
                process_id=123,
            )
            for choice in choices:
                recorder.emit("post_approval_response", choice=choice)

            recorded = {event.data["choice"] for event in recorder.read_events()}
            self.assertEqual(recorded, choices)

    def test_register_exposes_supported_hooks_and_status_command(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, {"HERMES_HOME": tmp}, clear=False),
        ):
            ctx = FakeContext(data_dir=Path(tmp) / "plugin-data" / "observatory")
            register(ctx)

            self.assertEqual(
                set(ctx.hooks),
                {
                    "on_session_start",
                    "pre_llm_call",
                    "pre_api_request",
                    "post_api_request",
                    "api_request_error",
                    "post_tool_call",
                    "post_llm_call",
                    "on_session_end",
                    "on_session_finalize",
                    "on_session_reset",
                    "subagent_start",
                    "subagent_stop",
                    "pre_approval_request",
                    "post_approval_response",
                    "pre_verify",
                    "on_skill_lifecycle",
                    "kanban_task_claimed",
                    "kanban_task_completed",
                    "kanban_task_blocked",
                    "on_kanban_worker_spawned",
                    "on_kanban_worker_exited",
                    "on_kanban_worker_stale_claim",
                    "on_kanban_task_updated",
                    "on_kanban_dispatch_tick",
                },
            )
            self.assertIn("observatory", ctx.commands)
            status = json.loads(ctx.commands["observatory"]("status"))
            self.assertEqual(status["capture_policy"], "metadata")
            self.assertEqual(status["ready"], True)
            self.assertEqual(status["event_count"], 0)

    def test_register_flushes_the_writer_at_process_exit(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, {"HERMES_HOME": tmp}, clear=False),
            patch("atexit.register") as register_at_exit,
        ):
            ctx = FakeContext(data_dir=Path(tmp) / "plugin-data" / "observatory")
            register(ctx)

            register_at_exit.assert_called_once()
            callback = register_at_exit.call_args.args[0]
            self.assertTrue(callback())

    def test_register_uses_the_context_profile_data_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            default_home = root / "default"
            profile_data = root / "secondary" / "plugin-data" / "observatory"
            profile_data.parent.parent.mkdir()
            with patch.dict(
                os.environ, {"HERMES_HOME": str(default_home)}, clear=False
            ):
                ctx = FakeContext(data_dir=profile_data)
                register(ctx)

            status = json.loads(ctx.commands["observatory"]("status"))
            self.assertTrue(Path(status["path"]).is_relative_to(profile_data.resolve()))
            self.assertFalse(default_home.exists())
            self.assertEqual(len(ctx.unload_callbacks), 1)
            self.assertTrue(ctx.unload_callbacks[0]())

    def test_package_registers_hermes_plugin_entry_point(self) -> None:
        entry_points = {
            entry.name: entry.value
            for entry in distribution("observatory").entry_points
            if entry.group == "hermes_agent.plugins"
        }

        self.assertEqual(entry_points["observatory"], "observatory.hermes_plugin")

    def test_package_version_marks_the_hermes_adapter_release(self) -> None:
        self.assertEqual(observatory.__version__, "0.2.0")
        self.assertEqual(distribution("observatory").version, observatory.__version__)

    def test_content_fields_are_reduced_to_sizes(self) -> None:
        secret = "planted-sensitive-value"
        with tempfile.TemporaryDirectory() as tmp:
            recorder = self.make_recorder(
                data_dir=Path(tmp) / "plugin-data" / "observatory",
                process_start_id="proc-start",
                process_id=123,
            )

            recorder.emit(
                "api_request_error",
                session_id="session-1",
                reason=secret,
                error={"type": secret, "message": secret},
                request={"prompt": secret},
                response={"answer": secret},
                usage={"input_tokens": 12, "output_tokens": 4, secret: 99},
            )

            event = next(iter(recorder.read_events()))
            self.assertNotIn(secret.encode(), recorder.path.read_bytes())
            self.assertNotIn("reason", event.data)
            self.assertEqual(event.data["reason_chars"], len(secret))
            self.assertEqual(len(event.data["error_type_hash"]), 64)
            self.assertEqual(event.data["error_message_chars"], len(secret))
            self.assertEqual(event.data["request_item_count"], 1)
            self.assertEqual(event.data["response_item_count"], 1)
            self.assertEqual(
                event.data["usage"], {"input_tokens": 12, "output_tokens": 4}
            )

    def test_unknown_source_schema_is_not_persisted(self) -> None:
        secret = "private-schema-value"
        with tempfile.TemporaryDirectory() as tmp:
            recorder = self.make_recorder(
                data_dir=Path(tmp) / "plugin-data" / "observatory",
                process_start_id="proc-start",
                process_id=123,
            )

            recorder.emit(
                "on_session_start",
                session_id="session-1",
                telemetry_schema_version=secret,
            )

            event = next(iter(recorder.read_events()))
            self.assertEqual(event.data["source_schema"], "unknown")
            self.assertNotIn(secret.encode(), recorder.path.read_bytes())

    def test_all_hook_strings_are_hashed_or_reduced_to_fixed_enums(self) -> None:
        secret = "planted-sensitive-value"
        with tempfile.TemporaryDirectory() as tmp:
            recorder = self.make_recorder(
                data_dir=Path(tmp) / "plugin-data" / "observatory",
                run_id=secret,
                process_start_id="proc-start",
                process_id=123,
            )

            first = recorder.emit(
                "on_skill_lifecycle",
                session_id=secret,
                task_id=secret,
                model=secret,
                provider=secret,
                skill_name=secret,
                pattern_key=secret,
                pattern_keys=[secret],
                changed_fields=[secret],
                board=secret,
                assignee=secret,
                status="success",
            )
            second = recorder.emit("on_session_end", session_id=secret)

            raw = recorder.path.read_bytes()
            self.assertNotIn(secret.encode(), raw)
            self.assertNotIn(secret, str(recorder.path))
            self.assertNotIn(secret, json.dumps(recorder.status()))
            self.assertEqual(first.subject, second.subject)
            self.assertEqual(len(first.subject), 64)
            self.assertEqual(first.data["run_id_hash"], first.subject)
            self.assertEqual(len(first.data["model_hash"]), 64)
            self.assertEqual(len(first.data["skill_name_hash"]), 64)
            self.assertEqual(first.data["status"], "success")

    def test_terminal_tool_records_known_operation_without_command(self) -> None:
        secret = "sensitive-search-term"
        with tempfile.TemporaryDirectory() as tmp:
            recorder = self.make_recorder(
                data_dir=Path(tmp) / "plugin-data" / "observatory",
                process_start_id="proc-start",
                process_id=123,
            )

            recorder.emit(
                "post_tool_call",
                tool_name="terminal",
                args={"command": f"qmd search platform --query {secret}"},
                session_id="session-1",
            )

            event = next(iter(recorder.read_events()))
            self.assertEqual(event.data["tool_operation"], "qmd")
            self.assertNotIn(secret.encode(), recorder.path.read_bytes())

    def test_sprint_helper_records_semantic_operation_without_path(self) -> None:
        secret = "private-directory"
        with tempfile.TemporaryDirectory() as tmp:
            recorder = self.make_recorder(
                data_dir=Path(tmp) / "plugin-data" / "observatory",
                process_start_id="proc-start",
                process_id=123,
            )

            recorder.emit(
                "post_tool_call",
                tool_name="terminal",
                args={"command": f"python3 /Users/{secret}/github_snapshot.py"},
                session_id="session-1",
            )

            event = next(iter(recorder.read_events()))
            self.assertEqual(event.data["tool_operation"], "sprint_github_snapshot")
            self.assertNotIn(secret.encode(), recorder.path.read_bytes())

    def test_tool_family_identifies_jira_without_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = self.make_recorder(
                data_dir=Path(tmp) / "plugin-data" / "observatory",
                process_start_id="proc-start",
                process_id=123,
            )

            recorder.emit(
                "post_tool_call",
                tool_name="mcp__atlassian__searchJiraIssuesUsingJql",
                args={"jql": "sensitive query"},
                session_id="session-1",
            )

            event = next(iter(recorder.read_events()))
            self.assertEqual(event.data["tool_family"], "jira")
            self.assertNotIn(b"sensitive query", recorder.path.read_bytes())

    def test_run_shard_is_private_and_has_stable_ordering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = self.make_recorder(
                data_dir=Path(tmp) / "plugin-data" / "observatory",
                run_id="sprint/DS-2911",
                process_start_id="proc-start",
                process_id=123,
            )

            first = recorder.emit("on_session_start", session_id="session-1")
            second = recorder.emit(
                "on_session_end", session_id="session-1", completed=True
            )

            self.assertNotIn("sprint-DS-2911", str(recorder.path))
            self.assertEqual(first.subject, second.subject)
            self.assertNotEqual(first.subject, "sprint/DS-2911")
            self.assertEqual(len(first.subject), 64)
            self.assertEqual(first.data["process_sequence"], 1)
            self.assertEqual(second.data["process_sequence"], 2)
            self.assertNotEqual(first.data["event_id"], second.data["event_id"])
            self.assertEqual(stat.S_IMODE(recorder.path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(recorder.path.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(recorder.data_dir.stat().st_mode), 0o700)
            self.assertEqual(
                stat.S_IMODE((recorder.data_dir / "runs").stat().st_mode), 0o700
            )

    def test_partial_os_write_still_persists_the_complete_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = self.make_recorder(
                data_dir=Path(tmp) / "plugin-data" / "observatory",
                process_start_id="proc-start",
                process_id=123,
            )
            original_write = os.write

            def partial_write(descriptor: int, value: bytes) -> int:
                return original_write(descriptor, value[: max(1, len(value) // 2)])

            with patch("observatory.hermes_plugin.os.write", side_effect=partial_write):
                recorder.emit("on_session_start", session_id="session-1")

            events = list(recorder.read_events())
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].kind, "hermes.session.start")

    @unittest.skipUnless(
        hasattr(os, "symlink") and hasattr(os, "O_NOFOLLOW"),
        "requires symlink protection",
    )
    def test_replaced_ledger_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = self.make_recorder(
                data_dir=Path(tmp) / "plugin-data" / "observatory",
                process_start_id="proc-start",
                process_id=123,
            )
            target = Path(tmp) / "target.txt"
            target.write_text("unchanged", encoding="utf-8")
            recorder.path.unlink()
            recorder.path.symlink_to(target)

            with self.assertRaises(ObservatoryError):
                recorder.emit("on_session_start", session_id="session-1")

            self.assertEqual(target.read_text(encoding="utf-8"), "unchanged")

    def test_status_reports_writer_failure_without_reading_unsafe_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = self.make_recorder(
                data_dir=Path(tmp) / "plugin-data" / "observatory",
                process_start_id="proc-start",
                process_id=123,
            )
            target = Path(tmp) / "target.txt"
            target.write_text("unchanged", encoding="utf-8")
            recorder.path.unlink()
            recorder.path.symlink_to(target)
            recorder.observe("on_session_start", session_id="session-1")
            self.assertTrue(recorder.flush(timeout=2))

            status = recorder.status()
            self.assertFalse(status["complete"])
            self.assertEqual(status["writer_errors"], 1)
            self.assertEqual(status["event_count"], 0)
            self.assertEqual(target.read_text(encoding="utf-8"), "unchanged")

    def test_replaced_ledger_hard_link_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = self.make_recorder(
                data_dir=Path(tmp) / "plugin-data" / "observatory",
                process_start_id="proc-start",
                process_id=123,
            )
            target = Path(tmp) / "target.txt"
            target.write_text("unchanged", encoding="utf-8")
            recorder.path.unlink()
            os.link(target, recorder.path)

            with self.assertRaises(ObservatoryError):
                recorder.emit("on_session_start", session_id="session-1")

            self.assertEqual(target.read_text(encoding="utf-8"), "unchanged")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
            recorder.close()

    def test_replaced_parent_directory_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recorder = self.make_recorder(
                data_dir=root / "plugin-data" / "observatory",
                process_start_id="proc-start",
                process_id=123,
            )
            parent = recorder.path.parent
            parked = root / "parked"
            outside = root / "outside"
            outside.mkdir()
            parent.rename(parked)
            parent.symlink_to(outside, target_is_directory=True)

            with self.assertRaises(ObservatoryError):
                recorder.emit("on_session_start", session_id="session-1")

            self.assertEqual(list(outside.iterdir()), [])
            recorder.close()

    def test_parent_directory_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "plugin-data" / "observatory"
            outside = root / "outside"
            outside.mkdir()
            (data_dir / "runs").mkdir(parents=True)
            (data_dir / "runs" / "unscoped").symlink_to(
                outside, target_is_directory=True
            )

            with self.assertRaises(ObservatoryError):
                HermesMetadataRecorder(
                    data_dir=data_dir,
                    process_start_id="proc-start",
                    process_id=123,
                )

            self.assertEqual(list(outside.iterdir()), [])

    def test_existing_data_directory_becomes_private(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "observatory"
            data_dir.mkdir(mode=0o755)
            recorder = self.make_recorder(
                data_dir=data_dir,
                process_start_id="proc-start",
                process_id=123,
            )

            self.assertEqual(stat.S_IMODE(recorder.data_dir.stat().st_mode), 0o700)

    def test_missing_no_follow_support_disables_recording_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "observatory"
            with (
                patch.object(os, "O_NOFOLLOW", 0),
                self.assertRaisesRegex(ObservatoryError, "secure storage capabilities"),
            ):
                HermesMetadataRecorder(data_dir=data_dir)
            self.assertFalse(data_dir.exists())

    def test_missing_directory_fd_support_disables_recording_without_writing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "observatory"
            with (
                patch.object(os, "supports_dir_fd", set()),
                self.assertRaisesRegex(ObservatoryError, "secure storage capabilities"),
            ):
                HermesMetadataRecorder(data_dir=data_dir)
            self.assertFalse(data_dir.exists())

    def test_missing_no_follow_stat_support_disables_recording_without_writing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "observatory"
            with (
                patch.object(os, "supports_follow_symlinks", set()),
                self.assertRaisesRegex(ObservatoryError, "secure storage capabilities"),
            ):
                HermesMetadataRecorder(data_dir=data_dir)
            self.assertFalse(data_dir.exists())

    def test_data_directory_symlink_is_rejected_without_changing_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside"
            outside.mkdir(mode=0o755)
            data_dir = root / "observatory"
            data_dir.symlink_to(outside, target_is_directory=True)

            with self.assertRaises(ObservatoryError):
                HermesMetadataRecorder(
                    data_dir=data_dir,
                    process_start_id="proc-start",
                    process_id=123,
                )

            self.assertEqual(list(outside.iterdir()), [])
            self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o755)


if __name__ == "__main__":
    unittest.main()
