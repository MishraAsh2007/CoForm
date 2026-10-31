"""One wrapper for every model call. Real mode: Anthropic API. Mock mode: a rule-based fake model
(agents/mock_policy.py) so the whole loop runs at zero cost.

complete(system, prompt, images, mock_fn) -> (text, usage)  usage = {input_tokens, output_tokens, cost_usd, mocked}
The API key is read from .env by python-dotenv and never printed or logged.
"""
import base64
import json
import os
import re
from pathlib import Path

from cad.common import ROOT


class LLM:
    def __init__(self, cfg, mock=False):
        self.cfg = cfg["llm"]
        self.model = self.cfg["model"]
        self.mock = mock
        self.client = None
        if not mock:
            from dotenv import load_dotenv
            load_dotenv(ROOT / ".env")
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise RuntimeError("ANTHROPIC_API_KEY not set in .env (use --mock to run without it)")
            import anthropic
            self.client = anthropic.Anthropic()

    def price(self, usage_in, usage_out):
        pin, pout = self.cfg["prices_per_mtok"].get(self.model, [0, 0])
        return usage_in / 1e6 * pin + usage_out / 1e6 * pout

    def complete(self, system, prompt, images=(), mock_fn=None):
        if self.mock:
            reply = mock_fn() if mock_fn else {"action": "pass", "reason": "mock"}
            return json.dumps(reply), {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "mocked": True}
        content = []
        if self.cfg.get("send_images"):
            for p in images:
                data = base64.standard_b64encode(Path(p).read_bytes()).decode("utf-8")
                content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}})
        content.append({"type": "text", "text": prompt})
        r = self.client.messages.create(
            model=self.model, max_tokens=self.cfg["max_tokens"], system=system,
            output_config={"effort": self.cfg["effort"]},
            messages=[{"role": "user", "content": content}])
        text = "".join(b.text for b in r.content if b.type == "text")
        if r.stop_reason == "refusal":
            text = json.dumps({"action": "pass", "reason": "model refused"})
        u = r.usage
        return text, {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                      "cost_usd": round(self.price(u.input_tokens, u.output_tokens), 6), "mocked": False}


def parse_json(text):
    """First JSON object in the reply (models sometimes wrap it in prose or ``` fences)."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {"action": "pass", "reason": "no JSON in reply"}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"action": "pass", "reason": "unparseable JSON in reply"}
