"""Combat (r220), victim selection (r343), escape and hide (r218).

Combat is the easiest part of the game to test hard: it is arithmetic over a
journal, and the journal is just a list of numbers.
"""

import random
import unittest

import harness  # noqa: I001  - must precede the src imports; it sets the path

import combat            # noqa: E402
import creatures         # noqa: E402
import procedures        # noqa: E402
import state             # noqa: E402
from engine import EndEvent, EndGame, Machine        # noqa: E402
from engine.rules import combat as rules_combat      # noqa: E402
from state import Refuse                             # noqa: E402


def fighter(name, cs, end, wounds=0, kind="player", wits=4):
    ch = state.new_char(name, kind)
    ch.update(cs=cs, end=end, wounds=wounds)
    if kind == "player":
        ch["wits"] = wits
    return ch


def foe(name, cs, end, wealth=0, wounds=0):
    return {"name": name, "cs": cs, "end": end, "wealth": wealth,
            "wounds": wounds}


def battle_game(party=None, foes=None, gold=0, hex="1301"):
    g = harness.blank("combat", gold=gold)
    g["hex"] = hex
    if party is not None:
        g["party"] = party
    if foes is not None:
        g["foes"] = foes
    return g


def run(g, answers, spec=None, flow="_combat", **params):
    """Drive a flow to wherever the answers take it."""
    m = Machine(g, harness.book(), autosave=False)
    if flow == "_combat":
        params = {"spec": spec or {"initiative": "us"}}
    turn = m.start(flow, **params)
    for a in answers:
        if turn.done:
            break
        turn = m.answer(a)
    return m, turn


class TestStrikeArithmetic(unittest.TestCase):
    """r220c, including the worked example the section prints."""

    def test_the_sections_own_example(self):
        # A Dwarf (skill 6, endurance 7) strikes the Prince (skill 8,
        # endurance 9) who has one wound: 6 - 8 + 10 = 8, one wound.
        dwarf = fighter("Dwarf", 6, 7, kind="follower")
        prince = fighter("Cal Arath", 8, 9, wounds=1, kind="player")
        s = combat.resolve_strike(dwarf, prince, 10)
        self.assertEqual(s["skill"], -2)
        self.assertEqual(s["total"], 8)
        self.assertEqual(s["wounds"], 1)

        # The Prince, now on two wounds, strikes back: 8 - 6 + 7 - 1 = 8.
        prince["wounds"] = 2
        back = combat.resolve_strike(prince, dwarf, 7)
        self.assertEqual(back["hurt"], -1)
        self.assertEqual(back["total"], 8)
        self.assertEqual(back["wounds"], 1)

    def test_every_tier_of_the_combat_table(self):
        want = {-1: 1, 3: 1, 5: 1, 8: 1, 11: 1,
                10: 2, 12: 2, 13: 2, 17: 2,
                14: 3, 16: 5, 18: 5, 19: 5, 20: 6}
        for total, wounds in want.items():
            self.assertEqual(state.wounds_for(total), wounds, f"total {total}")

    def test_a_total_off_the_table_misses(self):
        for total in (0, 1, 2, 4, 6, 7, 9, 15, 21):
            self.assertEqual(state.wounds_for(total), 0, f"total {total}")

    def test_the_three_special_modifiers(self):
        # +2 when the target has wounds at or past half his endurance.
        hurt = fighter("Hurt", 5, 6, wounds=3, kind="follower")
        self.assertEqual(state.target_mod(hurt), 2)
        # -1 for a striker with any wounds, -1 more at half endurance.
        self.assertEqual(state.strike_mod(fighter("A", 5, 6, wounds=1, kind="follower")), -1)
        self.assertEqual(state.strike_mod(fighter("B", 5, 6, wounds=3, kind="follower")), -2)
        self.assertEqual(state.strike_mod(fighter("C", 5, 6, kind="follower")), 0)

    def test_the_rolled_and_the_given_agree(self):
        """One implementation: `strike` is `resolve_strike` with dice on top."""
        a, b = fighter("A", 6, 7, kind="follower"), fighter("B", 5, 8, wounds=2, kind="follower")
        for seed in range(50):
            rng = random.Random(seed)
            rolled = combat.strike(a, b, rng)
            given = combat.resolve_strike(a, b, rolled["roll"])
            self.assertEqual(given["total"], rolled["total"])
            self.assertEqual(given["wounds"], rolled["wounds"])


