import os
import re
import shlex
import subprocess
import threading
from contextlib import contextmanager
from typing import Dict, Any, Iterable, Iterator, List, Optional
import google.protobuf.text_format as text_format
from tool_definition_pb2 import (
    ToolRegistryConfig,
    ToolDefinition,
    ParameterType,
    CommandExecution
)

PARAM_TYPE_MAP = {
    ParameterType.TYPE_STRING: "string",
    ParameterType.TYPE_INTEGER: "integer",
    ParameterType.TYPE_NUMBER: "number",
    ParameterType.TYPE_BOOLEAN: "boolean",
}

REMOTE_SAFE_CONFIRMATION_TOOLS = {
    "speak_aloud",
    "desktop_notification",
    "fetch_webpage_text",
    "read_clipboard",
    "write_clipboard",
}

# Per-thread set of tool names the user already approved ahead of time (e.g. when saving an
# automation). Lets scheduled automations run confirmation-gated tools without blocking on input().
_preapproval_context = threading.local()


@contextmanager
def preapproved_tools(tool_names: Iterable[str]) -> Iterator[None]:
    """Within this context (current thread only), treat `tool_names` as already confirmed by the user."""
    previous = getattr(_preapproval_context, "names", frozenset())
    _preapproval_context.names = frozenset(previous) | frozenset(tool_names)
    try:
        yield
    finally:
        _preapproval_context.names = previous


def prompt_user_confirmation(
    tool_name: str,
    args: Dict[str, Any],
    command_preview: Optional[str] = None
) -> bool:
    """Prompt the user in the terminal for explicit acknowledgement before running a sensitive tool."""
    if tool_name in getattr(_preapproval_context, "names", frozenset()):
        print(f"\033[93m[CONFIRMATION]\033[0m Tool '{tool_name}' -> pre-approved (saved automation)")
        return True
    if os.environ.get("ETAI_AGENT_AUTO_APPROVE_ALL") == "1":
        return True
    if os.environ.get("ETAI_AGENT_REMOTE_MODE") == "1":
        approved = tool_name in REMOTE_SAFE_CONFIRMATION_TOOLS
        status_str = "auto-approved (remote safe list)" if approved else "denied in remote mode"
        print(f"\033[93m[REMOTE CONFIRMATION]\033[0m Tool '{tool_name}' -> {status_str}")
        return approved

    print(f"\n\033[93m⚠️  [USER CONFIRMATION REQUIRED]\033[0m")
    print(f"   \033[1mTool:\033[0m      {tool_name}")
    print(f"   \033[1mArguments:\033[0m {args}")
    if command_preview:
        preview = command_preview if len(command_preview) <= 120 else command_preview[:120] + "..."
        print(f"   \033[1mCommand:\033[0m   {preview}")
    try:
        answer = input(f"\033[93m   Allow execution of '{tool_name}'? [y/N]: \033[0m").strip().lower()
        approved = answer in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        approved = False

    if approved:
        print(f"\033[92m   [✓] Approved by user.\033[0m")
    else:
        print(f"\033[91m   [✗] Denied by user.\033[0m")
    return approved

