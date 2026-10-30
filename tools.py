import inspect
import platform
import datetime
import json
import os
import math
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from llm import LLMClient, _bge_small_embedding, BGE_EMBEDDINGS_FILE
from proto_tools import ProtoToolRegistry, prompt_user_confirmation
from memory_search import MemorySearchEngine

proto_registry = ProtoToolRegistry()
_memory_search_engine = MemorySearchEngine(memories_dir="memories", index_file=BGE_EMBEDDINGS_FILE)


def warmup_memory_indices_on_startup() -> Dict[str, Any]:
    """Pre-load memory embeddings index at server startup."""
    return _memory_search_engine.warmup_on_startup()

MEMORIES_DIR = "memories"
EMBEDDINGS_INDEX_FILE = os.path.join(MEMORIES_DIR, "embeddings.json")

TYPE_MAP = {
    "str": "string",
    "string": "string",
    "int": "integer",
    "integer": "integer",
    "float": "number",
    "number": "number",
    "bool": "boolean",
    "boolean": "boolean",
    "dict": "object",
    "list": "array",
}

def tool(func=None, *, require_confirmation: bool = False):
    """Register a tool and generate its JSON schema, optionally requiring user confirmation."""
    def decorator(f):
        sig = inspect.signature(f)
        properties = {}
        required = []
        parameters_meta = {}

        for name, param in sig.parameters.items():
            type_name = param.annotation.__name__.lower() if param.annotation is not inspect.Parameter.empty else "string"
            json_type = TYPE_MAP.get(type_name, "string")
            properties[name] = {
                "type": json_type,
                "description": f"Parameter '{name}'",
            }
            is_required = param.default is inspect.Parameter.empty
            if is_required:
                required.append(name)
            parameters_meta[name] = {
                "type": json_type,
                "required": is_required
            }

        desc = (f.__doc__ or "").strip()
        if require_confirmation:
            desc += " (Requires user confirmation before executing)"

        schema = {
            "type": "function",
            "function": {
                "name": f.__name__,
                "description": desc,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                }
            }
        }

        f.schema = schema
        f._parameters_meta = parameters_meta
        f._require_confirmation = require_confirmation
        return f

    if func is not None:
        return decorator(func)
    return decorator

def get_tools_schema() -> List[Dict[str, Any]]:
    """Return OpenAI-compatible tool schemas for all decorated tools in this module and protobuf tools"""
    tools = []
    for obj in globals().values():
        if inspect.isfunction(obj) and hasattr(obj, "schema"):
            tools.append(obj.schema)
    # Include declarative protobuf tools first, then place core Python/Smart Home/Media tools at the end
    # so smaller local models (with 4k context windows) always see the smart home & media tools in their prompt tail.
    return proto_registry.get_schemas() + tools

def _resolve_file_path(path: str) -> str:
    """Normalize file path, routing bare filenames into the memories/ directory by default."""
    if os.path.exists(path):
        return path
    mem_path = os.path.join(MEMORIES_DIR, path)
    if os.path.exists(mem_path):
        return mem_path
    if not path.startswith("/") and not path.startswith("./") and not path.startswith("memories/"):
        return os.path.join(MEMORIES_DIR, path)
    return path

