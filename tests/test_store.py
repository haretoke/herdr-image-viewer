import hashlib
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from herdr_image_viewer.store import CapacityError, NewerSchema, Store

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class StoreFixture(unittest.TestCase):
    """A store under a temp directory with a controllable clock."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name)
        self.sources = self.base / "sources"
        self.sources.mkdir()
        self.now = 1_000_000.0
        self.store = Store(self.base / "state", clock=lambda: self.now)

    def tearDown(self):
        self.directory.cleanup()

    def image(self, name, content=PNG):
        path = self.sources / name
        path.write_bytes(content)
        return path


class StoreTest(StoreFixture):
    def test_publishing_an_image_records_it_as_the_newest_entry_with_an_archived_copy(self):
        source = self.image("shot.png")

        self.store.publish("claude:s1", source)

        history = self.store.history("claude:s1")
        self.assertEqual(len(history), 1)
        entry = history[-1]
        self.assertEqual(entry.sha256, hashlib.sha256(PNG).hexdigest())
        self.assertEqual(entry.name, "shot.png")
        self.assertEqual(entry.source, str(source))
        self.assertEqual(entry.format, "png")
        self.assertEqual(entry.published_at, self.now)
        self.assertEqual(self.store.archive_path("claude:s1", entry).read_bytes(), PNG)

    def test_publishing_the_same_content_again_moves_it_to_the_newest_position(self):
        first = self.image("first.png", PNG + b"first")
        self.store.publish("claude:s1", first)
        self.store.publish("claude:s1", self.image("second.png", PNG + b"second"))
        self.now += 60

        self.store.publish("claude:s1", self.image("first-again.png", PNG + b"first"))

        history = self.store.history("claude:s1")
        self.assertEqual([entry.name for entry in history], ["second.png", "first-again.png"])
        self.assertEqual(history[-1].published_at, self.now)

    def test_publishing_new_content_at_an_already_published_path_adds_a_new_entry(self):
        source = self.image("shot.png", PNG + b"v1")
        self.store.publish("claude:s1", source)
        source.write_bytes(PNG + b"v2")

        self.store.publish("claude:s1", source)

        history = self.store.history("claude:s1")
        self.assertEqual([entry.name for entry in history], ["shot.png", "shot.png"])
        self.assertNotEqual(history[0].sha256, history[1].sha256)
        self.assertEqual(self.store.archive_path("claude:s1", history[0]).read_bytes(), PNG + b"v1")
        self.assertEqual(self.store.archive_path("claude:s1", history[1]).read_bytes(), PNG + b"v2")

    def test_the_history_keeps_at_most_30_entries_and_deletes_unreferenced_archives(self):
        for number in range(1, 30):
            self.store.publish("claude:s1", self.image(f"{number}.png", PNG + str(number).encode()))
        self.assertEqual(len(self.store.history("claude:s1")), 29)
        self.store.publish("claude:s1", self.image("30.png", PNG + b"30"))
        history = self.store.history("claude:s1")
        self.assertEqual(len(history), 30)
        oldest = history[0]

        self.store.publish("claude:s1", self.image("31.png", PNG + b"31"))

        history = self.store.history("claude:s1")
        self.assertEqual(len(history), 30)
        self.assertEqual(history[0].name, "2.png")
        self.assertEqual(history[-1].name, "31.png")
        self.assertFalse(self.store.archive_path("claude:s1", oldest).exists())
        for entry in history:
            self.assertTrue(self.store.archive_path("claude:s1", entry).exists(), entry.name)

    def test_histories_of_different_conversation_keys_are_independent(self):
        shared = self.image("shared.png", PNG + b"shared")
        self.store.publish("claude:s1", shared)
        self.store.publish("claude:s1", self.image("only-s1.png", PNG + b"s1"))
        self.store.publish("pane:/run/herdr.sock#w1:p3", shared)

        self.assertEqual([e.name for e in self.store.history("claude:s1")], ["shared.png", "only-s1.png"])
        other = self.store.history("pane:/run/herdr.sock#w1:p3")
        self.assertEqual([e.name for e in other], ["shared.png"])
        self.assertNotEqual(
            self.store.archive_path("claude:s1", other[0]),
            self.store.archive_path("pane:/run/herdr.sock#w1:p3", other[0]),
        )
        self.assertEqual(self.store.history("claude:unknown"), [])

    def test_concurrent_publishes_to_one_conversation_do_not_lose_entries(self):
        start = self.base / "start"
        worker = (
            "import sys, time\n"
            "from pathlib import Path\n"
            "from herdr_image_viewer.store import Store\n"
            "root, source, start = sys.argv[1:4]\n"
            "while not Path(start).exists():\n"
            "    time.sleep(0.001)\n"
            "Store(root).publish('claude:s1', source)\n"
        )
        repository = Path(__file__).resolve().parents[1]
        workers = [
            subprocess.Popen(
                [sys.executable, "-c", worker, str(self.base / "state"),
                 str(self.image(f"{number}.png", PNG + str(number).encode())), str(start)],
                cwd=repository,
            )
            for number in range(12)
        ]
        time.sleep(0.5)  # let every worker reach the start line
        start.touch()
        for process in workers:
            self.assertEqual(process.wait(timeout=30), 0)

        names = sorted(entry.name for entry in self.store.history("claude:s1"))
        self.assertEqual(names, sorted(f"{number}.png" for number in range(12)))

    def test_an_entry_stays_viewable_from_the_archive_after_its_source_is_deleted(self):
        source = self.image("scratch.png", PNG + b"temporary screenshot")
        entry = self.store.publish("claude:s1", source)

        source.unlink()

        self.assertEqual(
            self.store.archive_path("claude:s1", entry).read_bytes(), PNG + b"temporary screenshot"
        )
        self.assertEqual(self.store.history("claude:s1")[-1].source, str(source))

    def test_a_crash_after_archiving_but_before_replacing_the_history_keeps_the_old_history(self):
        self.store.publish("claude:s1", self.image("kept.png", PNG + b"kept"))
        real_replace = os.replace

        def crash_on_history(source, destination):
            if str(destination).endswith("history.json"):
                raise OSError("simulated crash")
            return real_replace(source, destination)

        with mock.patch("herdr_image_viewer.store.os.replace", side_effect=crash_on_history):
            with self.assertRaises(OSError):
                self.store.publish("claude:s1", self.image("lost.png", PNG + b"lost"))

        self.assertEqual([entry.name for entry in self.store.history("claude:s1")], ["kept.png"])
        conversation = self.store.conversation_dir("claude:s1")
        leftovers = [path.name for path in conversation.rglob(".*") if path.is_file()]
        self.assertEqual(leftovers, [])

    def test_a_corrupt_history_is_moved_aside_and_its_archives_are_kept(self):
        old = self.store.publish("claude:s1", self.image("old.png", PNG + b"old"))
        old_archive = self.store.archive_path("claude:s1", old)
        history_path = self.store.history_path("claude:s1")
        for corrupt in (b"{not json", b"[1, 2, 3]", b'{"schema_version": 1, "entries": [{"x": 1}]}'):
            with self.subTest(corrupt=corrupt):
                history_path.write_bytes(corrupt)

                self.assertEqual(self.store.history("claude:s1"), [])
                self.store.publish("claude:s1", self.image("new.png", PNG + corrupt))

                self.assertEqual([e.name for e in self.store.history("claude:s1")], ["new.png"])
                aside = sorted(history_path.parent.glob("history.corrupt-*.json"))
                self.assertIn(corrupt, [path.read_bytes() for path in aside])
                self.assertTrue(old_archive.exists())

    def test_a_history_with_a_newer_schema_version_is_left_untouched(self):
        self.store.publish("claude:s1", self.image("old.png", PNG + b"old"))
        history_path = self.store.history_path("claude:s1")
        newer = b'{"schema_version": 2, "entries": "a format this version does not know"}'
        history_path.write_bytes(newer)

        self.assertEqual(self.store.history("claude:s1"), [])
        with self.assertRaises(NewerSchema):
            self.store.publish("claude:s1", self.image("new.png", PNG + b"new"))

        self.assertEqual(history_path.read_bytes(), newer)
        self.assertEqual(list(history_path.parent.glob("history.corrupt-*.json")), [])
        archive = history_path.parent / "archive"
        self.assertEqual([p.name for p in archive.iterdir() if p.name.startswith(".")], [])


DAY = 24 * 60 * 60


class GcTest(StoreFixture):
    def test_gc_removes_conversations_not_updated_for_more_than_14_days(self):
        self.store.publish("claude:expired", self.image("a.png", PNG + b"a"))
        self.now += 1
        self.store.publish("claude:kept", self.image("b.png", PNG + b"b"))
        self.now += 14 * DAY  # "expired" is 14 days + 1 s old, "kept" exactly 14 days

        self.store.gc()

        self.assertFalse(self.store.conversation_dir("claude:expired").exists())
        self.assertEqual([e.name for e in self.store.history("claude:kept")], ["b.png"])

    def test_gc_never_collects_newer_schema_or_set_aside_corrupt_histories(self):
        self.store.publish("claude:newer", self.image("n.png", PNG + b"n"))
        self.store.history_path("claude:newer").write_bytes(b'{"schema_version": 2}')
        self.store.publish("claude:corrupt", self.image("c.png", PNG + b"c"))
        self.store.history_path("claude:corrupt").write_bytes(b"{broken")
        self.store.publish("claude:set-aside", self.image("s1.png", PNG + b"s1"))
        self.store.history_path("claude:set-aside").write_bytes(b"{broken")
        self.store.publish("claude:set-aside", self.image("s2.png", PNG + b"s2"))
        self.now += 15 * DAY

        self.store.gc()

        for key in ("claude:newer", "claude:corrupt", "claude:set-aside"):
            with self.subTest(key=key):
                self.assertTrue(self.store.conversation_dir(key).exists())

    def stored_bytes(self):
        return sum(p.stat().st_size for p in (self.base / "state" / "conversations").rglob("*") if p.is_file())

    def test_gc_trims_the_total_oldest_first_and_skips_conversations_being_written(self):
        for number, key in enumerate(("claude:oldest", "claude:middle", "claude:newest")):
            self.now += 1
            self.store.publish(key, self.image(f"{number}.png", PNG + bytes(100 * (number + 1))))
        total = self.stored_bytes()

        self.store.max_total_bytes = total
        self.store.gc()
        self.assertEqual(self.stored_bytes(), total)

        self.store.max_total_bytes = total - 1
        with self.store._conversation_lock("claude:oldest"):  # a publish is writing it
            self.store.gc()

        self.assertTrue(self.store.conversation_dir("claude:oldest").exists())
        self.assertFalse(self.store.conversation_dir("claude:middle").exists())
        self.assertTrue(self.store.conversation_dir("claude:newest").exists())

        self.store.max_total_bytes = self.stored_bytes() - 1
        self.store.gc()

        self.assertFalse(self.store.conversation_dir("claude:oldest").exists())
        self.assertTrue(self.store.conversation_dir("claude:newest").exists())

    def test_concurrent_gc_runs_and_a_publish_elsewhere_stay_consistent(self):
        for number in range(40):
            self.store.publish(f"claude:old-{number}", self.image(f"{number}.png", PNG + bytes([number])))
        later = self.now + 15 * DAY
        start = self.base / "start"
        preamble = (
            "import sys, time\n"
            "from pathlib import Path\n"
            "from herdr_image_viewer.store import Store\n"
            "root, now, start = sys.argv[1], float(sys.argv[2]), sys.argv[3]\n"
            "store = Store(root, clock=lambda: now)\n"
            "while not Path(start).exists():\n"
            "    time.sleep(0.001)\n"
        )
        gc_worker = preamble + "store.gc()\n"
        publish_worker = preamble + (
            "for source in sys.argv[4:]:\n"
            "    store.publish('claude:fresh', source)\n"
        )
        fresh = [str(self.image(f"fresh-{n}.png", PNG + b"fresh" + bytes([n]))) for n in range(10)]
        repository = Path(__file__).resolve().parents[1]
        arguments = [str(self.base / "state"), str(later), str(start)]
        workers = [
            subprocess.Popen([sys.executable, "-c", gc_worker, *arguments], cwd=repository, stderr=subprocess.PIPE),
            subprocess.Popen([sys.executable, "-c", gc_worker, *arguments], cwd=repository, stderr=subprocess.PIPE),
            subprocess.Popen([sys.executable, "-c", publish_worker, *arguments, *fresh], cwd=repository, stderr=subprocess.PIPE),
        ]
        time.sleep(0.5)
        start.touch()
        for process in workers:
            _, error = process.communicate(timeout=60)
            self.assertEqual(process.returncode, 0, error.decode())

        remaining = sorted(p.name for p in (self.base / "state" / "conversations").iterdir())
        self.assertEqual(remaining, [self.store.conversation_dir("claude:fresh").name])
        self.assertEqual(len(self.store.history("claude:fresh")), 10)

    def test_a_gc_started_while_another_runs_does_nothing(self):
        self.store.publish("claude:expired", self.image("a.png", PNG + b"a"))
        self.now += 15 * DAY

        with self.store._lock("gc"):  # another GC run holds the global lock
            self.store.gc()

        self.assertTrue(self.store.conversation_dir("claude:expired").exists())

    def test_sizes_ignore_files_removed_while_gc_walks_the_tree(self):
        self.store.publish("claude:s1", self.image("a.png", PNG + b"a"))
        real_stat = os.stat

        def vanishing_stat(path, *args, **kwargs):
            if str(path).endswith(".png"):
                raise FileNotFoundError(path)
            return real_stat(path, *args, **kwargs)

        with mock.patch("herdr_image_viewer.store.os.stat", vanishing_stat):
            self.store.gc()

        self.assertEqual(len(self.store.history("claude:s1")), 1)

    def test_publish_makes_room_or_fails_with_a_capacity_error_keeping_the_history(self):
        self.store.publish("claude:old", self.image("old.png", PNG + bytes(400)))
        self.now += 1
        self.store.publish("claude:current", self.image("kept.png", PNG + b"kept"))
        self.store.max_total_bytes = self.stored_bytes()

        self.now += 1
        self.store.publish("claude:current", self.image("room.png", PNG + bytes(300)))

        self.assertFalse(self.store.conversation_dir("claude:old").exists())
        self.assertEqual([e.name for e in self.store.history("claude:current")], ["kept.png", "room.png"])

        # Now only protected data exists besides the conversation being written.
        self.store.publish("claude:protected", self.image("p.png", PNG + b"p"))
        self.store.history_path("claude:protected").write_bytes(b'{"schema_version": 2}')
        before = self.store.history_path("claude:current").read_bytes()
        self.store.max_total_bytes = self.stored_bytes()

        with self.assertRaises(CapacityError):
            self.store.publish("claude:current", self.image("too-big.png", PNG + bytes(5000)))

        self.assertEqual(self.store.history_path("claude:current").read_bytes(), before)
        archive = self.store.conversation_dir("claude:current") / "archive"
        self.assertEqual([p.name for p in archive.iterdir() if p.name.startswith(".")], [])
        self.assertTrue(self.store.conversation_dir("claude:protected").exists())

    def test_publish_runs_the_age_based_gc_at_most_once_an_hour(self):
        start = self.now
        self.store.publish("claude:a", self.image("a.png", PNG + b"a"))
        self.now = start + 14 * DAY - 30 * 60
        self.store.publish("claude:b", self.image("b.png", PNG + b"b"))  # first run: nothing expired yet

        self.now = start + 14 * DAY + 10 * 60  # "a" has expired, but GC ran 40 minutes ago
        self.store.publish("claude:c", self.image("c.png", PNG + b"c"))
        self.assertTrue(self.store.conversation_dir("claude:a").exists())

        self.now = start + 14 * DAY + 31 * 60  # 61 minutes after the last run
        self.store.publish("claude:d", self.image("d.png", PNG + b"d"))
        self.assertFalse(self.store.conversation_dir("claude:a").exists())
        self.assertTrue(self.store.conversation_dir("claude:b").exists())

    def test_gc_removes_orphans_and_stale_temp_files_except_in_protected_conversations(self):
        kept = self.store.publish("claude:s1", self.image("kept.png", PNG + b"kept"))
        archive = self.store.conversation_dir("claude:s1") / "archive"
        orphan = archive / ("0" * 64 + ".png")  # left by a crash before the history was replaced
        orphan.write_bytes(PNG)
        stale = archive / ".incoming-stale"
        stale.write_bytes(PNG)
        fresh = archive / ".incoming-fresh"
        fresh.write_bytes(PNG)
        os.utime(stale, (self.now - 2 * 60 * 60, self.now - 2 * 60 * 60))
        os.utime(fresh, (self.now - 10 * 60, self.now - 10 * 60))
        self.store.publish("claude:protected", self.image("p.png", PNG + b"p"))
        self.store.history_path("claude:protected").write_bytes(b'{"schema_version": 2}')
        protected_orphan = self.store.conversation_dir("claude:protected") / "archive" / ("1" * 64 + ".png")
        protected_orphan.write_bytes(PNG)

        self.store.gc()

        self.assertTrue(self.store.archive_path("claude:s1", kept).exists())
        self.assertFalse(orphan.exists())
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())  # may belong to a publish in progress
        self.assertTrue(protected_orphan.exists())


if __name__ == "__main__":
    unittest.main()
