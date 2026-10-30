import os
import json
import socket
import argparse
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from typing import Dict, Any, Optional

from telegram_bot import (
    AgentSessionManager,
    TelegramBotRunner,
    load_mobile_config,
    save_mobile_config,
    MOBILE_CONFIG_FILE,
)
from tools import warmup_memory_indices_on_startup


def get_lan_ip() -> str:
    """Detect the Mac's active local Wi-Fi/LAN IPv4 address."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.168.86.1", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except Exception:
        pass
    for iface in ("en0", "en1"):
        try:
            res = subprocess.run(["ipconfig", "getifaddr", iface], capture_output=True, text=True, timeout=2.0)
            ip = res.stdout.strip()
            if ip and not ip.startswith("127."):
                return ip
        except Exception:
            pass
    return "127.0.0.1"


MOBILE_WEB_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover" />
  <meta name="apple-mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
  <meta name="apple-mobile-web-app-title" content="EtaiAgent" />
  <title>EtaiAgent Mobile</title>
  <style>
    :root {
      --bg: #0b0f19;
      --panel: #151c2c;
      --panel-border: rgba(255, 255, 255, 0.09);
      --accent: #6366f1;
      --accent-glow: rgba(99, 102, 241, 0.25);
      --text: #f3f4f6;
      --muted: #9ca3af;
      --user-bubble: #4f46e5;
      --bot-bubble: #1e293b;
      --chip-bg: rgba(255, 255, 255, 0.06);
    }
    * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      display: flex;
      flex-direction: column;
      height: 100dvh;
      overflow: hidden;
    }
    header {
      padding: calc(env(safe-area-inset-top, 0px) + 12px) 16px 12px;
      background: rgba(21, 28, 44, 0.88);
      backdrop-filter: blur(14px);
      border-bottom: 1px solid var(--panel-border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      z-index: 10;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .status-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: #10b981;
      box-shadow: 0 0 10px #10b981;
    }
    .brand h1 {
      font-size: 17px;
      font-weight: 600;
      margin: 0;
    }
    .brand span {
      font-size: 12px;
      color: var(--muted);
      display: block;
    }
    .header-actions {
      display: flex;
      gap: 8px;
    }
    .icon-btn {
      background: var(--chip-bg);
      color: var(--text);
      border: 1px solid var(--panel-border);
      border-radius: 10px;
      padding: 7px 11px;
      font-size: 12px;
      font-weight: 500;
      cursor: pointer;
    }
    .chips-bar {
      display: flex;
      gap: 8px;
      overflow-x: auto;
      padding: 10px 14px;
      background: rgba(15, 23, 42, 0.75);
      border-bottom: 1px solid var(--panel-border);
      scrollbar-width: none;
    }
    .chips-bar::-webkit-scrollbar { display: none; }
    .chip {
      flex-shrink: 0;
      background: var(--chip-bg);
      border: 1px solid var(--panel-border);
      color: #e5e7eb;
      padding: 7px 12px;
      border-radius: 999px;
      font-size: 13px;
      cursor: pointer;
      white-space: nowrap;
      transition: background 0.15s;
    }
    .chip:active { background: var(--accent); }
    #chat-container {
      flex: 1;
      overflow-y: auto;
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .msg {
      max-width: 86%;
      padding: 12px 15px;
      border-radius: 18px;
      line-height: 1.45;
      font-size: 15px;
      word-wrap: break-word;
      white-space: pre-wrap;
    }
    .msg.user {
      align-self: flex-end;
      background: var(--user-bubble);
      color: #fff;
      border-bottom-right-radius: 5px;
    }
    .msg.assistant {
      align-self: flex-start;
      background: var(--bot-bubble);
      border: 1px solid var(--panel-border);
      border-bottom-left-radius: 5px;
    }
    .tool-pills {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 8px;
    }
    .tool-pill {
      font-size: 11px;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      background: rgba(99, 102, 241, 0.16);
      color: #a5b4fc;
      border: 1px solid rgba(99, 102, 241, 0.35);
      padding: 3px 8px;
      border-radius: 6px;
    }
    .composer {
      padding: 10px 12px calc(env(safe-area-inset-bottom, 0px) + 10px);
      background: rgba(21, 28, 44, 0.94);
      border-top: 1px solid var(--panel-border);
      display: flex;
      gap: 8px;
      align-items: center;
    }
    .composer input {
      flex: 1;
      background: rgba(255, 255, 255, 0.06);
      border: 1px solid var(--panel-border);
      color: var(--text);
      padding: 12px 15px;
      border-radius: 22px;
      font-size: 16px;
      outline: none;
    }
    .composer input:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 2px var(--accent-glow);
    }
    .send-btn {
      background: var(--accent);
      color: #fff;
      border: none;
      border-radius: 50%;
      width: 42px;
      height: 42px;
      font-size: 18px;
      font-weight: bold;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }
    .send-btn:disabled { opacity: 0.45; }
    .modal-backdrop {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.65);
      z-index: 30;
      align-items: center;
      justify-content: center;
      padding: 16px;
    }
    .modal {
      background: var(--panel);
      border: 1px solid var(--panel-border);
      border-radius: 16px;
      padding: 18px;
      width: 100%;
      max-width: 420px;
    }
    .modal h2 { margin: 0 0 8px; font-size: 17px; }
    .modal p { margin: 0 0 12px; font-size: 13px; color: var(--muted); line-height: 1.4; }
    .modal input {
      width: 100%;
      background: rgba(255, 255, 255, 0.07);
      border: 1px solid var(--panel-border);
      color: var(--text);
      padding: 10px 12px;
      border-radius: 10px;
      font-size: 14px;
      margin-bottom: 12px;
    }
    .modal-actions { display: flex; justify-content: flex-end; gap: 8px; }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <div class="status-dot"></div>
      <div>
        <h1>EtaiAgent</h1>
        <span id="sub-status">PS5 • JustWatch • Hue Lights</span>
      </div>
    </div>
    <div class="header-actions">
      <button class="icon-btn" onclick="openTelegramModal()">Telegram</button>
      <button class="icon-btn" onclick="resetChat()">Reset</button>
    </div>
  </header>

  <div class="chips-bar">
    <button class="chip" onclick="sendQuick('What comedy shows are popular on Peacock right now?')">🦚 Peacock Comedies</button>
    <button class="chip" onclick="sendQuick('What sci-fi thrillers are streaming on Max or Apple TV+?')">🛸 Sci-Fi Thrillers</button>
    <button class="chip" onclick="sendQuick('Play The Office on Peacock on PS5')">▶️ Play The Office</button>
    <button class="chip" onclick="sendQuick('Pause the PS5')">⏸️ Pause PS5</button>
    <button class="chip" onclick="sendQuick('Set Living Room Go light to 20% candlelight')">🕯️ Movie Lighting</button>
    <button class="chip" onclick="sendQuick('Turn off all lights')">🌙 All Lights Off</button>
  </div>

  <div id="chat-container"></div>

  <form class="composer" onsubmit="handleSubmit(event)">
    <input id="msg-input" type="text" placeholder="Ask what's streaming or control PS5 / lights..." autocomplete="off" />
    <button id="send-btn" class="send-btn" type="submit">↑</button>
  </form>

  <div id="tg-modal" class="modal-backdrop">
    <div class="modal">
      <h2>Telegram Bot Setup</h2>
      <p>Message <b>@BotFather</b> on Telegram with <code>/newbot</code>, then paste your bot token below to message EtaiAgent from anywhere over 5G or Wi-Fi.</p>
      <input id="tg-token-input" type="text" placeholder="123456789:ABCdefGHIjklMNOpqrSTUvwxYZ" />
      <div class="modal-actions">
        <button class="icon-btn" onclick="closeTelegramModal()">Cancel</button>
        <button class="icon-btn" style="background: var(--accent); border-color: var(--accent);" onclick="saveTelegramToken()">Save & Start Bot</button>
      </div>
    </div>
  </div>

  <script>
    const chatContainer = document.getElementById('chat-container');
    const msgInput = document.getElementById('msg-input');
    const sendBtn = document.getElementById('send-btn');
    const subStatus = document.getElementById('sub-status');

    function appendMessage(role, content, toolCalls = []) {
      const div = document.createElement('div');
      div.className = 'msg ' + role;
      if (role === 'assistant' && toolCalls && toolCalls.length > 0) {
        const pills = document.createElement('div');
        pills.className = 'tool-pills';
        toolCalls.forEach(tc => {
          const pill = document.createElement('span');
          pill.className = 'tool-pill';
          pill.textContent = '⚡ ' + tc.name;
          pills.appendChild(pill);
        });
        div.appendChild(pills);
      }
      const textNode = document.createElement('div');
      textNode.textContent = content;
      div.appendChild(textNode);
      chatContainer.appendChild(div);
      chatContainer.scrollTop = chatContainer.scrollHeight;
      return div;
    }

    async function loadHistory() {
      try {
        const r = await fetch('/api/history?session_id=mobile_web');
        const data = await r.json();
        chatContainer.innerHTML = '';
        if (!data.history || data.history.length === 0) {
          appendMessage(
            'assistant',
            'Hi! You can ask me what shows are streaming on any app (via JustWatch), launch shows on your PS5, or control your Hue lights.'
          );
        } else {
          data.history.forEach(item => appendMessage(item.role, item.content, item.tool_calls || []));
        }
        if (data.telegram_bot) {
          subStatus.textContent = 'Telegram @' + data.telegram_bot + ' • Active';
        }
      } catch (e) {}
    }

    async function sendText(text) {
      if (!text.trim()) return;
      appendMessage('user', text);
      msgInput.value = '';
      sendBtn.disabled = true;
      const thinkingDiv = appendMessage('assistant', 'Thinking...');

      try {
        const r = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: text, session_id: 'mobile_web' })
        });
        const data = await r.json();
        thinkingDiv.remove();
        appendMessage('assistant', data.reply || data.error || 'No response', data.tool_calls || []);
      } catch (err) {
        thinkingDiv.textContent = 'Connection error: ' + err.message;
      } finally {
        sendBtn.disabled = false;
      }
    }

    function handleSubmit(e) {
      e.preventDefault();
      sendText(msgInput.value);
    }

    function sendQuick(prompt) {
      sendText(prompt);
    }

    async function resetChat() {
      await fetch('/api/reset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: 'mobile_web' })
      });
      loadHistory();
    }

    function openTelegramModal() {
      document.getElementById('tg-modal').style.display = 'flex';
    }
    function closeTelegramModal() {
      document.getElementById('tg-modal').style.display = 'none';
    }
    async function saveTelegramToken() {
      const token = document.getElementById('tg-token-input').value.trim();
      if (!token) return;
      const r = await fetch('/api/telegram_config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ telegram_bot_token: token })
      });
      const data = await r.json();
      closeTelegramModal();
      if (data.success) {
        appendMessage('assistant', '✅ Telegram bot started (@' + (data.bot_username || 'bot') + ')! Open Telegram on your phone and send /start to your bot.');
        loadHistory();
      } else {
        appendMessage('assistant', '❌ Telegram token error: ' + (data.error || 'Invalid token'));
      }
    }

    loadHistory();
  </script>
</body>
</html>
"""


