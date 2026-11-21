# Interview Arena — Product Requirements Document (PRD) v1

| | |
|---|---|
| **Owner** | Project maintainer |
| **Version** | v1.1 (21 Nov 2026) — includes cross-validation fixes |
| **Budget** | ₹0 — free tiers only (top priority) |
| **Purpose** | Resume / interview showcase for Agentic AI roles |
| **Status** | Approved for build |

---

## 1. Overview

**Interview Arena** is an agentic AI mock-interview app. A candidate uploads a **resume** and pastes a **job description (JD)**. AI agents extract both, find the **biggest skill gaps**, run a **10-question interview** (Technical, Project, HR), **evaluate** each answer against the JD, and produce a **report** that is saved as a **PDF** and placed as a **Gmail draft** for the candidate.

---

## 2. Problem Statement

Job candidates struggle to prepare for interviews because:
1. Practice questions online are **generic**, not tailored to a specific JD and their own resume.
2. They don't know **which gaps** between their resume and the JD an interviewer will probe.
3. They get **no honest, structured feedback** — why an answer was weak and how to improve it.
4. Human mock interviews are **expensive** and hard to schedule.
5. A plain chatbot asks random questions and gives inconsistent feedback.

## 3. Solution

An AI interview panel that:
- **Reads both** the JD and resume and builds a **gap analysis** (matched / partial / missing skills).
- **Targets the biggest gaps** with a realistic 10-question interview across 3 rounds.
- **Scores every answer** with a fixed rubric, out of 10, against the JD.
- **Generates a report** (scores, strengths, gaps, better sample answers, hire recommendation) and drafts it to the candidate's Gmail.

---

## 4. Goals & Non-Goals

### Goals (v1)
- G1. End-to-end flow: input → gap analysis → 10 questions → evaluation → report → Gmail draft.
- G2. Runs entirely on **free tiers**.
- G3. Demonstrable in **under 3 minutes** in an interview.
- G4. Showcases **LangGraph, CrewAI, RAG, MCP**, tool calling, human-in-the-loop.
- G5. Public live link on Streamlit Community Cloud.

### Non-Goals (v1)
- Voice / video interviews
- Multiple users, login, session history
- Auto-sending emails (draft only)
- Coding rounds with code execution
- Company-specific rounds (e.g., leadership-principle questions) — v2
- Non-English languages

---

## 5. Target User

**Primary:** Job candidates (freshers to ~5 years) preparing for software / AI roles.
**Persona:** *A developer with about one year of experience applying for an Agentic AI role. Wants to know where the resume falls short for the JD and practise those exact areas.*

---

## 6. Decisions (from clarity questions)

| # | Topic | Decision |
|---|---|---|
| 1 | Frameworks | **LangGraph** (orchestrator) + **CrewAI** (extraction crew) |
| 2 | RAG | Yes — index resume + JD sections + rubric; retrieve evidence for questions and evaluation |
| 3 | MCP | Own MCP server: `save_report_pdf`, `create_gmail_draft` |
| 4 | Resume input | PDF upload **or** pasted text |
| 5 | JD input | Pasted text |
| 6 | Language | English only |
| 7 | Extraction | Skills, tools, years of experience, projects, education, certifications, role level |
| 8 | Gap analysis | Shown to user **before** interview starts |
| 9 | Rounds | **4 Technical · 3 Project/Resume · 3 HR = 10** |
| 10 | HR style | Generic behavioural (STAR format); company leadership principles in v2 |
| 11 | Follow-ups | Max **1 follow-up** per session total (on the weakest answer) |
| 12 | Answer input | Text only |
| 13 | Time limit | None |
| 14 | Skip | Allowed → scored 0, marked "skipped" |
| 15 | Difficulty | Based on the **JD's level** (the job's bar: junior / mid / senior), not the candidate's years of experience (decision 21 Nov 2026) |
| 16 | Scoring criteria | Tech/Project: correctness, depth, clarity, JD relevance. HR: STAR completeness, clarity, impact/metrics |
| 17 | Scale | 0–10 per answer + overall score + recommendation (Strong Hire / Hire / Lean No / No Hire) |
| 18 | Sample answers | Yes, for answers scoring < 6 |
| 19 | Report format | PDF + short email body summary |
| 20 | Email | **Gmail draft only** (user reviews and sends) |
| 21 | Recipient | Candidate only |
| 22 | User type | Candidates |
| 23 | History | None in v1 (each session independent) |
| 24 | Deployment | Local first → Streamlit Community Cloud at the end |

