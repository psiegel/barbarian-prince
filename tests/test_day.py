"""The daily loop: r203, dusk (r215-r217), rest (r222) and the clock.

Most of these drive the real flow rather than calling the steps, because the
order dusk runs in is half of what the plan is for.
"""

import unittest

import harness  # noqa: I001  - must precede the src imports; it sets the path

import procedures                      # noqa: E402
import state                           # noqa: E402
from engine import EndDay, EndGame, Machine        # noqa: E402
from engine.rules import day                       # noqa: E402
from procedures import Refuse                      # noqa: E402

# Countryside, no settlement, north of the Tragoth (so e002 is checked).
OPEN = "1301"
# Ogon: a town, and one of the two hexes e002 looks hardest in.
TOWN = "0101"
SOUTH = "1010"                     # south of the Tragoth: no e002
DESERT = None                      # filled in below, if the map has one


def _find_terrain(name, with_feature=None, without=None):
    book = harness.book()
    for hid, e in book.map["hexes"].items():
        if (e.get("terrain") or []) != [name]:
            continue
        feats = set(e.get("features") or [])
        if with_feature and with_feature not in feats:
            continue
        if without and feats & set(without):
            continue
        return hid
    return None


DESERT = _find_terrain("desert", without=("oasis",))
OASIS = _find_terrain("desert", with_feature="oasis")
MOUNTAIN = _find_terrain("mountains")


def game(hexid=OPEN, day_=1, gold=10, food=3, wits=4, followers=0, mounts=0,
         pay=0, wounds=0):
    g = harness.blank("day", gold=gold)
    g["hex"] = hexid
    g["day"] = day_
    g["food"] = food
    p = g["party"][0]
    p.update(cs=8, end=9, wits=wits, wounds=wounds)
    for i in range(followers):
        f = state.new_char(f"Lancer {i + 1}")
        f.update(cs=5, end=5, pay=pay, wounds=wounds)
        g["party"].append(f)
    for i in range(mounts):
        g["party"].append(state.new_char(f"Horse {i + 1}", "mount"))
    return g


class Day:
    """A machine that remembers what it has said."""

    def __init__(self, g, flow="day", **params):
        self.m = Machine(g, harness.book(), autosave=False)
        self.log = []
        self.turn = self.m.start(flow, **params)
        self.log += [e.text for e in self.turn.events]

    def answer(self, v):
        self.turn = self.m.answer(v)
        self.log += [e.text for e in self.turn.events]
        return self.turn

    def until(self, needle, limit=60, **auto):
        """Answer with sensible defaults until a prompt matches."""
        for _ in range(limit):
            if self.turn.done or needle in (self.ask.prompt or ""):
                return self.turn
            self.answer(self.default(auto))
        raise AssertionError(f"never reached {needle!r}; last {self.ask}")

    def default(self, auto):
        ask = self.ask
        if ask.kind in auto:
            return auto[ask.kind]
        if ask.kind == "die":
            return ask.spec["min"]
        if ask.kind == "confirm":
            return False
        if ask.kind == "choice":
            return ask.spec["options"][0]
        if ask.kind == "pick_char":
            return ask.spec["names"][0]
        return ask.spec.get("min") or 1

    @property
    def g(self):
        return self.m.g

    @property
    def ask(self):
        return self.turn.ask

    @property
    def notes(self):
        return " | ".join(self.log)


