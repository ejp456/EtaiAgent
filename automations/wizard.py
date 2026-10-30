"""Guided, conversational automation builder for EtaiAgent.

The wizard is a deterministic state machine that interviews the user until an automation is fully
specified, then saves it to `automations.textproto`. The LLM is used only for two narrow, one-shot
translation jobs (free text -> structured draft, free text -> tool actions); every field it returns
is validated and anything missing or invalid is asked about explicitly.

Entry points (shared by the CLI, Telegram and the mobile bridge):
  - `preprocess_user_message(session_id, text)` -> reply or None. Handles `/automation ...` commands,
    routes replies to an active wizard, and fires keyword-triggered automations.
  - `postprocess_agent_turn(session_id)` -> reply or None. Starts a wizard if the LLM agent called the
    `create_automation` tool during the turn.
"""
import re
import json
import shlex
import datetime
import threading
from typing import Any, Dict, List, Optional, Tuple

from automations.automation_pb2 import Automation, Weekday
from automations.engine import (
    AUTOMATION_ACTION_TOOLS,
    WEEKDAY_ORDER,
    describe_automation,
    automation_to_textproto,
    format_trigger,
    get_store,
    now_local,
    run_automation,
    run_keyword_automations,
    summarize_run,
    validate_automation,
    _tool_param_schemas,
)

CANCEL_WORDS = {"cancel", "/cancel", "stop", "abort", "nevermind", "never mind", "quit"}
YES_WORDS = {"y", "yes", "yep", "yeah", "sure", "ok", "okay", "save", "confirm", "do it", "looks good", "👍"}
NO_WORDS = {"n", "no", "nope", "nah"}

DAY_ALIASES = {
    "mon": Weekday.MONDAY, "tue": Weekday.TUESDAY, "wed": Weekday.WEDNESDAY, "thu": Weekday.THURSDAY,
    "fri": Weekday.FRIDAY, "sat": Weekday.SATURDAY, "sun": Weekday.SUNDAY,
}

HELP_TEXT = (
    "🤖 Automations\n"
    "/automation <description> — build a new automation (I'll ask follow-up questions)\n"
    "/automations — list saved automations\n"
    "/automation show <id> — show details + textproto\n"
    "/automation run <id> — run it right now\n"
    "/automation enable <id> | disable <id>\n"
    "/automation delete <id>\n"
    "/cancel — abort the builder"
)


# ==========================================
# Deterministic parsers
# ==========================================

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower()).strip(" .!?")


def parse_time(text: str) -> Optional[Tuple[int, int]]:
    """Parse '7am', '7:30 pm', '22:30', '0630', 'noon', 'midnight' -> (hour, minute)."""
    s = _norm(text)
    s = re.sub(r"^(at|@)\s+", "", s).replace(".", "")
    if s in ("noon", "midday"):
        return 12, 0
    if s == "midnight":
        return 0, 0
    m = re.fullmatch(r"(\d{1,2})(?::?(\d{2}))?\s*(am|pm|a|p)?", s)
    if not m:
        return None
    hour, minute, meridiem = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem.startswith("p") else 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def parse_days(text: str) -> Optional[List[int]]:
    """Parse 'every day' / 'weekdays' / 'weekends' / 'mon, wed and fri' -> list of Weekday (empty = every day)."""
    s = _norm(text)
    if s in ("every day", "everyday", "daily", "all", "all days", "each day", "always", "every"):
        return []
    if s in ("weekdays", "weekday", "work days", "workdays", "mon-fri", "monday-friday", "monday to friday"):
        return list(WEEKDAY_ORDER[:5])
    if s in ("weekends", "weekend", "sat-sun", "saturday and sunday"):
        return [Weekday.SATURDAY, Weekday.SUNDAY]
    days: List[int] = []
    for token in re.split(r"[\s,/&]+|\band\b", s):
        token = token.strip()
        if not token or token in ("every", "on", "only", "each"):
            continue
        day = DAY_ALIASES.get(token[:3])
        if day is None:
            return None
        if day not in days:
            days.append(day)
    return sorted(days) if days else None