---

## 7. User Flow

```
[1] Upload resume (PDF/text) + paste JD + enter email
        ↓
[2] Extraction (CrewAI crew)  →  structured JD profile + resume profile
        ↓
[3] Gap analysis  →  shown to user: match %, matched / partial / missing skills
        ↓   (user clicks "Start Interview"  ← human-in-the-loop)
[4] Question plan: 4 Tech + 3 Project + 3 HR, weighted toward biggest gaps
        ↓
[5] Interview loop (one question at a time)
        user answers → evaluator scores → next question
        (1 follow-up allowed on weakest answer)
        ↓
[6] Final evaluation  →  overall score + recommendation
        ↓
[7] Report  →  PDF saved (MCP) + Gmail draft created (MCP, after user clicks "Send to my Gmail")
```

---

## 8. Functional Requirements

### FR-1 Input
- FR-1.1 Accept resume as PDF (≤ 2 MB) or pasted text.
- FR-1.2 Accept JD as pasted text (≤ 8,000 characters).
- FR-1.3 Accept candidate email (validated format).
- FR-1.4 Show clear error for empty/unreadable input.

### FR-2 Extraction (CrewAI)
- FR-2.1 **JD Analyst agent** outputs JSON: role title, level, required skills, nice-to-have skills, responsibilities.
- FR-2.2 **Resume Analyst agent** outputs JSON: skills, tools, total experience, projects (name, tech, impact), education, certifications.
- FR-2.3 Outputs must follow a fixed **JSON schema** (Pydantic validation; retry once on invalid output).

### FR-3 Gap Analysis
- FR-3.1 For each required JD skill, classify: **Matched / Partial / Missing** (with resume evidence).
- FR-3.2 Compute match % = (matched + 0.5 × partial) / total required.
- FR-3.3 Rank gaps by importance (required > nice-to-have).
- FR-3.4 Display gap analysis to the user before the interview.

### FR-4 Question Planning
- FR-4.1 Generate 10 questions: 4 Technical, 3 Project/Resume, 3 HR.
- FR-4.2 At least **3 Technical questions target Missing/Partial skills**.
- FR-4.3 Project questions reference **real projects from the resume** (retrieved via RAG).
- FR-4.4 Difficulty matches the **JD's level** (`JDProfile.level` — the job's bar), not the candidate's years of experience.
- FR-4.5 No duplicate questions.

### FR-5 Interview
- FR-5.1 Show one question at a time with round label (Technical / Project / HR) and progress (e.g., 3/10).
- FR-5.2 Accept text answer; allow **Skip**. Empty answers, "skip", "don't know" / "idk" / "no idea", or answers under 3 words count as **skipped**: score 0, no LLM call, feedback "No answer given" (decision 21 Nov 2026).
- FR-5.3 After the 10 questions, ask **1 follow-up** on the lowest-scoring **attempted** answer (not skipped, scored, score > 0). If there is none, the follow-up is skipped (0 calls). The follow-up score is **reported separately** and does not change the overall score (decision 21 Nov 2026).
- FR-5.4 Session state persists across Streamlit reruns (graph cached with `st.cache_resource`, `thread_id` in `st.session_state`, `InMemorySaver` checkpointer).
- FR-5.5 Every wait for the user is a LangGraph `interrupt()` resumed with `Command(resume=...)`: Start gate, each of the 10 answers, the follow-up answer, and the Send gate (13 pauses; +1 if the user creates a Gmail draft, because the graph returns to the Send gate so Download/Finish stay available — decision 21 Nov 2026).
- FR-5.6 Input limits: resume ≤ 12,000 chars, JD ≤ 8,000 chars, answer ≤ 2,000 chars. If PDF text < 200 chars (scanned PDF), ask the user to paste text.