class TestSetup(unittest.TestCase):
    """r202, r225 and e001, walked with no judgment in any of it."""

    def blank(self):
        g = game()
        g["party"][0]["wits"] = None
        g["gold"] = 0
        g["hex"] = None
        return g

    def test_a_blank_sheet_runs_setup_first(self):
        d = Day(self.blank())
        self.assertEqual(d.ask.why, "r202")

    def test_a_wit_and_wiles_roll_of_one_counts_as_two(self):
        d = Day(self.blank())
        d.answer(1)
        self.assertEqual(d.g["party"][0]["wits"], 2)
        self.assertIn("a 1 counts as 2", d.notes)

    def test_any_other_roll_stands(self):
        d = Day(self.blank())
        d.answer(5)
        self.assertEqual(d.g["party"][0]["wits"], 5)

    def test_the_prince_gets_his_printed_stats(self):
        d = Day(self.blank())
        d.answer(3)
        p = d.g["party"][0]
        self.assertEqual((p["cs"], p["end"]), (8, 9))

    def test_starting_gold_comes_off_the_r226_grid(self):
        rows = procedures.treasure_rows(harness.book())
        for die in range(1, 7):
            d = Day(self.blank())
            d.answer(4)                    # wit & wiles
            d.answer(die)                  # gold
            self.assertEqual(d.g["gold"], int(rows["2"][die - 1]), f"die {die}")

    def test_the_caravan_sets_the_starting_hex(self):
        d = Day(self.blank())
        d.answer(4)
        d.answer(3)
        d.answer(4)                        # destination 4 is hex 1301
        self.assertEqual(d.g["hex"], "1301")

    def test_the_caravan_typo_is_corrected_and_said_so(self):
        # e001 prints 0801 for destination 3; the ruins are in 0901.
        d = Day(self.blank())
        d.answer(4)
        d.answer(3)
        d.answer(3)
        self.assertEqual(d.g["hex"], "0901")
        self.assertIn("typo", d.notes)

    def test_setup_does_not_run_twice(self):
        d = Day(game())                    # wits already set
        self.assertEqual(d.ask.why, "r203")


class TestActions(unittest.TestCase):
    def labels(self, hexid):
        return Day(game(hexid)).ask.spec["options"]

    def test_open_country_offers_travel_rest_and_cache(self):
        self.assertEqual(self.labels(OPEN),
                         ["Travel to a new hex", "Rest here",
                          "Search for a cache"])

    def test_a_town_offers_the_settlement_actions(self):
        labels = self.labels(TOWN)
        self.assertIn("Seek news and information", labels)
        self.assertIn("Seek to hire followers", labels)
        self.assertIn("Seek an audience with the local lord", labels)

    def test_a_temple_offers_an_offering_and_a_town_does_not(self):
        temple = _find_terrain_with("temple")
        self.assertIn("Submit an offering", self.labels(temple))
        self.assertNotIn("Submit an offering", self.labels(TOWN))

    def test_ruins_are_only_offered_where_there_are_ruins(self):
        ruins = _find_terrain_with("ruins")
        self.assertIn("Search the ruins", self.labels(ruins))
        self.assertNotIn("Search the ruins", self.labels(OPEN))

    def test_an_unimplemented_action_refuses_by_name(self):
        d = Day(game(TOWN))
        with self.assertRaises(Refuse) as cm:
            d.answer("Seek news and information")
        self.assertIn("r209", str(cm.exception))
        self.assertIn("plan 07", str(cm.exception))

    def test_only_one_action_a_day(self):
        d = Day(game(OPEN))
        d.answer("Rest here")
        d.until("hunt", limit=10)          # straight on to dusk


def _find_terrain_with(feature):
    for hid, e in harness.book().map["hexes"].items():
        if feature in (e.get("features") or []) and len(e.get("terrain") or []) == 1:
            return hid
    raise AssertionError(f"no hex with {feature}")


class TestRest(unittest.TestCase):
    def test_a_quiet_day_heals_one_wound_each(self):
        d = Day(game(OPEN, wounds=3, followers=1))
        d.answer("Rest here")
        d.answer(2)                        # no encounter
        self.assertEqual(d.g["party"][0]["wounds"], 2)
        self.assertEqual(d.g["party"][1]["wounds"], 2)

    def test_resting_still_checks_for_an_encounter(self):
        d = Day(game(OPEN, wounds=1))
        d.answer("Rest here")
        self.assertIn("resting in", d.ask.prompt)
        self.assertEqual(d.ask.spec["die"], "2d6")

    def test_a_fight_stops_the_healing(self):
        g = game(OPEN, wounds=3)
        d = Day(g)
        d.answer("Rest here")
        d.g.setdefault("day_flags", {})["fought"] = True
        d.answer(2)
        self.assertEqual(d.g["party"][0]["wounds"], 3)

    def test_healing_never_goes_below_nothing(self):
        d = Day(game(OPEN, wounds=0))
        d.answer("Rest here")
        d.answer(2)
        self.assertEqual(d.g["party"][0]["wounds"], 0)


