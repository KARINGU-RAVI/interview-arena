"""LLM router: one door for every LLM call in the LangGraph part of the app.

Order: Gemini (main) -> Groq (backup 1) -> OpenRouter (backup 2).

What it does for each call:
1. Waits if we are about to break a provider's requests-per-minute limit.
2. Retries the same provider with exponential backoff on 429 / 5xx / network errors.
3. On a non-retryable error (bad key, unknown model) or after retries run out,
   falls back to the next provider.
4. For structured output, validates with Pydantic and retries once if invalid.
5. Counts calls per session: target 18 (warns above it), hard cap 24 (blocks there).
6. Logs node, provider, model, latency and a token estimate — never prompts or keys.

Use ONE LLMRouter object per interview session (it holds the counter and the lock).
CrewAI does NOT use this router; it has its own fallback (see crew/extraction_crew.py).
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TypeVar

from langchain_core.exceptions import (
    ModelAPIError,
    ModelConnectionError,
    ModelError,
    ModelRateLimitError,
    ModelTimeoutError,
)
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, ValidationError

import config

log = logging.getLogger("interview_arena.llm")

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------- errors


class BudgetExceededError(RuntimeError):
    """Raised before a call that would go over the per-session hard cap (24)."""


class AllProvidersFailedError(RuntimeError):
    """Raised when every provider failed. The message is safe to show to users."""


class InvalidOutputError(RuntimeError):
    """The model answered, but the answer did not match the Pydantic schema."""


# ---------------------------------------------------------------- data classes


@dataclass
class Provider:
    name: str  # unique label used for locking + rate limits: "gemini", "groq", "groq_b", ...
    backend: str  # which SDK client to build: "gemini" | "groq" | "openrouter"
    model: str
    key_name: str
    # "json_schema" = the provider's native structured output.
    # None = we ask for JSON in the prompt and validate it ourselves.
    structured_method: str | None


@dataclass
class CallRecord:
    """One completed LLM call. Kept for logs and for the report (which model scored what)."""

    node: str
    provider: str
    model: str
    latency_s: float
    tokens: int
    ok: bool


@dataclass
class _RateLimiter:
    """Sliding 60-second window: allows at most `rpm` requests in any 60 seconds."""

    rpm: int
    sent: deque = field(default_factory=deque)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def wait_for_slot(self) -> None:
        # The lock makes waiting threads take turns, so two sessions can't both
        # grab the last free slot.
        with self.lock:
            now = time.monotonic()
            while self.sent and now - self.sent[0] >= 60:
                self.sent.popleft()
            if len(self.sent) >= self.rpm:
                wait = 60 - (now - self.sent[0]) + 0.1
                log.info("rate limiter: waiting %.1fs to stay under %d RPM", wait, self.rpm)
                time.sleep(wait)
                self.sent.popleft()
            self.sent.append(time.monotonic())


# ONE limiter per provider for the whole process: free-tier limits are per account /
# Google Cloud project, not per session, so every session, test and the CrewAI crew must share them.
_LIMITERS: dict[str, _RateLimiter] = {}
_LIMITERS_LOCK = threading.Lock()


def rate_limiter(provider_name: str) -> _RateLimiter:
    with _LIMITERS_LOCK:
        if provider_name not in _LIMITERS:
            _LIMITERS[provider_name] = _RateLimiter(config.RPM_LIMITS.get(provider_name, 10))
        return _LIMITERS[provider_name]


def reserve_slots(provider_name: str, n: int) -> None:
    """Wait until `n` requests to this provider fit under its RPM limit.

    Used by the CrewAI crew, whose LLM calls don't go through the router.
    """
    for _ in range(n):
        rate_limiter(provider_name).wait_for_slot()


# ---------------------------------------------------------------- helpers


def _status_code(exc: Exception) -> int | None:
    """Get the HTTP status from any of the 3 SDKs' exceptions.

    Groq/OpenAI errors use `.status_code`; the Google SDK uses `.code`.
    """
    for attr in ("status_code", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    return None


def is_retryable(exc: Exception) -> bool:
    """429 (rate limit), 5xx (server trouble), timeouts and network errors are worth retrying.

    400/401/403/404 are not: a bad key or unknown model will fail every time,
    so we go straight to the next provider instead of wasting time.
    """
    # Gemini errors are LangChain's standard error types (langchain_core.exceptions),
    # which already tell us if a retry could help. Groq/OpenAI errors are not, so for
    # those we look at the HTTP status code below.
    if isinstance(exc, (ModelRateLimitError, ModelAPIError, ModelTimeoutError, ModelConnectionError)):
        return True
    if isinstance(exc, ModelError):  # auth, not found, invalid request...
        return False
    status = _status_code(exc)
    if status is not None:
        return status == 429 or status >= 500
    name = type(exc).__name__.lower()
    return any(word in name for word in ("timeout", "connection", "ratelimit", "server"))


def _estimate_tokens(text: str) -> int:
    """Rough rule of thumb: about 4 characters per token in English."""
    return max(1, len(text) // 4)


def _extract_json(text: str) -> str:
    """Pull the JSON object out of a reply that may have ``` fences or extra words."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise InvalidOutputError("no JSON object found in model output")
    return text[start : end + 1]


def _gemini_ignores_temperature(model: str) -> bool:
    """Gemini 3.5 Flash-Lite (and newer) uses fixed sampling: temperature is ignored.

    We ask the installed langchain-google-genai, which keeps the official list.
    It is a private helper, so if it ever disappears we safely assume "not ignored".
    """
    try:
        from langchain_google_genai.chat_models import (
            _uses_fixed_sampling_and_disallows_prefill as fixed,
        )

        return bool(fixed(model))
    except Exception:
        return False


def default_providers() -> list[Provider]:
    """Providers in fallback order, skipping any that has no key or no model name."""
    candidates = [
        Provider("gemini", "gemini", config.MAIN_MODEL, "GOOGLE_API_KEY", "json_schema"),
        # gpt-oss on Groq fails with "function_calling" (tool use isn't allowed together
        # with structured outputs), but supports json_schema — see Groq structured-outputs docs.
        Provider("groq", "groq", config.BACKUP_MODEL_1, "GROQ_API_KEY", "json_schema"),
        # Second Groq model: Groq limits are per model, so it has its own free quota.
        Provider("groq_b", "groq", config.BACKUP_MODEL_1B, "GROQ_API_KEY", "json_schema"),
        # Free OpenRouter models vary in tool/JSON support, so we parse JSON ourselves.
        Provider("openrouter", "openrouter", config.BACKUP_MODEL_2, "OPENROUTER_API_KEY", None),
    ]
    return [p for p in candidates if p.model and config.has_key(p.key_name)]


# ---------------------------------------------------------------- the router


class LLMRouter:
    """Create one per interview session."""

    def __init__(
        self,
        providers: list[Provider] | None = None,
        max_calls: int = config.LLM_CALL_HARD_CAP,
        target_calls: int = config.LLM_CALL_TARGET,
    ) -> None:
        self.providers = providers if providers is not None else default_providers()
        if not self.providers:
            raise AllProvidersFailedError("No LLM provider is configured. Add at least one API key to .env.")
        self.max_calls = max_calls  # hard cap: calls beyond this are refused
        self.target_calls = target_calls  # normal budget: going over it is only logged
        self._warned_over_target = False
        self.call_count = 0  # completed calls (these use tokens and count to the budget)
        self.failed_requests = 0  # rejected requests (429/5xx/errors), for the logs
        self.history: list[CallRecord] = []
        self.calls_by_node: dict[str, int] = {}  # e.g. {'extract': 2, 'evaluate_T1': 1}
        self.locked_provider: str | None = None

    # ---- public API ------------------------------------------------------

    def invoke_text(
        self, prompt: str, *, temperature: float = 0, node: str = "", use_lock: bool = False
    ) -> str:
        """Send a prompt, get plain text back."""

        def attempt(llm: BaseChatModel, provider: Provider) -> tuple[str, int]:
            msg = llm.invoke(prompt)
            return msg.text, self._tokens(msg, prompt)

        return self._run(attempt, temperature=temperature, node=node, use_lock=use_lock)

    def invoke_structured(
        self,
        prompt: str,
        schema: type[T],
        *,
        temperature: float = 0,
        node: str = "",
        use_lock: bool = False,
    ) -> T:
        """Send a prompt, get a validated Pydantic object back.

        Native structured output where supported; otherwise JSON parse + validate.
        If the output is invalid, we retry ONCE on the same provider with the error
        message added, then fall back to the next provider.
        """

        def attempt(llm: BaseChatModel, provider: Provider) -> tuple[T, int]:
            last_error = ""
            for try_no in range(2):  # first try + 1 validation retry
                text = (
                    prompt
                    if not last_error
                    else (
                        f"{prompt}\n\nYour previous reply was invalid: {last_error}\n"
                        "Reply again with ONLY valid output that matches the schema."
                    )
                )
                if try_no == 1:
                    self._check_budget()  # the retry is a real, extra LLM call
                try:
                    result, tokens = self._structured_once(llm, provider, text, schema)
                except InvalidOutputError as exc:
                    self.call_count += 1  # model answered (tokens used) but output was bad
                    last_error = str(exc)[:300]
                    log.warning("invalid structured output from %s: %s", provider.name, last_error)
                    continue
                self.call_count += 1
                return result, tokens
            raise InvalidOutputError(last_error)

        # counts_itself=True: attempt() above already counted each completed reply.
        return self._run(attempt, temperature=temperature, node=node, use_lock=use_lock, counts_itself=True)

    def lock_model(self, provider_name: str | None = None) -> str:
        """Stick to one provider (used by the evaluator so all answers are scored alike).

        With no argument, locks to the provider of the last successful call,
        or the first configured provider if nothing has run yet.
        """
        names = [p.name for p in self.providers]
        if provider_name is None:
            last_ok = next((r.provider for r in reversed(self.history) if r.ok), None)
            provider_name = last_ok or names[0]
        if provider_name not in names:
            raise ValueError(f"unknown or unconfigured provider: {provider_name}")
        self.locked_provider = provider_name
        log.info("evaluator model locked to %s", provider_name)
        return provider_name

    @property
    def last_call(self) -> CallRecord | None:
        return self.history[-1] if self.history else None

    def summary(self) -> dict:
        """Small dict for logs / the report. Contains no prompts and no keys."""
        return {
            "calls": self.call_count,
            "target": self.target_calls,
            "hard_cap": self.max_calls,
            "over_target": self.call_count > self.target_calls,
            "failed_requests": self.failed_requests,
            "locked_provider": self.locked_provider,
            "models_used": sorted({f"{r.provider}:{r.model}" for r in self.history if r.ok}),
            "calls_by_node": dict(self.calls_by_node),
        }

    # ---- internals -------------------------------------------------------

    def _order(self, use_lock: bool) -> list[Provider]:
        """Normal order, but the locked provider goes first when use_lock=True."""
        if use_lock and self.locked_provider:
            locked = [p for p in self.providers if p.name == self.locked_provider]
            others = [p for p in self.providers if p.name != self.locked_provider]
            return locked + others
        return list(self.providers)

    def _check_budget(self) -> None:
        if self.call_count >= self.max_calls:
            raise BudgetExceededError(f"LLM call hard cap reached ({self.max_calls} calls this session).")

    def add_external_calls(self, n: int, node: str = "") -> None:
        """Add calls made outside the router (e.g. the CrewAI crew) to the session count."""
        self.call_count += n
        self.calls_by_node[node or "-"] = self.calls_by_node.get(node or "-", 0) + n
        log.info(
            "node=%s external llm_calls=%d calls=%d/%d", node or "-", n, self.call_count, self.target_calls
        )
        self._warn_if_over_target()

    def _warn_if_over_target(self) -> None:
        if self.call_count > self.target_calls and not self._warned_over_target:
            self._warned_over_target = True
            log.warning(
                "session is over the %d-call target (now %d; hard cap %d)",
                self.target_calls,
                self.call_count,
                self.max_calls,
            )

    def _build(self, provider: Provider, temperature: float) -> BaseChatModel:
        """Create the LangChain chat model.

        SDK auto-retries are switched off so THIS router controls every retry
        (no hidden extra requests). Note: Google treats max_retries=1 as "no retries".
        """
        key = config.get_key(provider.key_name)
        if provider.backend == "gemini":
            from langchain_google_genai import ChatGoogleGenerativeAI

            kwargs = dict(
                model=provider.model, api_key=key, max_retries=1, timeout=config.REQUEST_TIMEOUT_SECONDS
            )
            if not _gemini_ignores_temperature(provider.model):
                kwargs["temperature"] = temperature
            return ChatGoogleGenerativeAI(**kwargs)
        if provider.backend == "groq":
            from langchain_groq import ChatGroq

            return ChatGroq(
                model=provider.model,
                api_key=key,
                temperature=temperature,
                max_retries=0,
                timeout=config.REQUEST_TIMEOUT_SECONDS,
            )
        if provider.backend == "openrouter":
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(
                model=provider.model,
                api_key=key,
                base_url=config.OPENROUTER_BASE_URL,
                temperature=temperature,
                max_retries=0,
                timeout=config.REQUEST_TIMEOUT_SECONDS,
            )
        raise ValueError(f"unknown provider backend {provider.backend}")

    def _structured_once(
        self, llm: BaseChatModel, provider: Provider, prompt: str, schema: type[T]
    ) -> tuple[T, int]:
        """One structured request. Raises InvalidOutputError if the reply doesn't fit."""
        if provider.structured_method:
            runnable = llm.with_structured_output(schema, method=provider.structured_method, include_raw=True)
            out = runnable.invoke(prompt)
            raw, parsed, err = out["raw"], out["parsed"], out["parsing_error"]
            tokens = self._tokens(raw, prompt)
            if parsed is None:
                raise InvalidOutputError(str(err) if err else "model returned no structured output")
            return parsed, tokens

        schema_json = json.dumps(schema.model_json_schema())
        full_prompt = (
            f"{prompt}\n\nReturn ONLY a JSON object (no markdown, no extra text) "
            f"that matches this JSON schema:\n{schema_json}"
        )
        msg = llm.invoke(full_prompt)
        tokens = self._tokens(msg, full_prompt)
        try:
            return schema.model_validate_json(_extract_json(msg.text)), tokens
        except ValidationError as exc:
            raise InvalidOutputError(str(exc)) from exc

    @staticmethod
    def _tokens(msg, prompt: str) -> int:
        """Real token count when the provider reports it, else an estimate."""
        usage = getattr(msg, "usage_metadata", None) or {}
        if usage.get("total_tokens"):
            return int(usage["total_tokens"])
        return _estimate_tokens(prompt) + _estimate_tokens(getattr(msg, "text", "") or "")

    def _run(self, attempt, *, temperature: float, node: str, use_lock: bool, counts_itself: bool = False):
        """Try providers in order with retries. `attempt(llm, provider)` does the real call."""
        self._check_budget()
        problems: list[str] = []
        count_before = self.call_count
        try:
            return self._try_providers(attempt, temperature, node, use_lock, counts_itself, problems)
        finally:
            # Per-node breakdown (includes validation retries), shown at the end of a session.
            used = self.call_count - count_before
            if used:
                self.calls_by_node[node or "-"] = self.calls_by_node.get(node or "-", 0) + used

    def _try_providers(
        self, attempt, temperature: float, node: str, use_lock: bool, counts_itself: bool, problems: list[str]
    ):

        for provider in self._order(use_lock):
            llm = self._build(provider, temperature)
            for retry in range(config.MAX_RETRIES_PER_PROVIDER + 1):
                rate_limiter(provider.name).wait_for_slot()
                start = time.perf_counter()
                try:
                    result, tokens = attempt(llm, provider)
                except InvalidOutputError:
                    # Already retried once inside attempt(); try the next provider.
                    self._record(node, provider, start, 0, ok=False)
                    problems.append(f"{provider.name}: invalid output")
                    break
                except BudgetExceededError:
                    raise
                except Exception as exc:  # provider/network error
                    self.failed_requests += 1
                    self._record(node, provider, start, 0, ok=False)
                    status = _status_code(exc)
                    # Log the error type + status only: messages could echo request details.
                    log.warning("%s failed: %s (status=%s)", provider.name, type(exc).__name__, status)
                    if is_retryable(exc) and retry < config.MAX_RETRIES_PER_PROVIDER:
                        wait = config.BACKOFF_BASE_SECONDS * (2**retry) + random.uniform(0, 1)
                        log.info("retrying %s in %.1fs", provider.name, wait)
                        time.sleep(wait)
                        continue
                    problems.append(f"{provider.name}: {type(exc).__name__}")
                    break  # move on to the next provider
                else:
                    if not counts_itself:
                        self.call_count += 1
                    self._record(node, provider, start, tokens, ok=True)
                    if use_lock and self.locked_provider and provider.name != self.locked_provider:
                        log.warning(
                            "locked provider %s failed; %s answered instead",
                            self.locked_provider,
                            provider.name,
                        )
                    return result

        raise AllProvidersFailedError(
            "All AI providers are busy or unavailable right now. "
            "Please wait a minute and try again. (" + "; ".join(problems) + ")"
        )

    def _record(self, node: str, provider: Provider, start: float, tokens: int, ok: bool) -> None:
        latency = time.perf_counter() - start
        self.history.append(CallRecord(node, provider.name, provider.model, latency, tokens, ok))
        if ok:
            log.info(
                "node=%s provider=%s model=%s latency=%.2fs tokens~%d calls=%d/%d",
                node or "-",
                provider.name,
                provider.model,
                latency,
                tokens,
                self.call_count,
                self.target_calls,
            )
            self._warn_if_over_target()
