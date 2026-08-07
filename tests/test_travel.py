"""Travel (r204), getting lost (r205) and the r207 table.

The single most likely bug in this plan is reading the wrong terrain: the lost
check uses the hex you LEAVE and the event check the hex you ENTER, with one
printed exception after a river crossing. That is asserted directly rather than
through a scenario.
"""

import unittest

import harness  # noqa: I001  - must precede the src imports; it sets the path

import procedures         # noqa: E402
import state              # noqa: E402
from engine import EndEvent, Machine       # noqa: E402
from engine.rules import travel            # noqa: E402
from procedures import Refuse                   # noqa: E402

# Chosen from the map so that from- and to- thresholds differ in both columns,
# which is what makes the asymmetry test sharp:
#   0104 countryside  lost 9+  event 9+
#   0204 hills        lost 8+  event 10+
FROM, TO = "0104", "0204"
PLAIN = ("0101", "0201")        # no river, no road
ROAD = ("0109", "0208")
RIVER = ("0101", "0102")
STRADDLE = "0807"               # the map records two terrains for it


def game(hexid=FROM, mounts=0, winged=False, guides=0, followers=0):
    g = harness.blank("travel")
    g["hex"] = hexid
    p = g["party"][0]
    p.update(cs=8, end=9, wits=4)
    for i in range(followers):
        f = state.new_char(f"Lancer {i + 1}")
        f.update(cs=5, end=5)
        g["party"].append(f)
    for i in range(guides):
        c = state.new_char(f"Guide {i + 1}")
        c.update(cs=4, end=5)
        c["guide"] = True
        g["party"].append(c)
    for i in range(mounts):
        m = state.new_char(f"Horse {i + 1}", "mount")
        m["winged"] = winged
        g["party"].append(m)
    return g


class Trip:
    """A machine that remembers everything it has said.

    `Machine.out` holds only the current segment - what the player has not yet
    seen - so a test that wants to assert on a note from three questions ago has
    to keep its own record.
    """

    def __init__(self, g, flow="travel", **params):
        self.m = Machine(g, harness.book(), autosave=False)
        self.log: list[str] = []
        self.turn = self.m.start(flow, **params)
        self.log += [e.text for e in self.turn.events]

    def answer(self, value):
        self.turn = self.m.answer(value)
        self.log += [e.text for e in self.turn.events]
        return self.turn

    @property
    def g(self):
        return self.m.g

    @property
    def ask(self):
        return self.turn.ask

    @property
    def done(self):
        return self.turn.done


def start(g, flow="travel", **params):
    t = Trip(g, flow, **params)
    return t, t.turn


def heading_to(here, there):
    """The choice label the flow will offer for this neighbour."""
    d = procedures.direction_between(here, there)
    try:
        terrain = procedures.hex_terrain(harness.book(), there)
    except Refuse:
        terrain = "terrain unclear"
    return f"{d} {there} ({terrain})"


def notes(trip):
    return " | ".join(trip.log)


class TestSpeeds(unittest.TestCase):
    def test_on_foot_is_one_hex_and_not_offered_a_choice(self):
        m, turn = start(game())
        self.assertEqual(turn.ask.prompt, "Travel where?")   # no speed question
        self.assertIn("no other speed", notes(m))

    def test_one_man_on_foot_keeps_the_whole_party_on_foot(self):
        # Two men, one horse: somebody walks, so the party walks (r204a).
        m, turn = start(game(followers=1, mounts=1))
        self.assertEqual(turn.ask.prompt, "Travel where?")

    def test_a_mounted_party_chooses_one_or_two(self):
        m, turn = start(game(mounts=1))
        self.assertEqual(turn.ask.spec["options"],
                         ["ride 2 hexes", "ride 1 hex"])

    def test_a_winged_party_may_fly_or_short_hop(self):
        m, turn = start(game(mounts=1, winged=True))
        self.assertEqual(turn.ask.spec["options"],
                         ["fly, up to 3 hexes", "ride 2 hexes", "ride 1 hex"])


