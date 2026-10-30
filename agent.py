import os
import json
from dataclasses import dataclass
from typing import List, Dict, Optional, Any
from llm import LLMClient
from tools import dispatch_tool, get_tools_schema

SYSTEM_INSTRUCTIONS_FILE = os.path.join(os.path.dirname(__file__), "SYSTEM_INSTRUCTIONS.md")

_FALLBACK_SYSTEM_PROMPT = (
    "You are a helpful AI agent with persistent long-term memory, smart home controls, "
    "media playback capabilities, and a full suite of system tools."
)


def load_system_instructions() -> str:
    """Load the system prompt from SYSTEM_INSTRUCTIONS.md if present, otherwise fallback to default."""
    if os.path.exists(SYSTEM_INSTRUCTIONS_FILE):
        try:
            with open(SYSTEM_INSTRUCTIONS_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return content
        except Exception as e:
            print(f"[WARN: load_system_instructions] Could not read '{SYSTEM_INSTRUCTIONS_FILE}': {e}")
    return _FALLBACK_SYSTEM_PROMPT


DEFAULT_SYSTEM_PROMPT = load_system_instructions()

@dataclass
class Message:
    role: str
    content: Optional[str] = ""
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"role": self.role}
        if self.content is not None:
            data["content"] = self.content
        if self.tool_call_id is not None:
            data["tool_call_id"] = self.tool_call_id
        if self.tool_calls is not None:
            data["tool_calls"] = self.tool_calls
        return data

def trim_context(messages: List[Message], max_turns: int = 10) -> List[Message]:
    """Trim conversation history to preserve the system message plus the last `max_turns` user turns.
    Preserves entire tool-call chains by slicing at user turn boundaries.
    """
    if not messages:
        return messages

    system_messages = [msg for msg in messages if msg.role == "system"]
    non_system = [msg for msg in messages if msg.role != "system"]

    user_indices = [i for i, msg in enumerate(non_system) if msg.role == "user"]
    if len(user_indices) <= max_turns:
        return system_messages + non_system

    cutoff_user_idx = user_indices[-max_turns]
    return system_messages + non_system[cutoff_user_idx:]

def run_agent(
    messages: List[Message], 
    tools: Optional[List[Dict]] = None, 
    max_turns: int = 8,
    llm_client: Optional[LLMClient] = None
) -> List[Message]:
    """Main agentic loop with explicit state management"""
    if llm_client is None:
        llm_client = LLMClient()
    if tools is None:
        tools = get_tools_schema()

    # Ensure a system prompt exists
    if not any(msg.role == "system" for msg in messages):
        messages.insert(0, Message(role="system", content=load_system_instructions()))

    # Retain system prompt + last 10 conversation turns
    messages = trim_context(messages, max_turns=10)
    
    for turn in range(max_turns):
        print(f"\033[94m[THOUGHT]\033[0m Turn {turn+1}/{max_turns}")
        
        # 1. Send messages + tools to LLM
        assistant_dict = llm_client.complete(messages, tools)
        
        # 2. Append assistant response
        content = assistant_dict.get("content") or ""
        tool_calls = assistant_dict.get("tool_calls") or []
        
        assistant_msg = Message(
            role="assistant",
            content=content,
            tool_calls=tool_calls if tool_calls else None
        )
        messages.append(assistant_msg)
        
        # 3. Check for tool calls
        if not tool_calls:
            print("\033[92m[FINAL ANSWER]\033[0m No more tool calls needed")
            break
            
        # 4. Process each tool call
        for tool_call in tool_calls:
            fn_info = tool_call.get("function", {})
            if isinstance(fn_info, dict):
                fn_name = fn_info.get("name", "")
                raw_args = fn_info.get("arguments", {})
            else:
                fn_name = str(fn_info)
                raw_args = tool_call.get("parameters", {})

            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args) if raw_args.strip() else {}
                except Exception:
                    args = {"expression": raw_args}
            elif isinstance(raw_args, dict):
                args = raw_args
            else:
                args = {}

            tool_call_id = tool_call.get("id")
            print(f"\033[93m[TOOL CALL]\033[0m {fn_name}({args})")
            
            tool_response = dispatch_tool(
                fn_name, 
                args,
                tool_call_id=tool_call_id
            )
            messages.append(Message(
                role=tool_response["role"],
                content=tool_response["content"],
                tool_call_id=tool_response.get("tool_call_id")
            ))
    
    return messages
