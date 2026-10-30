"""EtaiAgent Automations Package.

Provides a declarative, textproto-backed rules and automation engine with:
- Deterministic time, date, and keyword triggers
- Safe action execution against registered tools without routing through LLMs
- Guided, conversational automation builder wizard
"""
from automations import engine
from automations import wizard
from automations.automation_pb2 import Automation, AutomationRegistry, Weekday
from automations.engine import (
    AUTOMATIONS_FILE,
    AUTOMATION_ACTION_TOOLS,
    AUTOMATION_PREAPPROVED_TOOLS,
    WEEKDAY_ORDER,
    WEEKDAY_SHORT,
    AutomationStore,
    AutomationScheduler,
    get_store,
    run_automation,
    summarize_run,
    format_trigger,
    format_action,
    describe_automation,
    automation_to_textproto,
    match_keyword_automations,
    run_keyword_automations,
    validate_automation,
    coerce_args,
    now_local,
    telegram_push,
)
from automations.wizard import (
    AutomationWizard,
    preprocess_user_message,
    postprocess_agent_turn,
    request_wizard,
    start_wizard,
    has_active_wizard,
)

__all__ = [
    "Automation",
    "AutomationRegistry",
    "Weekday",
    "AUTOMATIONS_FILE",
    "AUTOMATION_ACTION_TOOLS",
    "AUTOMATION_PREAPPROVED_TOOLS",
    "WEEKDAY_ORDER",
    "WEEKDAY_SHORT",
    "AutomationStore",
    "AutomationScheduler",
    "get_store",
    "run_automation",
    "summarize_run",
    "format_trigger",
    "format_action",
    "describe_automation",
    "automation_to_textproto",
    "match_keyword_automations",
    "run_keyword_automations",
    "validate_automation",
    "coerce_args",
    "now_local",
    "telegram_push",
    "AutomationWizard",
    "preprocess_user_message",
    "postprocess_agent_turn",
    "request_wizard",
    "start_wizard",
    "has_active_wizard",
]
