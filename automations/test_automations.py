"""Tests for the rules engine (automations.py) and guided builder (automation_wizard.py).

The LLM and all device-controlling tools are mocked, so these run offline and never touch the home.
Run: ./.venv/bin/python -m unittest test_automations -v
"""
import os
import datetime
import tempfile
import unittest
from unittest import mock

import automations
from automations import wizard as wiz
from automations import engine as eng
from automations import Automation, Weekday, AutomationStore, AutomationScheduler, validate_automation


def _fake_dispatch(calls):
    def dispatch(tool_name, args, tool_call_id=None):
        calls.append((tool_name, args))
        return {"role": "tool", "content": '{"success": true}', "tool_call_id": tool_call_id}
    return dispatch


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmpdir.name, "automations.textproto")
        self._old_store = eng._store
        eng._store = AutomationStore(self.path)
        self.calls = []
        self._dispatch_patch = mock.patch("tools.dispatch_tool", side_effect=_fake_dispatch(self.calls))
        self._dispatch_patch.start()
        self._push_patch = mock.patch("automations.telegram_push")
        self.push = self._push_patch.start()

    def tearDown(self):
        self._dispatch_patch.stop()
        self._push_patch.stop()
        eng._store = self._old_store
        self.tmpdir.cleanup()

    def make_time_auto(self, hour=7, minute=0, days=(), enabled=True, notify=False):
        auto = Automation(name="Morning blinds", enabled=enabled, notify=notify)
        auto.trigger.time.hour = hour
        auto.trigger.time.minute = minute
        auto.trigger.time.days.extend(days)
        a = auto.actions.add(tool="control_blinds")
        a.args["device"] = "Bedroom"
        a.args["action"] = "open"
        return automations.get_store().add(auto)


class ParserTests(unittest.TestCase):
    def test_parse_time(self):
        self.assertEqual(wiz.parse_time("7am"), (7, 0))
        self.assertEqual(wiz.parse_time("7:30 pm"), (19, 30))
        self.assertEqual(wiz.parse_time("at 22:15"), (22, 15))
        self.assertEqual(wiz.parse_time("12am"), (0, 0))
        self.assertEqual(wiz.parse_time("noon"), (12, 0))
        self.assertEqual(wiz.parse_time("0630"), (6, 30))
        self.assertIsNone(wiz.parse_time("25:00"))
        self.assertIsNone(wiz.parse_time("banana"))

    def test_parse_days(self):
        self.assertEqual(wiz.parse_days("every day"), [])
        self.assertEqual(wiz.parse_days("weekdays"), [1, 2, 3, 4, 5])
        self.assertEqual(wiz.parse_days("Weekends"), [Weekday.SATURDAY, Weekday.SUNDAY])
        self.assertEqual(wiz.parse_days("mon, wed and fri"), [1, 3, 5])
        self.assertEqual(wiz.parse_days("every monday"), [1])
        self.assertIsNone(wiz.parse_days("sometimes"))

    def test_parse_datetime(self):
        now = datetime.datetime(2026, 10, 29, 22, 0).astimezone()
        self.assertEqual(wiz.parse_datetime("tomorrow 7am", now), datetime.datetime(2026, 10, 30, 7, 0))
        self.assertEqual(wiz.parse_datetime("in 30 minutes", now), datetime.datetime(2026, 10, 29, 22, 30))
        self.assertEqual(wiz.parse_datetime("2026-11-01 08:00", now), datetime.datetime(2026, 11, 1, 8, 0))
        # Thu 10pm -> next Friday 9pm is tomorrow
        self.assertEqual(wiz.parse_datetime("friday 9pm", now), datetime.datetime(2026, 10, 30, 21, 0))
        # Bare time already passed today -> tomorrow
        self.assertEqual(wiz.parse_datetime("8am", now), datetime.datetime(2026, 10, 30, 8, 0))

    def test_parse_manual_actions(self):
        actions = wiz.parse_manual_actions('control_light device=Go color=green; speak_aloud message="Good night"')
        self.assertEqual(actions, [
            {"tool": "control_light", "args": {"device": "Go", "color": "green"}},
            {"tool": "speak_aloud", "args": {"message": "Good night"}},
        ])
        self.assertIsNone(wiz.parse_manual_actions("sandboxed_bash command=rm"))
        self.assertIsNone(wiz.parse_manual_actions("turn on the lights"))