class TestE002(unittest.TestCase):
    def test_north_of_the_tragoth_is_checked(self):
        d = Day(game(OPEN))
        d.answer("Rest here")
        d.answer(2)
        self.assertEqual(d.ask.why, "e002")

    def test_south_of_the_tragoth_is_not(self):
        d = Day(game(SOUTH))
        d.answer("Rest here")
        d.answer(2)
        self.assertIn("south of the Tragoth", d.notes)
        self.assertNotEqual(d.ask.why, "e002")

    def test_the_player_is_never_asked_which_side_they_are_on(self):
        d = Day(game(SOUTH))
        d.answer("Rest here")
        d.answer(2)
        self.assertNotIn("Tragoth", d.ask.prompt or "")

    def test_the_two_starting_towns_add_one(self):
        d = Day(game(TOWN))
        d.answer("Rest here")
        d.answer(2)
        self.assertIn("+ 1 for 0101", d.notes)

    def test_a_low_roll_is_no_event(self):
        d = Day(game(OPEN))
        d.answer("Rest here")
        d.answer(2)
        d.answer(1)                        # 1 - 3 = -2
        self.assertIn("no event", d.notes)

    def test_a_high_roll_brings_the_guard(self):
        d = Day(game(OPEN))
        d.answer("Rest here")
        d.answer(2)
        d.answer(6)                        # 6 - 3 = 3
        self.assertIn("Riders are on the road", d.notes)


class TestHunt(unittest.TestCase):
    def test_the_level_is_skill_plus_half_current_endurance(self):
        hunter = state.new_char("Cal", "player")
        hunter.update(cs=8, end=9, wounds=0)
        level, bits = day.hunt_level(hunter, [])
        self.assertEqual(level, 12)        # 8 + 9//2
        hunter["wounds"] = 3
        level, _ = day.hunt_level(hunter, [])
        self.assertEqual(level, 11)        # 8 + (9-3)//2

    def test_a_guide_adds_one(self):
        hunter = state.new_char("Guide", "follower")
        hunter.update(cs=4, end=6, guide=True)
        self.assertEqual(day.hunt_level(hunter, [])[0], 8)      # 4 + 3 + 1

    def test_an_extra_hunter_adds_one_and_a_guide_two(self):
        hunter = state.new_char("Cal", "player")
        hunter.update(cs=8, end=9)
        plain = state.new_char("Lancer")
        plain.update(cs=2, end=2)
        guide = state.new_char("Scout")
        guide.update(cs=2, end=2, guide=True)
        self.assertEqual(day.hunt_level(hunter, [plain])[0], 13)
        self.assertEqual(day.hunt_level(hunter, [guide])[0], 14)

    def test_hunting_is_prohibited_in_a_town(self):
        d = Day(game(TOWN))
        d.answer("Rest here")
        d.answer(2)
        d.answer(1)                        # e002, no event
        self.assertIn("no hunting here", d.notes)

    def test_a_successful_hunt_adds_food(self):
        d = Day(game(OPEN, food=0))
        d.answer("Rest here")
        d.answer(2)
        d.answer(1)
        d.answer(True)                     # hunt
        d.answer(8)                        # level 12 - 8 = 4 units
        self.assertIn("brings back 4 food units", d.notes)
        # The meal follows straight on and eats one of them.
        self.assertEqual(d.g["food"], 3)

    def test_a_failed_hunt_brings_back_nothing(self):
        d = Day(game(OPEN, food=0))
        d.answer("Rest here")
        d.answer(2)
        d.answer(1)
        d.answer(True)
        d.answer(12)                       # a 12 also hurts him
        self.assertIn("hurt in the hunt", d.notes)

    def test_a_twelve_wounds_the_hunter(self):
        d = Day(game(OPEN, food=0))
        d.answer("Rest here")
        d.answer(2)
        d.answer(1)
        d.answer(True)
        d.answer(12)
        self.assertIn("How badly", d.ask.prompt)
        d.answer(3)
        self.assertEqual(d.g["party"][0]["wounds"], 3)

    def test_declining_the_hunt_is_not_recorded_as_done(self):
        # The meal may still need the food, so the offer must come round again.
        d = Day(game(OPEN, food=0))
        d.answer("Rest here")
        d.answer(2)
        d.answer(1)
        d.answer(False)
        self.assertNotIn("hunt", d.g["day_flags"]["done"])