### FR-6 Evaluation
- FR-6.1 Each answer scored using the rubric (§11). The LLM returns JSON: per-criterion scores (0–10) each with an evidence quote, strengths, weaknesses, one-line feedback. The answer score (0–10) is the weighted sum, computed in Python.
- FR-6.2 Evaluation retrieves relevant JD/resume chunks (RAG) as evidence.
- FR-6.3 Overall score = weighted average (Technical 40%, Project 30%, HR 30%).
- FR-6.4 Recommendation from overall score: ≥8 Strong Hire · 6.5–7.9 Hire · 5–6.4 Lean No · <5 No Hire.

### FR-7 Report
- FR-7.1 Report contains: candidate name, role, date, match %, gap table, per-question score + feedback, sample better answers (score < 6), overall score, recommendation, top 3 improvement actions.
- FR-7.2 Report rendered in the app and saved as **PDF** via MCP tool as soon as the report is built; the PDF download is available immediately, before any Gmail choice (decision 21 Nov 2026).
- FR-7.3 On "Send to Gmail", MCP tool creates a **Gmail draft** addressed to the candidate with summary in body and PDF attached. Note: Gmail drafts are created in the mailbox that authorized the app, so this works in **local/owner mode** (v1). Everyone can always **download the PDF**.
- FR-7.4 Overall score and recommendation are computed in **Python** (deterministic); the LLM only writes the summary, top 3 actions and sample answers (one batched call).

### FR-8 Errors & Fallback
- FR-8.1 LLM call fails / rate-limited → retry with backoff → switch provider (Gemini → Groq → OpenRouter).
- FR-8.2 Gmail not configured → still allow PDF download.
- FR-8.3 Friendly message if all providers are exhausted.
- FR-8.4 The evaluator model is **locked per session** to `EVALUATOR_PROVIDER` (`gemini` default, or `groq`); if a fallback occurs, the report notes which model scored which answers.
- FR-8.5 CrewAI uses its own LiteLLM config (`allow_delegation=False`, `max_iter=1`, Pydantic output); on failure the crew is rebuilt with the backup model and retried once.
- FR-8.6 Candidate answers and uploaded text are wrapped in delimiters and treated as data (prompt-injection guard). Skipped answers get 0 without an LLM call.

---

## 9. Non-Functional Requirements

| ID | Requirement |
|---|---|
| NFR-1 Cost | ₹0. No billing account linked to any provider. |
| NFR-2 LLM budget | **Target 18** LLM calls, **hard cap 24**, ≤ 100K tokens per session. Failed requests and retries are logged and shown in the report but never stop a session midway; the router blocks only at 24. |
| NFR-3 Latency | Each question/evaluation step < 15 s on Gemini Flash-Lite. |
| NFR-4 Privacy | Warn users not to upload sensitive personal data (free tiers may use prompts for training). No data stored after session. |
| NFR-5 Security | Keys/tokens in `.env` / Streamlit secrets; Gmail scope `gmail.compose` only; never send automatically. |
| NFR-6 Reliability | 3-provider fallback; model IDs configurable. |
| NFR-7 Hosting | Fits Streamlit Community Cloud (~1 GB RAM) — no PyTorch. |
| NFR-8 Maintainability | One module per responsibility; typed state; README with architecture diagram. |

---

## 10. Architecture

```
┌──────────────────────────── Streamlit UI ────────────────────────────┐
│ Input form │ Gap analysis │ Interview chat │ Report │ [Send to Gmail] │
└──────┬───────────────────────────────────────────────────────▲───────┘
       ▼                                                       │
┌──────────────────── LangGraph Orchestrator ─────────────────────────┐
│ extract → gap_analysis → [HUMAN: start] → plan_questions →          │
│ ask_question ⇄ evaluate_answer (loop ×10) → follow_up →             │
│ final_evaluation → build_report → [HUMAN: send] → email_draft       │
└──┬───────────────┬───────────────────┬────────────────────┬────────┘
   ▼               ▼                   ▼                    ▼
 CrewAI         RAG (Chroma +       LLM Router           MCP Server (own)
 Extraction     FastEmbed)          Gemini → Groq →      • save_report_pdf
 Crew           resume, JD,         OpenRouter           • create_gmail_draft
 (JD + Resume   rubric chunks
 Analysts)
```

