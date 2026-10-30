"""Rules engine for EtaiAgent.

Automations are stored declaratively in `automations.textproto` (schema: `automation.proto`)
and executed deterministically by calling registered tools via `dispatch_tool`, without
routing through the LLM, so they fire reliably even on small local models.

Components:
  - `AutomationStore`:     thread-safe load/save/CRUD over the textproto file.
  - `run_automation`:      executes an automation's actions in order.
  - `match_keyword_automations`: finds keyword-triggered automations for a user message.
  - `AutomationScheduler`: background thread that fires time / once triggers.
"""
import os
import re
import json
import time
import fcntl
import datetime
import threading
from typing import Any, Callable, Dict, List, Optional

import google.protobuf.text_format as text_format
from automations.automation_pb2 import Automation, AutomationRegistry, Weekday

AUTOMATIONS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(AUTOMATIONS_DIR)

AUTOMATIONS_FILE = os.path.join(AUTOMATIONS_DIR, "automations.textproto")
SCHEDULER_LOCK_FILE = os.path.join(AUTOMATIONS_DIR, ".automations_scheduler.lock")
MOBILE_CONFIG_FILE = os.path.join(PROJECT_ROOT, "mobile_config.json")

# Tools an automation is allowed to run. Deliberately excludes shell / code execution tools so a
# saved rule can never run arbitrary commands unattended.
AUTOMATION_ACTION_TOOLS = [
    "control_light",
    "control_blinds",
    "cast_to_tv",
    "watch_on_ps5",
    "control_ps5_media",
    "speak_aloud",
    "desktop_notification",
]
# Confirmation-gated tools that are implicitly approved once the user saves an automation using them.
AUTOMATION_PREAPPROVED_TOOLS = {"speak_aloud", "desktop_notification"}

WEEKDAY_ORDER = [
    Weekday.MONDAY, Weekday.TUESDAY, Weekday.WEDNESDAY, Weekday.THURSDAY,
    Weekday.FRIDAY, Weekday.SATURDAY, Weekday.SUNDAY,
]
WEEKDAY_SHORT = {
    Weekday.MONDAY: "Mon", Weekday.TUESDAY: "Tue", Weekday.WEDNESDAY: "Wed",
    Weekday.THURSDAY: "Thu", Weekday.FRIDAY: "Fri", Weekday.SATURDAY: "Sat", Weekday.SUNDAY: "Sun",
}

_FILE_HEADER = (
    "# proto-file: automation.proto\n"
    "# proto-message: etaiagent.automations.AutomationRegistry\n"
    "#\n"
    "# EtaiAgent automations. Create with `/automation <description>` in the CLI or Telegram.\n"
    "# Hand edits are fine; changes are picked up automatically by the scheduler.\n\n"
)


def now_local() -> datetime.datetime:
    return datetime.datetime.now().astimezone()


def slugify(text: str, max_len: int = 32) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return (slug[:max_len].rstrip("_")) or "automation"


# ==========================================
# Human-readable formatting
# ==========================================

def format_days(days: List[int]) -> str:
    day_set = set(days)
    if not day_set or len(day_set) == 7:
        return "every day"
    if day_set == set(WEEKDAY_ORDER[:5]):
        return "weekdays"
    if day_set == {Weekday.SATURDAY, Weekday.SUNDAY}:
        return "weekends"
    return ", ".join(WEEKDAY_SHORT[d] for d in WEEKDAY_ORDER if d in day_set)


def format_trigger(auto: Automation) -> str:
    kind = auto.trigger.WhichOneof("kind")
    if kind == "time":
        t = auto.trigger.time
        clock = datetime.time(t.hour, t.minute).strftime("%-I:%M %p")
        return f"at {clock}, {format_days(list(t.days))}"
    if kind == "once":
        try:
            dt = datetime.datetime.fromisoformat(auto.trigger.once.datetime)
            return f"once on {dt.strftime('%a %b %-d at %-I:%M %p')}"
        except ValueError:
            return f"once at {auto.trigger.once.datetime}"
    if kind == "keyword":
        return f'when I say "{auto.trigger.keyword.phrase}"'
    return "no trigger"


def format_action(action) -> str:
    args = ", ".join(f"{k}={v}" for k, v in sorted(action.args.items()))
    return f"{action.tool}({args})"


