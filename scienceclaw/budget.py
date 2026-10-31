"""Persistent, conservative dollar and call guard for small live API pilots.

Rates are standard text rates checked 2026-10-30. Input admission reserves one
token per UTF-8 byte plus framing; charged estimates ignore cache discounts.
This local guard is not a provider-side account billing limit.
"""
from pathlib import Path
import sqlite3
import threading
import math

from .domain import identifier, utc_now
from .providers import ProviderError, input_token_upper_bound


RATES_PER_MILLION = {
    ("openai", "gpt-6-luna"): (0.10, 0.50),
    ("anthropic", "claude-haiku-5-5"): (0.10, 0.50),
    ("gemini", "gemini-2.5-flash-lite"): (0.10, 0.40),
}


# NVIDIA's hosted API is used on free trial credits: no verified dollar price, so its
# calls are capped by count, separately from the paid providers' dollar/call limits.
FREE_CREDIT_PROVIDERS = {"nvidia"}


class SpendGuard:
    def __init__(self, path, *, max_usd=0.05, max_calls=6, max_per_provider=2, max_free_calls=2000):
        if not math.isfinite(max_usd) or max_usd <= 0 or min(max_calls, max_per_provider) < 1:
            raise ValueError("Live cost and call limits must be positive")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_usd, self.max_calls, self.max_per_provider = max_usd, max_calls, max_per_provider
        self.max_free_calls = max_free_calls
        self.lock = threading.RLock()
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY, provider TEXT, model TEXT, timestamp TEXT, reserved_usd REAL, charged_usd REAL, status TEXT, input_tokens INTEGER, output_tokens INTEGER)")

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def reserve(self, provider, model, system, payload, output_tokens):
        if provider in FREE_CREDIT_PROVIDERS:
            with self.lock, self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                used = db.execute("SELECT count(*) FROM calls WHERE provider=?", (provider,)).fetchone()[0]
                if used >= self.max_free_calls:
                    raise ProviderError(f"{provider} call cap of {self.max_free_calls} reached; no call made")
                call_id = identifier("call")
                db.execute("INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?)", (call_id, provider, model, utc_now(), 0.0, 0.0, "reserved", None, None))
                return call_id
        rates = RATES_PER_MILLION.get((provider, model))
        if rates is None:
            raise ProviderError("Live spend guard has no verified price for this model; no call made")
        input_tokens = input_token_upper_bound(system, payload)
        amount = (input_tokens * rates[0] + output_tokens * rates[1]) / 1e6
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count, charged = db.execute("SELECT count(*), coalesce(sum(charged_usd),0) FROM calls WHERE provider NOT IN ('nvidia')").fetchone()
            provider_count = db.execute("SELECT count(*) FROM calls WHERE provider=?", (provider,)).fetchone()[0]
            if count >= self.max_calls or provider_count >= self.max_per_provider or charged + amount > self.max_usd:
                raise ProviderError("Persistent live pilot budget reached; no call made")
            call_id = identifier("call")
            db.execute("INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?)", (call_id, provider, model, utc_now(), amount, amount, "reserved", None, None))
            return call_id

    def settle(self, call_id, provider, model, input_tokens, output_tokens, status):
        rates = (0.0, 0.0) if provider in FREE_CREDIT_PROVIDERS else RATES_PER_MILLION[(provider, model)]
        with self.lock, self.connect() as db:
            if input_tokens + output_tokens:
                cost = (input_tokens * rates[0] + output_tokens * rates[1]) / 1e6
                db.execute("UPDATE calls SET charged_usd=?,status=?,input_tokens=?,output_tokens=? WHERE id=?", (cost,status,input_tokens,output_tokens,call_id))
            else:
                db.execute("UPDATE calls SET status=? WHERE id=?", (status,call_id))

    def summary(self):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            records = [dict(r) for r in db.execute("SELECT * FROM calls ORDER BY timestamp,id")]
        return {"max_usd": self.max_usd, "max_calls": self.max_calls, "max_calls_per_provider": self.max_per_provider,
                "calls": len(records), "estimated_or_reserved_usd": sum(r["charged_usd"] for r in records), "records": records,
                "pricing_checked": "2026-10-30", "actual_invoice_verified": False}


class BudgetedProvider:
    def __init__(self, provider, guard):
        self.provider, self.guard = provider, guard
        self.name, self.model = provider.name, provider.model

    def complete_json(self, system, payload, max_output_tokens=800):
        call_id = self.guard.reserve(self.name, self.model, system, payload, max_output_tokens)
        try:
            response = self.provider.complete_json(system, payload, max_output_tokens)
        except ProviderError as exc:
            self.guard.settle(call_id, self.name, self.model, exc.input_tokens, exc.output_tokens, "failed_usage_known" if exc.input_tokens + exc.output_tokens else "failed_usage_unknown")
            raise
        except Exception:
            self.guard.settle(call_id, self.name, self.model, 0, 0, "failed_usage_unknown")
            raise ProviderError("Provider failed unexpectedly; cost reservation retained") from None
        self.guard.settle(call_id, self.name, self.model, response.input_tokens, response.output_tokens, "completed")
        return response


def guard_from_environment():
    import os
    limit = os.getenv("SCIENCECLAW_LIVE_MAX_USD")
    if not limit:
        return None
    return SpendGuard(os.getenv("SCIENCECLAW_LIVE_LEDGER", "data/live-pilot.sqlite3"), max_usd=float(limit),
                      max_calls=int(os.getenv("SCIENCECLAW_LIVE_MAX_CALLS", "6")),
                      max_per_provider=int(os.getenv("SCIENCECLAW_LIVE_MAX_CALLS_PER_PROVIDER", "2")),
                      max_free_calls=int(os.getenv("SCIENCECLAW_NVIDIA_MAX_CALLS", "2000")))