class TestMeal(unittest.TestCase):
    def dusk_at(self, **kw):
        d = Day(game(**kw))
        d.answer("Rest here")
        d.answer(2)                        # rest encounter
        if d.ask and d.ask.why == "e002":
            d.answer(1)
        if d.ask and "hunt" in (d.ask.prompt or ""):
            d.answer(False)
        return d

    def test_one_unit_a_man(self):
        d = self.dusk_at(hexid=OPEN, food=5, followers=2)
        self.assertEqual(d.g["food"], 2)

    def test_mounts_that_can_forage_eat_nothing(self):
        d = self.dusk_at(hexid=OPEN, food=5, mounts=2)
        self.assertEqual(d.g["food"], 4)   # countryside: fodder

    def test_mounts_that_cannot_forage_eat_two_each(self):
        if MOUNTAIN is None:
            self.skipTest("no single-terrain mountain hex")
        d = self.dusk_at(hexid=MOUNTAIN, food=9, mounts=2)
        self.assertEqual(d.g["food"], 4)   # 1 man + 2 mounts x 2

    def test_a_waterless_desert_doubles_the_meal(self):
        if DESERT is None:
            self.skipTest("no desert hex without an oasis")
        d = self.dusk_at(hexid=DESERT, food=9)
        self.assertEqual(d.g["food"], 7)   # one man, doubled

    def test_an_oasis_has_water(self):
        if OASIS is None:
            self.skipTest("no desert hex with an oasis")
        d = self.dusk_at(hexid=OASIS, food=9)
        self.assertEqual(d.g["food"], 8)

    def test_the_arithmetic_matches_state_food_needed(self):
        g = game(OPEN, followers=2, mounts=1)
        need, _ = state.food_needed(g, fodder=False, water=True)
        self.assertEqual(need, 5)          # 3 men + 1 mount x 2
        need, _ = state.food_needed(g, fodder=True, water=False)
        self.assertEqual(need, 6)          # 3 men, doubled

    def test_a_settlement_offers_a_bought_meal(self):
        d = Day(game(TOWN, gold=20, food=0))
        d.answer("Rest here")
        d.answer(2)
        d.answer(1)                        # e002
        self.assertIn("Buy tonight's meals", d.ask.prompt)
        d.answer(True)
        self.assertEqual(d.g["gold"], 19)  # a gold a head, one man

    def test_too_little_food_puts_the_choice_to_the_player(self):
        d = self.dusk_at(hexid=OPEN, food=0, followers=2)
        self.assertIn("not enough to go round", d.notes)
        self.assertEqual(d.ask.spec["options"],
                         ["share out what there is", "everyone goes without"])

    def test_going_without_marks_everyone_starving(self):
        d = self.dusk_at(hexid=OPEN, food=0, followers=1)
        d.answer("everyone goes without")
        self.assertTrue(all(c["starve"] == 1 for c in d.g["party"]))

    def test_sharing_out_avoids_the_starvation_penalties(self):
        d = self.dusk_at(hexid=OPEN, food=1, followers=1)
        d.answer("share out what there is")
        self.assertTrue(all(c["starve"] == 0 for c in d.g["party"]))
        self.assertEqual(d.g["food"], 0)


