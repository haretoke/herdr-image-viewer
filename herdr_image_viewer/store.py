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


@dataclass(frozen=True)
class Entry:
    sha256: str
    name: str
    source: str
    format: str
    published_at: float


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
        try:
            data = json.loads(self.history_path(key).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        return [Entry(**item) for item in data["entries"]]

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
                os.replace(temporary, self.archive_path(key, entry))
                # The same content published again moves to the newest position.
                entries = [old for old in self.history(key) if old.sha256 != entry.sha256] + [entry]
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
