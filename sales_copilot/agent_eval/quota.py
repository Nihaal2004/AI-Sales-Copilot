"""Sequential live-evaluation pacing and safe, bounded provider retries.

Only normalized diagnostics leave this module; provider bodies/headers may contain
project identifiers, prompts or credentials and must never be persisted.
"""

import math
import random
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from ..errors import model_error_result


@dataclass(frozen=True)
class QuotaConfig:
    call_interval: float = 4.0
    batch_size: int = 5                 # Scenario-trials, not provider Batch API jobs.
    batch_pause: float = 30.0
    max_attempts: int = 3               # Includes the initial request.
    backoff_base: float = 2.0
    backoff_cap: float = 30.0
    max_retry_wait: float = 120.0       # Cumulative sleep budget per logical call.

    def __post_init__(self):
        for field in ("call_interval", "batch_pause", "backoff_base", "backoff_cap", "max_retry_wait"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
                raise ValueError("invalid_quota_" + field)
        if type(self.batch_size) is not int or not 1 <= self.batch_size <= 600:
            raise ValueError("invalid_quota_batch_size")
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 10:
            raise ValueError("invalid_quota_max_attempts")

    def manifest(self):
        return asdict(self)


def _seconds(value):
    try:
        if isinstance(value, dict):
            number = float(value.get("seconds", 0)) + float(value.get("nanos", 0)) / 1e9
        else:
            number = float(str(value).removesuffix("s"))
        return max(0.0, number) if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def provider_diagnostic(exc, *, now=None):
    """Read structured QuotaFailure/RetryInfo and Retry-After, never infer from prose."""
    normalized = model_error_result(exc)
    status = normalized["http_status"]
    quota_types, delays = set(), []
    daily = False
    current, seen = exc, set()
    for _ in range(8):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        response = getattr(current, "response", None)
        if status is None and type(getattr(response, "status_code", None)) is int:
            status = response.status_code
        headers = getattr(response, "headers", None) or getattr(current, "headers", None) or {}
        raw = headers.get("retry-after", headers.get("Retry-After"))
        if raw is not None:
            delay = _seconds(raw)
            if delay is None:
                try:
                    date = parsedate_to_datetime(str(raw))
                    delay = max(0.0, date.timestamp() - (now or datetime.now(timezone.utc)).timestamp())
                except (ValueError, TypeError, OverflowError):
                    pass
            if delay is not None:
                delays.append(delay)
        body = getattr(current, "details", None)
        if not isinstance(body, (dict, list)):
            body = getattr(current, "response_json", None)
        if isinstance(body, dict):
            body = body.get("error", body)
            body = body.get("details", []) if isinstance(body, dict) else []
        for detail in body if isinstance(body, list) else []:
            if not isinstance(detail, dict):
                continue
            kind = detail.get("@type", "")
            if kind.endswith("google.rpc.RetryInfo"):
                delay = _seconds(detail.get("retryDelay"))
                if delay is not None:
                    delays.append(delay)
            if not kind.endswith("google.rpc.QuotaFailure"):
                continue
            for violation in detail.get("violations", []):
                if not isinstance(violation, dict):
                    continue
                # Quota IDs commonly encode the time window; metric names alone
                # may omit it. Unknown/new quotas stay unknown, not guessed RPM.
                identifier = re.sub(r"[^a-z]", "", str(violation.get("quotaId", "")).lower())
                if "perday" not in identifier and "perminute" not in identifier:
                    identifier = re.sub(r"[^a-z]", "", str(violation.get("quotaMetric", "")).lower())
                if "perday" in identifier:
                    daily = True
                    quota_types.add("RPD" if "request" in identifier and "token" not in identifier else "unknown")
                elif "perminute" in identifier and "token" in identifier:
                    quota_types.add("TPM")
                elif "perminute" in identifier and "request" in identifier:
                    quota_types.add("RPM")
                else:
                    quota_types.add("unknown")
        current = current.__cause__ or current.__context__
    rate_limited = status == 429 or normalized["error"] == "model_rate_limited"
    return {"http_status": status, "quota_types": sorted(quota_types or {"unknown"}) if rate_limited else [],
            "daily_exhausted": daily if rate_limited else False,
            "retry_after_seconds": max(delays) if delays else None,
            "availability": "model_rate_limited" if rate_limited else "model_error"}


class QuotaController:
    """One shared controller across all live scenario-trials in a run."""

    def __init__(self, config=None, *, sleep=time.sleep, clock=time.monotonic, jitter=random.uniform):
        self.config = config or QuotaConfig()
        self.sleep, self.clock, self.jitter = sleep, clock, jitter
        self.last_call = None
        self.halt_reason = None
        self.halted_status = None

    def wait(self, seconds, trace, phase):
        if seconds > 0:
            trace.event("provider_wait", phase=phase, delay_seconds=round(seconds, 6))
            # Small slices allow interruption; no CRM transaction is held here.
            remaining = seconds
            while remaining > 0:
                duration = min(remaining, 60.0)
                self.sleep(duration)
                remaining -= duration

    def invoke(self, delegate, messages, trace):
        if self.halt_reason:
            trace.event("provider_call_deferred", http_status=self.halted_status)
            failure = RuntimeError("Evaluation provider calls paused; resume explicitly in a new run")
            failure.code = self.halted_status
            raise failure
        waited = 0.0
        for attempt in range(1, self.config.max_attempts + 1):
            spacing = 0 if self.last_call is None else max(0, self.config.call_interval - (self.clock() - self.last_call))
            self.wait(spacing, trace, "call_pacing")
            self.last_call = self.clock()
            trace.event("provider_invocation", provider="google_genai", attempt=attempt)
            try:
                result = delegate.invoke(messages)
                trace.event("provider_attempt_completed", attempt=attempt, availability="model_available")
                return result
            except Exception as exc:
                diagnostic = provider_diagnostic(exc)
                trace.event("provider_attempt_failed", attempt=attempt, **diagnostic)
                if diagnostic["daily_exhausted"]:
                    self.halt_reason = "daily_quota_exhausted"
                    self.halted_status = diagnostic["http_status"]
                    raise
                transient = diagnostic["http_status"] in (429, 503)
                if not transient or attempt == self.config.max_attempts:
                    if diagnostic["http_status"] == 429:
                        self.halt_reason = "quota_retry_budget_exhausted"
                        self.halted_status = 429
                    raise
                ceiling = min(self.config.backoff_cap, self.config.backoff_base * 2 ** (attempt - 1))
                delay = max(self.jitter(ceiling / 2, ceiling), diagnostic["retry_after_seconds"] or 0)
                if waited + delay > self.config.max_retry_wait:
                    self.halt_reason = "retry_after_exceeds_wait_budget"
                    self.halted_status = diagnostic["http_status"]
                    trace.event("provider_retry_deferred", attempt=attempt, delay_seconds=delay)
                    raise
                trace.event("provider_retry_scheduled", attempt=attempt, delay_seconds=delay)
                self.wait(delay, trace, "retry_backoff")
                waited += delay
