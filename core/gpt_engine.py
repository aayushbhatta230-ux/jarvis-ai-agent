"""Unified GPT and Deep Reasoning Engine for JARVIS.

Supports cloud OpenAI models (GPT-4o, GPT-4o-mini) and local Ollama
models (LLaMA 3.2) via standard OpenAI-compatible endpoints with
streaming, failover resilience, and chain-of-thought reasoning.
"""

from __future__ import annotations

import os
import time
from typing import Any, Generator
import openai
from pathlib import Path


class GPTEngine:
    """Multi-provider neural reasoning engine supporting OpenAI GPT and local Ollama."""

    DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434/v1"
    DEFAULT_LOCAL_MODEL = "llama3.2:latest"
    DEFAULT_GPT_MODEL = "gpt-4o-mini"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        preferred_model: str | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.base_url = base_url
        self.preferred_model = preferred_model

        # Determine default provider
        self.has_cloud_gpt = bool(self.api_key and not self.api_key.startswith("your_"))
        
        # Initialize clients
        self._cloud_client: openai.OpenAI | None = None
        self._local_client: openai.OpenAI | None = None

        if self.has_cloud_gpt:
            try:
                self._cloud_client = openai.OpenAI(api_key=self.api_key)
            except Exception as exc:
                print(f"[GPT] Notice: Could not init cloud OpenAI client: {exc}")
                self.has_cloud_gpt = False

        # Local Ollama client (always configured as dependable local fallback)
        try:
            ollama_url = self.base_url or os.environ.get("OLLAMA_BASE_URL", self.DEFAULT_OLLAMA_URL)
            self._local_client = openai.OpenAI(
                base_url=ollama_url,
                api_key="ollama",
                timeout=30.0,
            )
        except Exception as exc:
            print(f"[GPT] Notice: Could not init local Ollama client: {exc}")

    @property
    def active_model(self) -> str:
        if self.preferred_model:
            return self.preferred_model
        if self.has_cloud_gpt:
            return self.DEFAULT_GPT_MODEL
        return self.DEFAULT_LOCAL_MODEL

    @property
    def active_provider(self) -> str:
        if self.has_cloud_gpt:
            return "OpenAI Cloud GPT"
        return "Local Ollama LLaMA"

    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 500,
        model: str | None = None,
    ) -> str:
        """Generate a complete text response with automatic fallback."""
        target_model = model or self.active_model
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # Try cloud client first if available and targeting GPT
        if self.has_cloud_gpt and self._cloud_client and ("gpt" in target_model.lower() or not model):
            try:
                resp = self._cloud_client.chat.completions.create(
                    model=target_model if "gpt" in target_model.lower() else self.DEFAULT_GPT_MODEL,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                if resp.choices and resp.choices[0].message.content:
                    return resp.choices[0].message.content.strip()
            except Exception as exc:
                print(f"[GPT] Cloud generation failed ({exc}); falling back to local model...")

        # Fallback to local Ollama
        if self._local_client:
            try:
                resp = self._local_client.chat.completions.create(
                    model=self.DEFAULT_LOCAL_MODEL,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                if resp.choices and resp.choices[0].message.content:
                    return resp.choices[0].message.content.strip()
            except Exception as exc:
                print(f"[GPT] Local Ollama generation error: {exc}")
                raise RuntimeError(f"All neural inference providers failed: {exc}") from exc

        raise RuntimeError("No available LLM provider configured.")

    def stream_generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 500,
        model: str | None = None,
    ) -> Generator[str, None, None]:
        """Stream response tokens synchronously."""
        target_model = model or self.active_model
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        client = self._cloud_client if (self.has_cloud_gpt and "gpt" in target_model.lower()) else self._local_client
        chosen_model = target_model if (client == self._cloud_client) else self.DEFAULT_LOCAL_MODEL

        if not client:
            client = self._local_client
            chosen_model = self.DEFAULT_LOCAL_MODEL

        if not client:
            raise RuntimeError("No active LLM client for streaming.")

        try:
            stream = client.chat.completions.create(
                model=chosen_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as exc:
            # If cloud streaming failed, fallback to local
            if client == self._cloud_client and self._local_client:
                print(f"[GPT] Cloud streaming failed, falling back to local: {exc}")
                stream = self._local_client.chat.completions.create(
                    model=self.DEFAULT_LOCAL_MODEL,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                )
                for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
            else:
                raise

    def deep_reason(self, query: str, context: str = "") -> dict[str, Any]:
        """Perform deep multi-step reasoning with Chain-of-Thought."""
        prompt = (
            f"Context Information:\n{context}\n\n"
            f"User Goal: {query}\n\n"
            f"Analyze step-by-step:\n"
            f"1. Core user intent and constraints\n"
            f"2. Required actions or information retrieval\n"
            f"3. Potential edge cases or confirmations required\n"
            f"4. Concise, actionable response for the user\n\n"
            f"Format as: REASONING: <brief steps> | ACTION: <command or capability> | RESPONSE: <spoken answer>"
        )
        system = "You are the deep analytical reasoning core of JARVIS. Be logical, direct, and precise."
        raw_output = self.generate(prompt, system_prompt=system, temperature=0.3, max_tokens=350)
        
        reasoning = raw_output
        action = ""
        response = raw_output

        if "RESPONSE:" in raw_output:
            parts = raw_output.split("RESPONSE:", 1)
            response = parts[1].strip()
            pre = parts[0]
            if "ACTION:" in pre:
                action_part = pre.split("ACTION:", 1)
                action = action_part[1].strip()
                reasoning = action_part[0].replace("REASONING:", "").strip()
            else:
                reasoning = pre.replace("REASONING:", "").strip()

        return {
            "query": query,
            "reasoning": reasoning,
            "action": action,
            "response": response,
            "model_used": self.active_model,
            "provider": self.active_provider,
        }


# Global singleton instance
_gpt_engine: GPTEngine | None = None


def get_gpt_engine() -> GPTEngine:
    global _gpt_engine
    if _gpt_engine is None:
        _gpt_engine = GPTEngine()
    return _gpt_engine