class MobileAgentHTTPHandler(BaseHTTPRequestHandler):
    """HTTP handler serving the mobile PWA and Siri / Apple Shortcuts JSON API."""

    session_manager: AgentSessionManager
    telegram_bot: TelegramBotRunner
    api_token: str = ""
    config_path: str = MOBILE_CONFIG_FILE

    def log_message(self, format: str, *args: Any) -> None:
        print(f"\033[90m[MobileHTTP {self.client_address[0]}]\033[0m {format % args}")

    def _check_auth(self) -> bool:
        if not self.api_token:
            return True
        auth_header = self.headers.get("Authorization", "")
        token_header = self.headers.get("X-API-Token", "")
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        query_token = (qs.get("token") or [""])[0]
        if (
            auth_header == f"Bearer {self.api_token}"
            or token_header == self.api_token
            or query_token == self.api_token
        ):
            return True
        self._send_json(401, {"success": False, "error": "Unauthorized. Provide Bearer token or ?token=..."})
        return False

    def _send_json(self, status_code: int, payload: Dict[str, Any]) -> None:
        raw = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def _read_body_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="replace").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
            return {"message": str(parsed)}
        except Exception:
            # Support plain-text POST bodies (e.g. directly from an iOS Shortcut)
            return {"message": raw}

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-API-Token")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            body = MOBILE_WEB_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if not self._check_auth():
            return

        if parsed.path == "/api/health":
            self._send_json(200, {
                "success": True,
                "status": "running",
                "lan_ip": get_lan_ip(),
                "telegram_configured": self.telegram_bot.is_configured,
                "telegram_running": self.telegram_bot.running,
                "telegram_bot_username": self.telegram_bot.bot_username,
            })
            return

        if parsed.path == "/api/history":
            qs = parse_qs(parsed.query)
            session_id = (qs.get("session_id") or ["mobile_web"])[0]
            self._send_json(200, {
                "success": True,
                "session_id": session_id,
                "history": self.session_manager.get_history(session_id),
                "telegram_bot": self.telegram_bot.bot_username,
            })
            return

        self._send_json(404, {"success": False, "error": f"Unknown endpoint '{parsed.path}'"})

    def do_POST(self) -> None:
        if not self._check_auth():
            return

        parsed = urlparse(self.path)
        data = self._read_body_json()

        if parsed.path in ("/api/chat", "/chat"):
            message = (
                data.get("message")
                or data.get("prompt")
                or data.get("text")
                or data.get("query")
                or ""
            )
            session_id = str(data.get("session_id") or "mobile_web")
            res = self.session_manager.send_message(session_id=session_id, user_text=str(message))
            status = 200 if res.get("success") else 400
            self._send_json(status, res)
            return

        if parsed.path in ("/api/reset", "/reset"):
            session_id = str(data.get("session_id") or "mobile_web")
            res = self.session_manager.reset_session(session_id=session_id)
            self._send_json(200, res)
            return

        if parsed.path == "/api/telegram_config":
            token = str(data.get("telegram_bot_token") or "").strip()
            if not token or ":" not in token:
                self._send_json(400, {"success": False, "error": "Invalid Telegram bot token format."})
                return

            cfg = load_mobile_config(self.config_path)
            cfg["telegram_bot_token"] = token
            save_mobile_config(cfg, self.config_path)

            self.telegram_bot.bot_token = token
            me = self.telegram_bot.get_me()
            if not me.get("ok"):
                self._send_json(400, {"success": False, "error": me.get("error") or "Telegram getMe failed."})
                return

            if not self.telegram_bot.running:
                self.telegram_bot.start_background()

            self._send_json(200, {
                "success": True,
                "bot_username": self.telegram_bot.bot_username,
                "message": f"Telegram bot @{self.telegram_bot.bot_username} is now active!",
            })
            return

        self._send_json(404, {"success": False, "error": f"Unknown POST endpoint '{parsed.path}'"})


