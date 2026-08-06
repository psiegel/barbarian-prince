"""The encounter graph, r300-r343.

The important test here is the sweep: drive every section at every die value at
every question and assert that all of it lands somewhere. A branch the rules can
reach and the code cannot is the failure mode this plan exists to prevent, and
it is cheap to enumerate because the input space is six-sided.
"""

import unittest

import harness
from engine import EndEvent, EndGame, EnterCombat, EscapeHex, HideHere
from engine.machine import Machine
from engine.refs import Ref, parse_ref, resolve_ref, sweep
from engine.rules.graph import GRAPH, expand
from state import Refuse

TERMINALS = (EndEvent, EnterCombat, EscapeHex, HideHere, EndGame)

# Bounded so the explorer stays finite: a die is interesting at all six values,
# a "how many join?" is not interesting at ninety-nine. One sample keeps the
# sweep to a few seconds; TestJoining covers the arithmetic of larger groups.
NUMBER_SAMPLES = [1]
MAX_ANSWERS = 40


def answers_for(ask):
    if ask.kind == "die":
        return list(range(ask.spec["min"], ask.spec["max"] + 1))
    if ask.kind == "choice":
        return list(ask.spec["options"])
    if ask.kind == "confirm":
        return [True, False]
    if ask.kind == "number":
        low = ask.spec.get("min") or 0
        return [max(low, n) for n in NUMBER_SAMPLES]
    if ask.kind == "pick_char":
        return list(ask.spec["names"])
    raise AssertionError(f"the explorer has no answers for a {ask.kind} question")


def revisit(trace):
    """The first section this path enters twice, if any.

    The graph genuinely cycles - r337 sends you to r342 and r342 sends you back
    - so a walk that followed every edge would never finish. A path that comes
    back to where it has been has proved what this test is for; stop it there.
    """
    seen = set()
    for sid in trace:
        if sid in seen:
            return sid
        seen.add(sid)
    return None


def explore(sid, g_factory, params=None, book=None):
    """Every path through a section. Yields (answers, (how, what)).

    Each path is replayed from the start in its own machine, so one cannot
    contaminate the next. Nothing is written to disk - see Machine(autosave).
    """
    book = book or harness.book()
    out = []
    stack = [[]]
    while stack:
        prefix = stack.pop()
        g = g_factory()
        m = Machine(g, book, autosave=False)
        try:
            turn = m.start(sid, **(params or {}))
            for a in prefix:
                turn = m.answer(a)
        except Refuse as e:
            out.append((prefix, ("refuse", str(e))))
            continue
        again = revisit(m.trace)
        if again:
            out.append((prefix, ("cycle", again)))
            continue
        if turn.done:
            out.append((prefix, ("outcome", turn.result)))
            continue
        if len(prefix) >= MAX_ANSWERS:
            out.append((prefix, ("runaway", turn.ask)))
            continue
        for a in answers_for(turn.ask):
            stack.append(prefix + [a])
    return out


def game(gold=500, wits=4, party=1):
    def make():
        g = harness.blank("graph", gold=gold)
        g["party"][0]["wits"] = wits
        for i in range(party - 1):
            f = __import__("state").new_char(f"Follower {i + 1}")
            f.update(cs=4, end=4)
            g["party"].append(f)
        return g
    return make


