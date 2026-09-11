"""Metadata-only Hermes observer for the Observatory reference ledger.

This adapter records identifiers, timings, statuses, usage, and payload sizes.
It never persists prompts, responses, tool arguments, or tool results.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import math
import os
import queue
import re
import shlex
import stat
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

from .contract import Event, ObservatoryError
from .ledger import JsonlLedger
from .probe import ProbeResult, probe

EVENT_SCHEMA = "hermes-observatory.v1"
CAPTURE_POLICY = "metadata"
SOURCE = "hermes-observatory"

_HOOK_KINDS = {
    "on_session_start": "hermes.session.start",
    "pre_llm_call": "hermes.turn.start",
    "pre_api_request": "hermes.model.request.start",
    "post_api_request": "hermes.model.request.finish",
    "api_request_error": "hermes.model.request.error",
    "post_tool_call": "hermes.tool.finish",
    "post_llm_call": "hermes.turn.response",
    "on_session_end": "hermes.turn.finish",
    "on_session_finalize": "hermes.session.finish",
    "on_session_reset": "hermes.session.reset",
    "subagent_start": "hermes.subagent.start",
    "subagent_stop": "hermes.subagent.finish",
    "pre_approval_request": "hermes.approval.request",
    "post_approval_response": "hermes.approval.decision",
    "pre_verify": "hermes.verify.request",
    "on_skill_lifecycle": "hermes.skill.lifecycle",
    "kanban_task_claimed": "hermes.kanban.task.claimed",
    "kanban_task_completed": "hermes.kanban.task.completed",
    "kanban_task_blocked": "hermes.kanban.task.blocked",
    "on_kanban_worker_spawned": "hermes.kanban.worker.spawned",
    "on_kanban_worker_exited": "hermes.kanban.worker.exited",
    "on_kanban_worker_stale_claim": "hermes.kanban.worker.stale_claim",
    "on_kanban_task_updated": "hermes.kanban.task.updated",
    "on_kanban_dispatch_tick": "hermes.kanban.dispatch.tick",
}

_HASH_FIELDS = {
    "session_id",
    "task_id",
    "turn_id",
    "api_request_id",
    "tool_call_id",
    "tool_name",
    "parent_session_id",
    "parent_turn_id",
    "parent_subagent_id",
    "child_session_id",
    "child_subagent_id",
    "model",
    "provider",
    "platform",
    "surface",
    "api_mode",
    "finish_reason",
    "response_model",
    "error_type",
    "turn_exit_reason",
    "action",
    "skill_name",
    "provenance",
    "profile_name",
    "board",
    "assignee",
    "exit_kind",
    "outcome",
    "retry_status",
    "decided_by",
    "pattern_key",
    "event_type",
    "child_role",
    "child_status",
}

_HASH_COLLECTION_FIELDS = {
    "pattern_keys",
    "changed_fields",
}

_NUMERIC_FIELDS = {
    "api_call_count",
    "retry_count",
    "max_retries",
    "status_code",
    "message_count",
    "tool_count",
    "approx_input_tokens",
    "request_char_count",
    "max_tokens",
    "api_duration",
    "assistant_content_chars",
    "assistant_tool_call_count",
    "duration_ms",
    "use_count",
    "worker_pid",
    "exit_code",
    "attempt",
}

_BOOLEAN_FIELDS = {
    "retryable",
    "completed",
    "failed",
    "interrupted",
    "reused",
    "reuse_after_patch",
    "heartbeat_stale",
    "dry_run",
    "coding",
}

_ENUM_FIELDS = {
    "status": {
        "blocked",
        "cancelled",
        "completed",
        "error",
        "failed",
        "ok",
        "success",
        "timeout",
    },
    "choice": {
        "allow",
        "always",
        "approve",
        "deny",
        "error",
        "notify_failed",
        "once",
        "session",
        "smart_approve",
        "smart_deny",
        "timeout",
        "transport_busy",
        "transport_error",
        "transport_interrupted",
        "transport_invalid",
        "transport_stale",
        "transport_timeout",
    },
}

_USAGE_FIELDS = {
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "request_count",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
}

_KNOWN_TERMINAL_OPERATIONS = {"gh", "git", "gitnexus", "qmd"}
_SPRINT_HELPER_OPERATIONS = {
    "github_snapshot.py": "sprint_github_snapshot",
    "validate_plan.py": "sprint_plan_validation",
}


def _slug(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return cleaned[:128] or fallback


def _hash_text(field: str, value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    bounded = value[:512]
    material = (f"hermes-observatory.v1\0{field}\0{len(value)}\0{bounded}").encode()
    return hashlib.sha256(material).hexdigest()


def _safe_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if abs(value) > 10**18:
        return None
    return value


def _terminal_operation(payload: dict[str, Any]) -> str | None:
    if payload.get("tool_name") != "terminal":
        return None
    args = payload.get("args")
    command = args.get("command") if isinstance(args, dict) else None
    if not isinstance(command, str):
        return None
    try:
        words = shlex.split(command[:2048], posix=True)
    except ValueError:
        return None
    if not words:
        return None
    executable = Path(words[0]).name
    if executable in _KNOWN_TERMINAL_OPERATIONS:
        return executable
    if executable.startswith("python"):
        for word in words[1:]:
            operation = _SPRINT_HELPER_OPERATIONS.get(Path(word).name)
            if operation:
                return operation
    return None


def _tool_family(tool_name: Any, operation: str | None) -> str | None:
    if not isinstance(tool_name, str):
        return None
    lowered = tool_name[:256].lower()
    if "atlassian" in lowered or "jira" in lowered:
        return "jira"
    if "github" in lowered or operation == "gh":
        return "github"
    if operation in {"qmd", "gitnexus"}:
        return "knowledge"
    if tool_name == "terminal":
        return "local"
    return None


def _reject_symlink_components(path: Path, root: Path) -> None:
    absolute = path.expanduser().absolute()
    trusted_root = root.expanduser().absolute()
    if not absolute.is_relative_to(trusted_root):
        raise ObservatoryError("Observatory storage path escaped its data directory")
    current = trusted_root
    for part in absolute.relative_to(trusted_root).parts:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise ObservatoryError(
                f"Observatory storage path must not contain a symlink: {current}"
            )


def _existing_trusted_root(path: Path) -> Path:
    current = path.expanduser().absolute()
    while True:
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            if current == current.parent:
                raise ObservatoryError("No existing Observatory storage ancestor")
            current = current.parent
            continue
        if stat.S_ISLNK(mode):
            raise ObservatoryError(
                f"Observatory storage path must not contain a symlink: {current}"
            )
        if not stat.S_ISDIR(mode):
            raise ObservatoryError(
                f"Observatory storage ancestor is not a directory: {current}"
            )
        return current


def _require_secure_storage_capabilities() -> None:
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    directory_only = getattr(os, "O_DIRECTORY", 0)
    supports_dir_fd = getattr(os, "supports_dir_fd", set())
    supports_no_follow = getattr(os, "supports_follow_symlinks", set())
    required_dir_fd_functions = (os.mkdir, os.open, os.stat)
    if (
        not isinstance(no_follow, int)
        or no_follow == 0
        or not isinstance(directory_only, int)
        or directory_only == 0
        or any(
            function not in supports_dir_fd for function in required_dir_fd_functions
        )
        or os.stat not in supports_no_follow
    ):
        raise ObservatoryError(
            "Hermes Observatory requires secure storage capabilities"
        )


def _open_private_run_directory(
    data_dir: Path, run_dir: str, *, profile_home: Path | None = None
) -> tuple[Path, int]:
    _require_secure_storage_capabilities()
    raw_data_dir = data_dir.expanduser().absolute()
    if raw_data_dir.is_symlink():
        raise ObservatoryError("Observatory data directory must not be a symlink")

    if profile_home is None:
        trusted_raw = _existing_trusted_root(raw_data_dir)
    else:
        trusted_raw = profile_home.expanduser().absolute()
        if trusted_raw.is_symlink():
            raise ObservatoryError("Hermes profile home must not be a symlink")
        if not raw_data_dir.is_relative_to(trusted_raw):
            raise ObservatoryError(
                "Observatory data directory escaped the Hermes profile"
            )
        if not trusted_raw.is_dir():
            raise ObservatoryError("Hermes profile home does not exist")

    relative_data = raw_data_dir.relative_to(trusted_raw)
    current_raw = trusted_raw
    for part in relative_data.parts:
        current_raw /= part
        try:
            mode = current_raw.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise ObservatoryError(
                f"Observatory storage path must not contain a symlink: {current_raw}"
            )

    trusted = trusted_raw.resolve(strict=True)
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    directory_fd = os.open(trusted, flags)
    current = trusted
    components = (*relative_data.parts, "runs", run_dir)
    private_index = len(relative_data.parts) - 1
    if not relative_data.parts and hasattr(os, "fchmod"):
        os.fchmod(directory_fd, 0o700)
    try:
        for index, part in enumerate(components):
            try:
                os.mkdir(part, 0o700, dir_fd=directory_fd)
            except FileExistsError:
                pass
            try:
                next_fd = os.open(part, flags, dir_fd=directory_fd)
            except OSError as exc:
                raise ObservatoryError(
                    "Observatory storage path contains an unsafe component"
                ) from exc
            os.close(directory_fd)
            directory_fd = next_fd
            current /= part
            if index >= private_index and hasattr(os, "fchmod"):
                os.fchmod(directory_fd, 0o700)
        canonical_data = trusted.joinpath(*relative_data.parts)
        return canonical_data, directory_fd
    except Exception:
        os.close(directory_fd)
        raise


class _PrivateJsonlLedger(JsonlLedger):
    """JSONL ledger pinned to a private directory and regular inode."""

    def __init__(self, path: Path, root: Path, directory_fd: int) -> None:
        super().__init__(path)
        self.root = root
        self._directory_fd = directory_fd
        self._io_lock = threading.Lock()
        self._closed = False
        flags = os.O_CREAT | os.O_APPEND | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        file_fd = -1
        try:
            file_fd = os.open(path.name, flags, 0o600, dir_fd=directory_fd)
            self._file_fd = file_fd
            self._validate_inode()
            if hasattr(os, "fchmod"):
                os.fchmod(self._file_fd, 0o600)
        except Exception:
            if file_fd >= 0:
                os.close(file_fd)
            os.close(directory_fd)
            raise

    def _validate_inode(self) -> os.stat_result:
        opened = os.fstat(self._file_fd)
        current = os.stat(
            self.path.name, dir_fd=self._directory_fd, follow_symlinks=False
        )
        if not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(current.st_mode):
            raise ObservatoryError("Observatory ledger is not a regular file")
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise ObservatoryError("Observatory ledger path was replaced")
        if opened.st_nlink != 1 or current.st_nlink != 1:
            raise ObservatoryError("Observatory ledger must have exactly one hard link")
        return opened

    def append(self, event: Event) -> None:
        if not isinstance(event, Event):
            raise ObservatoryError("append() takes an Event")
        row = event.to_dict()
        row["data"] = self.redactor.scrub(row.get("data") or {})
        line = json.dumps(row, ensure_ascii=False, default=str, separators=(",", ":"))
        if "\n" in line:
            line = line.replace("\n", "\\n")
        encoded = (line + "\n").encode()

        with self._io_lock:
            if self._closed:
                raise ObservatoryError("Observatory ledger is closed")
            _reject_symlink_components(self.path, self.root)
            self._validate_inode()
            remaining = memoryview(encoded)
            while remaining:
                written = os.write(self._file_fd, remaining)
                if written <= 0:
                    raise ObservatoryError("Observatory ledger write made no progress")
                remaining = remaining[written:]
            os.fsync(self._file_fd)
            self._validate_inode()
            self.appended += 1

    def raw_bytes(self) -> bytes:
        with self._io_lock:
            if self._closed:
                raise ObservatoryError("Observatory ledger is closed")
            _reject_symlink_components(self.path, self.root)
            size = self._validate_inode().st_size
            if hasattr(os, "pread"):
                return os.pread(self._file_fd, size, 0)
            position = os.lseek(self._file_fd, 0, os.SEEK_CUR)
            try:
                os.lseek(self._file_fd, 0, os.SEEK_SET)
                return os.read(self._file_fd, size)
            finally:
                os.lseek(self._file_fd, position, os.SEEK_SET)

    def read(
        self,
        *,
        subject: str | None = None,
        kind: str | None = None,
        since: str | None = None,
    ) -> Iterator[Event]:
        for raw_line in self.raw_bytes().splitlines():
            try:
                row = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            if subject is not None and row.get("subject") != subject:
                continue
            if kind is not None and row.get("kind") != kind:
                continue
            if since is not None and str(row.get("at_utc", "")) < since:
                continue
            try:
                yield Event.from_dict(row)
            except (ObservatoryError, TypeError):
                continue

    def close(self) -> None:
        with self._io_lock:
            if self._closed:
                return
            self._closed = True
            os.close(self._file_fd)
            os.close(self._directory_fd)


class HermesMetadataRecorder:
    """Translate Hermes observer hooks into metadata-only Observatory events."""

    def __init__(
        self,
        *,
        data_dir: Path,
        run_id: str = "",
        process_start_id: str | None = None,
        process_id: int | None = None,
        queue_size: int = 4096,
        profile_home: Path | None = None,
    ) -> None:
        self.run_id = run_id[:512].strip()
        self.process_start_id = (process_start_id or uuid.uuid4().hex)[:128]
        self.process_id = os.getpid() if process_id is None else process_id
        run_dir = _hash_text("subject", self.run_id) or "unscoped"
        file_name = (
            f"{self.process_id}-{_slug(self.process_start_id, 'process')}.events.jsonl"
        )
        self.data_dir, run_directory_fd = _open_private_run_directory(
            data_dir, run_dir, profile_home=profile_home
        )
        self.path = self.data_dir / "runs" / run_dir / file_name
        self.ledger = _PrivateJsonlLedger(self.path, self.data_dir, run_directory_fd)
        self._sequence = 0
        self._lock = threading.Lock()
        self.probe_result: ProbeResult = probe(self.ledger)
        self._queue: queue.Queue[Event | None] = queue.Queue(maxsize=max(1, queue_size))
        self._dropped_events = 0
        self._writer_errors = 0
        self._recorded_events = 0
        self._last_error_type: str | None = None
        self._closing = False
        self._stop_queued = False
        self._closed = False
        self._worker = threading.Thread(
            target=self._write_events,
            name="hermes-observatory-writer",
            daemon=True,
        )
        self._worker.start()

    def read_events(self, *, include_probe: bool = False) -> Iterable[Event]:
        events = self.ledger.read()
        if include_probe:
            return events
        return (event for event in events if event.kind != "probe.canary")

    def _subject(self, payload: dict[str, Any]) -> str:
        value = (
            self.run_id
            or payload.get("session_id")
            or payload.get("task_id")
            or payload.get("parent_session_id")
        )
        return (
            _hash_text("subject", value)
            or _hash_text("subject", "unscoped")
            or "0" * 64
        )

    def _metadata(self, payload: dict[str, Any]) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for name in _HASH_FIELDS:
            value = _hash_text(name, payload.get(name))
            if value:
                data[f"{name}_hash"] = value
        for name in _NUMERIC_FIELDS:
            value = _safe_number(payload.get(name))
            if value is not None:
                data[name] = value
        for name in _BOOLEAN_FIELDS:
            value = payload.get(name)
            if isinstance(value, bool):
                data[name] = value
        for name, allowed in _ENUM_FIELDS.items():
            value = payload.get(name)
            if isinstance(value, str) and value in allowed:
                data[name] = value
        for name in _HASH_COLLECTION_FIELDS:
            value = payload.get(name)
            if isinstance(value, (list, tuple)):
                data[f"{name}_hashes"] = [
                    hashed
                    for item in value[:100]
                    if (hashed := _hash_text(name, item)) is not None
                ]

        error = payload.get("error")
        if type(error) is dict:
            error_type = error.get("type")
            if isinstance(error_type, str):
                data["error_type_hash"] = _hash_text("error_type", error_type)
            error_message = error.get("message")
            if isinstance(error_message, str):
                data["error_message_chars"] = len(error_message)

        if payload.get("skill_name") == "sprint-planning":
            data["workflow"] = "sprint-planning"

        operation = _terminal_operation(payload)
        if operation:
            data["tool_operation"] = operation
        family = _tool_family(payload.get("tool_name"), operation)
        if family:
            data["tool_family"] = family

        text_size_fields = {
            "user_message": "user_message_chars",
            "assistant_response": "assistant_response_chars",
            "final_response": "final_response_chars",
            "child_goal": "child_goal_chars",
            "child_summary": "child_summary_chars",
            "summary": "summary_chars",
            "reason": "reason_chars",
            "error_message": "error_message_chars",
        }
        for source, target in text_size_fields.items():
            value = payload.get(source)
            if isinstance(value, str):
                data[target] = len(value)

        shape_fields = {
            "args": "argument",
            "result": "result",
            "request": "request",
            "response": "response",
        }
        for source, target in shape_fields.items():
            value = payload.get(source)
            if isinstance(value, str):
                data[f"{target}_chars"] = len(value)
            elif isinstance(value, (bytes, bytearray, memoryview)):
                data[f"{target}_bytes"] = len(value)
            elif isinstance(value, (list, tuple, dict, set)):
                data[f"{target}_item_count"] = len(value)

        count_fields = {
            "conversation_history": "conversation_message_count",
            "tool_call_history": "tool_call_history_count",
            "changed_paths": "changed_path_count",
        }
        for source, target in count_fields.items():
            value = payload.get(source)
            if isinstance(value, (list, tuple, dict, set)):
                data[target] = len(value)

        usage = payload.get("usage")
        if type(usage) is dict:
            safe_usage = {}
            for key in _USAGE_FIELDS:
                value = _safe_number(usage.get(key))
                if value is not None:
                    safe_usage[key] = value
            if safe_usage:
                data["usage"] = safe_usage
        return data

    def _build_event(self, hook_name: str, payload: dict[str, Any]) -> Event:
        kind = _HOOK_KINDS[hook_name]
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
            data = {
                "event_schema": EVENT_SCHEMA,
                "source_schema": (
                    "hermes.observer.v1"
                    if payload.get("telemetry_schema_version", "hermes.observer.v1")
                    == "hermes.observer.v1"
                    else "unknown"
                ),
                "capture_policy": CAPTURE_POLICY,
                "event_id": uuid.uuid4().hex,
                "process_id": self.process_id,
                "process_start_id": self.process_start_id,
                "process_sequence": sequence,
                **self._metadata(payload),
            }
            if self.run_id:
                data["run_id_hash"] = _hash_text("subject", self.run_id)
            event = Event(
                kind=kind,
                subject=self._subject(payload),
                grade="native",
                source=SOURCE,
                data=data,
            )
        return event

    def emit(self, hook_name: str, **payload: Any) -> Event:
        event = self._build_event(hook_name, payload)
        self.ledger.append(event)
        with self._lock:
            self._recorded_events += 1
        return event

    def observe(self, hook_name: str, **payload: Any) -> None:
        """Queue a sanitized event without waiting for file I/O."""
        event = self._build_event(hook_name, payload)
        with self._lock:
            if self._closing or self._closed:
                self._dropped_events += 1
                return
            try:
                self._queue.put_nowait(event)
            except queue.Full:
                self._dropped_events += 1

    def _write_events(self) -> None:
        while True:
            event = self._queue.get()
            try:
                if event is None:
                    return
                self.ledger.append(event)
                with self._lock:
                    self._recorded_events += 1
            except Exception as exc:  # noqa: BLE001 - shadow recording fails open by contract
                with self._lock:
                    self._writer_errors += 1
                    self._last_error_type = type(exc).__name__
            finally:
                self._queue.task_done()

    def flush(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + max(timeout, 0.0)
        while self._queue.unfinished_tasks:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.005)
        return True

    def close(self, timeout: float = 5.0) -> bool:
        if self._closed:
            return not self._worker.is_alive()
        with self._lock:
            self._closing = True
        if not self._stop_queued:
            flushed = self.flush(timeout=timeout)
            if not flushed:
                return False
            with self._lock:
                if not self._stop_queued:
                    try:
                        self._queue.put_nowait(None)
                    except queue.Full:
                        return False
                    self._stop_queued = True
        self._worker.join(timeout=max(timeout, 0.0))
        stopped = not self._worker.is_alive()
        if stopped:
            self.ledger.close()
            self._closed = True
        return stopped

    def status(self) -> dict[str, Any]:
        with self._lock:
            dropped_events = self._dropped_events
            writer_errors = self._writer_errors
            last_error_type = self._last_error_type
            recorded_events = self._recorded_events
        return {
            "ready": self.probe_result.ready,
            "complete": dropped_events == 0 and writer_errors == 0,
            "capture_policy": CAPTURE_POLICY,
            "event_schema": EVENT_SCHEMA,
            "event_count": recorded_events,
            "queued_events": self._queue.qsize(),
            "dropped_events": dropped_events,
            "writer_errors": writer_errors,
            "last_error_type_hash": _hash_text("last_error_type", last_error_type),
            "path": str(self.path),
            "run_id_hash": _hash_text("subject", self.run_id),
            "probe": [
                {"name": check.name, "ok": check.ok, "detail": check.detail}
                for check in self.probe_result.checks
            ],
        }


def register(ctx: Any) -> None:
    """Register metadata-only observer hooks through Hermes' public plugin API."""
    run_id = str(
        ctx.get_config("run_id", os.environ.get("OBSERVATORY_RUN_ID", "")) or ""
    )
    data_dir = Path(ctx.state.data_dir)
    recorder = HermesMetadataRecorder(
        data_dir=data_dir,
        run_id=run_id,
        profile_home=data_dir.parent.parent,
    )
    atexit.register(recorder.close)
    ctx.on_unload(recorder.close)

    def callback_for(hook_name: str) -> Callable[..., None]:
        def callback(**kwargs: Any) -> None:
            recorder.observe(hook_name, **kwargs)

        return callback

    for hook_name in _HOOK_KINDS:
        ctx.register_hook(hook_name, callback_for(hook_name))

    def command(raw_args: str) -> str:
        if raw_args.strip() not in {"", "status"}:
            return json.dumps({"error": "Usage: /observatory [status]"}, sort_keys=True)
        return json.dumps(recorder.status(), sort_keys=True)

    ctx.register_command(
        "observatory",
        command,
        description="Show metadata-only Observatory recorder status.",
        args_hint="[status]",
    )