def parse_datetime(text: str, now: Optional[datetime.datetime] = None) -> Optional[datetime.datetime]:
    """Parse ISO ('2026-10-30 07:00'), 'in 30 minutes', 'tomorrow 7am', 'friday 9pm', or a bare time."""
    now = (now or now_local()).replace(second=0, microsecond=0)
    raw = (text or "").strip()
    try:
        dt = datetime.datetime.fromisoformat(raw.replace(" ", "T", 1))
        return dt.replace(tzinfo=None)
    except ValueError:
        pass

    s = _norm(raw)
    m = re.fullmatch(r"in (\d+) ?(m|min|mins|minute|minutes|h|hr|hrs|hour|hours)", s)
    if m:
        amount = int(m.group(1))
        delta = datetime.timedelta(hours=amount) if m.group(2).startswith("h") else datetime.timedelta(minutes=amount)
        return (now + delta).replace(tzinfo=None)

    m = re.fullmatch(r"(today|tonight|tomorrow|[a-z]+day)?\s*(?:at\s+)?(.+)", s)
    if not m:
        return None
    day_word, time_part = m.group(1), m.group(2)
    hm = parse_time(time_part)
    if hm is None:
        return None
    base = now.replace(hour=hm[0], minute=hm[1]).replace(tzinfo=None)
    naive_now = now.replace(tzinfo=None)
    if day_word == "tomorrow":
        return base + datetime.timedelta(days=1)
    if day_word in ("today", "tonight"):
        return base
    if day_word:
        target = DAY_ALIASES.get(day_word[:3])
        if target is None:
            return None
        offset = (WEEKDAY_ORDER.index(target) - naive_now.weekday()) % 7
        candidate = base + datetime.timedelta(days=offset)
        return candidate if candidate > naive_now else candidate + datetime.timedelta(days=7)
    return base if base > naive_now else base + datetime.timedelta(days=1)


def parse_manual_actions(text: str) -> Optional[List[Dict[str, Any]]]:
    """Parse explicit syntax: `control_light device=Go color=green; speak_aloud message="Hi"`."""
    actions: List[Dict[str, Any]] = []
    for chunk in [c.strip() for c in text.split(";") if c.strip()]:
        try:
            tokens = shlex.split(chunk)
        except ValueError:
            return None
        if not tokens or tokens[0] not in AUTOMATION_ACTION_TOOLS:
            return None
        args: Dict[str, str] = {}
        last_key: Optional[str] = None
        for tok in tokens[1:]:
            if "=" not in tok:
                if last_key is None:
                    return None
                args[last_key] += " " + tok  # tolerate unquoted values: device=living room
                continue
            key, value = tok.split("=", 1)
            last_key = key.strip()
            args[last_key] = value.strip()
        actions.append({"tool": tokens[0], "args": args})
    return actions or None


# ==========================================
# LLM-assisted translation
# ==========================================

def _device_catalog() -> str:
    """Compact list of known device names/rooms from smart_devices.json for grounding the LLM."""
    import os
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(project_root, "smart_devices.json")
    if not os.path.exists(path):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smart_devices.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            devices = json.load(f)
        items = [f"{d.get('name')} ({d.get('device_type')}, room: {d.get('room')})" for d in devices.values()]
        return "; ".join(items)
    except Exception:
        return "unknown"


def _action_tool_catalog() -> str:
    schemas = _tool_param_schemas()
    from tools import get_tools_schema
    descriptions = {s["function"]["name"]: s["function"].get("description", "") for s in get_tools_schema()}
    lines = []
    for name in AUTOMATION_ACTION_TOOLS:
        if name not in schemas:
            continue
        props = schemas[name].get("properties", {})
        required = set(schemas[name].get("required", []))
        params = ", ".join(
            f"{p}:{meta.get('type', 'string')}{'' if p in required else '?'}" for p, meta in props.items()
        )
        desc = re.sub(r"\s+", " ", descriptions.get(name, ""))[:400]
        lines.append(f"- {name}({params}): {desc}")
    return "\n".join(lines)


def _extract_json(content: str) -> Optional[Dict[str, Any]]:
    content = re.sub(r"<think>.*?</think>", "", content or "", flags=re.S).strip()
    for candidate in (content, (re.search(r"\{.*\}", content, flags=re.S) or [None])[0]):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def _llm_json(system: str, user: str) -> Optional[Dict[str, Any]]:
    try:
        from llm import LLMClient
        reply = LLMClient(timeout=90.0).complete(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            tools=None,
            response_format="json",
        )
        return _extract_json(reply.get("content", ""))
    except Exception as e:
        print(f"\033[31m[AutomationWizard]\033[0m LLM translation failed: {e}")
        return None


