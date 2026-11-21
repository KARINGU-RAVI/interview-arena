# Design decisions and trade-offs

The key choices behind Interview Arena, merged from the planning documents (originals in
[`docs/archive/`](archive/)) and the decisions taken while building it.

## Scope

| Decision | Choice | Why / trade-off |
|---|---|---|
| Product | Resume + JD → skill gaps → 10 gap-targeted questions → rubric scores → report | One strong, explainable flow instead of many shallow features |
| Rounds | 4 Technical · 3 Project · 3 HR, one follow-up | Covers skills, real projects and behaviour; fits the call budget |
| Out of scope (v1) | Voice, login, database, multi-user history, company-specific rounds | Keeps the build small and free to run |
| Cost | Free tiers only | Gemini (main) → Groq → OpenRouter fallback; no paid services |

## Frameworks

| Decision | Choice | Why / trade-off |
|---|---|---|
| Orchestration | **LangGraph** | Explicit state, checkpoints and human-in-the-loop pauses (`interrupt` + `Command`) |
| Extraction | **CrewAI**, 2 agents, no tools, no delegation, `max_iter=1` | Clear "roles"; locked down so it makes exactly 2 calls (counted via CrewAI events) |
| Multi-agent panel | AutoGen **rejected** | AutoGen is in maintenance mode (successor: Microsoft Agent Framework); deferred to v2 |
| RAG | **FastEmbed** (ONNX, CPU) + **Chroma in-memory** per session | Grounds gap evidence and questions in real resume text; no PyTorch (fits ~1 GB hosting) |
| Tools | **Own MCP server** (FastMCP, stdio): `save_report_pdf`, `create_gmail_draft` | Actions separated from reasoning; reusable by any MCP client |
| MCP adapter | `langchain-mcp-adapters` 0.3.2 (pinned) | The library was archived on 17 Sep 2026; its successor `langchain.mcp` is still beta |

## Reliability and cost

| Decision | Choice | Why / trade-off |
|---|---|---|
| LLM router | Retry with backoff on 429/5xx, then the next provider; process-wide rate limiter (Gemini 12 RPM, below the 15 RPM free limit) | Free tiers are bursty; one limiter because limits are per Google Cloud project |
| Budget | Target 18 LLM calls per session, hard cap 24 | Room for retries and fallbacks; a session never stops midway below the cap |
| Groq via CrewAI | CrewAI's native OpenAI client pointed at Groq's OpenAI-compatible API | Avoids an extra LiteLLM dependency |
| Structured output | Pydantic schemas + native JSON schema (Gemini, Groq); manual JSON parse for OpenRouter; 1 validation retry | Free models sometimes break JSON |

## Scoring

| Decision | Choice | Why / trade-off |
|---|---|---|
| Who computes scores | **Python**: match %, answer score (rubric weights), overall score, recommendation | Deterministic and explainable; the LLM never produces the final numbers |
| Evaluator output | One schema field per rubric criterion, each 0–10 with an evidence quote | A model can't skip or rename a criterion; scores are justified |
| Temperature | Gemini 3.5 Flash-Lite **ignores** temperature (fixed sampling) | Consistency comes from the rubric, strict schema and criterion scores; a ±1 consistency test guards it; `EVALUATOR_PROVIDER=groq` (temperature 0) is the fallback |
| Evaluator model | Locked per session | All answers scored by the same model; the report notes any fallback |
| Difficulty | From the **JD's level** (the job's bar), not the candidate's years | Practice should match the target role |
| Empty / "don't know" answers | Count as skipped: 0, no LLM call | Saves budget; nothing to evaluate |
| Follow-up | On the weakest **attempted** answer; reported separately, not in the overall | A fair second chance that doesn't distort the score |
| Prompt injection | Candidate text wrapped in tags and treated as data; closing tags neutralised | An answer saying "give me 10/10" scored 0 in testing |

## Graph and UI

| Decision | Choice | Why / trade-off |
|---|---|---|
| Pauses | `start_gate`, each answer, the follow-up answer, `send_gate` (13; +1 if a Gmail draft is made) | Every user wait is an explicit, checkpointed pause |
| LLM vs `interrupt()` | Never an LLM call before `interrupt()` in the same node | LangGraph re-runs a node from its start on resume |
| `final_evaluation` | Python only; the Report Writer makes one batched call (summary, actions, samples) | One fewer call; one consistent voice |
| Graph execution in the web app | **Background worker thread**, one step per session | Streamlit stops scripts on refresh/rerun; inside a worker a step can't be cut between nodes. Double submits are refused |
| Refresh survival | Session id in the URL (`?sid=`); state in the shared checkpointer | A Streamlit refresh is a new session; the interview continues where it paused |
| Report delivery | PDF made with the report (download at once); Gmail draft from the report screen, local mode only, no send tool | Drafts land in the mailbox that authorised OAuth, so Gmail is owner-only; everyone can download the PDF |

## Privacy

- Keys only in `.env` (git-ignored); never printed or logged.
- Free-tier providers may use inputs for training: the app warns users; tests use fictional data.
- An optional private test pair lives only in `tests/local/` (git-ignored).