class TestParseRef(unittest.TestCase):
    def test_the_ordinary_shapes(self):
        self.assertEqual(parse_ref("converse r341"), Ref("r341", None, "", "", ""))
        self.assertEqual(parse_ref("surprise r303"), Ref("r303", None, "", "", ""))
        self.assertEqual(parse_ref("bribe (5) r322"), Ref("r322", 5, "", "", ""))
        self.assertEqual(parse_ref("bribe to pass (25) r323"),
                         Ref("r323", 25, "", "", ""))

    def test_the_space_pdftotext_ate(self):
        self.assertEqual(parse_ref("hider318").sid, "r318")
        self.assertEqual(parse_ref("passr325").sid, "r325")

    def test_footnote_markers(self):
        r = parse_ref("***inquiry r342")
        self.assertEqual(r.sid, "r342")
        self.assertEqual(r.marker, "***")
        self.assertEqual(parse_ref("†ally r334").sid, "r334")

    def test_ocr_fragments_still_yield_their_target(self):
        for cell, want in [("pe r311", "r311"), ("p e116", "e116"),
                           ("be-pass (5) r321", "r321"),
                           ("conversation r341 esca", "r341")]:
            self.assertEqual(parse_ref(cell).sid, want, cell)

    def test_a_cell_naming_no_section(self):
        self.assertIsNone(parse_ref("roll again").sid)
        self.assertIsNone(parse_ref("**audience").sid)

    def test_two_sections_in_one_cell_refuses_rather_than_guesses(self):
        with self.assertRaises(Refuse) as cm:
            parse_ref("see r330 or r341")
        self.assertIn("more than one section", str(cm.exception))

    def test_a_bare_number_is_not_read_as_a_section(self):
        # "bribe 100 gold" must not become a jump to e100.
        self.assertIsNone(parse_ref("bribe 100 gold").sid)

    def test_errata_is_applied(self):
        self.assertEqual(resolve_ref(harness.book(), "hide r119").sid, "r319")


class TestSweep(unittest.TestCase):
    """The plan 02 gate: every option cell in the book yields one target."""

    @classmethod
    def setUpClass(cls):
        cls.result = sweep(harness.book())

    def test_nothing_fails_to_parse(self):
        self.assertEqual(
            [f"{s} {r}/{c}: {t!r}" for s, r, c, t, _ in self.result["failed"]], [])

    def test_the_only_targetless_cells_are_the_two_known_ones(self):
        got = {(s, r, c) for s, r, c, _, _ in self.result["targetless"]}
        # e021's "roll again" and e130's "**audience", whose rule is in a
        # footnote under the table rather than in the cell.
        self.assertEqual(got, {("e021", "6", "talk"), ("e130", "4", "talk")})

    def test_every_target_is_a_section_that_exists(self):
        book = harness.book()
        missing = sorted({ref.sid for *_, ref in self.result["ok"]
                          if book.get(ref.sid) is None})
        self.assertEqual(missing, [])

    def test_the_amounts_are_the_ones_the_book_prints(self):
        got = sorted({ref.amount for *_, ref in self.result["ok"]
                      if ref.amount is not None})
        self.assertEqual(got, [5, 8, 10, 15, 20, 25, 30])


class TestGraphData(unittest.TestCase):
    def test_all_forty_four_sections_are_encoded(self):
        want = {f"r{n}" for n in range(300, 343)} | {"r343"}
        want.discard("r343")            # plan 03 owns victim selection
        self.assertEqual(set(GRAPH), want)

    def test_every_section_exists_in_the_book(self):
        book = harness.book()
        self.assertEqual([s for s in GRAPH if book.get(s) is None], [])

    def test_every_goto_target_resolves(self):
        book = harness.book()
        bad = []
        for sid, spec in GRAPH.items():
            for node in walk_nodes(spec):
                if node.get("do") == "goto" and book.get(node["sid"]) is None:
                    bad.append(f"{sid} -> {node['sid']}")
        self.assertEqual(bad, [])

    def test_the_two_comparison_forms_are_not_collapsed(self):
        # r301/r302, r305/r306, r308/r309 differ only in >= versus >.
        for a, b in [("r301", "r302"), ("r305", "r306"), ("r308", "r309")]:
            self.assertEqual(GRAPH[a]["op"], "ge")
            self.assertEqual(GRAPH[b]["op"], "gt")

    def test_party_size_checks_use_men_only(self):
        for sid in ("r303", "r319", "r320"):
            self.assertEqual(GRAPH[sid]["stat"], "party_size")


def walk_nodes(spec):
    """Every outcome descriptor inside a section's encoding."""
    for key in ("pass", "fail"):
        if isinstance(spec.get(key), dict):
            yield spec[key]
    for key in ("rows", "hostile", "decline"):
        block = spec.get(key)
        if isinstance(block, dict):
            rows = block.get("rows", block)
            if isinstance(rows, dict):
                for node in rows.values():
                    if isinstance(node, dict):
                        yield node
                        for o in node.get("options", []):
                            yield o


