"""Per-conversation image histories and their archives.

Layout under the store root:

    conversations/<hashed key>/history.json
    conversations/<hashed key>/archive/<sha256>.<ext>
    locks/<hashed key>.lock
"""

import contextlib
import fcntl
import hashlib
import json
import os
import shutil
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from . import keys, limits, safety

SCHEMA_VERSION = 1
EXTENSIONS = {
    "png": "png",
    "jpeg": "jpg",
    "gif": "gif",
    "webp": "webp",
    "bmp": "bmp",
    "tiff": "tiff",
    "heic": "heic",
}
COPY_CHUNK = 1024 * 1024


class NewerSchema(Exception):
    """The history was written by a newer version of the plugin."""


@dataclass(frozen=True)
class Entry:
    sha256: str
    name: str
    source: str
    format: str
    published_at: float


ENTRY_TYPES = {"sha256": str, "name": str, "source": str, "format": str, "published_at": (int, float)}


def tree_size(directory):
    return sum(path.stat().st_size for path in Path(directory).rglob("*") if path.is_file())


def read_history_file(path):
    """Return (status, entries, updated_at) with status "missing", "ok",
    "corrupt", or "newer" (written by a newer version: never modified or
    collected)."""
    try:
        data = json.loads(Path(path).read_bytes())
    except FileNotFoundError:
        return "missing", [], None
    except ValueError:
        return "corrupt", [], None
    version = data.get("schema_version") if isinstance(data, dict) else None
    if isinstance(version, int) and not isinstance(version, bool) and version > SCHEMA_VERSION:
        return "newer", [], None
    if version != SCHEMA_VERSION:
        return "corrupt", [], None
    entries = data.get("entries")
    updated_at = data.get("updated_at")
    if (
        not isinstance(entries, list)
        or not all(map(valid_entry, entries))
        or not isinstance(updated_at, (int, float))
    ):
        return "corrupt", [], None
    return "ok", [Entry(**item) for item in entries], updated_at


def valid_entry(item):
    return (
        isinstance(item, dict)
        and set(item) == set(ENTRY_TYPES)
        and all(isinstance(item[field], kind) for field, kind in ENTRY_TYPES.items())
        and item["format"] in EXTENSIONS
    )