class ProtoToolRegistry:
    """Registry that loads declarative tools from Protocol Buffer configurations."""

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or "tools.textproto"
        self.tools: Dict[str, ToolDefinition] = {}
        if os.path.exists(self.config_path):
            self.load_from_file(self.config_path)

    def load_from_file(self, path: str) -> None:
        """Parse tools from a .textproto or binary .pb file."""
        self.config_path = path
        registry_proto = ToolRegistryConfig()

        if path.endswith(".pb") or path.endswith(".binproto"):
            with open(path, "rb") as f:
                registry_proto.ParseFromString(f.read())
        else:
            with open(path, "r", encoding="utf-8") as f:
                text_format.Parse(f.read(), registry_proto)

        self.tools.clear()
        for tool in registry_proto.tools:
            self.tools[tool.name] = tool

    def has_tool(self, name: str) -> bool:
        return name in self.tools

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        return self.tools.get(name)

    def get_schemas(self) -> List[Dict[str, Any]]:
        """Generate OpenAI-compatible tool schemas from registered protobuf tools."""
        schemas = []
        for tool in self.tools.values():
            properties = {}
            required = []

            for param in tool.parameters:
                p_type = PARAM_TYPE_MAP.get(param.type, "string")
                properties[param.name] = {
                    "type": p_type,
                    "description": param.description or f"Parameter '{param.name}'"
                }
                if param.required:
                    required.append(param.name)

            desc = tool.description.strip()
            if tool.require_confirmation:
                desc += " (Requires user confirmation before executing)"

            schemas.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": desc,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required
                    }
                }
            })
        return schemas

    def execute_tool(self, tool_name: str, args: Dict[str, Any], call_id: Optional[str] = None) -> Dict[str, Any]:
        """Execute a declared protobuf tool and return formatted tool response."""
        tool = self.tools.get(tool_name)
        if not tool:
            return {
                "role": "tool",
                "content": f"Error: Protobuf tool '{tool_name}' not found",
                "tool_call_id": call_id
            }

        # 1. Fill defaults and validate required parameters
        exec_args = dict(args or {})
        for param in tool.parameters:
            if param.name not in exec_args:
                if param.default_value:
                    if param.type == ParameterType.TYPE_INTEGER:
                        exec_args[param.name] = int(param.default_value)
                    elif param.type == ParameterType.TYPE_NUMBER:
                        exec_args[param.name] = float(param.default_value)
                    elif param.type == ParameterType.TYPE_BOOLEAN:
                        exec_args[param.name] = param.default_value.lower() in ("true", "1")
                    else:
                        exec_args[param.name] = param.default_value
                elif param.required:
                    return {
                        "role": "tool",
                        "content": f"Error: Missing required parameter '{param.name}' for tool '{tool_name}'",
                        "tool_call_id": call_id
                    }

        # 2. Handle execution based on oneof type
        exec_type = tool.WhichOneof("execution")
        if exec_type == "command":
            return self._execute_command(tool, tool.command, exec_args, call_id)

        return {
            "role": "tool",
            "content": f"Error: Unsupported execution type '{exec_type}' for tool '{tool_name}'",
            "tool_call_id": call_id
        }

    def _execute_command(
        self,
        tool: ToolDefinition,
        cmd_def: CommandExecution,
        args: Dict[str, Any],
        call_id: Optional[str]
    ) -> Dict[str, Any]:
        """Interpolate parameters, request user confirmation if configured, and run shell command."""
        cmd_str = cmd_def.command_template

        # Substitute {{param}} templates with quoted arguments,
        # and also expose raw values as ARG_<NAME> environment variables
        env = os.environ.copy()
        for k, v in args.items():
            env[f"ARG_{k.upper()}"] = str(v)
            quoted_val = shlex.quote(str(v))
            cmd_str = cmd_str.replace(f"{{{{{k}}}}}", quoted_val)

        # Check for unfulfilled {{param}} templates
        unresolved = re.findall(r"\{\{(\w+)\}\}", cmd_str)
        if unresolved:
            return {
                "role": "tool",
                "content": f"Error: Unresolved template variables: {unresolved}",
                "tool_call_id": call_id
            }

        # Enforce user acknowledgement if require_confirmation is set
        if tool.require_confirmation:
            if not prompt_user_confirmation(tool.name, args, command_preview=cmd_str):
                return {
                    "role": "tool",
                    "content": f"Execution aborted: User denied permission to run '{tool.name}'.",
                    "tool_call_id": call_id
                }

        cwd = cmd_def.working_directory or os.getcwd()
        timeout = cmd_def.timeout_seconds if cmd_def.timeout_seconds > 0 else 15

        # Check if execution should run under Anthropic srt
        srt_bin = "./node_modules/.bin/srt"
        srt_cfg = "srt-settings.json"
        if cmd_def.run_in_sandbox and os.path.exists(srt_bin) and os.path.exists(srt_cfg):
            full_command = [srt_bin, "--settings", srt_cfg, "--", "bash", "-c", cmd_str]
        else:
            full_command = ["bash", "-c", cmd_str]

        try:
            res = subprocess.run(
                full_command,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            output = res.stdout.strip()
            if res.returncode != 0:
                err = res.stderr.strip()
                result_text = f"Exit code {res.returncode}:\n{err or output or 'Command failed'}"
            else:
                result_text = output if output else "Command completed successfully (no output)."

            return {
                "role": "tool",
                "content": result_text,
                "tool_call_id": call_id
            }
        except subprocess.TimeoutExpired:
            return {
                "role": "tool",
                "content": f"Error: Command timed out after {timeout} seconds",
                "tool_call_id": call_id
            }
        except Exception as e:
            return {
                "role": "tool",
                "content": f"Execution error: {str(e)}",
                "tool_call_id": call_id
            }
