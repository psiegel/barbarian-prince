"""The machine: driving, suspending, persisting, replaying, undoing.

These are the tests that make decision D1 real. If a day's journal does not
replay to the same sheet, every save in the game is quietly wrong, so the
round-trip is asserted directly rather than through a scenario.
"""

import unittest

import harness
from engine import EndEvent, machine as machine_mod
from engine.sections import REGISTRY, section
from engine.types import Ask
from state import Refuse

ANSWERS = ["fight", 5, True, "Cal", 3]


class TestDriving(unittest.TestCase):
    def test_runs_to_a_terminal_outcome(self):
        with harness.temp_game("mach-run") as g:
            m = harness.machine(g)
            turn = harness.play(m, ANSWERS, flow="demo")
            self.assertTrue(turn.done)
            self.assertIsInstance(turn.result, EndEvent)

    def test_asks_in_order_and_types(self):
        with harness.temp_game("mach-order") as g:
            m = harness.machine(g)
            kinds, turn = [], m.start("demo")
            for a in ANSWERS:
                kinds.append(turn.ask.kind)
                turn = m.answer(a)
            self.assertEqual(kinds,
                             ["choice", "die", "confirm", "pick_char", "die"])

    def test_mutations_land_on_the_sheet(self):
        with harness.temp_game("mach-mutate") as g:
            m = harness.machine(g)
            harness.play(m, ANSWERS, flow="demo")
            self.assertEqual(m.g["party"][0]["wounds"], 1)

    def test_events_are_not_repeated_across_turns(self):
        with harness.temp_game("mach-events") as g:
            m = harness.machine(g)
            turn = m.start("demo")
            first = [e.text for e in turn.events]
            self.assertTrue(any(e.voice for e in turn.events))
            turn = m.answer("fight")
            second = [e.text for e in turn.events]
            self.assertFalse(set(first) & set(second),
                             "an event the player already saw came back")


class TestPersistence(unittest.TestCase):
    def test_resume_lands_on_the_same_question(self):
        with harness.temp_game("mach-resume") as g:
            m = harness.machine(g)
            turn = harness.play(m, ANSWERS[:2], flow="demo")
            live_ask, live_sheet = turn.ask, harness.sheet_of(m.g)

            # A fresh process: read the file, replay the day.
            g2 = harness.reload(g["name"])
            m2 = harness.machine(g2)
            turn2 = m2.resume()

            self.assertEqual(turn2.ask.kind, live_ask.kind)
            self.assertEqual(turn2.ask.prompt, live_ask.prompt)
            self.assertEqual(turn2.ask.spec, live_ask.spec)
            self.assertEqual(harness.sheet_of(m2.g), live_sheet)

    def test_resume_shows_only_the_current_segment(self):
        with harness.temp_game("mach-segment") as g:
            m = harness.machine(g)
            turn = harness.play(m, ANSWERS[:3], flow="demo")
            live = [e.text for e in turn.events]
            m2 = harness.machine(harness.reload(g["name"]))
            self.assertEqual([e.text for e in m2.resume().events], live)

    def test_replay_is_deterministic(self):
        with harness.temp_game("mach-determ") as g:
            m = harness.machine(g)
            harness.play(m, ANSWERS, flow="demo")
            first = harness.sheet_of(m.g)
            for _ in range(3):
                m.resume()
                self.assertEqual(harness.sheet_of(m.g), first)

    def test_commit_makes_the_current_sheet_the_new_dawn(self):
        with harness.temp_game("mach-commit") as g:
            m = harness.machine(g)
            harness.play(m, ANSWERS, flow="demo")
            m.commit()
            self.assertEqual(m.eng["journal"], [])
            self.assertEqual(m.eng["day_start"]["party"][0]["wounds"], 1)

    def test_empty_journal_refreshes_dawn(self):
        # So that using `bp` on a save between days cannot leave day_start stale.
        with harness.temp_game("mach-dawn") as g:
            harness.machine(g)                      # writes day_start
            g["gold"] = 999                         # as if `bp gold +989` ran
            import state
            state.write_game(g)
            m2 = harness.machine(harness.reload(g["name"]))
            self.assertEqual(m2.eng["day_start"]["gold"], 999)


class TestUndo(unittest.TestCase):
    def test_undo_returns_to_the_previous_question(self):
        with harness.temp_game("mach-undo") as g:
            m = harness.machine(g)
            m.start("demo")
            m.answer("fight")
            before = m.ask
            m.answer(5)
            self.assertNotEqual(m.ask.kind + m.ask.prompt,
                                before.kind + before.prompt)
            m.undo()
            self.assertEqual(m.ask.prompt, before.prompt)
            self.assertEqual(m.ask.kind, before.kind)

    def test_undo_rewinds_the_sheet(self):
        with harness.temp_game("mach-undo-sheet") as g:
            m = harness.machine(g)
            harness.play(m, ANSWERS, flow="demo")
            self.assertEqual(m.g["party"][0]["wounds"], 1)
            m.undo()            # un-answer the direction die
            m.undo()            # un-answer "who was wounded"
            self.assertEqual(m.g["party"][0]["wounds"], 0)

    def test_undo_at_dawn_returns_none(self):
        with harness.temp_game("mach-undo-dawn") as g:
            m = harness.machine(g)
            m.start("demo")
            self.assertIsNone(m.undo())


