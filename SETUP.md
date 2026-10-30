# EtaiAgent — Machine Setup & Running Instructions

This guide walks you through setting up and running **EtaiAgent** on any new or secondary machine (macOS, Linux, Raspberry Pi, or WSL).

---

## 1. Prerequisites

- **Python 3.10+**
- **Git**
- **Ollama** running on your local network (or locally) with your preferred model (e.g. `qwen3:8b`)
- *(Optional)* **Telegram account** to connect `@kelsofezbot`
- *(Optional)* **Home Assistant** instance for smart devices, lights, blinds, and Google Cast

---

## 2. Clone the Repository

### Option A: Using SSH (Deploy Key or GitHub SSH Key)
```bash
git clone git@github.com:ejp456/EtaiAgent.git
cd EtaiAgent
```

### Option B: Using HTTPS
```bash
git clone https://github.com/ejp456/EtaiAgent.git
cd EtaiAgent
```

*(If you need to generate a deploy key on the new machine: `ssh-keygen -t ed25519 -C "new-machine-deploy" -f ~/.ssh/etaiagent_deploy -N ""` and add the public key to [GitHub Repo Settings → Deploy Keys](https://github.com/ejp456/EtaiAgent/settings/keys)).*

---

## 3. Python Environment & Dependencies

Create and activate a virtual environment, then install the pinned dependencies:

```bash
# 1. Create virtual environment
python3 -m venv .venv

# 2. Activate it
# On macOS / Linux / WSL:
source .venv/bin/activate
# On Windows PowerShell:
# .venv\Scripts\Activate.ps1

# 3. Install packages
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 4. Configuration (`.env`)

Copy the template configuration file:

```bash
cp .env.example .env
```

Open `.env` in an editor and set your network variables:

```ini
# LLM Endpoint (IP and port of your Ollama host)
OLLAMA_BASE_URL=http://192.168.86.39:11434
OLLAMA_MODEL=qwen3:8b
OLLAMA_NUM_CTX=32768

# Telegram Bot Bridge (from @BotFather)
TELEGRAM_BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN_HERE
TELEGRAM_ALLOWED_CHAT_IDS=YOUR_TELEGRAM_CHAT_ID

# Smart Home / Home Assistant (optional)
HA_URL=http://127.0.0.1:8123
HA_TOKEN=

# Philips Hue Bridge IP (optional)
HUE_BRIDGE_IP=192.168.86.46

# Mobile Web / Siri API Port
MOBILE_HOST=0.0.0.0
MOBILE_PORT=8650
```

> [!NOTE]
> EtaiAgent automatically loads `.env` on startup. You do not need to export environment variables manually in your shell.

---

### How to Get Your Telegram Bot Token & Chat ID

1. **Create a Bot & Get Token:**
   - Open Telegram and message **[@BotFather](https://t.me/BotFather)**.
   - Send `/newbot` and follow the prompts to name your bot and choose a username (ending in `bot`).
   - Copy the HTTP API token provided (format: `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`).
   - Paste it into your local `.env` file as `TELEGRAM_BOT_TOKEN=...`.

2. **Find Your Telegram Chat ID:**
   - Message **[@userinfobot](https://t.me/userinfobot)** on Telegram.
   - It will reply with your numeric `Id` (e.g. `123456789`).
   - Add it to `.env` as `TELEGRAM_ALLOWED_CHAT_IDS=123456789`.
   - *(Alternatively, leave `TELEGRAM_ALLOWED_CHAT_IDS` empty in `.env`: EtaiAgent will automatically bind to the very first user who messages `/start` to the bot).*

3. **Security:**
   - Never commit your `.env` file to git. `.env` is listed in `.gitignore` to protect your tokens.
   - If a token is ever accidentally exposed, send `/revoke` to **@BotFather** immediately to generate a new token.

---

## 5. Running the Agent

EtaiAgent uses a single unified launcher (`./run.sh` or `python main.py`) that manages all subsystems simultaneously.

### A. Standard Interactive Mode (Terminal CLI + Telegram + Automations)
Runs interactive chat in the terminal while running the Telegram bot and automations scheduler in background threads:

```bash
./run.sh
# or: npm start
```

### B. Headless Daemon Mode (24/7 Server / Background Service)
Runs silently in the background without needing a terminal prompt. Ideal for servers, Raspberry Pis, or `tmux`:

```bash
./run.sh --headless
# or: npm run headless
```

### C. With Mobile Web App & Siri Bridge
Starts the local mobile web UI and Siri shortcut REST API on port `8650`:

```bash
./run.sh --mobile
# or: npm run mobile
```

---

## 6. Command-Line Reference

```bash
./run.sh [FLAGS] [PROMPT...]
```

| Flag | Short | Description |
|---|---|---|
| `--headless` | `-d` | Runs as a background daemon (no interactive terminal prompt). |
| `--mobile` | `-m` | Also starts the mobile web server & Siri shortcut API. |
| `--port <port>` | | Port for the mobile web server (default: `8650`). |
| `--no-telegram` | | Disable the Telegram bot background listener. |
| `--no-scheduler`| | Disable the automations background scheduler. |
| `--token <token>` | | Provide or override the Telegram bot token. |

---

## 7. Optional: Running as a 24/7 systemd Service (Linux)

To have EtaiAgent run automatically on boot on a Linux server or Raspberry Pi, create a systemd service:

```ini
# /etc/systemd/system/etaiagent.service
[Unit]
Description=EtaiAgent Assistant Daemon
After=network.target

[Service]
Type=simple
User=YOUR_USER
WorkingDirectory=/path/to/EtaiAgent
ExecStart=/path/to/EtaiAgent/run.sh --headless
Restart=always
RestartSec=10
EnvironmentFile=/path/to/EtaiAgent/.env

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable etaiagent
sudo systemctl start etaiagent
sudo systemctl status etaiagent
```

---

## 8. Verification & Tests

To verify that the offline tests, schema, and rules engine pass on the new machine:

```bash
./.venv/bin/python -m unittest automations/test_automations.py
```
*(All 21 tests run offline without touching any real smart devices).*
