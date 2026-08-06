"""The save format: migration, and the engine block staying out of the way.

`bp` and `play2` work on the same files. A version 1 save must load, and a save
the engine has touched must still be readable by every existing bp command.
"""

import json
import types
import unittest

import harness
import state
from engine.machine import restore, snapshot


class TestMigration(unittest.TestCase):
    def test_a_version_1_save_loads(self):
        with harness.temp_game("save-v1") as g:
            g["version"] = 1
            g.pop("engine", None)
            state.write_game(g)
            back = harness.reload(g["name"])
            self.assertEqual(back["version"], state.VERSION)
            self.assertEqual(back["gold"], g["gold"])

    def test_an_unknown_version_refuses(self):
        with harness.temp_game("save-v99") as g:
            g["version"] = 99
            state.write_game(g)
            with self.assertRaises(state.Refuse) as cm:
                harness.reload(g["name"])
            self.assertIn("version 99", str(cm.exception))

    def test_migration_is_idempotent(self):
        g = harness.blank("save-idem")
        once = state.migrate(dict(g, version=1))
        twice = state.migrate(dict(once))
        self.assertEqual(once, twice)


class TestSnapshot(unittest.TestCase):
    def test_snapshot_excludes_the_engine_block(self):
        g = harness.blank("save-snap")
        g["engine"] = {"journal": [1, 2, 3]}
        self.assertNotIn("engine", snapshot(g))

    def test_snapshot_is_a_deep_copy(self):
        g = harness.blank("save-deep")
        snap = snapshot(g)
        g["party"][0]["wounds"] = 5
        self.assertEqual(snap["party"][0]["wounds"], 0)

    def test_restore_keeps_the_engine_block(self):
        g = harness.blank("save-restore")
        snap = snapshot(g)
        g["engine"] = {"journal": ["keep me"]}
        g["gold"] = 500
        restore(g, snap)
        self.assertEqual(g["gold"], 10)
        self.assertEqual(g["engine"]["journal"], ["keep me"])


class TestInteroperability(unittest.TestCase):
    def test_bp_still_reads_a_save_the_engine_has_written(self):
        with harness.temp_game("save-interop") as g:
            m = harness.machine(g)
            harness.play(m, ["fight", 5, True, "Cal Arath", 3], flow="demo")
            back = harness.reload(g["name"])
            # The sheet renders, which is what every bp command depends on.
            self.assertIn("Cal Arath", state.sheet(back))
            self.assertIn("engine", back)

    def test_the_save_is_plain_json(self):
        with harness.temp_game("save-json") as g:
            m = harness.machine(g)
            harness.play(m, ["fight", 5], flow="demo")
            text = state.path_for(g["name"]).read_text()
            json.loads(text)        # raises if the engine wrote anything exotic

    def test_the_journal_holds_only_player_answers(self):
        with harness.temp_game("save-journal") as g:
            m = harness.machine(g)
            harness.play(m, ["fight", 5, True, "Cal Arath", 3], flow="demo")
            journal = harness.reload(g["name"])["engine"]["journal"]
            self.assertEqual([e["kind"] for e in journal],
                             ["choice", "die", "confirm", "pick_char", "die"])
            self.assertEqual([e["value"] for e in journal],
                             ["fight", 5, True, "Cal Arath", 3])


class TestHygiene(unittest.TestCase):
    def test_the_harness_cleans_up_after_itself(self):
        with harness.temp_game("save-hygiene") as g:
            path = state.path_for(g["name"])
            self.assertTrue(path.exists())
        self.assertFalse(path.exists())

    def test_the_harness_does_not_touch_the_current_pointer(self):
        before = state.current_name()
        with harness.temp_game("save-current"):
            pass
        self.assertEqual(state.current_name(), before)


if __name__ == "__main__":
    unittest.main()