_DRAFT_SCHEMA_DOC = """Return ONLY a JSON object with exactly these keys:
{
  "name": short human-readable name (3-5 words) or null,
  "trigger": null OR one of
      {"type": "time", "time": "HH:MM" (24h) or null, "days": ["mon","tue",...] or "every day" or "weekdays" or "weekends" or null}
      {"type": "once", "datetime": "YYYY-MM-DDTHH:MM" or null}
      {"type": "keyword", "phrase": "phrase the user will say" or null},
  "actions": [ {"tool": tool name, "args": {param: value}} ],
  "notify": true / false / null
}
Rules:
- Use null for anything the user did NOT clearly specify. Never invent times, days or phrases.
- "days" must be null unless the user said which days (e.g. "every day", "weekdays", "on mondays").
- Only use tools from the list. Use exact device names from the device list when possible.
- For lights include state ("on"/"off") and only the brightness/color/color_temperature the user asked for."""


def llm_draft(description: str, current: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Translate a natural-language request (or a change request against `current`) into a raw draft dict."""
    now = now_local()
    system = (
        "You convert smart-home automation requests into structured JSON.\n"
        f"Current local time: {now.strftime('%A %Y-%m-%d %H:%M')}.\n\n"
        f"Available action tools:\n{_action_tool_catalog()}\n\n"
        f"Known devices: {_device_catalog()}\n\n"
        f"{_DRAFT_SCHEMA_DOC}"
    )
    if current is None:
        user = f"Automation request: {description}"
    else:
        user = (
            f"Current automation draft:\n{json.dumps(current, indent=2)}\n\n"
            f"Apply this change and return the FULL updated draft: {description}"
        )
    return _llm_json(system, user)


def llm_actions(description: str) -> Optional[List[Dict[str, Any]]]:
    system = (
        "You convert a description of smart-home actions into tool calls.\n\n"
        f"Available action tools:\n{_action_tool_catalog()}\n\n"
        f"Known devices: {_device_catalog()}\n\n"
        'Return ONLY JSON: {"actions": [{"tool": tool name, "args": {param: value}}]}. '
        "Only include args the user asked for (plus required ones)."
    )
    data = _llm_json(system, description)
    if not data or not isinstance(data.get("actions"), list):
        return None
    return data["actions"]


# ==========================================
# Draft normalization
# ==========================================

def _normalize_actions(raw: Any) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    if not isinstance(raw, list):
        return actions
    for item in raw:
        if not isinstance(item, dict) or not item.get("tool"):
            continue
        args = item.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        clean: Dict[str, str] = {}
        for k, v in args.items():
            if v is None or v == "":
                continue
            clean[str(k)] = ("true" if v else "false") if isinstance(v, bool) else str(v)
        actions.append({"tool": str(item["tool"]).strip(), "args": clean})
    return actions


def _normalize_days(raw: Any) -> Optional[List[int]]:
    if raw is None:
        return None
    if isinstance(raw, str):
        return parse_days(raw)
    if isinstance(raw, list):
        if not raw:
            return None
        return parse_days(", ".join(str(d) for d in raw))
    return None


def normalize_draft(raw: Dict[str, Any], description: str) -> Dict[str, Any]:
    """Convert an LLM draft into the wizard's internal draft, dropping anything invalid (-> will be asked)."""
    draft: Dict[str, Any] = {
        "name": None, "description": description, "trigger": None, "actions": [], "notify": None,
    }
    name = raw.get("name")
    if isinstance(name, str) and name.strip():
        draft["name"] = name.strip()[:60]

    trig = raw.get("trigger")
    if isinstance(trig, dict):
        kind = str(trig.get("type", "")).lower()
        if kind == "time":
            hm = parse_time(str(trig.get("time"))) if trig.get("time") else None
            draft["trigger"] = {
                "type": "time",
                "hour": hm[0] if hm else None,
                "minute": hm[1] if hm else None,
                "days": _normalize_days(trig.get("days")),
            }
        elif kind == "once":
            dt = parse_datetime(str(trig.get("datetime"))) if trig.get("datetime") else None
            draft["trigger"] = {"type": "once", "datetime": dt.isoformat(timespec="minutes") if dt else None}
        elif kind == "keyword":
            phrase = str(trig.get("phrase") or "").strip().strip("'\"")
            draft["trigger"] = {"type": "keyword", "phrase": phrase or None}

    draft["actions"] = _normalize_actions(raw.get("actions"))
    if isinstance(raw.get("notify"), bool):
        draft["notify"] = raw["notify"]
    return draft


def draft_to_proto(draft: Dict[str, Any]) -> Automation:
    auto = Automation(
        name=draft.get("name") or "",
        description=draft.get("description") or "",
        enabled=True,
        notify=bool(draft.get("notify")),
    )
    trig = draft.get("trigger") or {}
    if trig.get("type") == "time" and trig.get("hour") is not None:
        auto.trigger.time.hour = trig["hour"]
        auto.trigger.time.minute = trig.get("minute") or 0
        auto.trigger.time.days.extend(trig.get("days") or [])
    elif trig.get("type") == "once" and trig.get("datetime"):
        auto.trigger.once.datetime = trig["datetime"]
    elif trig.get("type") == "keyword" and trig.get("phrase"):
        auto.trigger.keyword.phrase = trig["phrase"]
    for action in draft.get("actions") or []:
        a = auto.actions.add(tool=action["tool"])
        for k, v in action["args"].items():
            a.args[k] = str(v)
    return auto


def _default_name(draft: Dict[str, Any]) -> str:
    actions = draft.get("actions") or []
    if actions:
        a = actions[0]
        target = a["args"].get("device") or a["args"].get("app") or a["args"].get("command") or ""
        verb = a["tool"].replace("control_", "").replace("_", " ")
        base = f"{target} {verb}".strip()
    else:
        base = "New automation"
    trig = draft.get("trigger") or {}
    if trig.get("type") == "time" and trig.get("hour") is not None:
        base += f" at {datetime.time(trig['hour'], trig.get('minute') or 0).strftime('%-I:%M%p').lower()}"
    elif trig.get("type") == "keyword" and trig.get("phrase"):
        base = f'"{trig["phrase"]}" → {base}'
    return base[:60]


# ==========================================
# Wizard state machine
# ==========================================

class AutomationWizard:
    """Interviews the user one question at a time until an automation is complete, then saves it."""

    def __init__(self) -> None:
        self.draft: Dict[str, Any] = {
            "name": None, "description": "", "trigger": None, "actions": [], "notify": None,
        }
        self.step: str = "describe"
        self.done: bool = False

    # ---------- public API ----------

    def start(self, description: str = "") -> str:
        description = (description or "").strip()
        if not description:
            self.step = "describe"
            return (
                "🛠️ Let's build an automation. What should it do?\n"
                "Describe it in a sentence, e.g.\n"
                "• \"Every weekday at 7am open the bedroom blinds\"\n"
                "• \"When I say movie time, dim the living room to 20%\"\n"
                "• \"Tomorrow at 6pm turn the Go light purple\"\n"
                "(Type /cancel anytime to stop.)"
            )
        return self._ingest_description(description)

    def handle(self, text: str) -> str:
        text = (text or "").strip()
        if _norm(text) in CANCEL_WORDS:
            self.done = True
            return "❌ Automation builder cancelled. Nothing was saved."
        handler = getattr(self, f"_on_{self.step}", None)
        if handler is None:
            self.done = True
            return "Something went wrong with the automation builder; please start again with /automation."
        return handler(text)

    # ---------- step handlers ----------

    def _on_describe(self, text: str) -> str:
        return self._ingest_description(text)

    def _on_trigger_kind(self, text: str) -> str:
        s = _norm(text)
        if s in ("1", "time", "schedule", "scheduled", "recurring", "repeat", "every day") or "time" in s:
            self.draft["trigger"] = {"type": "time", "hour": None, "minute": None, "days": None}
        elif s in ("2", "once", "one time", "one-time", "date") or "once" in s:
            self.draft["trigger"] = {"type": "once", "datetime": None}
        elif s in ("3", "keyword", "phrase", "say", "when i say") or "phrase" in s or "say" in s:
            self.draft["trigger"] = {"type": "keyword", "phrase": None}
        else:
            return "Please answer 1, 2 or 3.\n\n" + self._question("trigger_kind")
        return self._advance()

    def _on_time(self, text: str) -> str:
        hm = parse_time(text)
        if hm is None:
            return "I couldn't read that time. Try something like 7am, 6:30 pm or 22:15."
        self.draft["trigger"]["hour"], self.draft["trigger"]["minute"] = hm
        return self._advance()

    def _on_days(self, text: str) -> str:
        days = parse_days(text)
        if days is None:
            return "I couldn't read those days. Try: every day, weekdays, weekends, or mon, wed, fri."
        self.draft["trigger"]["days"] = days
        return self._advance()

    def _on_datetime(self, text: str) -> str:
        dt = parse_datetime(text)
        if dt is None:
            return "I couldn't read that. Try: tomorrow 7am, friday 9pm, in 30 minutes, or 2026-11-01 08:00."
        if dt <= now_local().replace(tzinfo=None):
            return f"{dt.strftime('%a %b %-d %-I:%M %p')} is in the past. When should it run?"
        self.draft["trigger"]["datetime"] = dt.isoformat(timespec="minutes")
        return self._advance()

    def _on_phrase(self, text: str) -> str:
        phrase = text.strip().strip("'\"").strip()
        if len(phrase) < 2 or phrase.startswith("/"):
            return "Please give me a short phrase, e.g. \"movie time\" or \"code red\"."
        self.draft["trigger"]["phrase"] = phrase
        return self._advance()

    def _on_actions(self, text: str) -> str:
        actions = parse_manual_actions(text)
        if actions is None:
            actions = _normalize_actions(llm_actions(text) or [])
        if not actions:
            return (
                "I couldn't turn that into an action. Describe it differently, or type it directly, e.g.\n"
                "control_light device=Go state=on color=green brightness=40"
            )
        problems = self._action_problems(actions)
        if problems:
            return "That action has problems:\n• " + "\n• ".join(problems) + "\n\nWhat should happen instead?"
        self.draft["actions"] = actions
        return self._advance()

    def _on_notify(self, text: str) -> str:
        s = _norm(text)
        if s in YES_WORDS:
            self.draft["notify"] = True
        elif s in NO_WORDS:
            self.draft["notify"] = False
        else:
            return "Please answer yes or no. " + self._question("notify")
        return self._advance()

    def _on_confirm(self, text: str) -> str:
        s = _norm(text)
        if s in YES_WORDS:
            return self._save()
        if s in NO_WORDS:
            return "OK — what would you like to change? (or /cancel to discard)"
        reply = self._apply_edit(text)
        return reply if reply is not None else self._advance()

    # ---------- internals ----------

    def _ingest_description(self, description: str) -> str:
        self.draft["description"] = description
        raw = llm_draft(description)
        if raw:
            fresh = normalize_draft(raw, description)
            fresh["actions"] = [a for a in fresh["actions"] if not self._action_problems([a])]
            self.draft = fresh
        return self._advance(intro=self._understood_prefix())

    def _understood_prefix(self) -> str:
        bits = []
        trig = self.draft.get("trigger")
        if trig:
            bits.append(f"trigger: {trig['type']}")
        if self.draft.get("actions"):
            bits.append(f"{len(self.draft['actions'])} action(s)")
        return f"Got it ({', '.join(bits)}).\n\n" if bits else ""

    def _action_problems(self, actions: List[Dict[str, Any]]) -> List[str]:
        probe = draft_to_proto({"name": "probe", "trigger": {"type": "keyword", "phrase": "probe"},
                                "actions": actions})
        return validate_automation(probe)

    def _next_missing(self) -> str:
        trig = self.draft.get("trigger")
        if not trig:
            return "trigger_kind"
        if trig["type"] == "time":
            if trig.get("hour") is None:
                return "time"
            if trig.get("days") is None:
                return "days"
        elif trig["type"] == "once" and not trig.get("datetime"):
            return "datetime"
        elif trig["type"] == "keyword" and not trig.get("phrase"):
            return "phrase"
        if not self.draft.get("actions"):
            return "actions"
        if self.draft.get("notify") is None:
            if trig["type"] == "keyword":
                self.draft["notify"] = False  # you're already chatting when a keyword fires
            else:
                return "notify"
        if not self.draft.get("name"):
            self.draft["name"] = _default_name(self.draft)
        return "confirm"

    def _question(self, step: str) -> str:
        if step == "trigger_kind":
            return ("When should it run?\n"
                    "1️⃣ On a schedule (a time of day, on certain days)\n"
                    "2️⃣ Once, at a specific date & time\n"
                    "3️⃣ When I say a phrase (e.g. \"code red\")")
        if step == "time":
            return "⏰ What time should it run? (e.g. 7am, 6:30 pm, 22:15)"
        if step == "days":
            return "📅 Which days? (every day, weekdays, weekends, or e.g. mon, wed, fri)"
        if step == "datetime":
            return "📅 When exactly? (e.g. tomorrow 7am, friday 9pm, in 30 minutes)"
        if step == "phrase":
            return "💬 What phrase should trigger it?"
        if step == "actions":
            return ("⚡ What should happen? Describe it (e.g. \"turn the Go light green at 40%\" or "
                    "\"close the bedroom blinds and turn off all lights\").\n"
                    f"Available: {', '.join(AUTOMATION_ACTION_TOOLS)}")
        if step == "notify":
            return "🔔 Should I send you a Telegram message each time it runs? (yes/no)"
        if step == "confirm":
            auto = draft_to_proto(self.draft)
            return (
                "Here's the automation:\n\n"
                f"{describe_automation(auto, include_id=False)}\n\n"
                f"textproto:\n{automation_to_textproto(auto)}\n"
                "Save it? Reply yes to save, or tell me what to change, e.g.\n"
                "• time 7:30am  • days weekends  • name Morning blinds\n"
                "• notify no  • phrase movie time  • when tomorrow 8pm\n"
                "• actions <new description>  • add <another action>\n"
                "• or describe any change in plain English"
            )
        return ""

    def _advance(self, intro: str = "") -> str:
        self.step = self._next_missing()
        return intro + self._question(self.step)

    def _apply_edit(self, text: str) -> Optional[str]:
        """Apply a confirm-step edit. Returns an error/clarification reply, or None on success."""
        m = re.match(r"^\s*(name|time|days|day|when|phrase|notify|actions|action|add|trigger)\b[:\s]*(.*)$",
                     text, flags=re.I | re.S)
        if m:
            field, value = m.group(1).lower(), m.group(2).strip()
            trig = self.draft.get("trigger") or {}
            if field == "name" and value:
                self.draft["name"] = value[:60]
                return None
            if field == "trigger":
                self.draft["trigger"] = None
                return None
            if field == "time":
                hm = parse_time(value)
                if hm is None:
                    return "I couldn't read that time. Try e.g. 'time 7:30am'."
                if trig.get("type") != "time":
                    self.draft["trigger"] = trig = {"type": "time", "days": None}
                trig["hour"], trig["minute"] = hm
                return None
            if field in ("days", "day"):
                days = parse_days(value)
                if days is None or trig.get("type") != "time":
                    return "Days only apply to scheduled automations; try e.g. 'days weekdays'."
                trig["days"] = days
                return None
            if field == "when":
                dt = parse_datetime(value)
                if dt is None:
                    return "I couldn't read that date/time. Try e.g. 'when tomorrow 8pm'."
                self.draft["trigger"] = {"type": "once", "datetime": dt.isoformat(timespec="minutes")}
                return None
            if field == "phrase" and value:
                self.draft["trigger"] = {"type": "keyword", "phrase": value.strip("'\"")}
                return None
            if field == "notify":
                self.draft["notify"] = _norm(value) in YES_WORDS or _norm(value) in ("on", "true")
                return None
            if field in ("actions", "action", "add") and value:
                actions = parse_manual_actions(value) or _normalize_actions(llm_actions(value) or [])
                problems = self._action_problems(actions) if actions else ["couldn't understand the action"]
                if problems:
                    return "That action has problems:\n• " + "\n• ".join(problems)
                self.draft["actions"] = (self.draft["actions"] + actions) if field == "add" else actions
                return None

        # Free-form change request -> let the LLM revise the whole draft, then re-validate.
        current = {
            "name": self.draft.get("name"),
            "trigger": self._trigger_for_llm(),
            "actions": self.draft.get("actions"),
            "notify": self.draft.get("notify"),
        }
        raw = llm_draft(text, current=current)
        if not raw:
            return "I couldn't apply that change. Try one of the explicit edits (e.g. 'time 7:30am')."
        revised = normalize_draft(raw, self.draft.get("description", ""))
        revised["actions"] = [a for a in revised["actions"] if not self._action_problems([a])]
        if revised.get("notify") is None:
            revised["notify"] = self.draft.get("notify")
        self.draft = revised
        return None

    def _trigger_for_llm(self) -> Optional[Dict[str, Any]]:
        trig = self.draft.get("trigger")
        if not trig:
            return None
        if trig["type"] == "time":
            days = trig.get("days")
            return {
                "type": "time",
                "time": f"{trig['hour']:02d}:{trig.get('minute') or 0:02d}" if trig.get("hour") is not None else None,
                "days": None if days is None else (
                    "every day" if not days else [Weekday.Name(d)[:3].lower() for d in days]),
            }
        return dict(trig)

    def _save(self) -> str:
        auto = draft_to_proto(self.draft)
        problems = validate_automation(auto)
        if problems:
            return "I can't save it yet:\n• " + "\n• ".join(problems) + "\n\nTell me what to change."
        saved = get_store().add(auto)
        self.done = True
        return (
            f"✅ Saved automation '{saved.name}' (id: {saved.id}).\n"
            f"It will run {format_trigger(saved)}.\n"
            f"Manage it with /automation run|disable|delete {saved.id}"
        )


# ==========================================
# Session routing (shared by CLI / Telegram / mobile)
# ==========================================

_wizards: Dict[str, AutomationWizard] = {}
_wizards_lock = threading.Lock()
_pending = threading.local()


def request_wizard(description: str) -> None:
    """Called by the `create_automation` agent tool: queue a wizard for the current thread's session."""
    _pending.description = description or ""


def _pop_pending() -> Optional[str]:
    desc = getattr(_pending, "description", None)
    _pending.description = None
    return desc


def start_wizard(session_id: str, description: str = "") -> str:
    wizard = AutomationWizard()
    reply = wizard.start(description)
    with _wizards_lock:
        if wizard.done:
            _wizards.pop(session_id, None)
        else:
            _wizards[session_id] = wizard
    return reply


def has_active_wizard(session_id: str) -> bool:
    with _wizards_lock:
        return session_id in _wizards


def _handle_command(session_id: str, text: str) -> Optional[str]:
    m = re.match(r"^/(automations?|rules?)\b\s*(.*)$", text.strip(), flags=re.I | re.S)
    if not m:
        return None
    rest = m.group(2).strip()
    sub, _, arg = rest.partition(" ")
    sub_l, arg = sub.lower(), arg.strip()
    store = get_store()

    if (m.group(1).lower() in ("automations", "rules") and not rest) or sub_l in ("list", "ls"):
        autos = store.list()
        if not autos:
            return "No automations yet. Create one with /automation <description>."
        return "🤖 Automations:\n\n" + "\n\n".join(describe_automation(a) for a in autos)
    if sub_l in ("help", "?"):
        return HELP_TEXT
    if sub_l in ("show", "run", "enable", "disable", "delete", "remove", "rm", "pause", "resume"):
        if not arg:
            return f"Usage: /automation {sub_l} <id>"
        auto = store.find(arg)
        if not auto:
            return f"No automation matching '{arg}'. Use /automations to see ids."
        if sub_l == "show":
            return f"{describe_automation(auto)}\n\ntextproto:\n{automation_to_textproto(auto)}"
        if sub_l == "run":
            return summarize_run(run_automation(auto, reason="manual run"))
        if sub_l in ("enable", "resume"):
            store.set_enabled(auto.id, True)
            return f"✅ Enabled '{auto.name}'. It will run {format_trigger(auto)}."
        if sub_l in ("disable", "pause"):
            store.set_enabled(auto.id, False)
            return f"⏸️ Disabled '{auto.name}'."
        store.delete(auto.id)
        return f"🗑️ Deleted '{auto.name}'."
    if sub_l in ("new", "add", "create"):
        return start_wizard(session_id, arg)
    return start_wizard(session_id, rest)


def preprocess_user_message(session_id: str, text: str) -> Optional[str]:
    """Handle automation commands / active wizard / keyword triggers. Returns a reply, or None to use the LLM."""
    stripped = (text or "").strip()
    _pending.description = None  # drop any stale agent request from a previous turn on this thread
    if not stripped:
        return None

    command_reply = _handle_command(session_id, stripped)
    if command_reply is not None:
        return command_reply

    with _wizards_lock:
        wizard = _wizards.get(session_id)
    if wizard is not None:
        reply = wizard.handle(stripped)
        if wizard.done:
            with _wizards_lock:
                _wizards.pop(session_id, None)
        return reply

    return run_keyword_automations(stripped)


def postprocess_agent_turn(session_id: str) -> Optional[str]:
    """If the agent called `create_automation` this turn, start the wizard and return its first message."""
    description = _pop_pending()
    if description is None:
        return None
    return start_wizard(session_id, description)