class TestTerrainSourcing(unittest.TestCase):
    """The asymmetry, asserted head on."""

    def move(self, lost_roll, event_roll=2):
        m, turn = start(game(FROM))
        turn = m.answer(heading_to(FROM, TO))
        turn = m.answer(lost_roll)
        return m, turn

    def test_the_lost_check_reads_the_hex_being_left(self):
        # 8 is lost in hills (8+) and not in countryside (9+). The party is
        # leaving countryside, so it must not be lost.
        m, turn = self.move(8)
        self.assertIn("Countryside: lost on 9+", notes(m))
        self.assertIn("not lost", notes(m))
        self.assertEqual(m.g["hex"], TO)

    def test_the_event_check_reads_the_hex_being_entered(self):
        # 9 is an event in countryside (9+) and not in hills (10+). The party
        # is entering hills, so nothing must happen.
        m, turn = self.move(2)
        turn = m.answer(9)
        self.assertIn("Hills: event on 10+", notes(m))
        self.assertIn("no event", notes(m))

    def test_the_two_are_not_the_same_row(self):
        m, turn = self.move(2)
        turn = m.answer(9)
        text = notes(m)
        self.assertIn("Countryside: lost", text)
        self.assertIn("Hills: event", text)


class TestThresholds(unittest.TestCase):
    def test_every_row_triggers_at_its_number_and_not_one_below(self):
        book = harness.book()
        for key, row in book.travel["terrain"].items():
            thr = procedures.threshold(row["lost_on"])
            if thr is not None:
                self.assertTrue(procedures.lost_verdict(book, key, thr)[0], key)
                self.assertFalse(
                    procedures.lost_verdict(book, key, thr - 1)[0], key)
            ethr = procedures.threshold(row["event_on"])
            self.assertTrue(procedures.event_verdict(book, key, ethr)[0], key)
            self.assertFalse(procedures.event_verdict(book, key, ethr - 1)[0],
                             key)

    def test_the_road_can_never_get_lost(self):
        lost, thr, net = procedures.lost_verdict(harness.book(), "on road", 12)
        self.assertIsNone(lost)

    def test_the_guide_takes_one_off(self):
        book = harness.book()
        self.assertEqual(procedures.lost_verdict(book, "forest", 8)[2], 8)
        self.assertEqual(procedures.lost_verdict(book, "forest", 8, True)[2], 7)
        self.assertTrue(procedures.lost_verdict(book, "forest", 8)[0])
        self.assertFalse(procedures.lost_verdict(book, "forest", 8, True)[0])

    def test_the_prose_and_the_verdict_agree(self):
        """`check_lost` prints what `lost_verdict` decides - one implementation."""
        book = harness.book()
        for key in book.travel["terrain"]:
            for total in range(2, 13):
                for guide in (False, True):
                    lost, _, _ = procedures.lost_verdict(book, key, total, guide)
                    text = procedures.check_lost(book, key, total, guide)
                    if lost is None:
                        self.assertIn("cannot get lost", text)
                    elif lost:
                        self.assertIn("-> LOST", text)
                    else:
                        self.assertIn("not lost", text)


class TestGettingLost(unittest.TestCase):
    def test_being_lost_keeps_the_party_put_but_still_checks_the_event(self):
        m, turn = start(game(FROM))
        turn = m.answer(heading_to(FROM, TO))
        turn = m.answer(12)                      # lost leaving countryside
        self.assertIn("-> LOST", notes(m))
        self.assertEqual(m.g["hex"], FROM)       # stayed put
        self.assertIn("Travel event check", turn.ask.prompt)

    def test_the_event_after_being_lost_is_the_hex_you_tried_to_enter(self):
        m, turn = start(game(FROM))
        m.answer(heading_to(FROM, TO))
        turn = m.answer(12)
        turn = m.answer(2)
        self.assertIn("Hills: event", notes(m))

    def test_being_lost_ends_the_day(self):
        m, turn = start(game(FROM, mounts=1))
        m.answer("ride 2 hexes")
        m.answer(heading_to(FROM, TO))
        m.answer(12)                             # lost
        turn = m.answer(2)                       # no event
        self.assertTrue(turn.done)
        self.assertEqual(m.g["hex"], FROM)


