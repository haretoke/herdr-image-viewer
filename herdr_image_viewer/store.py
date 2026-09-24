"""Per-conversation image histories and their archives.

Layout under the store root:

    conversations/<hashed key>/history.json
    conversations/<hashed key>/archive/<sha256>.<ext>
"""

import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from . import keys, safety

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
        sha256, image_format = self._copy_in(source_path, archive_dir)
        entry = Entry(
            sha256=sha256,
            name=Path(source_path).name,
            source=str(source_path),
            format=image_format,
            published_at=self.clock(),
        )
        os.replace(archive_dir / f".incoming-{sha256}", self.archive_path(key, entry))
        self._write_history(key, self.history(key) + [entry])
        return entry

    def _copy_in(self, source_path, archive_dir):
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
            sha256 = digest.hexdigest()
            os.replace(temporary, archive_dir / f".incoming-{sha256}")
            return sha256, image_format
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

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
