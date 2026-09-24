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


def valid_entry(item):
    return (
        isinstance(item, dict)
        and set(item) == set(ENTRY_TYPES)
        and all(isinstance(item[field], kind) for field, kind in ENTRY_TYPES.items())
        and item["format"] in EXTENSIONS
    )


class Store:
    def __init__(self, root, clock=time.time):
        self.root = Path(root)
        self.clock = clock

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
        """Return (status, entries) with status "missing", "ok", "corrupt", or
        "newer" (written by a newer version: never modified or collected)."""
        try:
            data = json.loads(self.history_path(key).read_bytes())
        except FileNotFoundError:
            return "missing", []
        except ValueError:
            return "corrupt", []
        version = data.get("schema_version") if isinstance(data, dict) else None
        if isinstance(version, int) and not isinstance(version, bool) and version > SCHEMA_VERSION:
            return "newer", []
        if version != SCHEMA_VERSION:
            return "corrupt", []
        entries = data.get("entries")
        if not isinstance(entries, list) or not all(map(valid_entry, entries)):
            return "corrupt", []
        return "ok", [Entry(**item) for item in entries]

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
        safety.private_directory(self.root / "locks")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(self.lock_path(key), flags, 0o600), "r+b") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

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
