"""
Credits:
- [Identity](https://www.youtube.com/watch?v=LykXu60aKoY)
"""

import os
from dataclasses import dataclass, field
from typing import Any

import httpx
from dotenv import load_dotenv
from rich.console import Console

load_dotenv(".env")


@dataclass
class Agent:
    model: str = field(default_factory=lambda: os.getenv("MODEL", "gpt-4"))
    base_url: str = field(
        default_factory=lambda: os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    )
    api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    messages: list[dict[str, Any]] = field(default_factory=list)
    default_instructions: str = field(
        default="You are a helpful assistant that provides accurate and \
        concise answers to user queries. Always provide clear and relevant information \
        based on the user's input."
    )

    def __post_init__(self):
        if not self.api_key:
            raise ValueError("API key is required. Please set the API_KEY environment variable.")
        self.base_url = self.base_url.rstrip("/")
        self.default_instructions = self.default_instructions.strip()
        self.client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            timeout=300,
        )

    def chat(self, messages: str, **kwargs) -> dict[str, Any]:
        self.messages.append({"role": "user", "content": messages})

        response = self.client.post(
            "/chat/completions",
            json={
                "model": self.model,
                "messages": self.messages,
                "instructions": self.default_instructions,
                **kwargs,
            },
        )
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices", [])

        if not choices:
            raise ValueError("No choices returned from the API.")

        message = choices[0].get("message", {})
        self.messages.append(message)
        return message

    def reset(self):
        self.messages.clear()


def main():
    console = Console()
    agent = Agent()

    console.print("[bold green]Welcome to the Simple Agent![/bold green]")
    console.print("Type your messages below. Type 'exit' to quit.")

    while True:
        user_input = console.input("[bold blue]You:[/bold blue] ")
        if user_input.strip().lower() in {"exit", "quit"}:
            console.print("[bold red]Goodbye![/bold red]")
            break

        try:
            with console.status("[bold yellow]Agent is thinking...[/bold yellow]"):
                response = agent.chat(user_input)
            console.print(f"[bold magenta]Agent:[/bold magenta] {response.get('content', '')}")
        except Exception as e:
            console.print(f"[bold red]Error:[/bold red] {e!s}")


if __name__ == "__main__":
    main()