def create_mobile_server(
    host: str = "0.0.0.0",
    port: int = 8650,
    api_token: str = "",
    telegram_token: str = "",
    config_path: str = MOBILE_CONFIG_FILE,
) -> ThreadingHTTPServer:
    """Create and configure the multi-threaded mobile HTTP server and optional Telegram bot."""
    cfg = load_mobile_config(config_path)
    if telegram_token.strip():
        cfg["telegram_bot_token"] = telegram_token.strip()
        save_mobile_config(cfg, config_path)
    if api_token.strip():
        cfg["api_token"] = api_token.strip()
        save_mobile_config(cfg, config_path)

    session_mgr = AgentSessionManager()
    # Pre-load BAAI/bge-small-en-v1.5 memory embeddings index at server startup
    warmup_memory_indices_on_startup()
    tg_bot = TelegramBotRunner(
        bot_token=cfg.get("telegram_bot_token", ""),
        allowed_chat_ids=cfg.get("allowed_chat_ids", []),
        session_manager=session_mgr,
        config_path=config_path,
    )
    if tg_bot.is_configured:
        tg_bot.start_background()

    class BoundHandler(MobileAgentHTTPHandler):
        pass

    BoundHandler.session_manager = session_mgr
    BoundHandler.telegram_bot = tg_bot
    BoundHandler.api_token = cfg.get("api_token", "")
    BoundHandler.config_path = config_path

    return ThreadingHTTPServer((host, port), BoundHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run EtaiAgent Mobile Web Server + Siri API + Telegram Bot")
    parser.add_argument("--host", default=None, help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="Bind port (default: 8650)")
    parser.add_argument("--telegram-token", default="", help="Optional @BotFather token to enable Telegram bot")
    parser.add_argument("--api-token", default="", help="Optional Bearer token for HTTP API authentication")
    args = parser.parse_args()

    cfg = load_mobile_config()
    host = args.host or cfg.get("host") or "0.0.0.0"
    port = args.port or int(cfg.get("port") or 8650)
    lan_ip = get_lan_ip()

    server = create_mobile_server(
        host=host,
        port=port,
        api_token=args.api_token,
        telegram_token=args.telegram_token,
    )

    print("=" * 68)
    print("  📱 EtaiAgent Mobile Server & Phone Bridge Active")
    print("=" * 68)
    print(f"  • Phone Web App (Wi-Fi):   http://{lan_ip}:{port}")
    print(f"  • Local Mac Browser:       http://127.0.0.1:{port}")
    print(f"  • Siri / iOS Shortcut API: POST http://{lan_ip}:{port}/api/chat")
    print("                             JSON body: {\"message\": \"Play Severance on PS5\"}")
    if server.RequestHandlerClass.telegram_bot.is_configured:
        print("  • Telegram Bot:            Configured (polling in background)")
    else:
        print("  • Telegram Bot:            Click 'Telegram' in the web UI or pass --telegram-token")
    print("=" * 68)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down mobile server...")
        server.server_close()


if __name__ == "__main__":
    main()