class TestGuide(unittest.TestCase):
    def test_a_single_guide_is_not_asked_about(self):
        m, turn = start(game(FROM, guides=1))
        self.assertEqual(turn.ask.prompt, "Travel where?")
        self.assertIn("guides today", notes(m))

    def test_two_guides_means_choosing_one(self):
        m, turn = start(game(FROM, guides=2))
        self.assertEqual(turn.ask.kind, "pick_char")
        self.assertEqual(turn.ask.spec["names"], ["Guide 1", "Guide 2"])

    def test_the_minus_one_is_applied(self):
        m, turn = start(game(FROM, guides=1))
        m.answer(heading_to(FROM, TO))
        turn = m.answer(9)          # 9 - 1 = 8, under countryside's 9+
        self.assertIn("9 - 1 for the guide = 8", notes(m))
        self.assertIn("not lost", notes(m))

    def test_no_desertion_roll_when_not_lost(self):
        m, turn = start(game(FROM, guides=1))
        m.answer(heading_to(FROM, TO))
        turn = m.answer(4)
        self.assertIn("Travel event check", turn.ask.prompt)

    def test_the_guide_may_desert_after_failing(self):
        m, turn = start(game(FROM, guides=1))
        m.answer(heading_to(FROM, TO))
        turn = m.answer(12)                     # lost even with the -1
        self.assertIn("stay after failing", turn.ask.prompt)
        turn = m.answer(5)                      # 4+ and he is gone
        self.assertEqual([c["name"] for c in m.g["party"]], ["Cal Arath"])

    def test_a_guide_who_rolls_low_stays(self):
        m, turn = start(game(FROM, guides=1))
        m.answer(heading_to(FROM, TO))
        m.answer(12)
        turn = m.answer(3)
        self.assertIn("Guide 1", [c["name"] for c in m.g["party"]])

    def test_only_one_guide_leaves(self):
        m, turn = start(game(FROM, guides=2))
        m.answer("Guide 1")
        m.answer(heading_to(FROM, TO))
        m.answer(12)
        turn = m.answer(6)
        left = [c["name"] for c in m.g["party"] if c.get("guide")]
        self.assertEqual(left, ["Guide 2"])


class TestRoad(unittest.TestCase):
    def test_a_road_move_never_rolls_for_lost(self):
        here, there = ROAD
        m, turn = start(game(here))
        turn = m.answer(heading_to(here, there))
        self.assertIn("no lost check", notes(m))
        self.assertIn("Travel event check, on the road", turn.ask.prompt)
        self.assertEqual(m.g["hex"], there)

    def test_no_road_event_means_the_terrain_line_is_consulted(self):
        here, there = ROAD
        m, turn = start(game(here))
        m.answer(heading_to(here, there))
        turn = m.answer(2)                      # no road event
        self.assertIn("no road event having occurred", turn.ask.prompt)

    def test_a_road_event_makes_the_terrain_check_optional(self):
        here, there = ROAD
        m, turn = start(game(here))
        m.answer(heading_to(here, there))
        m.answer(12)                            # a road event occurs
        turn = m.answer(1)                      # which one
        # The event resolves, then the terrain line is offered, not forced.
        for _ in range(30):
            if turn.done or turn.ask.kind == "confirm":
                break
            ask = turn.ask
            turn = m.answer(ask.spec["max"] if ask.kind == "die" else
                            ask.spec["options"][0] if ask.kind == "choice" else
                            ask.spec["names"][0] if ask.kind == "pick_char" else
                            1 if ask.kind == "number" else False)
        if not turn.done:
            self.assertIn("terrain line", turn.ask.prompt)


class TestRiver(unittest.TestCase):
    def test_the_crossing_has_its_own_lost_check_first(self):
        here, there = RIVER
        m, turn = start(game(here))
        turn = m.answer(heading_to(here, there))
        self.assertIn("over the river", turn.ask.prompt)
        turn = m.answer(4)                      # cross river lost 8+, so fine
        self.assertIn("Cross River: lost on 8+", notes(m))
        self.assertIn("crossing the river", turn.ask.prompt)

    def test_being_lost_crossing_means_no_event_at_all(self):
        here, there = RIVER
        m, turn = start(game(here))
        m.answer(heading_to(here, there))
        turn = m.answer(12)                     # lost finding a crossing
        self.assertTrue(turn.done)
        self.assertEqual(m.g["hex"], here)
        self.assertIn("no travel event at all", notes(m))

    def test_the_far_bank_is_checked_against_the_hex_entered(self):
        # r205d, against the general rule: the second lost check uses the
        # terrain being ENTERED.
        here, there = RIVER
        m, turn = start(game(here))
        m.answer(heading_to(here, there))
        m.answer(4)                             # crossed
        turn = m.answer(2)                      # no river event
        self.assertIn("entering the far hex", turn.ask.prompt)

    def test_lost_on_the_far_bank_ends_where_you_started_but_across(self):
        here, there = RIVER
        m, turn = start(game(here))
        m.answer(heading_to(here, there))
        m.answer(4)
        m.answer(2)
        turn = m.answer(12)                     # lost entering the far hex
        self.assertEqual(m.g["hex"], here)
        self.assertEqual(m.g["day_flags"]["across_river"], there)
        self.assertIn("Travel event check", turn.ask.prompt)

    def test_a_clean_crossing_moves_the_party(self):
        here, there = RIVER
        m, turn = start(game(here))
        m.answer(heading_to(here, there))
        m.answer(4)
        m.answer(2)
        m.answer(2)                             # not lost on the far bank
        turn = m.answer(2)                      # no event there either
        self.assertEqual(m.g["hex"], there)
        self.assertTrue(turn.done)