class StoreAndEngineTests(StoreTestCase):
    def test_textproto_round_trip(self):
        saved = self.make_time_auto(days=[Weekday.MONDAY])
        self.assertEqual(saved.id, "morning_blinds")
        reloaded = AutomationStore(self.path).get("morning_blinds")
        self.assertEqual(reloaded.trigger.time.hour, 7)
        self.assertEqual(list(reloaded.trigger.time.days), [Weekday.MONDAY])
        with open(self.path) as f:
            text = f.read()
        self.assertIn("proto-message: etaiagent.automations.AutomationRegistry", text)
        self.assertIn('tool: "control_blinds"', text)

    def test_unique_ids(self):
        self.make_time_auto()
        second = self.make_time_auto()
        self.assertEqual(second.id, "morning_blinds_2")

    def test_validation_rejects_unsafe_and_unknown(self):
        auto = Automation(name="bad")
        auto.trigger.keyword.phrase = "x"
        auto.actions.add(tool="sandboxed_bash").args["command"] = "rm -rf /"
        self.assertTrue(any("can't be used" in e for e in validate_automation(auto)))

        auto2 = Automation(name="bad args")
        auto2.trigger.keyword.phrase = "x"
        auto2.actions.add(tool="control_light").args["sparkle"] = "yes"
        self.assertTrue(any("no parameter 'sparkle'" in e for e in validate_automation(auto2)))

    def test_run_coerces_types(self):
        auto = Automation(name="dim", enabled=True)
        auto.trigger.keyword.phrase = "dim"
        a = auto.actions.add(tool="control_light")
        a.args["device"] = "Go"
        a.args["brightness"] = "40"
        saved = automations.get_store().add(auto)
        run = automations.run_automation(saved)
        self.assertTrue(run["ok"])
        self.assertEqual(self.calls, [("control_light", {"device": "Go", "brightness": 40})])
        self.assertTrue(automations.get_store().get(saved.id).last_run)

    def test_scheduler_fires_once_per_minute_and_respects_days(self):
        self.make_time_auto(hour=7, minute=0, days=[Weekday.FRIDAY], notify=True)
        sched = AutomationScheduler(notifier=self.push)
        friday_7 = datetime.datetime(2026, 10, 30, 7, 0, 5).astimezone()
        thursday_7 = datetime.datetime(2026, 10, 29, 7, 0, 5).astimezone()

        self.assertEqual(sched.tick(thursday_7), [])
        self.assertEqual(len(sched.tick(friday_7)), 1)
        self.assertEqual(sched.tick(friday_7 + datetime.timedelta(seconds=20)), [])  # same minute
        self.assertEqual(len(self.calls), 1)
        self.push.assert_called_once()

    def test_scheduler_skips_disabled(self):
        self.make_time_auto(hour=7, minute=0, enabled=False)
        sched = AutomationScheduler(notifier=None)
        self.assertEqual(sched.tick(datetime.datetime(2026, 10, 30, 7, 0).astimezone()), [])

    def test_once_trigger_fires_then_disables(self):
        auto = Automation(name="one shot", enabled=True)
        auto.trigger.once.datetime = "2026-10-30T18:00"
        auto.actions.add(tool="control_light").args["device"] = "Go"
        saved = automations.get_store().add(auto)
        sched = AutomationScheduler(notifier=None)
        self.assertEqual(sched.tick(datetime.datetime(2026, 10, 30, 17, 59).astimezone()), [])
        self.assertEqual(len(sched.tick(datetime.datetime(2026, 10, 30, 18, 1).astimezone())), 1)
        self.assertFalse(automations.get_store().get(saved.id).enabled)

    def test_keyword_match_is_word_bounded(self):
        auto = Automation(name="code red", enabled=True)
        auto.trigger.keyword.phrase = "code red"
        auto.actions.add(tool="control_light").args["device"] = "Go"
        automations.get_store().add(auto)
        self.assertEqual(len(automations.match_keyword_automations("CODE RED!")), 1)
        self.assertEqual(automations.match_keyword_automations("unicode redirect"), [])

    def test_preapproval_scoped_to_automation_run(self):
        from proto_tools import preapproved_tools, prompt_user_confirmation
        with mock.patch.dict(os.environ, {"ETAI_AGENT_REMOTE_MODE": "1"}):
            with preapproved_tools({"run_python_snippet"}):
                self.assertTrue(prompt_user_confirmation("run_python_snippet", {}))
            self.assertFalse(prompt_user_confirmation("run_python_snippet", {}))