class Store:
    def __init__(self, root, clock=time.time, max_total_bytes=limits.GC_MAX_TOTAL_BYTES):
        self.root = Path(root)
        self.clock = clock
        self.max_total_bytes = max_total_bytes

    def conversation_dir(self, key):
        return self.root / "conversations" / keys.directory_name(key)

    def history_path(self, key):
        return self.conversation_dir(key) / "history.json"

    def archive_path(self, key, entry):
        return self.conversation_dir(key) / "archive" / f"{entry.sha256}.{EXTENSIONS[entry.format]}"

    def history(self, key):
        """The entries of a conversation, oldest first; empty if there are none
        or the history is corrupt."""
        return self._read_history(key)[1]

    def _read_history(self, key):
        status, entries, _ = read_history_file(self.history_path(key))
        return status, entries

    def gc(self):
        """Remove conversations not updated for more than the GC age, then the
        least recently updated ones until the total fits max_total_bytes.

        Conversations being written (locked) and protected ones are skipped.
        """
        conversations = self.root / "conversations"
        if not conversations.is_dir():
            return
        for directory in conversations.iterdir():
            if self._expired(directory):
                self._remove_unless_locked(directory, self._expired)
        sizes = {directory: tree_size(directory) for directory in conversations.iterdir()}
        total = sum(sizes.values())
        candidates = []
        for directory in sizes:
            updated_at = self._collectable_since(directory)
            if updated_at is not None:
                candidates.append((updated_at, directory))
        for _, directory in sorted(candidates):
            if total <= self.max_total_bytes:
                break
            if self._remove_unless_locked(directory, lambda d: self._collectable_since(d) is not None):
                total -= sizes[directory]

    def _remove_unless_locked(self, directory, still_wanted):
        with self._lock(directory.name, blocking=False) as held:
            # Skip one being written; re-check once nothing can change it.
            if held and still_wanted(directory):
                shutil.rmtree(directory)
                return True
        return False

    def _collectable_since(self, directory):
        """updated_at of a conversation GC may remove, or None if it is protected."""
        # Newer-schema, corrupt, and set-aside histories are kept for inspection.
        if any(directory.glob("history.corrupt-*.json")):
            return None
        status, _, updated_at = read_history_file(directory / "history.json")
        return updated_at if status == "ok" else None

    def _expired(self, directory):
        updated_at = self._collectable_since(directory)
        return updated_at is not None and self.clock() - updated_at > limits.GC_MAX_AGE_SECONDS

    def _set_aside(self, key):
        path = self.history_path(key)
        os.replace(path, path.with_name(f"history.corrupt-{int(self.clock())}-{uuid.uuid4().hex[:8]}.json"))

    def publish(self, key, source_path):
        """Archive the image and record it as the newest entry of the history.

        Order: copy to a temp file while hashing, commit the archive file, then
        atomically replace the history.
        """
        conversation = self.conversation_dir(key)
        archive_dir = conversation / "archive"
        for directory in (self.root, self.root / "conversations", conversation, archive_dir):
            safety.private_directory(directory)
        # Copies may run in parallel; everything that reads or changes the
        # history, including archive files it references, runs under the lock.
        temporary, sha256, image_format = self._copy_in(source_path, archive_dir)
        try:
            with self._conversation_lock(key):
                entry = Entry(
                    sha256=sha256,
                    name=Path(source_path).name,
                    source=str(source_path),
                    format=image_format,
                    published_at=self.clock(),
                )
                status, current = self._read_history(key)
                if status == "newer":
                    raise NewerSchema(
                        f"{self.history_path(key)} was written by a newer version; left untouched"
                    )
                if status == "corrupt":
                    # Keep it (and every archive file) for inspection; start over.
                    self._set_aside(key)
                os.replace(temporary, self.archive_path(key, entry))
                # The same content published again moves to the newest position.
                entries = [old for old in current if old.sha256 != entry.sha256] + [entry]
                kept, dropped = entries[-limits.MAX_HISTORY:], entries[:-limits.MAX_HISTORY]
                self._write_history(key, kept)
                # Only after the history no longer references them.
                for old in dropped:
                    self.archive_path(key, old).unlink(missing_ok=True)
        finally:
            temporary.unlink(missing_ok=True)
        return entry

    def lock_path(self, key):
        """Outside the conversation directory, so GC can remove that directory."""
        return self.root / "locks" / f"{keys.directory_name(key)}.lock"

    @contextlib.contextmanager
    def _conversation_lock(self, key):
        with self._lock(keys.directory_name(key)):
            yield

    @contextlib.contextmanager
    def _lock(self, name, blocking=True):
        """Hold locks/<name>.lock; yield whether it was acquired."""
        safety.private_directory(self.root)
        safety.private_directory(self.root / "locks")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        path = self.root / "locks" / f"{name}.lock"
        with os.fdopen(os.open(path, flags, 0o600), "r+b") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError:
                yield False
                return
            yield True

    def _copy_in(self, source_path, archive_dir):
        """Copy the source into a new temp file; return it with its hash and format."""
        temporary = archive_dir / f".incoming-{uuid.uuid4().hex}"
        digest = hashlib.sha256()
        try:
            with safety.open_source(source_path) as source, safety.create_private_file(temporary) as target:
                safety.check_size(os.fstat(source.fileno()).st_size)
                head = source.read(64)
                image_format = safety.image_format(head)
                copied = 0
                chunk = head
                while chunk:
                    copied += len(chunk)
                    safety.check_size(copied)
                    digest.update(chunk)
                    target.write(chunk)
                    chunk = source.read(COPY_CHUNK)
                target.flush()
                os.fsync(target.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return temporary, digest.hexdigest(), image_format

    def _write_history(self, key, entries):
        path = self.history_path(key)
        temporary = path.with_name(f".history-{uuid.uuid4().hex}.json")
        document = {
            "schema_version": SCHEMA_VERSION,
            "key": key,
            "updated_at": self.clock(),
            "entries": [asdict(entry) for entry in entries],
        }
        try:
            with safety.create_private_file(temporary) as handle:
                handle.write(json.dumps(document, ensure_ascii=False, indent=1).encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