class TestAirborne(unittest.TestCase):
    def fly(self, hexid=FROM):
        m, turn = start(game(hexid, mounts=1, winged=True))
        turn = m.answer("fly, up to 3 hexes")
        return m, turn

    def test_flying_uses_the_airborne_row_for_lost(self):
        m, turn = self.fly()
        turn = m.answer(heading_to(FROM, TO))
        turn = m.answer(5)
        self.assertIn("Airborne: lost on 12+", notes(m))

    def test_flying_crosses_a_river_without_a_crossing_check(self):
        here, there = RIVER
        m, turn = self.fly(here)
        turn = m.answer(heading_to(here, there))
        self.assertIn("flying out of the hex", turn.ask.prompt)
        self.assertNotIn("Cross River", notes(m))

    def test_a_lost_flight_may_drift(self):
        m, turn = self.fly()
        m.answer(heading_to(FROM, TO))
        turn = m.answer(12)                     # airborne lost on 12+
        self.assertIn("drift", turn.ask.prompt.lower())
        turn = m.answer(5)                      # 4+ means it drifts
        self.assertIn("Which way", turn.ask.prompt)

    def test_drift_moves_one_hex_from_where_you_were_headed(self):
        m, turn = self.fly()
        m.answer(heading_to(FROM, TO))
        m.answer(12)
        m.answer(5)
        turn = m.answer(4)                      # S
        blown = procedures.neighbours(*procedures.parse_hex(TO))["S"]
        self.assertEqual(m.g["hex"], blown)

    def test_no_drift_on_a_low_roll(self):
        m, turn = self.fly()
        m.answer(heading_to(FROM, TO))
        m.answer(12)
        turn = m.answer(2)                      # under 4, no drift
        self.assertEqual(m.g["hex"], TO)
        self.assertIn("no drift", notes(m))

    def test_the_landing_hex_gets_a_terrain_check_when_nothing_happened_aloft(self):
        """r204d puts this on the last hex of the day - and the last hex is not
        known until the flight stops, which the player may do early."""
        m, turn = self.fly()
        m.answer(heading_to(FROM, TO))
        m.answer(5)                             # not lost
        turn = m.answer(2)                      # no airborne event
        self.assertIn("Travel on?", turn.ask.prompt)
        turn = m.answer(False)                  # the flight ends here
        self.assertIn("in the hex you land in", turn.ask.prompt)

    def test_no_landing_check_when_something_happened_aloft(self):
        m, turn = self.fly()
        m.answer(heading_to(FROM, TO))
        m.answer(5)
        m.answer(12)                            # an airborne event occurs
        turn = m.answer(1)                      # which one
        for _ in range(40):
            if turn.done or "land in" in (turn.ask.prompt or ""):
                break
            ask = turn.ask
            turn = m.answer(ask.spec["max"] if ask.kind == "die" else
                            ask.spec["options"][0] if ask.kind == "choice" else
                            ask.spec["names"][0] if ask.kind == "pick_char" else
                            2 if ask.kind == "number" else False)
        if not turn.done:
            self.assertNotIn("land in", turn.ask.prompt)

    def test_a_lost_flight_still_gets_its_landing_check(self):
        m, turn = self.fly()
        m.answer(heading_to(FROM, TO))
        m.answer(12)                            # lost aloft
        m.answer(2)                             # no drift
        turn = m.answer(2)                      # no airborne event
        self.assertIn("in the hex you land in", turn.ask.prompt)