class TestDesertion(unittest.TestCase):
    """r216a and r217 share the check: 2d6 less wit & wiles, 4 or more and he
    goes. The boundary is exactly 4."""

    def hungry(self, wits=4, followers=1):
        d = Day(game(OPEN, food=0, wits=wits, followers=followers))
        d.answer("Rest here")
        d.answer(2)
        d.answer(1)
        d.answer(False)                    # no hunt
        d.answer("everyone goes without")
        return d

    def test_exactly_four_deserts(self):
        d = self.hungry(wits=4)
        d.answer(8)                        # 8 - 4 = 4
        self.assertEqual([c["name"] for c in d.g["party"]], ["Cal Arath"])

    def test_three_stays(self):
        d = self.hungry(wits=4)
        d.answer(7)                        # 7 - 4 = 3
        self.assertIn("Lancer 1", [c["name"] for c in d.g["party"]])

    def test_a_higher_wit_and_wiles_keeps_them(self):
        d = self.hungry(wits=6)
        d.answer(9)                        # 9 - 6 = 3
        self.assertIn("Lancer 1", [c["name"] for c in d.g["party"]])

    def test_the_prince_is_never_rolled_for(self):
        d = self.hungry(wits=4, followers=1)
        self.assertNotIn("Cal Arath", d.ask.prompt)

    def test_each_follower_is_rolled_for_separately(self):
        d = self.hungry(wits=4, followers=2)
        d.answer(2)
        self.assertIn("Lancer 2", d.ask.prompt)


class TestWagesAndLodging(unittest.TestCase):
    def to_wages(self, **kw):
        d = Day(game(**kw))
        d.answer("Rest here")
        d.answer(2)
        if d.ask and d.ask.why == "e002":
            d.answer(1)
        if d.ask and "hunt" in (d.ask.prompt or ""):
            d.answer(False)
        return d

    def test_wages_are_paid_at_the_meal(self):
        d = self.to_wages(hexid=OPEN, gold=20, food=5, followers=1, pay=2)
        self.assertEqual(d.g["gold"], 18)

    def test_a_party_that_owes_nothing_says_nothing(self):
        d = self.to_wages(hexid=OPEN, gold=20, food=5)
        self.assertEqual(d.g["gold"], 20)

    def test_wages_that_cannot_be_paid_lose_the_henchmen(self):
        d = self.to_wages(hexid=OPEN, gold=1, food=5, followers=1, pay=2)
        self.assertEqual([c["name"] for c in d.g["party"]], ["Cal Arath"])
        self.assertIn("unpaid henchmen do not stay", d.notes)

    def test_pay_that_starts_tomorrow_is_not_due_tonight(self):
        g = game(OPEN, gold=20, food=5, followers=1, pay=2)
        g["party"][1]["pay_starts"] = 2
        d = Day(g)
        d.answer("Rest here")
        d.answer(2)
        d.answer(1)
        d.answer(False)
        self.assertEqual(d.g["gold"], 20)

    def test_lodging_is_offered_in_a_settlement(self):
        d = Day(game(TOWN, gold=20, food=5))
        d.answer("Rest here")
        d.answer(2)
        d.answer(1)                        # e002
        d.answer(False)                    # do not buy meals
        self.assertIn("Buy lodging", d.ask.prompt)

    def test_lodging_is_not_offered_in_open_country(self):
        d = self.to_wages(hexid=OPEN, gold=20, food=5)
        self.assertNotIn("lodging", (d.ask.prompt or "") if d.ask else "")

    def test_sleeping_rough_risks_the_followers(self):
        d = Day(game(TOWN, gold=20, food=5, followers=1))
        d.answer("Rest here")
        d.answer(2)
        d.answer(1)
        d.answer(False)                    # no bought meal
        d.answer(False)                    # no lodging
        self.assertIn("penurious", d.ask.prompt)

    def test_sleeping_rough_risks_the_mounts(self):
        d = Day(game(TOWN, gold=20, food=5, mounts=1))
        d.answer("Rest here")
        d.answer(2)
        d.answer(1)
        d.answer(False)
        d.answer(False)
        self.assertIn("still there at dawn", d.ask.prompt)
        d.answer(5)                        # 4+ and it is stolen
        self.assertEqual(len(d.g["party"]), 1)