class WizardTests(StoreTestCase):
    SESSION = "test_session"

    def setUp(self):
        super().setUp()
        wiz._wizards.clear()

    def send(self, text):
        return wiz.preprocess_user_message(self.SESSION, text)

    def test_full_flow_with_partial_llm_draft(self):
        # LLM understood the action and time, but not which days or notify preference.
        draft = {
            "name": "Morning blinds",
            "trigger": {"type": "time", "time": "07:00", "days": None},
            "actions": [{"tool": "control_blinds", "args": {"device": "Bedroom", "action": "open"}}],
            "notify": None,
        }
        with mock.patch.object(wiz, "llm_draft", return_value=draft):
            reply = self.send("/automation at 7am open the bedroom blinds")
        self.assertIn("Which days", reply)
        self.assertIn("Telegram", self.send("weekdays"))
        reply = self.send("yes")
        self.assertIn("Save it?", reply)
        self.assertIn('tool: "control_blinds"', reply)
        reply = self.send("yes")
        self.assertIn("Saved automation 'Morning blinds'", reply)
        self.assertFalse(wiz.has_active_wizard(self.SESSION))

        saved = automations.get_store().get("morning_blinds")
        self.assertEqual(list(saved.trigger.time.days), [1, 2, 3, 4, 5])
        self.assertTrue(saved.notify)
        self.assertTrue(saved.enabled)

    def test_flow_without_llm_asks_everything(self):
        with mock.patch.object(wiz, "llm_draft", return_value=None), \
             mock.patch.object(wiz, "llm_actions", return_value=None):
            self.assertIn("What should it do", self.send("/automation"))
            self.assertIn("When should it run", self.send("something cozy"))
            self.assertIn("phrase", self.send("3"))
            self.assertIn("What should happen", self.send("movie time"))
            # LLM unavailable -> manual syntax still works
            self.assertIn("couldn't turn that", self.send("make it cozy"))
            reply = self.send("control_light device=living room brightness=20")
            self.assertIn("Save it?", reply)  # keyword -> notify defaults to no
            reply = self.send("name Movie time")
            self.assertIn("Movie time", reply)
            self.assertIn("Saved", self.send("save"))
        saved = automations.get_store().get("movie_time")
        self.assertEqual(saved.trigger.keyword.phrase, "movie time")
        self.assertFalse(saved.notify)

    def test_confirm_edits(self):
        draft = {
            "name": "Go green",
            "trigger": {"type": "time", "time": "22:00", "days": "every day"},
            "actions": [{"tool": "control_light", "args": {"device": "Go", "state": "on", "color": "green"}}],
            "notify": False,
        }
        with mock.patch.object(wiz, "llm_draft", return_value=draft):
            self.assertIn("Save it?", self.send("/automation every day at 10pm turn Go green"))
        reply = self.send("time 9:45pm")
        self.assertIn("9:45 PM", reply)
        reply = self.send("days weekends")
        self.assertIn("weekends", reply)
        reply = self.send("add speak_aloud message=bedtime")
        self.assertIn("speak_aloud", reply)
        self.assertIn("Saved", self.send("yes"))
        saved = automations.get_store().get("go_green")
        self.assertEqual((saved.trigger.time.hour, saved.trigger.time.minute), (21, 45))
        self.assertEqual(len(saved.actions), 2)

    def test_llm_hallucinated_tool_is_dropped_and_asked(self):
        draft = {
            "name": "Hack",
            "trigger": {"type": "keyword", "phrase": "do it"},
            "actions": [{"tool": "sandboxed_bash", "args": {"command": "rm -rf ~"}}],
            "notify": None,
        }
        with mock.patch.object(wiz, "llm_draft", return_value=draft):
            reply = self.send("/automation when I say do it run a shell command")
        self.assertIn("What should happen", reply)

    def test_cancel(self):
        with mock.patch.object(wiz, "llm_draft", return_value=None):
            self.send("/automation")
        self.assertIn("cancelled", self.send("/cancel"))
        self.assertFalse(wiz.has_active_wizard(self.SESSION))
        self.assertEqual(automations.get_store().list(), [])

    def test_management_commands(self):
        self.make_time_auto()
        self.assertIn("Morning blinds", self.send("/automations"))
        self.assertIn("Disabled", self.send("/automation disable morning_blinds"))
        self.assertFalse(automations.get_store().get("morning_blinds").enabled)
        self.assertIn("ran", self.send("/automation run morning"))
        self.assertEqual(self.calls[0][0], "control_blinds")
        self.assertIn("Deleted", self.send("/automation delete morning_blinds"))
        self.assertEqual(automations.get_store().list(), [])

    def test_agent_tool_handoff(self):
        from tools import create_automation
        with mock.patch.object(wiz, "llm_draft", return_value=None):
            create_automation("remind me at 9pm")
            reply = wiz.postprocess_agent_turn(self.SESSION)
        self.assertIn("When should it run", reply)
        self.assertTrue(wiz.has_active_wizard(self.SESSION))
        self.assertIsNone(wiz.postprocess_agent_turn(self.SESSION))  # consumed

    def test_keyword_trigger_bypasses_llm(self):
        auto = Automation(name="code red", enabled=True)
        auto.trigger.keyword.phrase = "code red"
        auto.actions.add(tool="control_light").args["device"] = "Go"
        automations.get_store().add(auto)
        reply = self.send("code red")
        self.assertIn("code red", reply)
        self.assertEqual(self.calls[0][0], "control_light")
        self.assertIsNone(self.send("what's the weather?"))


if __name__ == "__main__":
    unittest.main()