### LangGraph nodes
| Node | Does | LLM calls |
|---|---|---|
| `extract` | Runs CrewAI crew (2 agents) | 2 |
| `gap_analysis` | Compares profiles, computes match % | 1 |
| `plan_questions` | Generates 10 questions (JSON) | 1 |
| `ask_question` | Shows next question (no LLM) | 0 |
| `evaluate_answer` | Scores answer with rubric + RAG evidence | 10 |
| `follow_up` + `evaluate_follow_up` | Writes 1 follow-up question; then pauses for the answer and scores it | 2 |
| `final_evaluation` | Overall score + recommendation (**Python only**, decision 21 Nov 2026) | 0 |
| `build_report` | Report Writer: summary + top 3 actions + sample answers (**one batched call**) | 1 |
| `email_draft` | Calls MCP tools (no LLM) | 0 |
| **Total** | | **≤18 calls target** (hard cap 24) ✅ within budget (see Orchestration Review for the corrected node list) |

### State (LangGraph `TypedDict`)
```python
class InterviewState(TypedDict):
    resume_text: str
    jd_text: str
    candidate_email: str
    jd_profile: dict
    resume_profile: dict
    gaps: list[dict]  # skill, status, importance, evidence
    match_percent: float
    questions: list[dict]  # id, round, text, target_skill
    current_index: int
    answers: list[dict]  # question_id, answer, skipped
    evaluations: list[dict]  # question_id, score, criteria, feedback
    follow_up: dict | None
    overall: dict  # score, recommendation, top_actions
    report_path: str | None
```

---

## 11. Scoring Rubric

### Technical & Project questions (0–10)
| Criterion | Weight | 0–2 | 3–5 | 6–8 | 9–10 |
|---|---|---|---|---|---|
| Correctness | 40% | Wrong | Partly right | Right | Right + precise |
| Depth | 25% | Surface | Basic | Explains why/how | Trade-offs, edge cases |
| Clarity | 15% | Confusing | Understandable | Clear | Clear + structured |
| JD relevance | 20% | Off-topic | Loosely related | Relevant | Directly tied to JD needs |

### HR questions (0–10)
| Criterion | Weight |
|---|---|
| STAR completeness (Situation, Task, Action, Result) | 40% |
| Impact / measurable result | 25% |
| Clarity & structure | 20% |
| Relevance to question | 15% |

Evaluator prompt must return **JSON only**: a score (0–10) for **each rubric criterion**, each with a short **evidence quote** from the answer. The overall answer score is computed in **Python** from the criterion weights above — not by the LLM.

**Consistency (decision 21 Nov 2026):** Gemini 3.5 Flash-Lite and newer use fixed sampling and **ignore temperature** (Google: "`temperature`, `top_p`, and `top_k` are deprecated and ignored"). Consistency therefore comes from the rubric, the strict JSON schema, criterion-level scoring and the Phase 9 consistency test (±1). If that test fails, set `EVALUATOR_PROVIDER=groq` (gpt-oss-120b, honours temperature 0).

---

## 12. MCP Server Spec (own server, FastMCP, stdio)

| Tool | Input | Output | Notes |
|---|---|---|---|
| `save_report_pdf` | `report_markdown: str, filename: str` | `{path}` | Markdown → PDF (`fpdf2`/`markdown` libs) |
| `create_gmail_draft` | `to: str, subject: str, body: str, attachment_path: str` | `{draft_id}` | Gmail API, scope `gmail.compose`, **no send tool exists** |

---

## 13. Tech Stack (all free)

| Layer | Choice |
|---|---|
| Language | Python 3.11 |
| Orchestration | LangGraph + LangChain |
| Extraction | CrewAI |
| LLM main | `gemini-3.5-flash-lite` (Google AI Studio free tier: 15 RPM, 250K TPM, 500 RPD — confirmed 21 Nov 2026) |
| LLM backups | Groq `openai/gpt-oss-120b` → Groq `qwen/qwen3.8-27b` → OpenRouter `:free` |
| Embeddings | FastEmbed (`BAAI/bge-small-en-v1.5`, ONNX, CPU) |
| Vector store | Chroma (in-memory per session) |
| PDF parsing | `pypdf` |
| PDF report | `fpdf2` |
| MCP | Official MCP Python SDK (FastMCP) + `langchain-mcp-adapters` |
| Email | Gmail API (`google-api-python-client`, `google-auth-oauthlib`) |
| UI / Hosting | Streamlit / Streamlit Community Cloud |
| Validation | Pydantic |