def describe_automation(auto: Automation, include_id: bool = True) -> str:
    status = "✅" if auto.enabled else "⏸️"
    head = f"{status} {auto.name}"
    if include_id:
        head += f"  [id: {auto.id}]"
    lines = [head, f"   Trigger: {format_trigger(auto)}"]
    for i, action in enumerate(auto.actions, 1):
        lines.append(f"   Action {i}: {format_action(action)}")
    lines.append(f"   Notify on Telegram: {'yes' if auto.notify else 'no'}")
    if auto.last_run:
        lines.append(f"   Last run: {auto.last_run}")
    return "\n".join(lines)


def automation_to_textproto(auto: Automation) -> str:
    return text_format.MessageToString(auto, as_utf8=True)


# ==========================================
# Validation & argument coercion
# ==========================================

def _tool_param_schemas() -> Dict[str, Dict[str, Any]]:
    """Map of tool name -> JSON-schema `parameters` object for every registered tool."""
    from tools import get_tools_schema  # lazy: tools.py imports this module
    result: Dict[str, Dict[str, Any]] = {}
    for schema in get_tools_schema():
        fn = schema.get("function", {})
        result[fn.get("name", "")] = fn.get("parameters", {}) or {}
    return result


def coerce_args(tool_name: str, raw_args: Dict[str, str]) -> Dict[str, Any]:
    """Convert stored string args into the types declared by the tool's schema."""
    params = _tool_param_schemas().get(tool_name, {})
    props = params.get("properties", {})
    out: Dict[str, Any] = {}
    for key, value in raw_args.items():
        json_type = props.get(key, {}).get("type", "string")
        try:
            if json_type == "integer":
                out[key] = int(float(value))
            elif json_type == "number":
                out[key] = float(value)
            elif json_type == "boolean":
                out[key] = str(value).strip().lower() in ("1", "true", "yes", "y", "on")
            else:
                out[key] = value
        except (TypeError, ValueError):
            out[key] = value
    return out


def validate_automation(auto: Automation) -> List[str]:
    """Return a list of human-readable problems (empty list = valid)."""
    errors: List[str] = []
    if not auto.name.strip():
        errors.append("Missing a name.")
    kind = auto.trigger.WhichOneof("kind")
    if kind is None:
        errors.append("Missing a trigger.")
    elif kind == "time":
        t = auto.trigger.time
        if not (0 <= t.hour <= 23 and 0 <= t.minute <= 59):
            errors.append(f"Invalid time {t.hour}:{t.minute:02d}.")
    elif kind == "once":
        try:
            datetime.datetime.fromisoformat(auto.trigger.once.datetime)
        except ValueError:
            errors.append(f"Invalid date/time '{auto.trigger.once.datetime}'.")
    elif kind == "keyword" and not auto.trigger.keyword.phrase.strip():
        errors.append("Keyword trigger needs a phrase.")

    if not auto.actions:
        errors.append("Needs at least one action.")
    schemas = _tool_param_schemas()
    for action in auto.actions:
        if action.tool not in AUTOMATION_ACTION_TOOLS:
            errors.append(
                f"Tool '{action.tool}' can't be used in automations "
                f"(allowed: {', '.join(AUTOMATION_ACTION_TOOLS)})."
            )
            continue
        if action.tool not in schemas:
            errors.append(f"Unknown tool '{action.tool}'.")
            continue
        params = schemas[action.tool]
        props = params.get("properties", {})
        for req in params.get("required", []):
            if req not in action.args:
                errors.append(f"{action.tool} is missing required arg '{req}'.")
        for key in action.args:
            if props and key not in props:
                errors.append(f"{action.tool} has no parameter '{key}'.")
    return errors


# ==========================================
# Store
# ==========================================

