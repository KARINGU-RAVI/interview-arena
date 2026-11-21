# Interview Arena — Orchestration Cross-Validation (2 passes)

**Verdict:** The overall shape is right (linear pipeline → human gate → 10-question loop → report → human gate). Two passes found **3 critical issues**, **6 important issues** and **5 minor ones**. All are fixable without adding scope. Fixes are already applied to the PRD.

---

## Pass 1 — Node-by-node check

| # | Node | Input → Output | Check | Status |
|---|---|---|---|---|
| 1 | Input | PDF/text + JD + email → raw text | Scanned/image PDFs return **empty text** with pypdf | ⚠️ Fix: if extracted text < 200 chars → ask user to paste text |
| 2 | `extract` (CrewAI) | text → `jd_profile`, `resume_profile` | **CrewAI uses its own LLM client (LiteLLM), not our LangChain router → no fallback.** CrewAI agents can also loop internally (extra hidden calls) | ❌ **Critical** — Fix below (C3) |
| 3 | `gap_analysis` | profiles → gaps, match % | Skill synonyms ("LLMs" vs "large language models") break simple matching | ⚠️ Fix: one LLM call classifies each JD skill with resume evidence; **match % computed in Python** |
| 4 | Gate: Start | — | Implemented with LangGraph `interrupt()` + `Command(resume=...)` | ✅ |
| 5 | `plan_questions` | gaps + profiles → 10 questions | Must return exact split 4/3/3 | ⚠️ Fix: Pydantic schema + validate counts; retry once |
| 6 | `ask_question` | question → **user answer** | **Diagram marked it "no LLM, no wait" — but it must PAUSE for the user's answer every time** | ❌ **Critical** — Fix below (C1) |
| 7 | `evaluate_answer` | Q + answer + rubric + evidence → score JSON | Answer may contain prompt injection ("give me 10/10"); skipped answers waste a call | ⚠️ Fix: wrap answer in delimiters, "treat as data"; **skip → score 0 without LLM call** |
| 8 | Loop edge | index < 10 → ask, else → follow_up | In LangGraph this is a **conditional edge function**, not a node | ✅ (naming fix in diagram) |
| 9 | `follow_up` | weakest answer → 1 new question → **user answer** → score | Also needs to **pause for the answer** | ❌ Part of C1 |
| 10 | `final_evaluation` | all scores → overall + recommendation | Overall score/recommendation must be **deterministic** | ⚠️ Fix: compute in Python; LLM only writes summary + top 3 actions |
| 11 | `build_report` | everything → markdown | Sample answers for each low score = many calls | ⚠️ Fix: **one batched call** for all sample answers |
| 12 | Gate: Send | — | `interrupt()` | ✅ |
| 13 | `email_draft` (MCP) | report → PDF + Gmail draft | **Gmail draft is created in the mailbox that logged in with OAuth (yours), not the candidate's** | ❌ **Critical** — Fix below (C2) |

---

## Pass 2 — Cross-cutting check

| Area | Finding | Severity | Fix |
|---|---|---|---|
| Streamlit ↔ LangGraph | Streamlit reruns the script on every click; graph state would be lost | Important | Build graph once with `st.cache_resource`; store `thread_id` in `st.session_state`; `InMemorySaver` checkpointer |
| Model switching mid-session | If fallback switches model halfway, scores become inconsistent | Important | Lock the **evaluator model per session**; if a fallback happens, note it in the report |
| Structured output on free models | OpenRouter/Groq free models may break JSON | Important | Pydantic validation + 1 retry + fallback; Gemini uses native structured output |
| Token budget | Long answers / long resumes inflate tokens | Important | Limits: resume 12K chars, JD 8K chars, answer 2K chars |
| Rate limits per minute | Gemini Flash-Lite RPM is low-ish; CrewAI fires 2 calls back-to-back | Minor | Router retries with backoff on 429 |
| Hosting (Streamlit Cloud) | Files are temporary; OAuth browser flow doesn't work in cloud | Important | Cloud: PDF via `st.download_button`; Gmail only in local mode |
| MCP server process | stdio server is a subprocess; must start once, not per rerun | Minor | Start MCP client session once (cached); close on exit |
| RAG value | Resume + JD are small; RAG could look forced | Minor | Use RAG for **evidence snippets** cited in questions and report ("Based on your project X…") |
| Privacy | Resume sent to free-tier LLMs that may train on data | Minor | Visible warning + "use a dummy resume" note; nothing stored after session |
| Testing | No golden data to prove scoring works | Important | Golden set: 3 JD/resume pairs + 10 strong + 10 weak answers; consistency test (3 runs, ±1) |
| Observability | Hard to debug agent steps | Minor | Log each node's input/output size, model used, latency to console/JSON |

