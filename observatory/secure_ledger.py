"""Private append-only JSONL storage for local Observatory records."""

from __future__ import annotations

import json
import os
import stat
import threading
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - unavailable on unsupported platforms
    fcntl = None

from .contract import Event, ObservatoryError
from .ledger import JsonlLedger, _encode_event
from .redact import Redactor


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
        or not callable(getattr(os, "fchmod", None))
        or fcntl is None
        or not callable(getattr(fcntl, "flock", None))
    ):
        raise ObservatoryError("SecureJsonlLedger requires secure storage capabilities")


def _validate_component(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or Path(value).name != value
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise ObservatoryError("Invalid relative path component")
    return value


def _absolute_components(path: Path) -> tuple[Path, tuple[str, ...]]:
    expanded = path.expanduser()
    absolute = expanded if expanded.is_absolute() else Path.cwd() / expanded
    anchor = Path(absolute.anchor)
    if str(anchor) != "/":
        raise ObservatoryError("SecureJsonlLedger requires a POSIX absolute path")
    components = tuple(_validate_component(part) for part in absolute.parts[1:])
    return anchor, components


def _reject_symlink_components(path: Path) -> None:
    anchor, components = _absolute_components(path)
    current = anchor
    for part in components:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise ObservatoryError(
                f"Secure storage path must not contain a symlink: {current}"
            )


def _open_private_directory(
    data_dir: Path,
    relative_dir: Sequence[str],
    *,
    trusted_root: Path | None,
) -> tuple[Path, int]:
    _require_secure_storage_capabilities()
    anchor, data_components = _absolute_components(data_dir)
    private_components = tuple(_validate_component(part) for part in relative_dir)

    trusted_count = 0
    if trusted_root is not None:
        trusted_anchor, trusted_components = _absolute_components(trusted_root)
        if trusted_anchor != anchor:
            raise ObservatoryError("Secure data directory escaped its trusted root")
        trusted_count = len(trusted_components)
        if data_components[:trusted_count] != trusted_components:
            raise ObservatoryError("Secure data directory escaped its trusted root")

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_fd = os.open(anchor, flags)
    private_index = len(data_components) - 1
    try:
        all_components = (*data_components, *private_components)
        for index, part in enumerate(all_components):
            created = False
            if index >= trusted_count:
                try:
                    os.mkdir(part, 0o700, dir_fd=directory_fd)
                    created = True
                except FileExistsError:
                    pass
                if created:
                    os.fsync(directory_fd)
            try:
                next_fd = os.open(part, flags, dir_fd=directory_fd)
            except OSError as exc:
                raise ObservatoryError(
                    "Secure storage path contains an unsafe component"
                ) from exc
            os.close(directory_fd)
            directory_fd = next_fd
            if created or index >= private_index:
                os.fchmod(directory_fd, 0o700)
        canonical_data = anchor.joinpath(*data_components)
        return canonical_data, directory_fd
    except Exception:
        os.close(directory_fd)
        raise


class SecureJsonlLedger(JsonlLedger):
    """A JSONL ledger pinned to private directories and one regular inode.

    The ledger refuses to start when descriptor-relative, no-follow file operations,
    private mode controls, or interprocess file locks are unavailable.
    """

    def __init__(
        self,
        data_dir: str | os.PathLike[str],
        *,
        relative_dir: Sequence[str] = (),
        filename: str = "events.jsonl",
        trusted_root: str | os.PathLike[str] | None = None,
        secrets: Iterable[str] = (),
        redactor: Redactor | None = None,
    ) -> None:
        _require_secure_storage_capabilities()
        safe_filename = _validate_component(filename)
        safe_relative_dir = tuple(_validate_component(part) for part in relative_dir)
        root, directory_fd = _open_private_directory(
            Path(data_dir),
            safe_relative_dir,
            trusted_root=Path(trusted_root) if trusted_root is not None else None,
        )
        path = root.joinpath(*safe_relative_dir, safe_filename)
        super().__init__(path, secrets=secrets, redactor=redactor)
        self.root = root
        self._directory_fd = directory_fd
        self._directory_path = path.parent
        self._io_lock = threading.Lock()
        self._closed = False
        file_fd = -1
        flags = os.O_APPEND | os.O_RDWR | os.O_NOFOLLOW
        file_created = False
        try:
            try:
                file_fd = os.open(
                    safe_filename,
                    flags | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=directory_fd,
                )
                file_created = True
            except FileExistsError:
                file_fd = os.open(safe_filename, flags, dir_fd=directory_fd)
            self._file_fd = file_fd
            self._validate_directory()
            self._validate_inode()
            os.fchmod(self._file_fd, 0o600)
            if file_created:
                os.fsync(self._file_fd)
                os.fsync(directory_fd)
        except Exception:
            if file_fd >= 0:
                os.close(file_fd)
            os.close(directory_fd)
            raise

    def _validate_directory(self) -> os.stat_result:
        opened = os.fstat(self._directory_fd)
        try:
            current = os.stat(self._directory_path, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise ObservatoryError("Secure ledger directory path was replaced") from exc
        if not stat.S_ISDIR(opened.st_mode) or not stat.S_ISDIR(current.st_mode):
            raise ObservatoryError("Secure ledger directory is not a directory")
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise ObservatoryError("Secure ledger directory path was replaced")
        return opened

    def _validate_inode(self) -> os.stat_result:
        opened = os.fstat(self._file_fd)
        try:
            current = os.stat(
                self.path.name, dir_fd=self._directory_fd, follow_symlinks=False
            )
        except FileNotFoundError as exc:
            raise ObservatoryError("Secure ledger path was replaced") from exc
        if not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(current.st_mode):
            raise ObservatoryError("Secure ledger is not a regular file")
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise ObservatoryError("Secure ledger path was replaced")
        if opened.st_nlink != 1 or current.st_nlink != 1:
            raise ObservatoryError("Secure ledger must have exactly one hard link")
        return opened

    def _validate_paths(self) -> os.stat_result:
        _reject_symlink_components(self.path)
        self._validate_directory()
        return self._validate_inode()

    def _write_all(self, value: bytes) -> None:
        remaining = memoryview(value)
        while remaining:
            written = os.write(self._file_fd, remaining)
            if written <= 0:
                raise ObservatoryError("Secure ledger write made no progress")
            remaining = remaining[written:]

    def _ends_with_newline(self, size: int) -> bool:
        if size == 0:
            return True
        if hasattr(os, "pread"):
            return os.pread(self._file_fd, 1, size - 1) == b"\n"
        position = os.lseek(self._file_fd, 0, os.SEEK_CUR)
        try:
            os.lseek(self._file_fd, size - 1, os.SEEK_SET)
            return os.read(self._file_fd, 1) == b"\n"
        finally:
            os.lseek(self._file_fd, position, os.SEEK_SET)

    def append(self, event: Event) -> None:
        if not isinstance(event, Event):
            raise ObservatoryError("append() takes an Event")
        encoded = _encode_event(event, self.redactor)

        with self._io_lock:
            if self._closed:
                raise ObservatoryError("Secure ledger is closed")
            fcntl.flock(self._file_fd, fcntl.LOCK_EX)
            try:
                opened = self._validate_paths()
                if not self._ends_with_newline(opened.st_size):
                    self._write_all(b"\n")
                self._write_all(encoded)
                os.fsync(self._file_fd)
                self._validate_paths()
                self.appended += 1
            finally:
                fcntl.flock(self._file_fd, fcntl.LOCK_UN)

    def _read_snapshot(self, size: int) -> bytes:
        if hasattr(os, "pread"):
            chunks: list[bytes] = []
            offset = 0
            while offset < size:
                chunk = os.pread(self._file_fd, size - offset, offset)
                if not chunk:
                    raise ObservatoryError(
                        "Secure ledger read ended before its snapshot"
                    )
                chunks.append(chunk)
                offset += len(chunk)
            return b"".join(chunks)

        position = os.lseek(self._file_fd, 0, os.SEEK_CUR)
        try:
            os.lseek(self._file_fd, 0, os.SEEK_SET)
            chunks = []
            remaining = size
            while remaining:
                chunk = os.read(self._file_fd, remaining)
                if not chunk:
                    raise ObservatoryError(
                        "Secure ledger read ended before its snapshot"
                    )
                chunks.append(chunk)
                remaining -= len(chunk)
            return b"".join(chunks)
        finally:
            os.lseek(self._file_fd, position, os.SEEK_SET)

    def raw_bytes(self) -> bytes:
        with self._io_lock:
            if self._closed:
                raise ObservatoryError("Secure ledger is closed")
            fcntl.flock(self._file_fd, fcntl.LOCK_SH)
            try:
                size = self._validate_paths().st_size
                snapshot = self._read_snapshot(size)
                self._validate_paths()
                return snapshot
            finally:
                fcntl.flock(self._file_fd, fcntl.LOCK_UN)

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
            except (json.JSONDecodeError, UnicodeDecodeError):
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