### Free-tier budget per session
Target 18 LLM calls (hard cap 24 for retries/fallbacks), ~60–80K tokens → **~27 sessions/day** on `gemini-3.5-flash-lite` (500 RPD). 15 RPM means the router must space bursts (CrewAI's 2 back-to-back calls are fine).

---

## 14. Folder Structure

```
interview-arena/
├── app.py                    # Streamlit UI
├── config.py                 # env, model IDs, thresholds
├── graph/
│   ├── state.py              # InterviewState
│   ├── nodes.py              # LangGraph nodes
│   └── build_graph.py        # graph + checkpointer
├── crew/
│   └── extraction_crew.py    # CrewAI JD + Resume analysts
├── rag/
│   └── store.py              # FastEmbed + Chroma
├── llm/
│   └── router.py             # Gemini → Groq → OpenRouter fallback
├── prompts/                  # question, evaluation, report prompts
├── data/
│   ├── rubric.md
│   ├── sample_jd.txt
│   └── sample_resume.pdf
├── mcp_server/
│   └── server.py             # save_report_pdf, create_gmail_draft
├── tests/
│   └── test_cases.md         # evaluation test set
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

---

## 15. Milestones (≈ 10 working days)

| Day | Milestone | Done when |
|---|---|---|
| 1 | Setup + LLM router + provider test | All 3 providers respond; fallback works |
| 2 | CrewAI extraction crew | Valid JD + resume JSON for sample files |
| 3 | Gap analysis + RAG store | Correct matched/partial/missing for sample |
| 4 | Question planner | 10 valid, gap-targeted questions |
| 5 | Evaluator + rubric | Scores JSON for sample answers |
| 6 | LangGraph wiring (full flow in terminal) | End-to-end run in CLI |
| 7 | Streamlit UI | Full flow in browser |
| 8 | MCP server (PDF + Gmail draft) | Draft with PDF appears in Gmail |
| 9 | Testing + consistency check | Acceptance criteria met |
| 10 | README, demo GIF, deploy to Streamlit Cloud | Public link works |

---

## 16. Acceptance Criteria

- [ ] Full session completes with **≤ 30 LLM calls**, ₹0 spent.
- [ ] Gap analysis correctly classifies ≥ 80% of skills on 3 test JD/resume pairs (checked manually).
- [ ] ≥ 3 of 4 Technical questions target Missing/Partial skills.
- [ ] Evaluator gives a **clearly weak** answer < 5 and a **strong** answer ≥ 7 in all test cases.
- [ ] **Consistency:** same answer scored 3 times → max difference ≤ 1 point.
- [ ] Report PDF generated and Gmail draft created with attachment.
- [ ] Provider fallback works when main model is disabled.
- [ ] App runs on Streamlit Community Cloud.

---

## 17. Success Metrics (for the README)

- Session cost: **₹0**
- LLM calls per session: ~19
- Scoring consistency: ±1 point
- Gap-detection accuracy on test set: target ≥ 80%
- End-to-end session time: target < 10 minutes

---

## 18. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Free limits change | 3-provider fallback; model IDs in config |
| Inconsistent scores | Rubric + strict JSON + per-criterion scores with evidence + Python-weighted score + consistency test (±1); fallback: `EVALUATOR_PROVIDER=groq` (temperature 0). Gemini 3.5 Flash-Lite ignores temperature. |
| CrewAI + LangGraph integration friction | CrewAI runs as one isolated node returning JSON |
| Gmail OAuth on Streamlit Cloud is tricky | Gmail draft works **locally**; deployed version offers PDF download (documented) |
| Personal data in free-tier prompts | Privacy warning; demo with dummy resume |
| Common project idea | Differentiate with gap-targeted questions, rubric scoring, consistency metric, MCP tools |

---

## 19. Future (v2)

- Company leadership-principle (bar-raiser style) round
- Microsoft Agent Framework panel (multi-agent group chat)
- Session history + weakness tracking
- Voice answers
- Deploy agent on AWS Bedrock AgentCore

---