class TestReachability(unittest.TestCase):
    """Every path from every entry point lands somewhere."""

    def test_every_graph_section_terminates(self):
        problems = []
        for sid in sorted(GRAPH):
            for path, (how, what) in explore(sid, game(), {"amount": 10}):
                if how == "runaway":
                    problems.append(f"{sid} via {path}: still asking after "
                                    f"{MAX_ANSWERS} answers")
                elif how == "outcome" and not isinstance(what, TERMINALS):
                    problems.append(f"{sid} via {path}: {what!r}")
        self.assertEqual(problems, [])

    def test_the_only_refusals_are_the_ones_that_should_refuse(self):
        """A refusal is the engine saying it will not guess (D5). Driven from a
        standing start every one of these is correct, and the set is asserted
        exactly so a new one cannot appear unnoticed.

        - r312/r313 need somebody expendable, and a solo party has only the
          Prince, whose abandonment ends the game.
        - r314/r315/r317/r318/r320 end in Retry, which only means anything
          under an option table. Reached properly they come back to one; see
          TestRetry. (r319 is absent because a one-man party passes its check on
          every die.)
        """
        got = {sid for sid in GRAPH
               for _, (how, _w) in explore(sid, game(), {"amount": 10})
               if how == "refuse"}
        self.assertEqual(got, {"r312", "r313", "r314", "r315", "r317",
                               "r318", "r320"})

    def test_the_retry_sections_do_not_refuse_when_reached_properly(self):
        """Reached from an option table, a Retry finds one. e003 row 3 evade is
        hide r317, and failing it must return to the options rather than run out
        of stack."""
        for path, (how, what) in explore("e003", game()):
            if how == "refuse":
                self.assertNotIn("previous option list", what,
                                 f"e003 via {path}")

    def test_a_mounted_party_escapes_by_r312_without_abandoning_anyone(self):
        g = harness.blank("graph-mounted")
        horse = __import__("state").new_char("Horse", "mount")
        g["party"].append(horse)
        m = Machine(g, harness.book(), autosave=False)
        m.start("e003")
        m.answer(1)                       # row 1: evade is escape mtd r312
        turn = m.answer("evade")
        self.assertIsInstance(turn.result, EscapeHex)
        self.assertEqual(len(m.g["party"]), 2)

    def test_r330_reaches_every_battle_section(self):
        seen = set()
        for path, (how, what) in explore("r330", game()):
            if how == "outcome" and isinstance(what, EnterCombat):
                seen.add(what.spec["from"])
        self.assertEqual(seen, {f"r{n}" for n in range(300, 311)})

    def test_r330_never_returns_to_itself(self):
        # Every r330 result is a terminal fight, so the graph cannot cycle here.
        for path, (how, what) in explore("r330", game()):
            self.assertEqual(how, "outcome")
            self.assertIsInstance(what, EnterCombat)

    def test_the_only_cycles_are_the_two_the_booklet_prints(self):
        """r337 sends you to r342 and r342 sends you back.

        This is in the 1981 rules, not an encoding mistake: r337's decline table
        ends at r342, and r342 on a 10 returns to r337. It terminates in play
        because most rolls leave the loop, but it means no walk of this graph
        can follow every edge - see `revisit`.
        """
        looping = set()
        for sid in sorted(GRAPH):
            for _, (how, what) in explore(sid, game(), {"amount": 10}):
                if how == "cycle":
                    looping.add((sid, what))
        self.assertEqual(looping, {("r337", "r337"), ("r342", "r342")})


class TestOptionTables(unittest.TestCase):
    def test_e003_every_row_and_column_resolves(self):
        problems = []
        for path, (how, what) in explore("e003", game()):
            if how == "runaway":
                problems.append(f"e003 via {path}: runaway")
            elif how == "outcome" and not isinstance(what, TERMINALS):
                problems.append(f"e003 via {path}: {what!r}")
        self.assertEqual(problems, [])

    def test_e003_covers_all_eighteen_cells(self):
        book = harness.book()
        cells = {(r, c) for r, cols in book.tables["e003"]["rows"].items()
                 for c in cols}
        self.assertEqual(len(cells), 18)
        for row, col in sorted(cells):
            g = game()()
            m = Machine(g, harness.book(), autosave=False)
            m.start("e003")
            turn = m.answer(int(row))
            self.assertIn(col, turn.ask.spec["options"], f"{row}/{col}")

    def test_every_options_table_in_the_book_can_be_entered(self):
        book = harness.book()
        sids = [s for s, t in book.tables.items() if t.get("kind") == "options"]
        self.assertGreaterEqual(len(sids), 20)
        problems = []
        for sid in sorted(sids):
            g = game()()
            m = Machine(g, book, autosave=False)
            try:
                turn = m.start(sid)
                if turn.ask is None or turn.ask.kind != "die":
                    problems.append(f"{sid}: opened with {turn.ask}")
            except Refuse as e:
                problems.append(f"{sid}: {e}")
        self.assertEqual(problems, [])