def _cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """Compute cosine similarity between two vector lists."""
    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0
    dot = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = math.sqrt(sum(a * a for a in vec1))
    norm2 = math.sqrt(sum(b * b for b in vec2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)

def _load_embeddings_index() -> Dict[str, Any]:
    """Load the embeddings index from disk."""
    if os.path.exists(EMBEDDINGS_INDEX_FILE):
        try:
            with open(EMBEDDINGS_INDEX_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def _save_embeddings_index(index_data: Dict[str, Any]) -> None:
    """Save the embeddings index to disk."""
    os.makedirs(MEMORIES_DIR, exist_ok=True)
    with open(EMBEDDINGS_INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index_data, f, indent=2)

def dispatch_tool(tool_name: str, args: Any, tool_call_id: Optional[str] = None) -> Dict[str, Any]:
    """Execute a tool and return formatted tool response"""
    print(f"\033[35m[DEBUG: dispatch_tool]\033[0m Exercising '{tool_name}' | args={args} | call_id={tool_call_id}")

    # Check declarative Protobuf tools first
    if proto_registry.has_tool(tool_name):
        parsed_args = args
        if isinstance(args, str):
            try:
                parsed_args = json.loads(args) if args.strip() else {}
            except Exception:
                parsed_args = {"input": args}
        elif not isinstance(args, dict):
            parsed_args = {}

        res = proto_registry.execute_tool(tool_name, parsed_args, call_id=tool_call_id)
        res_preview = str(res.get("content", "")).replace("\n", " ")
        if len(res_preview) > 90:
            res_preview = res_preview[:90] + "..."
        print(f"\033[35m[DEBUG: dispatch_tool]\033[0m (Protobuf) '{tool_name}' finished -> {res_preview}")
        return res

    tool_func = None
    for obj in globals().values():
        if inspect.isfunction(obj) and hasattr(obj, "schema"):
            if obj.schema["function"]["name"] == tool_name:
                tool_func = obj
                break

    if not tool_func:
        print(f"\033[31m[DEBUG: dispatch_tool]\033[0m Tool '{tool_name}' NOT FOUND in registered tools.")
        return {
            "role": "tool",
            "content": f"Error: Tool '{tool_name}' not found",
            "tool_call_id": tool_call_id
        }

    try:
        # Handle string args
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except Exception:
                args = {"expression": args}
        elif not isinstance(args, dict):
            args = {}

        # Validate arguments against tool schema
        params_meta = getattr(tool_func, "_parameters_meta", {})
        for param, meta in params_meta.items():
            if param in args:
                val = args[param]
                if meta["type"] == "integer" and not isinstance(val, int):
                    raise ValueError(f"Parameter '{param}' must be an integer")
                elif meta["type"] == "number" and not isinstance(val, (int, float)):
                    raise ValueError(f"Parameter '{param}' must be a number")
                elif meta["type"] == "boolean" and not isinstance(val, bool):
                    raise ValueError(f"Parameter '{param}' must be a boolean")
            elif meta["required"]:
                raise ValueError(f"Missing required parameter '{param}'")

        # Execute the tool with only parameters accepted by func, falling back to defaults
        sig = inspect.signature(tool_func)
        call_kwargs = {}
        for name, param in sig.parameters.items():
            if name in args:
                call_kwargs[name] = args[name]
            elif param.default is not inspect.Parameter.empty:
                call_kwargs[name] = param.default

        if getattr(tool_func, "_require_confirmation", False):
            if not prompt_user_confirmation(tool_name, call_kwargs):
                return {
                    "role": "tool",
                    "content": f"Execution aborted: User denied permission to run '{tool_name}'.",
                    "tool_call_id": tool_call_id
                }

        result = tool_func(**call_kwargs)
        res_preview = str(result).replace("\n", " ")
        if len(res_preview) > 90:
            res_preview = res_preview[:90] + "..."
        print(f"\033[35m[DEBUG: dispatch_tool]\033[0m '{tool_name}' finished -> {res_preview}")

        return {
            "role": "tool",
            "content": str(result) if not isinstance(result, (dict, list)) else json.dumps(result, indent=2),
            "tool_call_id": tool_call_id
        }
    except Exception as e:
        print(f"\033[31m[DEBUG: dispatch_tool]\033[0m '{tool_name}' ERROR: {str(e)}")
        return {
            "role": "tool",
            "content": f"Error: {str(e)}",
            "tool_call_id": tool_call_id
        }

# ==========================================
# Core Agent Tools
# ==========================================

@tool
def calculate(expression: str) -> float:
    """Evaluate mathematical expressions"""
    return eval(expression)

@tool
def read_local_file(path: str) -> str:
    """Read contents of a local file"""
    target_path = _resolve_file_path(path)
    with open(target_path, 'r', encoding="utf-8") as f:
        return f.read()

@tool
def write_local_file(path: str, content: str, mode: str = "write") -> str:
    """Write or update contents of a file. Supports mode='write' (overwrite) or mode='append'."""
    target_path = _resolve_file_path(path)
    dir_name = os.path.dirname(target_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    file_mode = "a" if mode.lower() == "append" else "w"
    with open(target_path, file_mode, encoding="utf-8") as f:
        f.write(content)

    return f"Successfully wrote {len(content)} characters to '{target_path}' (mode='{mode}')"

@tool
def calculate_file_embedding(path: str) -> Dict[str, Any]:
    """Calculate and store vector embeddings for a file's contents using BAAI/bge-small-en-v1.5."""
    target_path = _resolve_file_path(path)
    if not os.path.exists(target_path):
        return {"error": f"File '{target_path}' does not exist"}

    with open(target_path, "r", encoding="utf-8") as f:
        content = f.read()

    bge_vec = _bge_small_embedding(content, is_query=False)

    index_data = _load_embeddings_index()
    index_data[target_path] = {
        "path": target_path,
        "content_preview": content[:120].strip() + ("..." if len(content) > 120 else ""),
        "embedding": bge_vec,
        "dimension": len(bge_vec),
    }
    _save_embeddings_index(index_data)

    startup_stats = _memory_search_engine.index_all(force_save=True)

    return {
        "status": "indexed",
        "file": target_path,
        "dimension": len(bge_vec),
        "preview": index_data[target_path]["content_preview"],
        "index_stats": startup_stats,
    }

@tool
def search_files_by_embedding(query: str, top_k: int = 3) -> List[Dict[str, Any]]:
    """Search memory files using BAAI/bge-small-en-v1.5 neural contextual vector search."""
    results = _memory_search_engine.search(query=query, top_k=top_k)
    if not results:
        return [{"message": "No indexed files found. Store files in memories/ first."}]

    formatted: List[Dict[str, Any]] = []
    for r in results:
        formatted.append({
            "path": r.doc_id,
            "chunk_id": r.chunk_id,
            "similarity": r.score,
            "preview": r.snippet,
            "parent_context": r.parent_context,
        })
    return formatted

@tool
def get_current_time() -> Dict[str, Any]:
    """Get the current live date, time, day of the week, and local timezone from the system clock."""
    now = datetime.datetime.now().astimezone()
    return {
        "datetime": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%I:%M:%S %p"),
        "day_of_week": now.strftime("%A"),
        "formatted": now.strftime("%A, %B %d, %Y at %I:%M:%S %p %Z"),
        "timezone": str(now.tzname())
    }

@tool
def get_system_info() -> Dict:
    """Return system information"""
    return {
        "os": platform.system(),
        "python_version": platform.python_version(),
        "architecture": platform.machine()
    }

# ==========================================
# Smart Home & Home Assistant Tools
# ==========================================
from smart_home import SmartHomeController

_smart_home_controller = SmartHomeController()

@tool
def list_smart_devices() -> List[Dict[str, Any]]:
    """List all available smart home devices (lights, rooms, TV, PS5, blinds) and their current states in Home Assistant."""
    return _smart_home_controller.list_devices()

@tool
def control_light(
    device: str = "all",
    state: str = "on",
    brightness: int = 100,
    color_temperature: str = "",
    color: str = ""
) -> Dict[str, Any]:
    """Turn lights on or off, or adjust brightness/color via Home Assistant.
    device: Name of the light, room, or 'all' for all lights (e.g. 'all', 'Living Room', 'Office', 'Kitchen', 'Bedroom', 'Go', 'Office Lamp')
    state: 'on' or 'off' (use 'off' to turn lights off)
    brightness: Brightness level from 0 to 100%
    color_temperature: Optional preset ('candlelight', 'warm', 'warm_white', 'neutral', 'cool', 'daylight') or Kelvin
    color: Optional color ('red', 'green', 'blue', 'yellow', 'orange', 'purple', 'pink', 'cyan', 'white', 'candlelight')
    """
    return _smart_home_controller.control_light(
        device=device,
        state=state,
        brightness=brightness,
        color_temperature=color_temperature,
        color=color
    )

@tool
def control_blinds(device: str, action: str, position: int = 100) -> Dict[str, Any]:
    """Control smart motorized blinds ('open', 'close', or 'set_position')."""
    return _smart_home_controller.control_blinds(device, action, position)

# ==========================================
# PS5 & Samsung Frame TV Media Tools (via Home Assistant)
# ==========================================
from ps5_media import PS5MediaController

_ps5_media_controller = PS5MediaController()

@tool
def list_streaming_apps() -> List[Dict[str, Any]]:
    """List all supported streaming apps on the PS5 and Samsung Frame TV (Peacock, Netflix, YouTube, Disney+, Hulu, Max, Prime Video, Apple TV+, Paramount+, Crunchyroll, Plex, Spotify, Twitch)."""
    return _ps5_media_controller.list_apps()

@tool
def recommend_shows_by_mood(mood: str, app: str = "", limit: int = 5) -> Dict[str, Any]:
    """Recommend TV shows to watch on the PS5 based on mood or vibe ('funny', 'cozy', 'lighthearted', 'thriller', 'mind-bending', 'sci-fi', 'dark', 'action', 'date_night') and optional app filter."""
    return _ps5_media_controller.recommend_by_mood(mood=mood, app=app, limit=limit)

@tool
def search_streaming_catalog(
    query: str = "",
    app: str = "",
    mood_or_genre: str = "",
    content_type: str = "",
    limit: int = 8
) -> Dict[str, Any]:
    """Query live streaming app catalogs via JustWatch to discover what shows/movies are on a specific PS5 app or find which app streams a specific title.
    query: Optional show or movie title to look up (e.g. 'Severance', 'Dune', 'The Day of the Jackal'). Leave empty to browse popular/trending titles.
    app: Optional PS5 streaming app filter (e.g. 'peacock', 'netflix', 'max', 'hulu', 'disney_plus', 'prime_video', 'apple_tv', 'paramount_plus', 'crunchyroll', 'plex').
    mood_or_genre: Optional genre or mood filter (e.g. 'comedy', 'thriller', 'sci-fi', 'drama', 'action', 'crime', 'horror', 'documentary', 'animation', 'funny', 'cozy').
    content_type: Optional filter for 'show' ('tv') or 'movie' (leave empty for both).
    limit: Maximum number of catalog results to return (default 8).
    """
    return _ps5_media_controller.search_streaming_catalog(
        query=query,
        app=app,
        mood_or_genre=mood_or_genre,
        content_type=content_type,
        limit=limit
    )

@tool
def watch_on_ps5(
    app: str = "auto",
    show_title: str = "",
    skip_profile_prompt: bool = True,
    dim_lights: bool = False
) -> Dict[str, Any]:
    """Wake the PS5 and Samsung Frame TV via Home Assistant, launch a streaming app (Peacock, Netflix, Hulu, Max, YouTube, Disney+, Prime Video, Apple TV+), and optionally search for and play a show.
    app: Streaming app name (e.g. 'peacock', 'netflix', 'hulu', 'max', 'youtube') or 'auto' to auto-detect which streaming app has `show_title` via JustWatch
    show_title: Optional title of the show/movie to search and start playing
    skip_profile_prompt: Auto-select default profile on 'Who's Watching?' screen
    dim_lights: If True, also dims the Living Room 'Go' light to 20% candlelight
    """
    res = _ps5_media_controller.watch_on_ps5(
        app=app,
        show_title=show_title,
        skip_profile_prompt=skip_profile_prompt
    )
    if dim_lights:
        light_res = _smart_home_controller.control_light(
            device="Go",
            state="on",
            brightness=20,
            color_temperature="candlelight"
        )
        res["ambient_lighting"] = light_res
    return res

@tool
def cast_to_tv(
    query_or_url: str,
    app: str = "youtube",
    target_device: str = "living_room_tv",
    dim_lights: bool = False
) -> Dict[str, Any]:
    """Cast a YouTube video (by search query, URL, or video ID), direct media stream URL, or TTS voice message to Google Cast via Home Assistant.
    query_or_url: YouTube search query (e.g. 'The Office cold open', 'Severance trailer'), YouTube URL/ID, direct media URL, or TTS message text.
    app: 'youtube' (default), 'url' (direct video/audio stream), or 'tts' (speak voice announcement on Cast speaker/TV).
    target_device: Google Cast target ('living_room_tv', 'bathroom_speaker', 'kitchen_clock', or 'home_group').
    dim_lights: If True, also dims the Living Room 'Go' light to 20% candlelight.
    """
    res = _ps5_media_controller.cast_to_tv(
        query_or_url=query_or_url,
        app=app,
        target_device=target_device
    )
    if dim_lights:
        light_res = _smart_home_controller.control_light(
            device="Go",
            state="on",
            brightness=20,
            color_temperature="candlelight"
        )
        res["ambient_lighting"] = light_res
    return res

@tool
def control_ps5_media(command: str, repeat: int = 1) -> Dict[str, Any]:
    """Send live playback or navigation commands ('play', 'pause', 'stop', 'up', 'down', 'left', 'right', 'enter', 'back', 'volume_up', 'volume_down', 'mute', 'ps5_wake', 'ps5_standby') to Google Cast, Samsung Frame TV, or PS5 via Home Assistant."""
    return _ps5_media_controller.control_media(command=command, repeat=repeat)

@tool
def configure_home_assistant(
    ha_url: str = "",
    ha_token: str = "",
    action: str = "status"
) -> Dict[str, Any]:
    """Check status or configure the Home Assistant connection (`http://127.0.0.1:8123`) and discover media_player, remote, and light entities."""
    ha = _ps5_media_controller.ha
    if ha_url.strip():
        ha.ha_url = ha_url.strip().rstrip("/")
    if ha_token.strip():
        ha.ha_token = ha_token.strip()
    if ha_url.strip() or ha_token.strip() or action.lower() == "save":
        ha.save_config()

    if ha.is_configured():
        return ha.discover_media_entities()
    return ha.check_connection()




