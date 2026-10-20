"""Very small client for any OpenAI-compatible chat API (Groq, OpenAI, Ollama, LM Studio...).

Using plain `requests` instead of the openai SDK so the same code works for all of them
and there's one less dependency to break.
"""

from __future__ import annotations

import json
import re

import requests

from . import config


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self):
        self.provider = config.LLM_PROVIDER
        base, model = config.DEFAULTS.get(self.provider, ("", ""))
        self.base_url = (config.LLM_BASE_URL or base).rstrip("/")
        self.model = config.LLM_MODEL or model
        self.key = config.LLM_API_KEY

    @property
    def enabled(self) -> bool:
        if self.provider == "offline" or not self.base_url:
            return False
        if self.provider in ("groq", "openai") and not self.key:
            return False
        return True

    def describe(self) -> dict:
        if not self.enabled:
            return {"mode": "offline", "label": "Offline mode (rule-based agents)"}
        return {"mode": "llm", "label": f"{self.provider} · {self.model}", "provider": self.provider,
                "model": self.model}

    def chat_json(self, system: str, user: str, retries: int = 2) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        body = {
            "model": self.model,
            "temperature": config.LLM_TEMPERATURE,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if self.provider in ("groq", "openai"):
            body["response_format"] = {"type": "json_object"}

        last_err = None
        for _ in range(retries + 1):
            try:
                r = requests.post(f"{self.base_url}/chat/completions", headers=headers, json=body, timeout=90)
                if r.status_code == 400 and "response_format" in body and "json" in r.text.lower():
                    # some models don't support json mode -> retry without it, parse_json copes
                    body.pop("response_format")
                    continue
                if r.status_code == 429:
                    raise LLMError("rate limited by the provider (free tier?). wait a minute and retry")
                if r.status_code >= 400:
                    raise LLMError(f"{r.status_code}: {r.text[:300]}")
                content = r.json()["choices"][0]["message"]["content"]
                return parse_json(content)
            except (requests.RequestException, LLMError, ValueError, KeyError) as e:
                last_err = e
        raise LLMError(f"LLM call failed: {last_err}")


def parse_json(text: str) -> dict:
    """Models love wrapping JSON in ```json fences or adding 'Sure! Here is...'. Strip that."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            raise ValueError("no JSON object in model output")
        return json.loads(m.group(0))
