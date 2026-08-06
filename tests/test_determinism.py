"""Decision D2, enforced rather than trusted.

A rule handler may not roll dice, read the clock, or reach outside `ctx`. Every
source of randomness in the game is a player answer - that is what makes a day's
journal replay to the same sheet. A single `random.randint` in a handler breaks
every save in the game, silently, so this catches it the day it is written.
"""

import ast
import unittest
from pathlib import Path

import harness  # noqa: F401

RULES = Path(harness.ROOT) / "src" / "engine" / "rules"

# Not "unsafe" in general - unsafe *inside a handler*, because none of them
# gives the same answer twice.
BANNED_MODULES = {"random", "time", "datetime", "secrets", "uuid"}
BANNED_CALLS = {("os", "environ"), ("os", "urandom")}


def rule_sources():
    return sorted(RULES.rglob("*.py"))


class TestNoHiddenRandomness(unittest.TestCase):
    def test_rules_dir_exists_and_has_modules(self):
        self.assertTrue(rule_sources(), f"no rule modules under {RULES}")

    def test_no_banned_imports(self):
        bad = []
        for path in rule_sources():
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        if a.name.split(".")[0] in BANNED_MODULES:
                            bad.append(f"{path.name}:{node.lineno} import {a.name}")
                elif isinstance(node, ast.ImportFrom):
                    root = (node.module or "").split(".")[0]
                    if root in BANNED_MODULES:
                        bad.append(f"{path.name}:{node.lineno} from {node.module}")
        self.assertEqual(bad, [], "handlers must take every die from the player:\n"
                                 + "\n".join(bad))

    def test_no_banned_attribute_access(self):
        bad = []
        for path in rule_sources():
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                    if (node.value.id, node.attr) in BANNED_CALLS:
                        bad.append(f"{path.name}:{node.lineno} "
                                   f"{node.value.id}.{node.attr}")
        self.assertEqual(bad, [], "\n".join(bad))


class TestReplayIsByteIdentical(unittest.TestCase):
    def test_same_journal_twice_in_one_process(self):
        """Catches accidental set-iteration and dict-ordering dependencies."""
        import json
        with harness.temp_game("determ-twice") as g:
            m = harness.machine(g)
            harness.play(m, ["fight", 5, True, "Cal Arath", 3], flow="demo")
            first = json.dumps(harness.sheet_of(m.g), sort_keys=True)
            m.resume()
            second = json.dumps(harness.sheet_of(m.g), sort_keys=True)
            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