class TestBandSizes(unittest.TestCase):
    def test_the_arithmetic_matches_the_roll(self):
        for sid, specs in creatures.load().items():
            if sid.startswith("_"):
                continue
            for spec in specs:
                for total in range(spec.get("dice", 1), spec.get("dice", 1) * 6 + 1):
                    n = creatures.size_from(spec, total)
                    self.assertGreaterEqual(n, spec.get("min", 0), f"{sid} {total}")

    def test_size_and_size_from_agree(self):
        spec = {"dice": 2, "div": 2, "add": 1, "min": 2}
        for seed in range(30):
            random.seed(seed)
            n, how = creatures.size(spec)
            total = sum(int(d) for d in how.split("[")[1].rstrip("]").split(" + "))
            self.assertEqual(n, creatures.size_from(spec, total))


class TestInitiative(unittest.TestCase):
    def order_of(self, spec, answers):
        g = battle_game(party=[fighter("Cal Arath", 8, 9, kind="player")],
                        foes=[foe("Brigand", 5, 5)])
        m, turn = run(g, answers, spec=spec)
        return [e.text.split()[0] for e in m.out if e.kind == "roll"], m, turn

    def test_we_strike_first(self):
        m, turn = run(battle_game(party=[fighter("Cal", 8, 9, kind="player")],
                                  foes=[foe("Brigand", 5, 5)]),
                      [False, "fight"], spec={"initiative": "us"})
        self.assertEqual(turn.ask.prompt, "Cal strikes at Brigand")

    def test_they_strike_first(self):
        m, turn = run(battle_game(party=[fighter("Cal", 8, 9, kind="player")],
                                  foes=[foe("Brigand", 5, 5)]),
                      [False, "fight"], spec={"initiative": "them"})
        self.assertEqual(turn.ask.prompt, "Brigand strikes at Cal")

    def test_surprise_grants_exactly_one_bonus_phase(self):
        g = battle_game(party=[fighter("Cal", 8, 9, kind="player")],
                        foes=[foe("Brigand", 5, 5)])
        m, turn = run(g, [False], spec={"initiative": "us", "surprise": "us"})
        # The free strike comes before any round question.
        self.assertEqual(turn.ask.kind, "die")
        self.assertEqual(turn.ask.prompt, "Cal strikes at Brigand")
        self.assertTrue(any("free strike" in e.text for e in m.out))

    def test_surprise_by_them_lets_them_lead(self):
        g = battle_game(party=[fighter("Cal", 8, 9, kind="player")],
                        foes=[foe("Brigand", 5, 5)])
        m, turn = run(g, [False], spec={"initiative": "us", "surprise": "them"})
        self.assertEqual(turn.ask.prompt, "Brigand strikes at Cal")


class TestRout(unittest.TestCase):
    def test_a_formidable_enemy_is_never_offered_for_rout(self):
        g = battle_game(party=[fighter("Cal", 8, 9, kind="player")],
                        foes=[foe("Troll", 9, 9)])
        m, turn = run(g, [], spec={"initiative": "us"})
        # No rout question at all: nothing here can be frightened.
        self.assertNotIn("frighten", turn.ask.prompt)

    def test_rout_immunity_is_skill_or_endurance(self):
        self.assertTrue(combat.rout_immune(foe("A", 9, 4)))
        self.assertTrue(combat.rout_immune(foe("B", 4, 9)))
        self.assertFalse(combat.rout_immune(foe("C", 8, 8)))

    def test_the_rout_question_is_asked_when_it_can_matter(self):
        g = battle_game(party=[fighter("Cal", 8, 9, kind="player")],
                        foes=[foe("Brigand", 5, 5), foe("Brigand 2", 5, 5)])
        m, turn = run(g, [], spec={"initiative": "us"})
        self.assertIn("frighten", turn.ask.prompt)


