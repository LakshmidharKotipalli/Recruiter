import json
import re
import logging
from typing import Any
from config import ModelConfig, APP_CONFIG

logger = logging.getLogger(__name__)


class BaseAgent:
    """Base agent class supporting 'anthropic' and 'openai' providers."""

    def __init__(self, config: ModelConfig):
        self.config = config
        self._client = None

    def _is_mock_mode(self) -> bool:
        """Return True if MOCK_MODE=true in .env or no real API key is set."""
        return APP_CONFIG.mock_mode

    def _get_client(self):
        """Lazily initialize and return the LLM client."""
        if self._client is None:
            if self.config.provider == "anthropic":
                import anthropic
                self._client = anthropic.Anthropic(api_key=self.config.active_api_key)
            elif self.config.provider == "openai":
                import openai
                self._client = openai.OpenAI(api_key=self.config.active_api_key)
            else:
                raise ValueError(
                    f"Unknown provider: {self.config.provider}. Must be 'anthropic' or 'openai'."
                )
        return self._client

    def _call_llm_with_tools(
        self, tools: list, messages: list, system: str
    ) -> tuple:
        """
        Call the Anthropic API with tool_use enabled.

        Returns (response_text, tool_calls) where:
          - response_text is any text content in the response (may be empty)
          - tool_calls is a list of dicts: [{"name": ..., "input": ..., "id": ...}, ...]

        Raises ValueError if the configured provider is not 'anthropic'.
        """
        if self.config.provider != "anthropic":
            raise ValueError(
                f"_call_llm_with_tools() requires provider='anthropic', "
                f"got '{self.config.provider}'."
            )

        client = self._get_client()
        response = client.messages.create(
            model=self.config.model_id,
            max_tokens=self.config.max_tokens,
            system=system,
            tools=tools,
            messages=messages,
        )

        text_parts = [
            block.text
            for block in response.content
            if block.type == "text"
        ]
        response_text = "\n".join(text_parts)

        tool_calls = [
            {"name": block.name, "input": block.input, "id": block.id}
            for block in response.content
            if block.type == "tool_use"
        ]

        return response_text, tool_calls

    def _call_llm(self, system: str, user: str) -> str:
        """Call the configured LLM and return the text response."""
        client = self._get_client()

        if self.config.provider == "anthropic":
            response = client.messages.create(
                model=self.config.model_id,
                max_tokens=self.config.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return response.content[0].text

        elif self.config.provider == "openai":
            response = client.chat.completions.create(
                model=self.config.model_id,
                max_tokens=self.config.max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            return response.choices[0].message.content

        else:
            raise ValueError(f"Unknown provider: {self.config.provider}")

    def _parse_json_response(self, text: str) -> Any:
        """Strip markdown code fences and parse JSON, with robust fallback."""
        stripped = text.strip()

        # Strip markdown code fences (```json ... ``` or ``` ... ```)
        fence_pattern = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.DOTALL)
        match = fence_pattern.match(stripped)
        if match:
            stripped = match.group(1).strip()

        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

        # Fallback: find the outermost JSON object or array using bracket matching
        result = self._extract_balanced_json(stripped)
        if result is not None:
            return result

        raise ValueError(f"Could not parse JSON from LLM response: {text[:200]}")

    @staticmethod
    def _extract_balanced_json(text: str) -> Any:
        """Extract JSON by finding balanced braces/brackets from the first opener."""
        for opener, closer in [('{', '}'), ('[', ']')]:
            start = text.find(opener)
            if start == -1:
                continue

            depth = 0
            in_string = False
            escape_next = False
            for i in range(start, len(text)):
                ch = text[i]
                if escape_next:
                    escape_next = False
                    continue
                if ch == '\\' and in_string:
                    escape_next = True
                    continue
                if ch == '"' and not escape_next:
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except json.JSONDecodeError:
                            break
            # If we didn't find balanced JSON for this opener, try next
        return None
