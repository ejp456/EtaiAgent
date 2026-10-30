# EtaiAgent

Personal AI assistant with persistent vector memory, Home Assistant & smart home control, PS5 & Google Cast media streaming, declarative rules automations, and a multi-platform Telegram bridge.

> 📖 **Looking to install on another machine?** See the complete **[SETUP.md](SETUP.md)** guide.

---

## Quick Start on Any Machine

### 1. Clone & Set Up Environment
```bash
git clone git@github.com:ejp456/EtaiAgent.git
cd EtaiAgent

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment
Copy the example configuration:
```bash
cp .env.example .env
```
Edit `.env` to set your endpoints:
- `OLLAMA_BASE_URL`: IP / URL of your Ollama server (e.g. `http://192.168.86.39:11434`)
- `TELEGRAM_BOT_TOKEN`: Bot token from [@BotFather](https://t.me/BotFather)
- `TELEGRAM_ALLOWED_CHAT_IDS`: Your Telegram numeric chat ID

---

## Running the Agent

EtaiAgent has a **single unified runner** (`./run.sh` or `python main.py`) that starts all services together:

### Standard Mode (Interactive CLI + Telegram + Automations)
```bash
./run.sh
# or: npm start / python main.py
```
- Interactive chat in the terminal
- Telegram bot active in background
- Automations scheduler active in background

### Headless / Server Mode (24/7 Background Daemon)
Best for servers, Raspberry Pi, `tmux`, or systemd services where no terminal prompt is needed:
```bash
./run.sh --headless
# or: npm run headless / python main.py --headless
```

### With Mobile Web App & Siri Bridge
Starts the local mobile web UI and HTTP REST bridge on port `8650`:
```bash
./run.sh --mobile
# or: npm run mobile / python main.py --mobile
```

---

## Command Flags

| Flag | Description |
|---|---|
| `-d`, `--headless` | Run as a background daemon (no interactive terminal prompt). |
| `-m`, `--mobile` | Launch mobile web UI and Siri shortcut API server. |
| `--port <port>` | Mobile server port (default: `8650`). |
| `--no-telegram` | Disable the Telegram bot background listener. |
| `--no-scheduler` | Disable the automations background scheduler. |
| `--token <token>` | Provide or override Telegram bot token. |

---

## Subsystems

- **[Automations (`automations/`)](automations/README.md)**: Declarative textproto-backed rules engine with time, date, and keyword triggers.
- **Memory Vector Search (`memory_search.py`)**: Local ONNX `BAAI/bge-small-en-v1.5` embeddings for document context retrieval.
- **Home Assistant (`home_assistant.py` & `smart_home.py`)**: Lights, blinds, thermostats, and room management.
- **Media Controller (`ps5_media.py`)**: PS5 console remote control, Samsung Frame TV, Google Cast streaming, and JustWatch catalog search.
- **Telegram Bot (`telegram_bot.py`)**: Remote bidirectional interface for querying the agent, checking smart devices, and receiving automation alerts.
