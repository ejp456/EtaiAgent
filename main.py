import os
import sys
import time
import argparse
import threading
from typing import List, Optional

from llm import LLMClient
from tools import get_tools_schema, warmup_memory_indices_on_startup
from agent import run_agent, Message, load_system_instructions, trim_context
from automations import AutomationScheduler, preprocess_user_message, postprocess_agent_turn

CLI_SESSION_ID = "cli"


def load_env_file(dotenv_path: str = ".env") -> None:
    """Load key-value pairs from a .env file into os.environ if not already set."""
    if not os.path.exists(dotenv_path):
        return
    try:
        with open(dotenv_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass


def main():
    load_env_file()

    parser = argparse.ArgumentParser(
        description="EtaiAgent - Unified AI assistant with smart home, media, automations & Telegram bridge"
    )
    parser.add_argument(
        "-d", "--headless", action="store_true",
        help="Run in headless daemon mode (runs scheduler + Telegram bot without interactive terminal prompt)"
    )
    parser.add_argument(
        "-m", "--mobile", action="store_true",
        help="Also launch mobile web server and Siri HTTP bridge"
    )
    parser.add_argument(
        "--port", type=int, default=8650,
        help="Port for mobile web server (default: 8650)"
    )
    parser.add_argument(
        "--no-telegram", action="store_true",
        help="Disable Telegram bot background listener"
    )
    parser.add_argument(
        "--no-scheduler", action="store_true",
        help="Disable automations background scheduler"
    )
    parser.add_argument(
        "--token", default="",
        help="Telegram bot token from @BotFather (overrides mobile_config.json/env)"
    )
    parser.add_argument(
        "prompt", nargs="*",
        help="Optional initial prompt to execute on start"
    )
    args = parser.parse_args()

    # Pre-compute/load memory embeddings
    startup_stats = warmup_memory_indices_on_startup()

    # 1. Automations Scheduler
    if not args.no_scheduler:
        scheduler = AutomationScheduler()
        scheduler.start()

    # 2. Telegram Bot Bridge
    tg_bot = None
    if not args.no_telegram:
        try:
            from telegram_bot import TelegramBotRunner
            tg_bot = TelegramBotRunner(bot_token=args.token)
            if tg_bot.is_configured:
                tg_bot.start_background()
            else:
                print("\033[90m[Telegram]\033[0m Bot token not configured (set TELEGRAM_BOT_TOKEN in .env or mobile_config.json to enable).")
        except Exception as e:
            print(f"\033[31m[Telegram]\033[0m Failed to start Telegram listener: {e}")

    # 3. Optional Mobile Web Server
    mobile_srv = None
    if args.mobile:
        try:
            from mobile_server import create_mobile_server, get_lan_ip
            mobile_srv = create_mobile_server(port=args.port, telegram_token=args.token)
            lan_ip = get_lan_ip()
            srv_thread = threading.Thread(target=mobile_srv.serve_forever, daemon=True)
            srv_thread.start()
            print(f"\033[92m[Mobile Server]\033[0m Web app live at http://{lan_ip}:{args.port}")
        except Exception as e:
            print(f"\033[31m[Mobile Server]\033[0m Failed to start: {e}")

    # 4. Headless daemon mode (for servers, Raspberry Pi, systemd, or non-interactive terminals)
    is_interactive = sys.stdin.isatty() and not args.headless
    if not is_interactive and not args.prompt:
        print("\033[92m[EtaiAgent]\033[0m Running in headless background mode (Scheduler + Telegram). Press Ctrl+C to stop.\n")
        try:
            while True:
                time.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            print("\n[EtaiAgent] Shutting down.")
            return

    # 5. Interactive CLI Session
    print("=" * 64)
    print("  🤖 EtaiAgent Interactive Session")
    print("  Continuous chat enabled (keeps last 10 turns of context).")
    print("  Type /automation to build an automation, /automations to list them.")
    print("  Type 'exit', 'quit', or 'q' to end.")
    print("=" * 64 + "\n")

    messages: List[Message] = [
        Message(role="system", content=load_system_instructions())
    ]
    tools = get_tools_schema()

    initial_prompt = " ".join(args.prompt).strip() if args.prompt else None
    first_turn = True

    while True:
        if first_turn and initial_prompt:
            user_input = initial_prompt
            print(f"\033[96mUser:\033[0m {user_input}\n")
            first_turn = False
        else:
            first_turn = False
            try:
                user_input = input("\033[96mUser:\033[0m ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n\nGoodbye!")
                break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit", "q"):
            print("\nGoodbye!")
            break

        # Automations: `/automation` commands, an in-progress builder, or keyword triggers
        automation_reply = preprocess_user_message(CLI_SESSION_ID, user_input)
        if automation_reply is not None:
            print(f"\n\033[92mAssistant:\033[0m\n{automation_reply}\n")
            continue

        # Append new user message
        messages.append(Message(role="user", content=user_input))

        # Trim context to keep system prompt + last 10 turns
        messages = trim_context(messages, max_turns=10)

        # Track start index of new messages for this interaction
        start_idx = len(messages) - 1

        # Run agent loop for this turn
        messages = run_agent(messages, tools, max_turns=8)

        # If the agent called `create_automation`, hand off to the guided builder
        wizard_reply = postprocess_agent_turn(CLI_SESSION_ID)
        if wizard_reply is not None:
            print(f"\n\033[92mAssistant:\033[0m\n{wizard_reply}\n")
            continue

        # Extract and print newly generated assistant response
        turn_responses = [
            msg for msg in messages[start_idx:]
            if msg.role == "assistant" and msg.content
        ]
        if turn_responses:
            print(f"\n\033[92mAssistant:\033[0m\n{turn_responses[-1].content}\n")
        else:
            print()


if __name__ == "__main__":
    main()