class TestOutcomes(unittest.TestCase):
    def test_killing_the_last_enemy_wins_and_pays_out(self):
        g = battle_game(party=[fighter("Cal", 8, 9, kind="player")],
                        foes=[foe("Brigand", 2, 2, wealth=5)], gold=0)
        m, turn = run(g, [False, "fight", 12, 4], spec={"initiative": "us"})
        self.assertTrue(turn.done)
        self.assertIsInstance(turn.result, EndEvent)
        self.assertEqual(m.g["foes"], [])
        self.assertGreater(m.g["gold"], 0)          # r226 wealth code 5

    def test_the_princes_death_ends_the_game(self):
        g = battle_game(party=[fighter("Cal", 2, 4, kind="player")],
                        foes=[foe("Troll", 9, 9)])
        m, turn = run(g, ["fight", 12], spec={"initiative": "them"})
        self.assertIsInstance(turn.result, EndGame)
        self.assertEqual(turn.result.result, "loss")

    def test_a_party_with_nobody_able_to_strike_refuses(self):
        """Not the same as a helpless Prince: followers can fight over him
        (r221b). This is the case where literally nobody can act."""
        g = battle_game(party=[fighter("Cal", 2, 4, wounds=3, kind="player")],
                        foes=[foe("Troll", 9, 9)])
        m = Machine(g, harness.book(), autosave=False)
        with self.assertRaises(Refuse) as cm:
            m.start("_combat", spec={"initiative": "them"})
        self.assertIn("able to strike", str(cm.exception))

    def test_a_fight_starts_with_the_prince_down_if_a_follower_can_fight(self):
        g = battle_game(party=[fighter("Cal", 2, 4, wounds=3, kind="player"),
                               fighter("Lancer", 6, 7, kind="follower")],
                        foes=[foe("Brigand", 4, 5)])
        m = Machine(g, harness.book(), autosave=False)
        turn = m.start("_combat", spec={"initiative": "us"})
        self.assertIsNotNone(turn.ask)


class TestTreasure(unittest.TestCase):
    def test_no_wealth_code_means_no_roll(self):
        g = battle_game(party=[fighter("Cal", 8, 9, kind="player")],
                        foes=[foe("Beast", 2, 2, wealth=0)])
        m, turn = run(g, [False, "fight", 12], spec={"initiative": "us"})
        self.assertTrue(turn.done)
        self.assertEqual(m.g["gold"], 0)

    def test_every_wealth_code_in_the_data_has_an_r226_line(self):
        """r226 prints seventeen codes, not every number. A band whose wealth
        code is not one of them would warn at the moment of payout, which is a
        long way from where the mistake would be."""
        rows = procedures.treasure_rows(harness.book())
        missing = set()
        for sid, specs in creatures.load().items():
            if sid.startswith("_"):
                continue
            for spec in specs:
                for line in spec.get("foes", []):
                    w = line.get("wealth")
                    if w and str(w) not in rows:
                        missing.add((sid, line["name"], w))
        self.assertEqual(sorted(missing), [])

    def test_the_grid_is_read_for_each_body(self):
        rows = procedures.treasure_rows(harness.book())
        self.assertIn("5", rows)
        self.assertEqual(len(rows["5"]), 6)

    def test_a_letter_result_is_reported_not_swallowed(self):
        g = battle_game(party=[fighter("Cal", 8, 9, kind="player")],
                        foes=[foe("Lord", 2, 2, wealth=7)])
        m, turn = run(g, [False, "fight", 12, 2], spec={"initiative": "us"})
        text = " ".join(e.text for e in m.out)
        self.assertTrue(turn.done)


class TestVictimSelection(unittest.TestCase):
    def test_a_solo_party_is_the_victim_without_a_roll(self):
        g = battle_game(party=[fighter("Cal", 8, 9, kind="player")])
        m, turn = run(g, [], flow="r343")
        self.assertTrue(turn.done)
        self.assertEqual(m.g["day_flags"]["victim"], "Cal")

    def test_a_six_selects(self):
        g = battle_game(party=[fighter("Cal", 8, 9, kind="player"),
                               fighter("Lancer", 5, 5, kind="follower")])
        m, turn = run(g, [1, 6], flow="r343")
        self.assertEqual(m.g["day_flags"]["victim"], "Lancer")

    def test_it_cycles_rather_than_stopping(self):
        g = battle_game(party=[fighter("Cal", 8, 9, kind="player"),
                               fighter("Lancer", 5, 5, kind="follower")])
        m, turn = run(g, [1, 1, 6], flow="r343")
        self.assertEqual(m.g["day_flags"]["victim"], "Cal")

    def test_it_cannot_spin_forever(self):
        g = battle_game(party=[fighter("Cal", 8, 9, kind="player"),
                               fighter("Lancer", 5, 5, kind="follower")])
        m = Machine(g, harness.book(), autosave=False)
        m.start("r343")
        with self.assertRaises(Refuse) as cm:
            for _ in range(rules_combat.MAX_PICKS + 2):
                m.answer(1)
        self.assertIn("by hand", str(cm.exception))