class TestRetry(unittest.TestCase):
    def test_a_failed_hide_returns_to_the_options_without_that_column(self):
        m = Machine(game()(), harness.book(), autosave=False)
        m.start("e003")
        m.answer(3)                 # row 3
        m.answer("evade")           # hide r317
        turn = m.answer(6)          # wit & wiles 4 >= 6 is false -> Retry
        self.assertEqual(turn.ask.kind, "choice")
        self.assertEqual(turn.ask.spec["options"], ["talk", "fight"])

    def test_the_row_is_not_rolled_again(self):
        m = Machine(game()(), harness.book(), autosave=False)
        m.start("e003")
        m.answer(3)
        m.answer("evade")
        turn = m.answer(6)
        self.assertNotEqual(turn.ask.kind, "die")

    def test_retrying_every_column_eventually_refuses(self):
        # e005 row 4: evade is hide r119 -> r319, a party-size check.
        m = Machine(game(party=6)(), harness.book(), autosave=False)
        m.start("e005")
        m.answer(4)
        m.answer("evade")           # r319: party 6 <= die is false for any d6
        turn = m.answer(1)
        self.assertEqual(turn.ask.spec["options"], ["talk", "fight"])

    def test_retry_survives_a_save_and_reload(self):
        with harness.temp_game("graph-retry", gold=500) as g:
            m = Machine(g, harness.book())      # this one must reach the disk
            m.start("e003")
            m.answer(3)
            m.answer("evade")
            m.answer(6)
            m2 = Machine(harness.reload(g["name"]), harness.book())
            turn = m2.resume()
            self.assertEqual(turn.ask.spec["options"], ["talk", "fight"])


class TestModifiers(unittest.TestCase):
    def landed_on(self, sid, first, second):
        """Where an r330 lookup ends up. Asserted from the trace because some of
        r300-r310 ask a die of their own and so do not finish the turn."""
        m = Machine(game()(), harness.book(), autosave=False)
        m.start(sid)
        m.answer(first)
        m.answer(second)
        return m.trace[-1]

    def test_battle_minus_one_shifts_the_r330_lookup(self):
        # wit & wiles 4 > 5 is false -> Battle(-1); then 4 - 1 = 3 -> r309.
        self.assertEqual(self.landed_on("r329", 5, 4), "r309")

    def test_battle_plus_one_shifts_the_other_way(self):
        # wit & wiles 4 >= 5 is false -> Battle(+1); then 4 + 1 = 5 -> r307.
        self.assertEqual(self.landed_on("r326", 5, 4), "r307")

    def test_an_unmodified_roll_is_unshifted(self):
        self.assertEqual(self.landed_on("r327", 5, 4), "r308")

    def test_the_ends_clamp(self):
        # 2 - 1 = 1 reads as "2 or less"; 12 + 1 = 13 reads as "12 or more".
        self.assertEqual(self.landed_on("r329", 5, 2), "r310")
        self.assertEqual(self.landed_on("r326", 5, 12), "r300")

    def test_e071_rows_zero_and_seven_exist(self):
        # The elf/dwarf note gives a +-1 on the option die, so the table runs
        # 0 to 7. A row the modifier can reach must be there.
        rows = harness.book().tables["e071"]["rows"]
        self.assertIn("0", rows)
        self.assertIn("7", rows)