class TestEvents(unittest.TestCase):
    def test_an_event_runs_the_section_it_names(self):
        m, turn = start(game(FROM))
        m.answer(heading_to(FROM, TO))
        m.answer(2)                             # not lost
        turn = m.answer(12)                     # hills event on 10+
        self.assertEqual(turn.ask.prompt, "Which event?")
        refs = harness.book().travel["terrain"]["hills"]["event_refs"]
        turn = m.answer(1)
        self.assertIn(refs[0], notes(m))

    def test_an_event_that_takes_the_day_stops_the_movement(self):
        self.assertTrue(travel.spends_day(EndEvent(time_cost="rest_of_day")))

    def test_talk_and_a_clean_kill_leave_the_day_alive(self):
        self.assertFalse(travel.spends_day(EndEvent(time_cost="minutes")))
        self.assertFalse(travel.spends_day(EndEvent(time_cost="none")))
        self.assertFalse(travel.spends_day(None))


class TestRefusals(unittest.TestCase):
    def test_a_straddle_hex_refuses_rather_than_picking_a_terrain(self):
        with self.assertRaises(Refuse) as cm:
            procedures.hex_terrain(harness.book(), STRADDLE)
        self.assertIn("straddles", str(cm.exception))

    def test_a_straddle_neighbour_is_offered_but_says_so(self):
        here = next(h for h, d in
                    procedures.neighbours(
                        *procedures.parse_hex(STRADDLE)).items()
                    if procedures.on_map(harness.book(), d))
        there = procedures.neighbours(*procedures.parse_hex(STRADDLE))[here]
        m, turn = start(game(there))
        labels = turn.ask.spec["options"]
        self.assertTrue(any("terrain unclear" in o for o in labels),
                        f"{STRADDLE} should be offered as unclear: {labels}")

    def test_moving_into_a_straddle_hex_refuses(self):
        nbrs = procedures.neighbours(*procedures.parse_hex(STRADDLE))
        start_hex = next(h for h in nbrs.values()
                         if procedures.on_map(harness.book(), h))
        m, turn = start(game(start_hex))
        label = heading_to(start_hex, STRADDLE)
        if label not in turn.ask.spec["options"]:
            self.skipTest("not adjacent in the offered set")
        with self.assertRaises(Refuse):
            turn = m.answer(label)
            for _ in range(6):
                if turn.done:
                    break
                turn = m.answer(2)

    def test_a_party_with_no_hex_refuses(self):
        g = game()
        g["hex"] = None
        with self.assertRaises(Refuse) as cm:
            start(g)
        self.assertIn("not on the sheet", str(cm.exception))


class TestMapInvariants(unittest.TestCase):
    """Regression, from CLAUDE.md: a re-transcribed map that moved an N edge
    into rows 01-02 would move the Tragoth."""

    def test_0101_is_north_of_the_tragoth_and_0102_is_south(self):
        hexes = procedures.hexes_of(harness.book())
        self.assertTrue(procedures.north_of_tragoth(hexes, "0101"))
        self.assertFalse(procedures.north_of_tragoth(hexes, "0102"))

    def test_1401_is_south_despite_being_in_row_01(self):
        hexes = procedures.hexes_of(harness.book())
        self.assertFalse(procedures.north_of_tragoth(hexes, "1401"))


class TestMultiHex(unittest.TestCase):
    def test_two_hexes_run_both_sets_of_checks(self):
        m, turn = start(game(FROM, mounts=1))
        m.answer("ride 2 hexes")
        m.answer(heading_to(FROM, TO))
        m.answer(2)                             # not lost
        turn = m.answer(2)                      # no event
        self.assertIn("Travel on?", turn.ask.prompt)
        turn = m.answer(True)
        self.assertEqual(turn.ask.prompt, "Travel where?")

    def test_stopping_early_is_allowed(self):
        m, turn = start(game(FROM, mounts=1))
        m.answer("ride 2 hexes")
        m.answer(heading_to(FROM, TO))
        m.answer(2)
        m.answer(2)
        turn = m.answer(False)                  # do not travel on
        self.assertTrue(turn.done)
        self.assertEqual(m.g["hex"], TO)

    def test_the_action_is_recorded(self):
        m, turn = start(game(FROM))
        m.answer(heading_to(FROM, TO))
        m.answer(2)
        turn = m.answer(2)
        self.assertTrue(turn.done)
        self.assertEqual(m.g["day_flags"]["action"], "travel")


if __name__ == "__main__":
    unittest.main()