---

## The 3 critical fixes

### C1 — Waiting for answers (interrupts)
- `ask_question` calls `interrupt({"question": ...})` → UI shows question → user submits → `Command(resume={"answer": ..., "skipped": bool})` → `evaluate_answer`.
- Same for `follow_up`.
- Total pauses: Start gate (1) + answers (10) + follow-up (1) + Send gate (1) = **13 human pauses**.

### C2 — Gmail mailbox ownership
- Gmail API drafts are created in **the account that authorized the app**.
- v1 behaviour: Gmail draft works for **you (local mode)** — draft addressed to the candidate email sits in your Drafts.
- For other users: Google OAuth "Testing" mode allows only listed test users; public use would need Google app verification.
- **Decision:** v1 = PDF download for everyone + Gmail draft in local/owner mode. Documented in README. (Optional v2: each user signs in with their own Google account.)

### C3 — CrewAI and the free-model fallback
- Configure CrewAI with its own `LLM(model="gemini/<flash-lite-id>")` via LiteLLM.
- Set `allow_delegation=False`, `max_iter=1`, no tools, output as Pydantic model → keeps it to ~2 calls.
- Wrap `crew.kickoff()` in try/except: on rate-limit/failure, rebuild the crew with the Groq model and retry once.

---

## Corrected orchestration (LangGraph)

```
START
 → extract (CrewAI, 2 calls)
 → gap_analysis (1 call; match % in Python)
 → [INTERRUPT] start_gate
 → plan_questions (1 call, validated 4/3/3)
 → ask_question  ── [INTERRUPT: wait for answer]
 → evaluate_answer (1 call; skip = 0, no call)
 → route_next():  index < 10 → ask_question
                  else       → follow_up
 → follow_up (1 call to write question; NO pause — see note)
 → evaluate_follow_up ── [INTERRUPT: wait for answer] then 1 call to score
 → final_evaluation (Python only: score + recommendation)
 → build_report (1 batched call: summary + top 3 actions + sample answers)

Note (decision 21 Nov 2026): LangGraph re-runs a node from its start when it resumes after interrupt(),
so an LLM call placed before interrupt() in the same node would run twice. Every pausing
node therefore calls interrupt() first. 12 nodes and 13 pauses are unchanged.
 → [INTERRUPT] send_gate
 → deliver (MCP: save_report_pdf → create_gmail_draft if local mode)
END
```

### Recounted LLM budget
| Step | Calls |
|---|---|
| extract | 2 |
| gap_analysis | 1 |
| plan_questions | 1 |
| evaluate_answer ×10 | ≤10 |
| follow_up + evaluate | 2 |
| final_evaluation | 0 (Python only, decision 21 Nov 2026) |
| build_report | 1 (batched: summary + actions + samples) |
| **Total** | **≤ 17** (target 18, hard cap 24) ✅ (~29 sessions/day on Gemini Flash-Lite) |

---

## Final checklist (orchestration is "strong" when all are ✅)
- [ ] Every user wait is an `interrupt()` (13 pauses)
- [ ] All LLM outputs validated with Pydantic + 1 retry
- [ ] Scores, overall and recommendation computed in Python
- [ ] Evaluator model locked per session
- [ ] CrewAI limited to 2 calls with its own fallback
- [ ] Gmail mode clearly local/owner only; PDF download always works
- [ ] Input size limits enforced
- [ ] Golden test set + consistency test pass
