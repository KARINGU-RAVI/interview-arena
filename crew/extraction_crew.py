"""CrewAI extraction crew: JD Analyst + Resume Analyst -> JDProfile + ResumeProfile.

Why CrewAI here: two clearly separate "roles" with their own instructions, each
returning a Pydantic model. Why so locked down: CrewAI agents can loop, delegate
and retry on their own, which would hide extra LLM calls. We switch all of that off
so the crew makes EXACTLY 2 calls (1 per agent) and we count them to prove it.

Fallback (Review C3): CrewAI uses its own LLM clients, not our router. So if the
Gemini crew fails, we rebuild the whole crew with the Groq model and retry once.
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# Must be set BEFORE importing crewai: no telemetry / tracing leaves this machine.
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

from crewai import LLM, Agent, Crew, Process, Task  # noqa: E402
from crewai.events import crewai_event_bus  # noqa: E402
from crewai.events.types.llm_events import (  # noqa: E402
    LLMCallCompletedEvent,
    LLMCallStartedEvent,
)

import config  # noqa: E402
from llm.router import is_retryable, reserve_slots  # noqa: E402
from schemas import JDProfile, ResumeProfile  # noqa: E402

log = logging.getLogger("interview_arena.crew")


class _TokenEstimateFilter(logging.Filter):
    """CrewAI logs a WARNING on every call: "Using an approximate input token count...".
    It's harmless noise for users, so we demote it to DEBUG and only show it when
    debug logging is on (python cli.py ... --debug). Other CrewAI warnings are untouched."""

    def filter(self, record: logging.LogRecord) -> bool:
        if "approximate input token count" in record.getMessage():
            record.levelno, record.levelname = logging.DEBUG, "DEBUG"
            return log.isEnabledFor(logging.DEBUG)
        return True


logging.getLogger("crewai.llms.base_llm").addFilter(_TokenEstimateFilter())

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


class ExtractionError(RuntimeError):
    """Both the Gemini crew and the Groq crew failed. Message is safe to show users."""


class InputTooLongError(ValueError):
    """JD or resume text is over the PRD limit."""


# ------------------------------------------------------------ call counting
# CrewAI emits an event for every LLM call. Each event carries the agent_id,
# so we count only OUR agents' calls — correct even if two sessions run at once.


@dataclass
class _CallCounter:
    started: int = 0  # requests sent (counts failed ones too)
    completed: int = 0  # successful responses


_watched: dict[str, _CallCounter] = {}
_watched_lock = threading.Lock()


@crewai_event_bus.on(LLMCallStartedEvent)
def _on_llm_started(source, event) -> None:
    with _watched_lock:
        counter = _watched.get(str(event.agent_id))
        if counter:
            counter.started += 1


@crewai_event_bus.on(LLMCallCompletedEvent)
def _on_llm_completed(source, event) -> None:
    with _watched_lock:
        counter = _watched.get(str(event.agent_id))
        if counter:
            counter.completed += 1


# ------------------------------------------------------------ result


@dataclass
class ExtractionResult:
    jd_profile: JDProfile
    resume_profile: ResumeProfile
    llm_calls: int  # every request sent, across all attempts (for the session budget)
    model: str  # the model that produced the profiles
    fallback_used: bool
    attempts: list[str] = field(default_factory=list)  # e.g. ["gemini: failed (RateLimit)", ...]


# ------------------------------------------------------------ building blocks


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _build_llm(provider: str) -> tuple[LLM, str]:
    """Return (CrewAI LLM, model label).

    Gemini: CrewAI's native Google provider (uses the google-genai SDK, no LiteLLM).
    Groq: CrewAI has no native Groq provider, and its LiteLLM path needs an extra
    package. Groq's API is OpenAI-compatible, so we use CrewAI's native OpenAI client
    with Groq's base URL (custom_openai=True). CrewAI strips a leading "openai/" from
    the model name in this mode, so we add one — "openai/openai/gpt-oss-120b" is sent
    to Groq as "openai/gpt-oss-120b".
    """
    if provider == "gemini":
        model = config.MAIN_MODEL
        llm = LLM(model=f"gemini/{model}", api_key=config.get_key("GOOGLE_API_KEY"))
        return llm, f"gemini:{model}"
    if provider == "groq":
        model = config.BACKUP_MODEL_1
        llm = LLM(
            model=f"openai/{model}",
            custom_openai=True,
            base_url=config.GROQ_BASE_URL,
            api_key=config.get_key("GROQ_API_KEY"),
            temperature=0,
            max_retries=0,  # SDK default is 2 hidden retries; our fallback handles failures
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        )
        return llm, f"groq:{model}"
    raise ValueError(f"unknown provider {provider}")


def _build_crew(llm: LLM, jd_text: str, resume_text: str) -> tuple[Crew, list[Task], list[Agent]]:
    # Settings that keep each agent to ONE LLM call:
    #   allow_delegation=False -> can't hand work to the other agent (extra calls)
    #   max_iter=1             -> one reasoning step, then it must answer
    #   max_retry_limit=0      -> no silent CrewAI retries on error (default is 2)
    #   tools=[]               -> no tool calls
    #   reasoning/planning     -> off by default (they would add planning calls)
    common = dict(llm=llm, allow_delegation=False, max_iter=1, max_retry_limit=0, tools=[], verbose=False)
    jd_agent = Agent(
        role="JD Analyst",
        goal="Turn a job description into an accurate structured profile.",
        backstory=(
            "A technical recruiter who reads job descriptions precisely and never invents requirements."
        ),
        **common,
    )
    resume_agent = Agent(
        role="Resume Analyst",
        goal="Turn a resume into an accurate structured profile.",
        backstory="A hiring manager who extracts only facts written in the resume.",
        **common,
    )

    # We fill the prompt text ourselves and call kickoff() WITHOUT inputs, so CrewAI
    # does no {placeholder} filling — resume text with "{" or "}" can't break it.
    jd_task = Task(
        description=_load_prompt("jd_analyst.md").replace("{{JD_TEXT}}", jd_text),
        expected_output="A JDProfile JSON object.",
        agent=jd_agent,
        output_pydantic=JDProfile,
        context=[],  # don't receive other tasks' output
    )
    resume_task = Task(
        # Today's date lets the model turn "Sep 2025 – Present" into years of experience.
        description=_load_prompt("resume_analyst.md")
        .replace("{{TODAY}}", date.today().strftime("%B %Y"))
        .replace("{{RESUME_TEXT}}", resume_text),
        expected_output="A ResumeProfile JSON object.",
        agent=resume_agent,
        output_pydantic=ResumeProfile,
        context=[],  # by default a sequential task gets the JD output too — not needed
    )
    crew = Crew(
        agents=[jd_agent, resume_agent],
        tasks=[jd_task, resume_task],
        process=Process.sequential,
        tracing=False,
        verbose=False,
    )
    return crew, [jd_task, resume_task], [jd_agent, resume_agent]


def _check_inputs(jd_text: str, resume_text: str) -> None:
    if not jd_text.strip() or not resume_text.strip():
        raise ValueError("Both the job description and the resume are needed.")
    if len(jd_text) > config.MAX_JD_CHARS:
        raise InputTooLongError(f"Job description is over {config.MAX_JD_CHARS:,} characters.")
    if len(resume_text) > config.MAX_RESUME_CHARS:
        raise InputTooLongError(f"Resume is over {config.MAX_RESUME_CHARS:,} characters.")


# ------------------------------------------------------------ public function


def _run_crew_once(
    provider: str, jd_text: str, resume_text: str
) -> tuple[JDProfile, ResumeProfile, int, str]:
    """One crew run on one provider. Returns (jd, resume, llm_calls, label); raises on failure.
    The raised exception carries .llm_calls so failed attempts still count toward the budget."""
    llm, label = _build_llm(provider)
    crew, tasks, agents = _build_crew(llm, jd_text, resume_text)
    counter = _CallCounter()
    with _watched_lock:
        for agent in agents:
            _watched[str(agent.id)] = counter
    # CrewAI calls don't pass through the router, so book their 2 RPM slots here
    # (same process-wide limiter as the router -> never more than 12/min on Gemini).
    reserve_slots(provider, n=2)
    start = time.perf_counter()
    try:
        crew.kickoff()
        jd, resume = tasks[0].output.pydantic, tasks[1].output.pydantic
        # CrewAI leaves .pydantic as None if the reply didn't fit the schema.
        if not isinstance(jd, JDProfile) or not isinstance(resume, ResumeProfile):
            raise ValueError("model output did not match the JSON schema")
        return jd, resume, counter.started, label
    except Exception as exc:
        crewai_event_bus.flush()  # count this attempt's calls before reporting them
        exc.llm_calls, exc.label = counter.started, label  # type: ignore[attr-defined]
        raise
    finally:
        crewai_event_bus.flush()  # make sure every LLM event has been counted
        with _watched_lock:
            for agent in agents:
                _watched.pop(str(agent.id), None)
        log.info(
            "extract provider=%s latency=%.2fs llm_calls=%d (ok=%d)",
            label,
            time.perf_counter() - start,
            counter.started,
            counter.completed,
        )


def extract_profiles(jd_text: str, resume_text: str) -> ExtractionResult:
    """Run the 2-agent crew: Gemini first, then Groq.

    Like the LLM router, a provider that is busy (429 / 5xx / network) is retried with backoff
    before falling back — free tiers often return short "high demand" spikes. A wrong answer
    (output that doesn't fit the schema) goes straight to the next provider. Every attempt's
    calls are counted, so the session budget stays honest.
    """
    _check_inputs(jd_text, resume_text)

    providers = [
        p for p, key in (("gemini", "GOOGLE_API_KEY"), ("groq", "GROQ_API_KEY")) if config.has_key(key)
    ]
    if not providers:
        raise ExtractionError("No API key for Gemini or Groq. Add one to .env.")

    total_calls = 0
    attempts: list[str] = []
    for i, provider in enumerate(providers):
        for retry in range(config.MAX_RETRIES_PER_PROVIDER + 1):
            try:
                jd, resume, calls, label = _run_crew_once(provider, jd_text, resume_text)
            except Exception as exc:  # provider busy, bad output, network...
                total_calls += getattr(exc, "llm_calls", 0)
                label = getattr(exc, "label", provider)
                attempts.append(
                    f"{label}: failed ({type(exc).__name__}) after {getattr(exc, 'llm_calls', 0)} call(s)"
                )
                log.warning("extraction with %s failed: %s", label, type(exc).__name__)
                if is_retryable(exc) and retry < config.MAX_RETRIES_PER_PROVIDER:
                    wait = config.BACKOFF_BASE_SECONDS * (2**retry) + random.uniform(0, 1)
                    log.info("retrying extraction on %s in %.1fs", label, wait)
                    time.sleep(wait)
                    continue
                break  # not worth retrying (or retries used up): next provider
            total_calls += calls
            attempts.append(f"{label}: ok ({calls} calls)")
            return ExtractionResult(jd, resume, total_calls, label, fallback_used=i > 0, attempts=attempts)

    raise ExtractionError(
        "Could not read the JD and resume right now (all AI providers failed). "
        "Please wait a minute and try again. [" + "; ".join(attempts) + "]"
    )
