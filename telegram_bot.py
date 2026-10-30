import os
import sys
import json
import time
import argparse
import threading
from typing import Dict, Any, List, Optional
import httpx

from agent import run_agent, Message, DEFAULT_SYSTEM_PROMPT, trim_context
from tools import get_tools_schema, list_streaming_apps, list_smart_devices

MOBILE_CONFIG_FILE = "mobile_config.json"


def load_mobile_config(config_path: str = MOBILE_CONFIG_FILE) -> Dict[str, Any]:
    """Load mobile server and Telegram bot configuration from disk or environment."""
    cfg: Dict[str, Any] = {
        "host": os.environ.get("MOBILE_HOST", "0.0.0.0"),
        "port": int(os.environ.get("MOBILE_PORT", "8650")),
        "api_token": os.environ.get("MOBILE_API_TOKEN", ""),
        "telegram_bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        "allowed_chat_ids": [],
    }
    env_chats = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
    if env_chats:
        for part in env_chats.split(","):
            part = part.strip()
            if part.lstrip("-").isdigit():
                cfg["allowed_chat_ids"].append(int(part))

    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                disk = json.load(f)
                if isinstance(disk, dict):
                    if disk.get("host"):
                        cfg["host"] = str(disk["host"])
                    if disk.get("port"):
                        cfg["port"] = int(disk["port"])
                    if disk.get("api_token") and not cfg["api_token"]:
                        cfg["api_token"] = str(disk["api_token"])
                    if disk.get("telegram_bot_token") and not cfg["telegram_bot_token"]:
                        cfg["telegram_bot_token"] = str(disk["telegram_bot_token"]).strip()
                    if isinstance(disk.get("allowed_chat_ids"), list) and not cfg["allowed_chat_ids"]:
                        cfg["allowed_chat_ids"] = [int(x) for x in disk["allowed_chat_ids"] if str(x).lstrip("-").isdigit()]
        except Exception:
            pass
    return cfg


def save_mobile_config(cfg: Dict[str, Any], config_path: str = MOBILE_CONFIG_FILE) -> None:
    """Persist mobile and Telegram configuration to disk."""
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


class AgentSessionManager:
    """Thread-safe multi-session manager wrapping `run_agent` for mobile web, Siri Shortcuts, and Telegram."""

    def __init__(self, max_turns_context: int = 10, max_agent_steps: int = 8):
        self.max_turns_context = max_turns_context
        self.max_agent_steps = max_agent_steps
        self.tools = get_tools_schema()
        self._sessions: Dict[str, List[Message]] = {}
        self._display_history: Dict[str, List[Dict[str, Any]]] = {}
        self._locks: Dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()

    def _get_session_lock(self, session_id: str) -> threading.Lock:
        with self._global_lock:
            if session_id not in self._locks:
                self._locks[session_id] = threading.Lock()
            if session_id not in self._sessions:
                self._sessions[session_id] = [Message(role="system", content=DEFAULT_SYSTEM_PROMPT)]
            if session_id not in self._display_history:
                self._display_history[session_id] = []
            return self._locks[session_id]

    def reset_session(self, session_id: str = "default") -> Dict[str, Any]:
        """Clear conversation history for a given session ID."""
        lock = self._get_session_lock(session_id)
        with lock:
            self._sessions[session_id] = [Message(role="system", content=DEFAULT_SYSTEM_PROMPT)]
            self._display_history[session_id] = []
        return {"success": True, "session_id": session_id, "message": "Conversation history cleared."}

    def get_history(self, session_id: str = "default") -> List[Dict[str, Any]]:
        """Return display-friendly chat history for a session."""
        lock = self._get_session_lock(session_id)
        with lock:
            return list(self._display_history.get(session_id, []))

    def send_message(self, session_id: str, user_text: str) -> Dict[str, Any]:
        """Process a user message through `run_agent` and return the reply and executed tool calls."""
        os.environ["ETAI_AGENT_REMOTE_MODE"] = "1"
        cleaned = (user_text or "").strip()
        if not cleaned:
            return {"success": False, "session_id": session_id, "error": "Empty message."}

        lock = self._get_session_lock(session_id)
        with lock:
            messages = self._sessions[session_id]
            messages.append(Message(role="user", content=cleaned))
            messages = trim_context(messages, max_turns=self.max_turns_context)
            start_idx = len(messages) - 1

            try:
                messages = run_agent(messages, self.tools, max_turns=self.max_agent_steps)
                self._sessions[session_id] = messages
            except Exception as e:
                err_msg = f"Agent error: {str(e)}"
                return {
                    "success": False,
                    "session_id": session_id,
                    "error": err_msg,
                    "reply": err_msg,
                    "tool_calls": [],
                }

            new_msgs = messages[start_idx + 1:]
            turn_tool_calls: List[Dict[str, Any]] = []
            assistant_replies: List[str] = []

            for m in new_msgs:
                if m.role == "assistant":
                    if m.tool_calls:
                        for tc in m.tool_calls:
                            fn = tc.get("function", {})
                            if isinstance(fn, dict):
                                name = fn.get("name", "tool")
                                args = fn.get("arguments", {})
                            else:
                                name = str(fn)
                                args = tc.get("parameters", {})
                            if isinstance(args, str):
                                try:
                                    args = json.loads(args) if args.strip() else {}
                                except Exception:
                                    pass
                            turn_tool_calls.append({"name": name, "arguments": args})
                    if m.content and m.content.strip():
                        assistant_replies.append(m.content.strip())

            final_reply = assistant_replies[-1] if assistant_replies else "Done."
            ts = time.strftime("%I:%M %p")
            self._display_history[session_id].append({
                "role": "user",
                "content": cleaned,
                "time": ts,
            })
            self._display_history[session_id].append({
                "role": "assistant",
                "content": final_reply,
                "tool_calls": turn_tool_calls,
                "time": ts,
            })
            # Cap display history at 40 entries
            if len(self._display_history[session_id]) > 40:
                self._display_history[session_id] = self._display_history[session_id][-40:]

            return {
                "success": True,
                "session_id": session_id,
                "reply": final_reply,
                "tool_calls": turn_tool_calls,
            }