class TestCombatEntry(unittest.TestCase):
    def test_the_four_initiative_shapes(self):
        want = {
            "r300": ("us", "us"), "r304": ("us", None),
            "r307": ("them", None), "r310": ("them", "them"),
        }
        for sid, (init, surprise) in want.items():
            m = Machine(game()(), harness.book(), autosave=False)
            turn = m.start(sid)
            self.assertIsInstance(turn.result, EnterCombat, sid)
            self.assertEqual(turn.result.spec["initiative"], init, sid)
            self.assertEqual(turn.result.spec["surprise"], surprise, sid)

    def test_the_assassin_strikes_at_the_prince(self):
        m = Machine(game()(), harness.book(), autosave=False)
        m.start("r341")
        turn = m.answer(2)
        self.assertEqual(turn.result.spec["target"], "player")


class TestJoining(unittest.TestCase):
    def test_free_followers_join_on_no_terms(self):
        g = game()()
        m = Machine(g, harness.book(), autosave=False)
        m.start("r334")
        m.answer(2)                 # how many
        m.answer(4)                 # combat skill
        turn = m.answer(5)          # endurance
        self.assertIsInstance(turn.result, EndEvent)
        self.assertEqual(len(m.g["party"]), 3)
        self.assertEqual(m.g["party"][1]["pay"], 0)

    def test_r335_records_that_they_leave_at_a_settlement(self):
        m = Machine(game()(), harness.book(), autosave=False)
        m.start("r335")
        m.answer(1)
        m.answer(4)
        m.answer(5)
        self.assertIn("leaves_at_settlement", m.g["party"][1]["terms"])

    def test_wages_are_paid_on_the_spot_when_the_section_says_so(self):
        g = game(gold=100)()
        m = Machine(g, harness.book(), autosave=False)
        m.start("r333")
        m.answer(True)              # hire them
        m.answer(3)                 # three of them
        m.answer(4)
        m.answer(5)
        self.assertEqual(m.g["gold"], 94)          # 3 at 2 gold, today
        self.assertEqual(m.g["party"][1]["pay"], 2)

    def test_r332_wages_start_tomorrow(self):
        g = game(gold=100)()
        m = Machine(g, harness.book(), autosave=False)
        m.start("r332", amount=20)
        m.answer(True)             # pay the bonus
        m.answer(1)
        m.answer(4)
        m.answer(5)
        self.assertEqual(m.g["gold"], 80)          # the bonus only
        self.assertEqual(m.g["party"][1]["pay_starts"], g["day"] + 1)

    def test_r338_is_a_three_way_check(self):
        rates = {}
        for die in (3, 4, 5):      # wit & wiles 4: >, ==, <
            g = game(gold=100)()
            m = Machine(g, harness.book(), autosave=False)
            m.start("r338")
            turn = m.answer(die)
            if turn.done:
                rates[die] = None
                continue
            m.answer(1)
            m.answer(4)
            m.answer(5)
            rates[die] = m.g["party"][1]["pay"]
        self.assertEqual(rates, {3: 1, 4: 2, 5: None})


class TestBribes(unittest.TestCase):
    def test_paying_ends_the_encounter(self):
        g = game(gold=50)()
        m = Machine(g, harness.book(), autosave=False)
        m.start("r322", amount=10)
        turn = m.answer(True)
        self.assertIsInstance(turn.result, EndEvent)
        self.assertEqual(m.g["gold"], 40)

    def test_refusing_goes_to_battle(self):
        m = Machine(game(gold=50)(), harness.book(), autosave=False)
        m.start("r321", amount=10)
        turn = m.answer(False)      # -> r330 at +1
        self.assertEqual(turn.ask.kind, "die")

    def test_an_empty_purse_is_not_asked_to_pay(self):
        m = Machine(game(gold=2)(), harness.book(), autosave=False)
        turn = m.start("r322", amount=10)
        self.assertEqual(turn.ask.kind, "die")      # straight to r330
        self.assertTrue(any("2" in e.text and "10" in e.text
                            for e in turn.events))

    def test_an_amount_is_required(self):
        m = Machine(game()(), harness.book(), autosave=False)
        with self.assertRaises(Refuse) as cm:
            m.start("r322")
        self.assertIn("amount", str(cm.exception))


class TestHelpers(unittest.TestCase):
    def test_expand(self):
        self.assertEqual(expand("1,2"), [1, 2])
        self.assertEqual(expand("7"), [7])
        self.assertEqual(expand("2(or less)"), [])


if __name__ == "__main__":
    unittest.main()