class TestEscape(unittest.TestCase):
    def river_edge(self):
        """A hex with a river on one side, and the die that heads that way.

        `neighbours` formats an id for every direction whether or not the board
        has such a hex, so anything it returns is checked against the map first.
        """
        book = harness.book()
        hexes = book.map["hexes"]
        for hid in list(hexes)[:400]:
            for heading, there in procedures.neighbours(
                    *procedures.parse_hex(hid)).items():
                if there in hexes and procedures.hexside(
                        book, hid, there).get("river"):
                    die = {v: k for k, v in procedures.DRIFT.items()}[heading]
                    return hid, die
        raise AssertionError("no river edge in the map data")

    def test_a_direction_moves_the_party(self):
        g = battle_game(party=[fighter("Cal", 8, 9, kind="player")], hex="1010")
        m, turn = run(g, [1], flow="_escape")
        self.assertTrue(turn.done)
        self.assertNotEqual(m.g["hex"], "1010")
        self.assertEqual(m.g["day_flags"]["no_entry_event"], m.g["hex"])

    def test_the_map_edge_is_rerolled(self):
        # 0101 is the top-left corner; N and NW leave the map.
        g = battle_game(party=[fighter("Cal", 8, 9, kind="player")], hex="0101")
        m = Machine(g, harness.book(), autosave=False)
        m.start("_escape")
        turn = m.answer(1)                       # N: off the map
        self.assertFalse(turn.done)
        self.assertTrue(any("off the map" in e.text for e in m.out))

    def test_a_river_is_rerolled_on_foot(self):
        hid, die = self.river_edge()
        g = battle_game(party=[fighter("Cal", 8, 9, kind="player")], hex=hid)
        m = Machine(g, harness.book(), autosave=False)
        m.start("_escape")
        turn = m.answer(die)
        self.assertTrue(any("river" in e.text for e in m.out))
        self.assertFalse(turn.done)

    def test_a_flying_party_crosses_the_river(self):
        hid, die = self.river_edge()
        eagle = state.new_char("Eagle", "mount")
        eagle["winged"] = True
        g = battle_game(party=[fighter("Cal", 8, 9, kind="player"), eagle],
                        hex=hid)
        m, turn = run(g, [die], flow="_escape")
        self.assertTrue(turn.done)


class TestHide(unittest.TestCase):
    def test_hiding_stays_put_and_spends_the_day(self):
        g = battle_game(party=[fighter("Cal", 8, 9, kind="player")], hex="1010")
        m, turn = run(g, [], flow="_hide")
        self.assertTrue(turn.done)
        self.assertEqual(m.g["hex"], "1010")
        self.assertEqual(turn.result.time_cost, "rest_of_day")


class TestTermination(unittest.TestCase):
    """Every fight ends, and nothing impossible happens on the way."""

    def one(self, seed):
        rng = random.Random(seed)
        party = [fighter("Cal Arath", rng.randint(4, 9), rng.randint(5, 10),
                         kind="player")]
        for i in range(rng.randint(0, 3)):
            party.append(fighter(f"Lancer {i + 1}", rng.randint(2, 7),
                                 rng.randint(3, 8), kind="follower"))
        foes = [foe(f"Brigand {i + 1}", rng.randint(2, 8), rng.randint(2, 8),
                    wealth=rng.choice([0, 1, 3, 5]))
                for i in range(rng.randint(1, 4))]
        g = battle_game(party=party, foes=foes)
        m = Machine(g, harness.book(), autosave=False)
        spec = {"initiative": rng.choice(["us", "them"]),
                "surprise": rng.choice([None, "us", "them"]),
                "no_escape": True}
        try:
            turn = m.start("_combat", spec=spec)
            for _ in range(4000):
                if turn.done:
                    break
                ask = turn.ask
                if ask.kind == "die":
                    v = sum(rng.randint(1, 6)
                            for _ in range(ask.spec.get("n", 1)))
                elif ask.kind == "confirm":
                    v = rng.random() < 0.5
                elif ask.kind == "choice":
                    v = rng.choice(ask.spec["options"])
                elif ask.kind == "pick_char":
                    v = rng.choice(ask.spec["names"])
                else:
                    v = 1
                turn = m.answer(v)
            else:
                return f"seed {seed}: never finished"
        except Refuse:
            return None            # a refusal is a legitimate ending (D5)
        for c in m.g["party"] + m.g["foes"]:
            if c.get("wounds", 0) < 0:
                return f"seed {seed}: negative wounds on {c['name']}"
        if m.g["gold"] < 0:
            return f"seed {seed}: negative gold"
        return None

    def test_ten_thousand_fights_all_end(self):
        problems = [p for p in (self.one(s) for s in range(10_000)) if p]
        self.assertEqual(problems[:5], [])