class AutomationStore:
    """Thread-safe CRUD over `automations.textproto`. Reloads automatically when the file changes."""

    def __init__(self, path: str = AUTOMATIONS_FILE):
        self.path = path
        self._lock = threading.RLock()
        self._registry = AutomationRegistry()
        self._mtime: float = -1.0
        self._reload_if_changed()

    def _reload_if_changed(self) -> None:
        with self._lock:
            mtime = os.path.getmtime(self.path) if os.path.exists(self.path) else 0.0
            if mtime == self._mtime:
                return
            registry = AutomationRegistry()
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    text_format.Parse(f.read(), registry)
            self._registry = registry
            self._mtime = mtime

    def _save(self) -> None:
        with self._lock:
            body = text_format.MessageToString(self._registry, as_utf8=True)
            tmp_path = self.path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(_FILE_HEADER + body)
            os.replace(tmp_path, self.path)
            self._mtime = os.path.getmtime(self.path)

    def list(self) -> List[Automation]:
        with self._lock:
            self._reload_if_changed()
            return [Automation.FromString(a.SerializeToString()) for a in self._registry.automations]

    def get(self, automation_id: str) -> Optional[Automation]:
        for auto in self.list():
            if auto.id == automation_id:
                return auto
        return None

    def find(self, id_or_name: str) -> Optional[Automation]:
        """Look up by exact id, then case-insensitive name, then unique id/name prefix."""
        needle = (id_or_name or "").strip().lower()
        autos = self.list()
        for auto in autos:
            if auto.id.lower() == needle or auto.name.lower() == needle:
                return auto
        matches = [a for a in autos if a.id.lower().startswith(needle) or a.name.lower().startswith(needle)]
        return matches[0] if len(matches) == 1 else None

    def unique_id(self, base: str) -> str:
        existing = {a.id for a in self.list()}
        candidate = slugify(base)
        n = 2
        while candidate in existing:
            candidate = f"{slugify(base)}_{n}"
            n += 1
        return candidate

    def add(self, auto: Automation) -> Automation:
        with self._lock:
            self._reload_if_changed()
            if not auto.id:
                auto.id = self.unique_id(auto.name)
            if not auto.created_at:
                auto.created_at = now_local().isoformat(timespec="seconds")
            self._registry.automations.append(auto)
            self._save()
            return auto

    def update(self, automation_id: str, mutate: Callable[[Automation], None]) -> Optional[Automation]:
        with self._lock:
            self._reload_if_changed()
            for auto in self._registry.automations:
                if auto.id == automation_id:
                    mutate(auto)
                    self._save()
                    return Automation.FromString(auto.SerializeToString())
            return None

    def delete(self, automation_id: str) -> bool:
        with self._lock:
            self._reload_if_changed()
            for i, auto in enumerate(self._registry.automations):
                if auto.id == automation_id:
                    del self._registry.automations[i]
                    self._save()
                    return True
            return False

    def set_enabled(self, automation_id: str, enabled: bool) -> Optional[Automation]:
        def _set(a: Automation) -> None:
            a.enabled = enabled
        return self.update(automation_id, _set)


_store: Optional[AutomationStore] = None
_store_lock = threading.Lock()


def get_store() -> AutomationStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = AutomationStore()
        return _store


# ==========================================
# Execution
# ==========================================