class TestHandlerContract(unittest.TestCase):
    """A handler that breaks decision D2 or D3 must fail loudly, not quietly."""

    def setUp(self):
        self._added = []

    def tearDown(self):
        for k in self._added:
            REGISTRY.pop(k, None)

    def add(self, name, fn):
        self._added.append(name)
        section(name)(fn)

    def test_yielding_something_other_than_an_ask_is_an_error(self):
        def bad(ctx):
            yield "roll a die please"
        self.add("t-badyield", bad)
        with harness.temp_game("mach-badyield") as g:
            with self.assertRaises(RuntimeError) as cm:
                harness.machine(g).start("t-badyield")
            self.assertIn("may only yield", str(cm.exception))

    def test_returning_something_other_than_a_verb_is_an_error(self):
        def bad(ctx):
            if False:
                yield
            return "done"
        self.add("t-badreturn", bad)
        with harness.temp_game("mach-badreturn") as g:
            with self.assertRaises(RuntimeError) as cm:
                harness.machine(g).start("t-badreturn")
            self.assertIn("must return one of the ctx verbs", str(cm.exception))

    def test_a_non_generator_handler_is_an_error(self):
        self.add("t-plainfn", lambda ctx: None)
        with harness.temp_game("mach-plainfn") as g:
            with self.assertRaises(RuntimeError) as cm:
                harness.machine(g).start("t-plainfn")
            self.assertIn("not a generator", str(cm.exception))

    def test_returning_nothing_ends_the_event(self):
        def quiet(ctx):
            if False:
                yield
        self.add("t-quiet", quiet)
        with harness.temp_game("mach-quiet") as g:
            turn = harness.machine(g).start("t-quiet")
            self.assertIsInstance(turn.result, EndEvent)

    def test_a_loop_that_never_asks_is_caught(self):
        def spin(ctx):
            if False:
                yield
            return ctx.goto("t-spin")
        self.add("t-spin", spin)
        with harness.temp_game("mach-spin") as g:
            with self.assertRaises(Refuse) as cm:
                harness.machine(g).start("t-spin")
            self.assertIn("that is a loop", str(cm.exception))

    def test_retry_without_an_option_frame_refuses(self):
        def gives_up(ctx):
            if False:
                yield
            return ctx.retry("no hiding place")
        self.add("t-retry", gives_up)
        with harness.temp_game("mach-retry") as g:
            with self.assertRaises(Refuse) as cm:
                harness.machine(g).start("t-retry")
            self.assertIn("option list", str(cm.exception))


class TestTailCallsAndSubflows(unittest.TestCase):
    def setUp(self):
        self._added = []

    def tearDown(self):
        for k in self._added:
            REGISTRY.pop(k, None)

    def add(self, name, fn):
        self._added.append(name)
        section(name)(fn)

    def test_goto_replaces_the_frame_and_carries_params(self):
        seen = {}

        def start(ctx):
            if False:
                yield
            return ctx.goto("t-target", mod=+1, amount=15)

        def target(ctx):
            if False:
                yield
            seen["mod"] = ctx.mod
            seen["amount"] = ctx.param("amount")
            seen["sid"] = ctx.sid
            return ctx.end_event()

        self.add("t-start", start)
        self.add("t-target", target)
        with harness.temp_game("mach-goto") as g:
            harness.machine(g).start("t-start")
        self.assertEqual(seen, {"mod": 1, "amount": 15, "sid": "t-target"})

    def test_invoke_returns_the_subflow_outcome_to_the_caller(self):
        got = {}

        def caller(ctx):
            out = yield ctx.invoke("t-sub")
            got["outcome"] = out
            return ctx.end_event()

        def sub(ctx):
            if False:
                yield
            return ctx.end_event(time_cost="minutes")

        self.add("t-caller", caller)
        self.add("t-sub", sub)
        with harness.temp_game("mach-invoke") as g:
            harness.machine(g).start("t-caller")
        self.assertEqual(got["outcome"].time_cost, "minutes")

    def test_need_refuses_when_a_parameter_was_not_supplied(self):
        def wants(ctx):
            if False:
                yield
            ctx.need("amount")
            return ctx.end_event()
        self.add("t-wants", wants)
        with harness.temp_game("mach-need") as g:
            with self.assertRaises(Refuse) as cm:
                harness.machine(g).start("t-wants")
            self.assertIn("amount", str(cm.exception))


class TestFingerprint(unittest.TestCase):
    def test_changed_rules_warn_rather_than_replay_silently(self):
        with harness.temp_game("mach-fp") as g:
            m = harness.machine(g)
            harness.play(m, ANSWERS[:2], flow="demo")
            m.eng["fingerprint"] = "0000000000000000"
            m.persist()
            turn = harness.machine(harness.reload(g["name"])).resume()
            self.assertTrue(any(e.kind == "warn" for e in turn.events),
                            "a save recorded against different rules replayed "
                            "with no warning")

    def test_fingerprint_is_stable(self):
        self.assertEqual(machine_mod.rules_fingerprint(),
                         machine_mod.rules_fingerprint())


class TestRefusals(unittest.TestCase):
    def test_spending_more_than_the_purse_refuses(self):
        with harness.temp_game("mach-broke", gold=3) as g:
            m = harness.machine(g)
            with self.assertRaises(Refuse) as cm:
                m.g["gold"] = 3
                from engine.ctx import Ctx
                Ctx(m.g, harness.book(), []).spend(10, "a bribe")
            self.assertIn("short by 7", str(cm.exception))

    def test_answering_when_nothing_was_asked_refuses(self):
        with harness.temp_game("mach-noask") as g:
            m = harness.machine(g)
            harness.play(m, ANSWERS, flow="demo")
            with self.assertRaises(Refuse):
                m.answer(1)


if __name__ == "__main__":
    unittest.main()