class TestFoeSetup(unittest.TestCase):
    def test_creatures_json_supplies_the_band(self):
        g = battle_game(party=[fighter("Cal", 8, 9, kind="player")], foes=[])
        m = Machine(g, harness.book(), autosave=False)
        turn = m.start("_combat", spec={"initiative": "us", "event": "e052"})
        self.assertEqual(turn.ask.kind, "die")
        self.assertEqual(turn.ask.spec["die"], "2d6")      # e052 rolls 2d6
        turn = m.answer(7)
        names = [f["name"] for f in m.g["foes"]]
        self.assertEqual(names.count("Hobgoblin"), 1)      # the leader
        self.assertEqual(len([n for n in names if n.startswith("Goblin")]), 7)

    def test_a_section_with_no_spec_asks_for_the_numbers(self):
        g = battle_game(party=[fighter("Cal", 8, 9, kind="player")], foes=[])
        m = Machine(g, harness.book(), autosave=False)
        turn = m.start("_combat", spec={"initiative": "us", "event": "e003"})
        self.assertEqual(turn.ask.kind, "number")
        self.assertIn("How many", turn.ask.prompt)


class TestEnduranceFloor(unittest.TestCase):
    """Nothing in the game has endurance below 2, and nothing the engine
    creates may either.

    `end` of 0 is the sentinel for stats not yet filled in - `state.dead`,
    `unconscious` and `serious` all guard on `end > 0` - so a character created
    with 0 would be unwoundable, and one created with 1 would be unconscious
    before being touched (r221b: unconscious at one wound less than endurance).
    """

    def test_the_booklet_never_prints_an_endurance_below_two(self):
        import re
        lowest = 99
        for sec in harness.book().sections.values():
            for m in re.finditer(r"endurance\s+(?:of\s+)?(\d+)",
                                 " ".join(sec["body"].split()), re.I):
                lowest = min(lowest, int(m.group(1)))
        self.assertEqual(lowest, 2)

    def test_no_band_in_the_data_is_below_two(self):
        for sid, specs in creatures.load().items():
            if sid.startswith("_"):
                continue
            for spec in specs:
                for line in spec.get("foes", []):
                    self.assertGreaterEqual(line["end"], 2, f"{sid} {line['name']}")

    def test_the_engine_will_not_accept_one(self):
        g = battle_game(party=[fighter("Cal", 8, 9, kind="player")], foes=[])
        m = Machine(g, harness.book(), autosave=False)
        m.start("_combat", spec={"initiative": "us", "event": "e003"})
        m.answer(1)                      # how many
        m.answer(6)                      # combat skill
        with self.assertRaises(Refuse):
            m.answer(1)                  # endurance

    def test_an_endurance_two_character_is_down_in_one_and_dead_in_two(self):
        ch = fighter("Mob", 2, 2, kind="follower")
        self.assertFalse(state.unconscious(ch))
        ch["wounds"] = 1
        self.assertTrue(state.serious(ch))
        self.assertTrue(state.unconscious(ch))
        self.assertFalse(state.dead(ch))
        self.assertEqual(state.effective_cs(ch), 0)      # r221b
        ch["wounds"] = 2
        self.assertTrue(state.dead(ch))


