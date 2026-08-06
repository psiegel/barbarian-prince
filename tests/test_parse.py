"""Input parsing: what the player types, without a model.

The rule these enforce is that nothing coerces silently. A 7 answered to a 1d6
is an error, not a 6; "hi" to a choice of three is an error, not the first one.
"""

import unittest

import harness  # noqa: F401  - puts src on the path
from engine.types import Ask
from ui import parse


def die(spec="1d6"):
    n, _, sides = spec.partition("d")
    n, sides = int(n), int(sides)
    return Ask("die", f"roll {spec}",
               {"die": spec, "n": n, "sides": sides, "min": n, "max": n * sides})


def choice(*opts):
    return Ask("choice", "which?", {"options": list(opts)})


class TestDice(unittest.TestCase):
    def test_plain_numbers(self):
        for text, want in [("3", 3), ("1", 1), ("6", 6)]:
            self.assertEqual(parse.parse(die(), text).value, want)

    def test_the_noise_a_person_types(self):
        for text in ["  3 ", "d3", "roll 3", "I rolled 3", "three", "3."]:
            r = parse.parse(die(), text)
            self.assertTrue(r.ok, f"{text!r}: {r.error}")
            self.assertEqual(r.value, 3)

    def test_two_dice_as_a_total_or_as_both(self):
        for text, want in [("7", 7), ("3 4", 7), ("3+4", 7), ("3,4", 7),
                           ("12", 12), ("6 6", 12)]:
            r = parse.parse(die("2d6"), text)
            self.assertTrue(r.ok, f"{text!r}: {r.error}")
            self.assertEqual(r.value, want)

    def test_out_of_range_is_refused_not_clamped(self):
        r = parse.parse(die(), "7")
        self.assertFalse(r.ok)
        self.assertIn("1-6", r.error)

    def test_a_single_die_result_is_not_taken_as_a_2d6_total(self):
        r = parse.parse(die("2d6"), "1")
        self.assertFalse(r.ok)
        self.assertIn("2-12", r.error)

    def test_impossible_component_is_refused(self):
        r = parse.parse(die("2d6"), "7 1")
        self.assertFalse(r.ok)
        self.assertIn("cannot roll 7", r.error)

    def test_wrong_number_of_dice(self):
        r = parse.parse(die("2d6"), "1 2 3")
        self.assertFalse(r.ok)
        self.assertIn("2 dice", r.error)

    def test_nonsense_and_empty(self):
        for text in ["", "   ", "banana", "-", "?"]:
            self.assertFalse(parse.parse(die(), text).ok, repr(text))


class TestChoices(unittest.TestCase):
    def setUp(self):
        self.ask = choice("talk", "evade", "fight")

    def test_exact_and_case(self):
        self.assertEqual(parse.parse(self.ask, "FIGHT").value, "fight")

    def test_unique_prefix(self):
        self.assertEqual(parse.parse(self.ask, "f").value, "fight")
        self.assertEqual(parse.parse(self.ask, "ev").value, "evade")

    def test_index(self):
        self.assertEqual(parse.parse(self.ask, "1").value, "talk")
        self.assertEqual(parse.parse(self.ask, "3").value, "fight")

    def test_index_out_of_range(self):
        r = parse.parse(self.ask, "4")
        self.assertFalse(r.ok)
        self.assertIn("3 options", r.error)

    def test_ambiguous_prefix_refuses_rather_than_picks(self):
        ask = choice("hide", "hire", "fight")
        r = parse.parse(ask, "hi")
        self.assertFalse(r.ok)
        self.assertIn("hide", r.error)
        self.assertIn("hire", r.error)

    def test_unknown(self):
        r = parse.parse(self.ask, "run away")
        self.assertFalse(r.ok)
        self.assertIn("talk, evade, fight", r.error)


class TestConfirm(unittest.TestCase):
    def test_yes_and_no(self):
        ask = Ask("confirm", "well?")
        for t in ["y", "Yes", "ok", "sure", "aye"]:
            self.assertIs(parse.parse(ask, t).value, True, t)
        for t in ["n", "no", "nope", "never"]:
            self.assertIs(parse.parse(ask, t).value, False, t)

    def test_anything_else_is_refused(self):
        self.assertFalse(parse.parse(Ask("confirm", "well?"), "maybe").ok)


class TestNumbers(unittest.TestCase):
    def test_range(self):
        ask = Ask("number", "how much?", {"min": 0, "max": 100})
        self.assertEqual(parse.parse(ask, "40").value, 40)
        self.assertFalse(parse.parse(ask, "101").ok)
        self.assertFalse(parse.parse(ask, "-1").ok)

    def test_open_top(self):
        ask = Ask("number", "how much?", {"min": 0, "max": None})
        self.assertEqual(parse.parse(ask, "9999").value, 9999)


class TestPickChar(unittest.TestCase):
    def setUp(self):
        self.ask = Ask("pick_char", "who?",
                       {"names": ["Cal Arath", "Garth", "Gareth"]})

    def test_exact_wins_over_partial(self):
        self.assertEqual(parse.parse(self.ask, "Garth").value, "Garth")

    def test_partial(self):
        self.assertEqual(parse.parse(self.ask, "cal").value, "Cal Arath")

    def test_ambiguous_refuses(self):
        r = parse.parse(self.ask, "gar")
        self.assertFalse(r.ok)
        self.assertIn("say which", r.error)

    def test_unknown_lists_the_party(self):
        r = parse.parse(self.ask, "Bob")
        self.assertFalse(r.ok)
        self.assertIn("Cal Arath", r.error)


class TestHex(unittest.TestCase):
    def test_good_and_bad(self):
        ask = Ask("hex", "where?")
        self.assertEqual(parse.parse(ask, "1301").value, "1301")
        self.assertEqual(parse.parse(ask, "13 01").value, "1301")
        for bad in ["131", "13011", "abcd", ""]:
            self.assertFalse(parse.parse(ask, bad).ok, bad)


class TestExpected(unittest.TestCase):
    def test_every_kind_says_what_it_wants(self):
        asks = [die(), die("2d6"), choice("a", "b"),
                Ask("confirm", "?"), Ask("number", "?", {"min": 0, "max": 9}),
                Ask("pick_char", "?", {"names": ["Cal"]}), Ask("hex", "?")]
        for a in asks:
            self.assertTrue(parse.expected(a), f"{a.kind} says nothing")


if __name__ == "__main__":
    unittest.main()
