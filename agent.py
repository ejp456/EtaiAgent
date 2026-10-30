import json
from dataclasses import dataclass
from typing import List, Dict, Optional, Any
from llm import LLMClient
from tools import dispatch_tool, get_tools_schema

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful AI agent with persistent long-term memory and tool-use capabilities.\n\n"
    "Guidelines for tool use and memory retrieval:\n"
    "1. Searching Context & Answering Questions: When the user asks a question about past projects, notes, "
    "facts, preferences, or tasks, first use `search_files_by_embedding` to find relevant memory files. "
    "If the search returns relevant files but the snippet preview is brief, call `read_local_file` to read the full context "
    "before answering.\n"
    "2. Grounded Responses: Ground your answers in the retrieved context. If relevant memory files exist, cite or refer to them. "
    "If no relevant memory is found after searching, inform the user and answer to the best of your general ability.\n"
    "3. Storing Memories: When asked to remember, note, or save information, use `write_local_file` to save it to a descriptive "
    "filename in `memories/`, then call `calculate_file_embedding` so it is indexed for future semantic searches.\n"
    "4. Updating Memories: Use `write_local_file` with mode='append' to add to existing notes, or mode='write' to overwrite, "
    "and re-index with `calculate_file_embedding`.\n"
    "5. Smart Home & Lights Control (via Home Assistant): You can inspect and control smart lights and blinds in the house. "
    "Use `list_smart_devices` to see all lights, rooms, and media players. "
    "Use `control_light(device, state, brightness, color_temperature, color)` to turn lights on or off (`state='on'` or `state='off'`), dim them, or change color. "
    "To turn off or turn on ALL lights in the house at once, call `control_light(device='all', state='off')` or `control_light(device='all', state='on')`. "
    "Use `control_blinds(device, action, position)` to open, close, or adjust blinds.\n"
    "6. Google Cast, Samsung Frame TV, & PS5 Media Control (via Home Assistant & JustWatch): You can query live streaming catalogs, cast directly to Google Cast devices, recommend shows based on mood, and control playback. "
    "Use `list_streaming_apps` to see all supported apps (Peacock, Netflix, YouTube, Disney+, Hulu, Max, Prime Video, Apple TV+, Paramount+, Crunchyroll, Plex, Spotify, Twitch). "
    "Use `search_streaming_catalog(query, app, mood_or_genre, content_type, limit)` to query JustWatch's live catalog—either to check which streaming app has a specific show/movie, or to browse popular titles on a specific service or genre. "
    "When the user asks for show ideas based on mood or vibe (e.g. 'funny', 'cozy', 'thriller', 'mind-bending'), call `recommend_shows_by_mood(mood, app)` or `search_streaming_catalog(mood_or_genre=..., app=...)`. "
    "To cast a YouTube video (by search query or URL), direct stream URL, or voice announcement (`app='tts'`) to the Living Room Chromecast Ultra (`living_room_tv`), `bathroom_speaker`, `kitchen_clock`, or `home_group`, call `cast_to_tv(query_or_url, app, target_device, dim_lights)`. "
    "When the user wants to launch a streaming app or play a show on the TV/PS5, call `watch_on_ps5(app, show_title, skip_profile_prompt, dim_lights)` (you can pass `app='auto'` to auto-detect the streaming app for `show_title` via JustWatch). "
    "For live playback or navigation commands ('pause', 'play', 'stop', 'up', 'down', 'left', 'right', 'enter', 'back', 'volume_up', 'volume_down', 'mute', 'ps5_standby'), call `control_ps5_media(command, repeat)`. "
    "To inspect or update the Home Assistant connection (`http://127.0.0.1:8123`), call `configure_home_assistant(ha_url, ha_token, action)`.\n"
    "7. Current Date & Time: Whenever you need to know the current date, time, day of the week, or answer time-sensitive queries, invoke the `get_current_time` tool.\n"
    "8. Web, System, & Productivity Tools: You have declarative tools for checking weather (`get_weather`), looking up Wikipedia summaries (`wikipedia_summary`), "
    "fetching webpage text (`fetch_webpage_text`), searching files (`list_directory`, `find_files`, `search_in_files`), checking system/network diagnostics (`check_disk_usage`, `top_processes`, `network_info`, `ping_host`), "
    "interacting with macOS (`read_clipboard`, `write_clipboard`, `speak_aloud`, `desktop_notification`), and running code/git (`git_status_summary`, `run_python_snippet`, `count_words`, `sandboxed_bash`, `calculate`, `get_system_info`)."
)

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
        messages.insert(0, Message(role="system", content=DEFAULT_SYSTEM_PROMPT))

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