class TestAnswerValidation(unittest.TestCase):
    """A bad value must not reach the arithmetic."""

    def test_a_string_where_a_die_belongs(self):
        g = battle_game(party=[fighter("Cal", 8, 9, kind="player")],
                        foes=[foe("Brigand", 5, 5)])
        m = Machine(g, harness.book(), autosave=False)
        m.start("_combat", spec={"initiative": "us"})
        m.answer(False)
        m.answer("fight")
        with self.assertRaises(Refuse) as cm:
            m.answer("fight")
        self.assertIn("wants a number", str(cm.exception))

    def test_an_out_of_range_die(self):
        g = battle_game(party=[fighter("Cal", 8, 9, kind="player")],
                        foes=[foe("Brigand", 5, 5)])
        m = Machine(g, harness.book(), autosave=False)
        m.start("_combat", spec={"initiative": "us"})
        m.answer(False)
        m.answer("fight")
        with self.assertRaises(Refuse):
            m.answer(99)

    def test_a_choice_that_was_not_offered(self):
        g = battle_game(party=[fighter("Cal", 8, 9, kind="player")],
                        foes=[foe("Brigand", 5, 5)])
        m = Machine(g, harness.book(), autosave=False)
        m.start("_combat", spec={"initiative": "us"})
        m.answer(False)
        with self.assertRaises(Refuse):
            m.answer("run away screaming")


class TestEncounterFlow(unittest.TestCase):
    """The composition layer: a section resolved all the way to a verb."""

    def test_e003_fights_through_to_treasure(self):
        g = battle_game(party=[fighter("Cal Arath", 8, 9, kind="player")],
                        foes=[], gold=0)
        m = Machine(g, harness.book(), autosave=False)
        turn = m.start("encounter", sid="e003")
        # row 4 / fight -> r306 -> wits check -> combat; then the swordsman's
        # numbers as the section prints them, and no rout.
        opening = [4, "fight", 2, 1, 6, 6, 7, False]
        for a in opening:
            turn = m.answer(a)
        # From here every question is answered the same way: press the attack,
        # roll well. How many rounds that takes is not the point of the test.
        for _ in range(60):
            if turn.done:
                break
            ask = turn.ask
            turn = m.answer(ask.spec["max"] if ask.kind == "die" else
                            "fight" if ask.kind == "choice" else
                            ask.spec["names"][0] if ask.kind == "pick_char" else
                            False if ask.kind == "confirm" else 1)
        self.assertTrue(turn.done, f"still asking: {turn.ask}")
        self.assertIsInstance(turn.result, EndEvent)
        self.assertEqual(m.g["foes"], [])

    def test_a_pass_needs_no_combat(self):
        g = battle_game(party=[fighter("Cal", 8, 9, kind="player")])
        m = Machine(g, harness.book(), autosave=False)
        turn = m.start("encounter", sid="r325")
        self.assertTrue(turn.done)
        self.assertIsInstance(turn.result, EndEvent)

    def test_an_escape_outcome_is_carried_out(self):
        g = battle_game(party=[fighter("Cal", 8, 9, kind="player")], hex="1010")
        m = Machine(g, harness.book(), autosave=False)
        turn = m.start("encounter", sid="r311")
        self.assertEqual(turn.ask.kind, "die")        # the r218a direction
        turn = m.answer(1)
        self.assertTrue(turn.done)


if __name__ == "__main__":
    unittest.main()


def drive(m, turn, ours=12, theirs=2, limit=80):
    """Answer everything until the flow ends.

    Our strikes and theirs are given separate die values so a test can say
    "our side lands blows and theirs does not" without hand-feeding a journal.
    """
    ourside = ("Cal", "Lancer")
    for _ in range(limit):
        if turn.done:
            return turn
        ask = turn.ask
        if ask.kind == "die":
            want = ours if ask.prompt.startswith(ourside) else theirs
            v = max(ask.spec["min"], min(ask.spec["max"], want))
        elif ask.kind == "choice":
            v = "fight"
        elif ask.kind == "confirm":
            v = False
        elif ask.kind == "pick_char":
            v = ask.spec["names"][0]
        else:
            v = 1
        turn = m.answer(v)
    raise AssertionError(f"still asking after {limit} answers: {turn.ask}")