class TelegramBotRunner:
    """Long-polling Telegram Bot bridge for EtaiAgent."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        allowed_chat_ids: Optional[List[int]] = None,
        session_manager: Optional[AgentSessionManager] = None,
        config_path: str = MOBILE_CONFIG_FILE,
    ):
        self.config_path = config_path
        cfg = load_mobile_config(config_path)
        self.bot_token = (bot_token or cfg.get("telegram_bot_token") or "").strip()
        self.allowed_chat_ids: List[int] = (
            list(allowed_chat_ids)
            if allowed_chat_ids is not None
            else list(cfg.get("allowed_chat_ids", []))
        )
        self.session_manager = session_manager or AgentSessionManager()
        self.running = False
        self.bot_username: Optional[str] = None
        self._offset: int = 0
        self._thread: Optional[threading.Thread] = None

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token and ":" in self.bot_token)

    def _api_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}/{method}"

    def get_me(self, timeout: float = 5.0) -> Dict[str, Any]:
        """Verify the Telegram bot token and fetch the bot's username."""
        if not self.is_configured:
            return {"ok": False, "error": "Telegram bot token is not configured."}
        try:
            r = httpx.get(self._api_url("getMe"), timeout=timeout)
            data = r.json()
            if data.get("ok"):
                self.bot_username = data.get("result", {}).get("username")
            return data
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def send_text(self, chat_id: int, text: str) -> None:
        """Send a text message to a Telegram chat, splitting into chunks if > 4000 characters."""
        if not text:
            return
        max_len = 4000
        chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)]
        for chunk in chunks:
            try:
                httpx.post(
                    self._api_url("sendMessage"),
                    json={"chat_id": chat_id, "text": chunk},
                    timeout=10.0,
                )
            except Exception as e:
                print(f"\033[31m[Telegram]\033[0m Failed to send message: {e}")

    def send_typing(self, chat_id: int) -> None:
        """Send 'typing...' indicator to the Telegram chat while the agent runs."""
        try:
            httpx.post(
                self._api_url("sendChatAction"),
                json={"chat_id": chat_id, "action": "typing"},
                timeout=4.0,
            )
        except Exception:
            pass

    def _authorize_chat(self, chat_id: int, username: str = "") -> bool:
        """Authorize a Telegram `chat_id`. Auto-binds the very first user if `allowed_chat_ids` is empty."""
        if not self.allowed_chat_ids:
            self.allowed_chat_ids.append(chat_id)
            cfg = load_mobile_config(self.config_path)
            cfg["telegram_bot_token"] = self.bot_token
            cfg["allowed_chat_ids"] = self.allowed_chat_ids
            save_mobile_config(cfg, self.config_path)
            print(
                f"\033[92m[Telegram]\033[0m Auto-registered owner chat_id={chat_id} "
                f"(@{username or 'owner'}) in {self.config_path}"
            )
            return True
        return chat_id in self.allowed_chat_ids

    def handle_message(self, message: Dict[str, Any]) -> None:
        """Handle a single incoming Telegram message."""
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        text = (message.get("text") or "").strip()
        user = message.get("from") or {}
        username = user.get("username") or user.get("first_name") or ""

        if chat_id is None or not text:
            return

        if not self._authorize_chat(int(chat_id), username=username):
            self.send_text(
                int(chat_id),
                f"⛔ Unauthorized chat_id ({chat_id}). Add this ID to allowed_chat_ids in {self.config_path} to grant access."
            )
            return

        session_id = f"telegram_{chat_id}"
        lower = text.lower()

        if lower in ("/start", "/help"):
            welcome = (
                "👋 EtaiAgent is connected!\n\n"
                "You can message me naturally to control your home and PS5:\n"
                "• \"What comedy shows are on Peacock?\"\n"
                "• \"Where can I stream Severance?\"\n"
                "• \"Play Severance on PS5 and dim the lights\"\n"
                "• \"Pause the PS5\" / \"Turn off all lights\"\n\n"
                "Commands:\n"
                "/reset — Clear conversation memory\n"
                "/apps — List PS5 streaming apps\n"
                "/devices — List smart home devices"
            )
            self.send_text(int(chat_id), welcome)
            return

        if lower in ("/reset", "/clear"):
            self.session_manager.reset_session(session_id)
            self.send_text(int(chat_id), "🧹 Conversation history cleared.")
            return

        if lower == "/apps":
            apps = list_streaming_apps()
            lines = ["📺 Supported PS5 Streaming Apps:"]
            for a in apps:
                lines.append(f"• {a['name']} ({a['id']}) — {a['description']}")
            self.send_text(int(chat_id), "\n".join(lines))
            return

        if lower == "/devices":
            devs = list_smart_devices()
            lines = ["🏠 Smart Home Devices:"]
            for d in devs[:25]:
                lines.append(f"• {d.get('name')} ({d.get('room', 'Home')}): {d.get('state')}")
            self.send_text(int(chat_id), "\n".join(lines))
            return

        print(f"\033[96m[Telegram @{username} ({chat_id})]\033[0m {text}")
        self.send_typing(int(chat_id))
        res = self.session_manager.send_message(session_id, text)
        reply = res.get("reply") or "Done."
        tools_used = [t["name"] for t in res.get("tool_calls", [])]
        if tools_used:
            reply += f"\n\n🔧 Tools: {', '.join(tools_used)}"
        self.send_text(int(chat_id), reply)

    def poll_forever(self) -> None:
        """Run the long-polling loop against Telegram's `getUpdates` API."""
        if not self.is_configured:
            print("\033[93m[Telegram]\033[0m No bot token configured yet. Skipping Telegram polling.")
            return

        me = self.get_me()
        if not me.get("ok"):
            print(f"\033[31m[Telegram]\033[0m Bot token check failed: {me.get('error') or me}")
            return

        print(f"\033[92m[Telegram]\033[0m Polling active for @{self.bot_username} (allowed_chat_ids={self.allowed_chat_ids or 'auto-bind first user'})")
        self.running = True
        while self.running:
            try:
                r = httpx.get(
                    self._api_url("getUpdates"),
                    params={"offset": self._offset, "timeout": 20},
                    timeout=25.0,
                )
                if r.status_code == 200:
                    data = r.json()
                    for upd in data.get("result", []):
                        upd_id = upd.get("update_id", 0)
                        if upd_id >= self._offset:
                            self._offset = upd_id + 1
                        msg = upd.get("message") or upd.get("edited_message")
                        if msg:
                            self.handle_message(msg)
                else:
                    time.sleep(3.0)
            except KeyboardInterrupt:
                self.running = False
                break
            except Exception:
                time.sleep(3.0)

    def start_background(self) -> bool:
        """Start the Telegram polling loop in a background daemon thread if configured."""
        if not self.is_configured:
            return False
        self._thread = threading.Thread(target=self.poll_forever, daemon=True)
        self._thread.start()
        return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Run EtaiAgent Telegram Bot")
    parser.add_argument("--token", default="", help="Telegram Bot Token from @BotFather (saved to mobile_config.json)")
    parser.add_argument("--chat-id", type=int, default=None, help="Optional Telegram chat_id to whitelist")
    args = parser.parse_args()

    cfg = load_mobile_config()
    if args.token.strip():
        cfg["telegram_bot_token"] = args.token.strip()
        save_mobile_config(cfg)
    if args.chat_id is not None and args.chat_id not in cfg["allowed_chat_ids"]:
        cfg["allowed_chat_ids"].append(args.chat_id)
        save_mobile_config(cfg)

    bot = TelegramBotRunner(
        bot_token=cfg["telegram_bot_token"],
        allowed_chat_ids=cfg["allowed_chat_ids"],
    )
    if not bot.is_configured:
        print("=" * 64)
        print("  Telegram Bot Token Needed")
        print("=" * 64)
        print("  1. Open Telegram on your phone and message @BotFather")
        print("  2. Send /newbot and follow the prompts to get your Bot Token")
        print("  3. Run:")
        print("     ./.venv/bin/python telegram_bot.py --token YOUR_BOT_TOKEN")
        print("=" * 64)
        sys.exit(1)

    bot.poll_forever()


if __name__ == "__main__":
    main()