def run_automation(auto: Automation, reason: str = "manual",
                   now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Execute every action of an automation in order via `dispatch_tool`. Never calls the LLM."""
    from tools import dispatch_tool  # lazy: tools.py imports this module
    from proto_tools import preapproved_tools

    results: List[Dict[str, Any]] = []
    ok = True
    for action in auto.actions:
        if action.tool not in AUTOMATION_ACTION_TOOLS:
            ok = False
            results.append({"tool": action.tool, "args": dict(action.args), "ok": False,
                            "result": "Error: tool not allowed in automations"})
            continue
        args = coerce_args(action.tool, dict(action.args))
        with preapproved_tools(AUTOMATION_PREAPPROVED_TOOLS):
            res = dispatch_tool(action.tool, args, tool_call_id=f"automation:{auto.id}")
        content = str(res.get("content", ""))
        failed = content.startswith("Error") or content.startswith("Execution aborted")
        ok = ok and not failed
        results.append({"tool": action.tool, "args": args, "ok": not failed, "result": content[:300]})

    stamp = (now or now_local()).isoformat(timespec="seconds")

    def _mark(a: Automation) -> None:
        a.last_run = stamp
        if a.trigger.WhichOneof("kind") == "once":
            a.enabled = False

    get_store().update(auto.id, _mark)
    return {"automation": auto.id, "name": auto.name, "reason": reason, "ok": ok, "results": results}


def summarize_run(run: Dict[str, Any]) -> str:
    icon = "⚡" if run["ok"] else "⚠️"
    lines = [f"{icon} Automation '{run['name']}' ran ({run['reason']})."]
    for r in run["results"]:
        mark = "✓" if r["ok"] else "✗"
        lines.append(f"  {mark} {r['tool']}" + ("" if r["ok"] else f": {r['result'][:120]}"))
    return "\n".join(lines)


def match_keyword_automations(text: str) -> List[Automation]:
    """Return enabled keyword automations whose phrase appears in `text` (case-insensitive, word-bounded)."""
    lowered = (text or "").lower()
    matches: List[Automation] = []
    for auto in get_store().list():
        if not auto.enabled or auto.trigger.WhichOneof("kind") != "keyword":
            continue
        phrase = auto.trigger.keyword.phrase.strip().lower()
        if phrase and re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", lowered):
            matches.append(auto)
    return matches


def run_keyword_automations(text: str) -> Optional[str]:
    """If `text` triggers keyword automations, run them and return a summary reply; else None."""
    matches = match_keyword_automations(text)
    if not matches:
        return None
    return "\n".join(summarize_run(run_automation(a, reason=f'keyword "{a.trigger.keyword.phrase}"')) for a in matches)


# ==========================================
# Scheduler
# ==========================================

def telegram_push(text: str) -> None:
    """Push `text` to every allowed Telegram chat in mobile_config.json (no-op if not configured)."""
    import httpx

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_ids: List[int] = []
    if os.path.exists(MOBILE_CONFIG_FILE):
        try:
            with open(MOBILE_CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            token = token or str(cfg.get("telegram_bot_token", "")).strip()
            chat_ids = [int(c) for c in cfg.get("allowed_chat_ids", []) if str(c).lstrip("-").isdigit()]
        except Exception:
            pass
    if not token or ":" not in token or not chat_ids:
        return
    for chat_id in chat_ids:
        try:
            httpx.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text[:4000]},
                timeout=10.0,
            )
        except Exception as e:
            print(f"\033[31m[Automations]\033[0m Telegram push failed: {e}")

def _is_due(auto: Automation, now: datetime.datetime) -> bool:
    kind = auto.trigger.WhichOneof("kind")
    last_run = None
    if auto.last_run:
        try:
            last_run = datetime.datetime.fromisoformat(auto.last_run)
        except ValueError:
            last_run = None

    if kind == "time":
        t = auto.trigger.time
        days = set(auto.trigger.time.days)
        if days and WEEKDAY_ORDER[now.weekday()] not in days:
            return False
        if (now.hour, now.minute) != (t.hour, t.minute):
            return False
        # Fire at most once per scheduled minute.
        return last_run is None or last_run.strftime("%Y-%m-%d %H:%M") != now.strftime("%Y-%m-%d %H:%M")

    if kind == "once":
        try:
            target = datetime.datetime.fromisoformat(auto.trigger.once.datetime)
        except ValueError:
            return False
        if target.tzinfo is None:
            target = target.replace(tzinfo=now.tzinfo)
        # Fire if due within the last 10 minutes (tolerates brief downtime), and not yet run.
        return target <= now <= target + datetime.timedelta(minutes=10) and last_run is None

    return False


class AutomationScheduler:
    """Background thread that fires time / once automations.

    Only one process at a time runs the scheduler (guarded by an exclusive file lock), so running
    the CLI and the Telegram bot simultaneously never double-fires an automation.
    """

    def __init__(self, notifier: Optional[Callable[[str], None]] = telegram_push, poll_seconds: float = 15.0):
        self.notifier = notifier
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock_fh = None

    def _acquire_process_lock(self) -> bool:
        try:
            self._lock_fh = open(SCHEDULER_LOCK_FILE, "w")
            fcntl.flock(self._lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_fh.write(str(os.getpid()))
            self._lock_fh.flush()
            return True
        except OSError:
            if self._lock_fh:
                self._lock_fh.close()
                self._lock_fh = None
            return False

    def start(self) -> bool:
        """Start the scheduler thread. Returns False if another process already owns the scheduler."""
        if self._thread and self._thread.is_alive():
            return True
        if not self._acquire_process_lock():
            print("\033[93m[Automations]\033[0m Scheduler already running in another process; not starting here.")
            return False
        self._thread = threading.Thread(target=self._loop, name="AutomationScheduler", daemon=True)
        self._thread.start()
        count = len([a for a in get_store().list() if a.enabled])
        print(f"\033[92m[Automations]\033[0m Scheduler started ({count} enabled automation(s)).")
        return True

    def stop(self) -> None:
        self._stop.set()

    def tick(self, now: Optional[datetime.datetime] = None) -> List[Dict[str, Any]]:
        """Run all due automations once. Exposed for testing."""
        now = now or now_local()
        runs = []
        for auto in get_store().list():
            if auto.enabled and _is_due(auto, now):
                run = run_automation(auto, reason=format_trigger(auto), now=now)
                runs.append(run)
                summary = summarize_run(run)
                print(f"\033[96m[Automations]\033[0m {summary}")
                if auto.notify and self.notifier:
                    try:
                        self.notifier(summary)
                    except Exception as e:
                        print(f"\033[31m[Automations]\033[0m Notifier failed: {e}")
        return runs

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:
                print(f"\033[31m[Automations]\033[0m Scheduler tick error: {e}")
            self._stop.wait(self.poll_seconds)
