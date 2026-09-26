"""Deep Web Research and Instant Knowledge Retrieval for JARVIS.

Uses DuckDuckGo Instant Answer API, semantic web extraction, and LLM
summarization to provide instant factual answers without slow scraping.
"""

from __future__ import annotations

import json
import urllib.request
import urllib.parse
from typing import Any


def research_topic(query: str) -> dict[str, Any]:
    """Retrieve instant factual knowledge for a topic or question."""
    clean_query = query.strip()
    if not clean_query:
        return {"ok": False, "spoken": "What would you like me to research, sir?"}

    encoded = urllib.parse.quote(clean_query)
    url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1&skip_disambig=1"
    req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-Assistant/2.0"})

    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            abstract = data.get("AbstractText", "").strip()
            source = data.get("AbstractSource", "Web Knowledge")
            source_url = data.get("AbstractURL", "")
            heading = data.get("Heading", clean_query)

            # If no direct abstract, check related topics
            if not abstract and data.get("RelatedTopics"):
                for item in data["RelatedTopics"]:
                    if isinstance(item, dict) and item.get("Text"):
                        abstract = item["Text"].strip()
                        source_url = item.get("FirstURL", source_url)
                        break

            if abstract:
                # Truncate first 2 sentences for spoken reply
                sentences = abstract.split(". ")
                spoken = ". ".join(sentences[:2]).strip()
                if not spoken.endswith("."):
                    spoken += "."

                display = (
                    f"🌐 **Research: {heading}** ({source})\n\n"
                    f"{abstract}\n\n"
                    f"[Source Reference]({source_url})" if source_url else f"{abstract}"
                )
                return {
                    "ok": True,
                    "heading": heading,
                    "summary": abstract,
                    "spoken": spoken,
                    "display": display,
                    "url": source_url,
                }
    except Exception as exc:
        print(f"[RESEARCH] Web lookup note: {exc}, using neural knowledge engine...")

    # Instant Neural LLM fallback
    try:
        from core.gpt_engine import get_gpt_engine
        prompt = f"Provide a factual, accurate 1 to 2 sentence explanation of: {clean_query}. Be direct, concise, and informative."
        answer = get_gpt_engine().generate(prompt, max_tokens=100)
        if answer:
            return {
                "ok": True,
                "heading": clean_query,
                "summary": answer,
                "spoken": answer,
                "display": f"🧠 **Neural Research: {clean_query}**\n\n{answer}",
                "url": "",
            }
    except Exception as gpt_err:
        print(f"[RESEARCH] Neural fallback note: {gpt_err}")

    return {
        "ok": False,
        "spoken": f"I couldn't retrieve instant records for '{clean_query}'. Would you like me to open a browser search?",
        "display": f"⚠️ No direct knowledge card found for **{clean_query}**."
    }
