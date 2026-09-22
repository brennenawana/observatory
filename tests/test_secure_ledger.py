from __future__ import annotations

import os
import stat
import tempfile
import time
import unittest
from multiprocessing import get_context
from pathlib import Path
from unittest.mock import patch

from observatory import (
    REDACTED,
    Event,
    JsonlLedger,
    ObservatoryError,
    SecureJsonlLedger,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - secure storage is unavailable there
    fcntl = None


def _event(
    kind: str = "task.start",
    source: str = "test",
    data: dict | None = None,
) -> Event:
    return Event(
        kind=kind,
        subject="run",
        grade="native",
        source=source,
        data=data or {},
    )


def _append_from_process(data_dir: str, finished) -> None:
    ledger = SecureJsonlLedger(
        data_dir,
        relative_dir=("runs", "shared"),
        filename="events.jsonl",
    )
    try:
        ledger.append(_event("task.finish", "child"))
        finished.set()
    finally:
        ledger.close()


class SecureJsonlLedgerTests(unittest.TestCase):
    def test_secret_named_byte_value_is_redacted_before_serialization(self) -> None:
        secret = "planted-sensitive-value"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            ledgers = (
                JsonlLedger(root / "plain.jsonl", secrets=[secret]),
                SecureJsonlLedger(
                    root / "secure", filename="events.jsonl", secrets=[secret]
                ),
            )
            for ledger in ledgers:
                self.addCleanup(getattr(ledger, "close", lambda: None))
                ledger.append(
                    _event("secret", data={"password": secret.encode("utf-8")})
                )
                raw = ledger.raw_bytes()
                self.assertNotIn(secret.encode("utf-8"), raw)
                self.assertEqual(next(iter(ledger.read())).data["password"], REDACTED)

    def test_registered_secret_is_removed_from_dictionary_keys(self) -> None:
        secret = "planted-sensitive-key-value"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            ledgers = (
                JsonlLedger(root / "plain.jsonl", secrets=(secret,)),
                SecureJsonlLedger(
                    root / "secure", filename="events.jsonl", secrets=(secret,)
                ),
            )
            for ledger in ledgers:
                self.addCleanup(getattr(ledger, "close", lambda: None))
                ledger.append(_event("secret-key", data={secret: "safe"}))

                raw = ledger.raw_bytes()
                self.assertNotIn(secret.encode("utf-8"), raw)
                stored = next(iter(ledger.read())).data
                self.assertEqual(list(stored.values()), ["safe"])
                self.assertNotIn(secret, stored)

    def test_non_string_dictionary_keys_are_rejected_before_storage(self) -> None:
        secret = "planted-sensitive-key-value"

        class SecretKey:
            def __str__(self) -> str:
                return secret

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            ledgers = (
                JsonlLedger(root / "plain.jsonl", secrets=(secret,)),
                SecureJsonlLedger(
                    root / "secure", filename="events.jsonl", secrets=(secret,)
                ),
            )
            for ledger in ledgers:
                self.addCleanup(getattr(ledger, "close", lambda: None))
                for key in (secret.encode(), SecretKey()):
                    with self.assertRaisesRegex(
                        ObservatoryError, "JSON-compatible values"
                    ):
                        ledger.append(_event("invalid-key", data={key: "safe"}))
                self.assertNotIn(secret.encode(), ledger.raw_bytes())

    def test_unicode_obfuscated_secret_key_names_are_detected(self) -> None:
        payload = {
            "pass\u200bword": "zero-width-value",
            "pass\u00adword": "soft-hyphen-value",
            "pass\ufe0fword": "variation-selector-value",
            "pass\u0301word": "combining-mark-value",
            "ｐａｓｓｗｏｒｄ": "fullwidth-value",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            ledgers = (
                JsonlLedger(root / "plain.jsonl"),
                SecureJsonlLedger(root / "secure", filename="events.jsonl"),
            )
            for ledger in ledgers:
                self.addCleanup(getattr(ledger, "close", lambda: None))
                ledger.append(_event("unicode-keys", data=payload))

                stored = next(iter(ledger.read())).data
                self.assertEqual(set(stored.values()), {REDACTED})

    def test_plain_ledger_synchronizes_new_path_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            parent = root / "new-parent"
            child = parent / "new-child"
            path = child / "events.jsonl"
            synced_directories: set[tuple[int, int]] = set()
            synced_regular_file = False
            real_fsync = os.fsync

            def record_fsync(file_descriptor: int) -> None:
                nonlocal synced_regular_file
                details = os.fstat(file_descriptor)
                if stat.S_ISDIR(details.st_mode):
                    synced_directories.add((details.st_dev, details.st_ino))
                if stat.S_ISREG(details.st_mode):
                    synced_regular_file = True
                real_fsync(file_descriptor)

            with patch("observatory.ledger.os.fsync", side_effect=record_fsync):
                JsonlLedger(path).append(_event())

            expected = {
                (directory.stat().st_dev, directory.stat().st_ino)
                for directory in (root, parent, child)
            }
            self.assertTrue(synced_regular_file)
            self.assertEqual(synced_directories, expected)

    def test_secure_ledger_synchronizes_new_path_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            parent = root / "new-parent"
            child = parent / "new-child"
            synced_directories: set[tuple[int, int]] = set()
            synced_regular_file = False
            real_fsync = os.fsync

            def record_fsync(file_descriptor: int) -> None:
                nonlocal synced_regular_file
                details = os.fstat(file_descriptor)
                if stat.S_ISDIR(details.st_mode):
                    synced_directories.add((details.st_dev, details.st_ino))
                if stat.S_ISREG(details.st_mode):
                    synced_regular_file = True
                real_fsync(file_descriptor)

            with patch("observatory.secure_ledger.os.fsync", side_effect=record_fsync):
                ledger = SecureJsonlLedger(parent, relative_dir=("new-child",))
                self.addCleanup(ledger.close)
                ledger.append(_event())

            expected = {
                (directory.stat().st_dev, directory.stat().st_ino)
                for directory in (root, parent, child)
            }
            self.assertTrue(synced_regular_file)
            self.assertEqual(synced_directories, expected)

    def test_unsupported_value_is_rejected_before_serialization(self) -> None:
        class SensitiveObject:
            def __str__(self) -> str:
                return "planted-sensitive-value"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            ledgers = (
                JsonlLedger(root / "plain.jsonl"),
                SecureJsonlLedger(root / "secure", filename="events.jsonl"),
            )
            for ledger in ledgers:
                self.addCleanup(getattr(ledger, "close", lambda: None))
                with self.assertRaisesRegex(ObservatoryError, "JSON-compatible values"):
                    ledger.append(_event("object", data={"value": SensitiveObject()}))
                self.assertNotIn(b"planted-sensitive-value", ledger.raw_bytes())

    def test_plain_ledger_separates_a_truncated_row_before_append(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp).resolve() / "events.jsonl"
            path.write_bytes(b'{"kind":"interrupted"')
            ledger = JsonlLedger(path)

            ledger.append(_event("survives"))

            self.assertEqual([event.kind for event in ledger.read()], ["survives"])
            self.assertEqual(len(ledger.raw_bytes().splitlines()), 2)

    def test_plain_ledger_skips_final_row_truncated_inside_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp).resolve() / "events.jsonl"
            ledger = JsonlLedger(path)
            ledger.append(_event("survives"))
            with path.open("ab") as file_handle:
                file_handle.write(b'{"value":"\xe2\x82')

            self.assertEqual([event.kind for event in ledger.read()], ["survives"])

    def test_secure_ledger_skips_final_row_truncated_inside_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp).resolve() / "secure"
            ledger = SecureJsonlLedger(data_dir)
            ledger.append(_event("survives"))
            path = ledger.path
            ledger.close()
            with path.open("ab") as file_handle:
                file_handle.write(b'{"value":"\xe2\x82')
            reopened = SecureJsonlLedger(data_dir)
            self.addCleanup(reopened.close)

            self.assertEqual([event.kind for event in reopened.read()], ["survives"])

    def make_ledger(
        self,
        data_dir: Path,
        *,
        trusted_root: Path | None = None,
        filename: str = "events.jsonl",
    ) -> SecureJsonlLedger:
        ledger = SecureJsonlLedger(
            data_dir,
            relative_dir=("runs", "run-hash"),
            filename=filename,
            trusted_root=trusted_root,
        )
        self.addCleanup(ledger.close)
        return ledger

    def test_append_round_trips_an_event_with_private_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp).resolve() / "observatory"
            ledger = self.make_ledger(data_dir)

            ledger.append(_event())

            self.assertEqual([item.kind for item in ledger.read()], ["task.start"])
            self.assertEqual(stat.S_IMODE(data_dir.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(ledger.path.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(ledger.path.stat().st_mode), 0o600)

    def test_existing_data_directory_becomes_private(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp).resolve() / "observatory"
            data_dir.mkdir(mode=0o755)

            self.make_ledger(data_dir)

            self.assertEqual(stat.S_IMODE(data_dir.stat().st_mode), 0o700)

    def test_partial_os_write_persists_the_complete_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = self.make_ledger(Path(tmp).resolve() / "observatory")
            original_write = os.write

            def partial_write(descriptor: int, value: bytes) -> int:
                return original_write(descriptor, value[: max(1, len(value) // 2)])

            with patch("observatory.secure_ledger.os.write", side_effect=partial_write):
                ledger.append(_event())

            self.assertEqual([item.kind for item in ledger.read()], ["task.start"])

    def test_interrupted_partial_row_does_not_corrupt_the_next_append(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp).resolve() / "observatory"
            ledger = self.make_ledger(data_dir)
            original_write = os.write
            calls = 0

            def interrupted_write(fd: int, data: bytes) -> int:
                nonlocal calls
                if calls == 0:
                    calls += 1
                    return original_write(fd, data[: max(1, len(data) // 2)])
                raise OSError("injected interruption")

            with (
                patch("observatory.secure_ledger.os.write", interrupted_write),
                self.assertRaisesRegex(OSError, "injected interruption"),
            ):
                ledger.append(_event("interrupted"))
            ledger.close()

            reopened = self.make_ledger(data_dir)
            reopened.append(_event("survives"))

            self.assertEqual(
                [event.kind for event in reopened.read()],
                ["survives"],
            )
            self.assertEqual(len(reopened.raw_bytes().splitlines()), 2)

    def test_invalid_relative_component_is_rejected_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp).resolve() / "observatory"

            with self.assertRaisesRegex(ObservatoryError, "relative path component"):
                SecureJsonlLedger(
                    data_dir,
                    relative_dir=("runs", "../outside"),
                    filename="events.jsonl",
                )

            self.assertFalse(data_dir.exists())

    def test_parent_traversal_in_data_directory_is_rejected_before_writing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            trusted = root / "trusted"
            trusted.mkdir()
            outside = root / "outside"

            with self.assertRaisesRegex(ObservatoryError, "relative path component"):
                SecureJsonlLedger(
                    trusted / ".." / "outside",
                    filename="events.jsonl",
                    trusted_root=trusted,
                )

            self.assertFalse(outside.exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "requires symbolic links")
    def test_symbolic_link_ancestor_is_rejected_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            target = root / "target"
            (target / "existing").mkdir(parents=True)
            link = root / "link"
            link.symlink_to(target, target_is_directory=True)
            data_dir = link / "existing" / "observatory"

            with self.assertRaises(ObservatoryError):
                SecureJsonlLedger(data_dir, filename="events.jsonl")

            self.assertFalse((target / "existing" / "observatory").exists())

    def test_missing_no_follow_support_disables_storage_before_writing(self) -> None:
        self._assert_missing_capability_makes_no_path("O_NOFOLLOW")

    def test_missing_directory_only_support_disables_storage_before_writing(
        self,
    ) -> None:
        self._assert_missing_capability_makes_no_path("O_DIRECTORY")

    def _assert_missing_capability_makes_no_path(self, name: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp).resolve() / "observatory"
            with (
                patch.object(os, name, 0),
                self.assertRaisesRegex(ObservatoryError, "secure storage capabilities"),
            ):
                SecureJsonlLedger(
                    data_dir,
                    relative_dir=("runs", "run-hash"),
                    filename="events.jsonl",
                )
            self.assertFalse(data_dir.exists())

    def test_missing_fchmod_support_disables_storage_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp).resolve() / "observatory"
            data_dir.mkdir(mode=0o755)
            ledger_path = data_dir / "events.jsonl"
            ledger_path.write_text("existing\n", encoding="utf-8")
            ledger_path.chmod(0o644)
            with (
                patch.object(os, "fchmod", None),
                self.assertRaisesRegex(ObservatoryError, "secure storage capabilities"),
            ):
                SecureJsonlLedger(data_dir, filename="events.jsonl")
            self.assertEqual(stat.S_IMODE(data_dir.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(ledger_path.stat().st_mode), 0o644)
            self.assertEqual(ledger_path.read_text(encoding="utf-8"), "existing\n")

    def test_missing_directory_fd_support_disables_storage_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp).resolve() / "observatory"
            with (
                patch.object(os, "supports_dir_fd", set()),
                self.assertRaisesRegex(ObservatoryError, "secure storage capabilities"),
            ):
                SecureJsonlLedger(
                    data_dir,
                    relative_dir=("runs", "run-hash"),
                    filename="events.jsonl",
                )
            self.assertFalse(data_dir.exists())

    def test_missing_no_follow_stat_support_disables_storage_before_writing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp).resolve() / "observatory"
            with (
                patch.object(os, "supports_follow_symlinks", set()),
                self.assertRaisesRegex(ObservatoryError, "secure storage capabilities"),
            ):
                SecureJsonlLedger(
                    data_dir,
                    relative_dir=("runs", "run-hash"),
                    filename="events.jsonl",
                )
            self.assertFalse(data_dir.exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "requires symbolic links")
    def test_data_directory_symlink_is_rejected_without_changing_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            outside = root / "outside"
            outside.mkdir(mode=0o755)
            data_dir = root / "observatory"
            data_dir.symlink_to(outside, target_is_directory=True)

            with self.assertRaises(ObservatoryError):
                SecureJsonlLedger(
                    data_dir,
                    relative_dir=("runs", "run-hash"),
                    filename="events.jsonl",
                )

            self.assertEqual(list(outside.iterdir()), [])
            self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o755)

    @unittest.skipUnless(hasattr(os, "symlink"), "requires symbolic links")
    def test_parent_directory_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            data_dir = root / "observatory"
            outside = root / "outside"
            outside.mkdir()
            (data_dir / "runs").mkdir(parents=True)
            (data_dir / "runs" / "run-hash").symlink_to(
                outside, target_is_directory=True
            )

            with self.assertRaises(ObservatoryError):
                SecureJsonlLedger(
                    data_dir,
                    relative_dir=("runs", "run-hash"),
                    filename="events.jsonl",
                )

            self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(hasattr(os, "symlink"), "requires symbolic links")
    def test_replaced_ledger_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            ledger = self.make_ledger(root / "observatory")
            target = root / "target.txt"
            target.write_text("unchanged", encoding="utf-8")
            ledger.path.unlink()
            ledger.path.symlink_to(target)

            with self.assertRaises(ObservatoryError):
                ledger.append(_event())

            self.assertEqual(target.read_text(encoding="utf-8"), "unchanged")

    def test_replaced_ledger_hard_link_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            ledger = self.make_ledger(root / "observatory")
            target = root / "target.txt"
            target.write_text("unchanged", encoding="utf-8")
            ledger.path.unlink()
            os.link(target, ledger.path)

            with self.assertRaises(ObservatoryError):
                ledger.append(_event())

            self.assertEqual(target.read_text(encoding="utf-8"), "unchanged")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)

    @unittest.skipUnless(hasattr(os, "symlink"), "requires symbolic links")
    def test_replaced_parent_directory_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            ledger = self.make_ledger(root / "observatory")
            parent = ledger.path.parent
            parked = root / "parked"
            outside = root / "outside"
            outside.mkdir()
            parent.rename(parked)
            parent.symlink_to(outside, target_is_directory=True)

            with self.assertRaises(ObservatoryError):
                ledger.append(_event())

            self.assertEqual(list(outside.iterdir()), [])

    def test_replaced_parent_directory_inode_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            ledger = self.make_ledger(root / "observatory")
            parent = ledger.path.parent
            parked = root / "parked"
            parent.rename(parked)
            parent.mkdir()

            with self.assertRaisesRegex(
                ObservatoryError, "directory path was replaced"
            ):
                ledger.append(_event())

            self.assertEqual(list(parent.iterdir()), [])

    def test_short_pread_is_retried_until_snapshot_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = self.make_ledger(Path(tmp).resolve() / "observatory")
            ledger.append(_event())
            expected = ledger.raw_bytes()
            original_pread = os.pread

            def short_pread(fd: int, size: int, offset: int) -> bytes:
                return original_pread(fd, min(size, 7), offset)

            with patch("observatory.secure_ledger.os.pread", side_effect=short_pread):
                actual = ledger.raw_bytes()

            self.assertEqual(actual, expected)

    @unittest.skipIf(fcntl is None, "requires POSIX file locks")
    def test_second_process_waits_for_the_ledger_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp).resolve() / "observatory"
            ledger = SecureJsonlLedger(
                data_dir,
                relative_dir=("runs", "shared"),
                filename="events.jsonl",
            )
            self.addCleanup(ledger.close)
            context = get_context("fork")
            finished = context.Event()
            fcntl.flock(ledger._file_fd, fcntl.LOCK_EX)
            process = context.Process(
                target=_append_from_process,
                args=(str(data_dir), finished),
            )
            process.start()
            try:
                time.sleep(0.2)
                self.assertFalse(finished.is_set())
                fcntl.flock(ledger._file_fd, fcntl.LOCK_UN)
                process.join(timeout=2)
                self.assertFalse(process.is_alive())
                self.assertEqual(process.exitcode, 0)
                self.assertTrue(finished.is_set())
            finally:
                fcntl.flock(ledger._file_fd, fcntl.LOCK_UN)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=2)

    def test_close_is_idempotent_and_prevents_more_io(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = self.make_ledger(Path(tmp).resolve() / "observatory")
            ledger.close()
            ledger.close()

            with self.assertRaisesRegex(ObservatoryError, "closed"):
                ledger.append(_event())


if __name__ == "__main__":
    unittest.main()
