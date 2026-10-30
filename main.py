import sys
from typing import List
from llm import LLMClient
from tools import get_tools_schema, warmup_memory_indices_on_startup
from agent import run_agent, Message, DEFAULT_SYSTEM_PROMPT, trim_context

def main():
    startup_stats = warmup_memory_indices_on_startup()
    print("=" * 60)
    print("  EtaiAgent Interactive Session")
    print("  Continuous chat enabled (keeps last 10 turns of context).")
    print("  Type 'exit', 'quit', or 'q' to end.")
    print("=" * 60 + "\n")

    # Initialize conversation state
    messages: List[Message] = [
        Message(role="system", content=DEFAULT_SYSTEM_PROMPT)
    ]
    tools = get_tools_schema()

    # Optional initial prompt passed via command line
    initial_prompt = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else None
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

        # Append new user message
        messages.append(Message(role="user", content=user_input))

        # Trim context to keep system prompt + last 10 turns
        messages = trim_context(messages, max_turns=10)

        # Track start index of new messages for this interaction
        start_idx = len(messages) - 1

        # Run agent loop for this turn
        messages = run_agent(messages, tools, max_turns=8)

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