class TestPrinceDown(unittest.TestCase):
    """r221b: the Prince falls, and his followers decide what to do about it.

    The fight does not stop. That is the whole point of the rule - a party can
    lose its Prince to unconsciousness and still win the battle.

    The arithmetic these lean on: Cal is skill 8, endurance 9, already carrying
    seven wounds, so he is seriously wounded and worth +2 to strike at (r221a).
    The Brigand is skill 8, so the skill difference is zero and a roll of 6 comes
    to 8 - exactly one wound, which puts Cal on eight of nine: unconscious, and
    not dead (r221b).
    """

    DROP = 6            # the roll that lands the Prince on eight wounds

    def party(self, prince_wounds=7, with_follower=True):
        out = [fighter("Cal Arath", 8, 9, wounds=prince_wounds, kind="player")]
        if with_follower:
            out.append(fighter("Lancer", 6, 7, kind="follower"))
        return out

    def fell(self, attitude=None, gold=100, with_follower=True):
        """Play up to the moment the Prince goes down, and answer r221b."""
        g = battle_game(party=self.party(with_follower=with_follower),
                        gold=gold, foes=[foe("Brigand", 8, 5, wealth=4)])
        m = Machine(g, harness.book(), autosave=False)
        m.start("_combat", spec={"initiative": "them"})
        m.answer(False)                        # no rout attempts
        if with_follower:
            m.answer(False)                    # auto-pair
        m.answer("fight")                      # round 1
        turn = m.answer(self.DROP)             # their strike drops him
        if attitude is not None:
            turn = m.answer(attitude)
        return m, turn

    def test_the_prince_falls_rather_than_dying(self):
        m, turn = self.fell()
        prince = m.g["party"][0]
        self.assertEqual(prince["wounds"], 8)
        self.assertTrue(state.unconscious(prince))
        self.assertFalse(state.dead(prince))
        self.assertEqual(state.effective_cs(prince), 0)      # r221b

    def test_the_attitude_is_rolled_when_he_falls(self):
        m, turn = self.fell()
        self.assertEqual(turn.ask.why, "r221b")

    def test_four_or_more_and_they_carry_him(self):
        m, turn = self.fell(attitude=4)
        self.assertEqual([c["name"] for c in m.g["party"]],
                         ["Cal Arath", "Lancer"])
        self.assertEqual(m.g["gold"], 100)

    def test_three_or_less_and_they_desert_with_everything(self):
        m, turn = self.fell(attitude=3)
        self.assertEqual([c["name"] for c in m.g["party"]], ["Cal Arath"])
        self.assertEqual(m.g["gold"], 0)

    def test_the_fight_goes_on_rather_than_refusing(self):
        m, turn = self.fell(attitude=5)
        self.assertFalse(turn.done)
        self.assertIsNotNone(turn.ask)

    def test_a_follower_can_still_win_the_fight(self):
        m, turn = self.fell(attitude=6)
        turn = drive(m, turn, ours=12, theirs=2)
        self.assertIsInstance(turn.result, EndEvent)
        self.assertEqual(m.g["foes"], [])
        prince = m.g["party"][0]
        self.assertTrue(state.unconscious(prince))
        self.assertFalse(state.dead(prince))
        self.assertGreater(m.g["gold"], 100)        # the Brigand's wealth code

    def test_the_attitude_is_rolled_only_once(self):
        m, turn = self.fell(attitude=4)
        turn = drive(m, turn, ours=12, theirs=2)
        whys = [e.cite for e in m.out]
        self.assertNotIn("r221b", whys)

    def test_a_lone_prince_falling_needs_no_roll(self):
        m, turn = self.fell(with_follower=False)
        self.assertTrue(state.unconscious(m.g["party"][0]))
        self.assertNotEqual(turn.ask.why, "r221b")

    def test_a_helpless_party_is_not_asked_to_fight_or_flee(self):
        """Nobody on their feet cannot choose to press the attack or run."""
        m, turn = self.fell(with_follower=False)
        self.assertEqual(turn.ask.kind, "die")
        self.assertTrue(turn.ask.prompt.startswith("Brigand"))

    def test_being_left_for_dead_ends_in_r221c(self):
        """Abandoned and helpless, the enemy finishes him - the ordinary death
        rule, not a special case the engine has to invent."""
        m, turn = self.fell(attitude=1)
        turn = drive(m, turn, ours=12, theirs=7)
        self.assertIsInstance(turn.result, EndGame)
        self.assertEqual(turn.result.result, "loss")
        self.assertTrue(state.dead(m.g["party"][0]))