class TestClockAndEnd(unittest.TestCase):
    def quiet_day(self, **kw):
        d = Day(game(**kw))
        d.answer("Rest here")
        d.answer(2)
        if d.ask and d.ask.why == "e002":
            d.answer(1)
        if d.ask and "hunt" in (d.ask.prompt or ""):
            d.answer(False)
        return d

    def test_the_day_advances_and_a_new_one_begins(self):
        d = self.quiet_day(hexid=OPEN, food=5)
        self.assertEqual(d.g["day"], 2)
        self.assertEqual(d.ask.why, "r203")          # dawn of day 2

    def test_the_journal_is_cleared_at_the_boundary(self):
        d = self.quiet_day(hexid=OPEN, food=5)
        self.assertEqual(d.m.eng["journal"], [])
        self.assertEqual(d.m.eng["day_start"]["day"], 2)

    def test_day_seventy_one_is_a_loss(self):
        d = self.quiet_day(hexid=OPEN, food=5, day_=70)
        self.assertTrue(d.turn.done)
        self.assertIsInstance(d.turn.result, EndGame)
        self.assertEqual(d.turn.result.result, "loss")

    def test_five_hundred_gold_in_the_northlands_wins(self):
        d = self.quiet_day(hexid=OPEN, food=5, gold=500)
        self.assertIsInstance(d.turn.result, EndGame)
        self.assertEqual(d.turn.result.result, "win")

    def test_four_hundred_and_ninety_nine_does_not(self):
        d = self.quiet_day(hexid=OPEN, food=5, gold=499)
        self.assertFalse(d.turn.done)

    def test_five_hundred_gold_south_of_the_river_does_not(self):
        d = self.quiet_day(hexid=SOUTH, food=5, gold=500)
        self.assertFalse(d.turn.done)

    def test_the_northlands_is_derived_not_hard_coded(self):
        hexes = procedures.hexes_of(harness.book())
        self.assertTrue(procedures.north_of_tragoth(hexes, OPEN))
        self.assertFalse(procedures.north_of_tragoth(hexes, SOUTH))

    def test_a_dead_prince_ends_it(self):
        g = game(OPEN, food=5)
        g["party"][0]["wounds"] = 9
        m = Machine(g, harness.book(), autosave=False)
        end = day.check_end(_ctx(m))
        self.assertIsInstance(end, EndGame)
        self.assertEqual(end.result, "loss")


def _ctx(m):
    from engine.ctx import Ctx
    return Ctx(m.g, m.book, [], sid="day")


class TestIdempotence(unittest.TestCase):
    def test_a_step_already_marked_is_not_run_again(self):
        g = game(OPEN, food=5)
        d = Day(g)
        d.answer("Rest here")
        d.answer(2)
        d.answer(1)                        # e002 done
        self.assertIn("e002", d.g["day_flags"]["done"])

    def test_the_flags_are_cleared_at_dawn(self):
        d = Day(game(OPEN, food=5))
        d.answer("Rest here")
        d.answer(2)
        d.answer(1)
        d.answer(False)
        self.assertEqual(d.g["day"], 2)
        self.assertEqual(d.g["day_flags"]["done"], [])


class TestHooks(unittest.TestCase):
    """Plan 06 subscribes here; the points exist now so nothing is retrofitted."""

    def test_every_named_hook_exists(self):
        self.assertEqual(set(day.HOOKS),
                         {"on_dawn", "on_dusk", "on_night", "on_enter_hex",
                          "on_settlement"})

    def test_a_registered_effect_fires_at_dawn(self):
        fired = []

        def effect(ctx):
            fired.append(ctx.day())
            if False:
                yield

        day.HOOKS["on_dawn"].append(effect)
        try:
            Day(game(OPEN, food=5))
        finally:
            day.HOOKS["on_dawn"].remove(effect)
        self.assertEqual(fired, [1])


class TestPersistence(unittest.TestCase):
    def test_a_day_in_progress_resumes(self):
        with harness.temp_game("day-resume", gold=10) as g:
            g["hex"] = OPEN
            g["food"] = 5
            m = Machine(g, harness.book())
            m.start("day")
            m.answer("Rest here")
            m.answer(2)
            live = m.ask
            m2 = Machine(harness.reload(g["name"]), harness.book())
            turn = m2.resume()
            self.assertEqual(turn.ask.prompt, live.prompt)
            self.assertEqual(turn.ask.why, live.why)


if __name__ == "__main__":
    unittest.main()
