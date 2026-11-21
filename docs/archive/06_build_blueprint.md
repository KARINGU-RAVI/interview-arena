# Interview Arena — Build Blueprint (Inventory + Phase Plan)

**Rule:** ₹0, free tiers only. Single source of truth together with the PRD.

---

## Part A — What we are building (component inventory)

### A1. At a glance

| Component | Count | Items |
|---|---|---|
| **Agents (LLM roles)** | **7** | JD Analyst, Resume Analyst, Gap Analyst, Question Planner, Evaluator, Follow-up Interviewer, Report Writer |
| **Frameworks** | 2 (+MCP) | LangGraph (orchestrator), CrewAI (extraction crew) |
| **LangGraph nodes** | 12 | extract, gap_analysis, start_gate, plan_questions, ask_question, evaluate_answer, follow_up, evaluate_follow_up, final_evaluation, build_report, send_gate, deliver |
| **Conditional edges** | 1 | `route_next` (10 done? → follow_up, else → ask_question) |
| **Human pauses (interrupts)** | 4 types / 13 pauses per session | Start gate, answer (×10), follow-up answer, Send gate |
| **MCP servers** | 1 (own) | `interview-arena-tools` |
| **MCP tools** | 2 | `save_report_pdf`, `create_gmail_draft` |
| **Internal Python functions (not LLM)** | 5 | `parse_resume_pdf`, `retrieve_evidence` (RAG), `compute_match_percent`, `compute_overall_score`, `get_recommendation` |
| **LLM providers** | 3 | gemini-3.5-flash-lite (main; 15 RPM, 500 RPD), Groq (backup 1), OpenRouter (backup 2) |
| **Embedding model** | 1 | FastEmbed `BAAI/bge-small-en-v1.5` (local, ONNX) |
| **Vector store** | 1 | Chroma, **in-memory per session** |
| **Database** | **0** | No SQL/NoSQL DB in v1 |
| **Short-term memory** | 1 | LangGraph `InterviewState` + `InMemorySaver` checkpointer |
| **Long-term memory** | 0 | None in v1 (v2: weakness history) |
| **Pydantic schemas** | 7 | JDProfile, ResumeProfile, GapItem, Question, QuestionPlan, Evaluation, ReportSummary |
| **Prompt files** | 7 | jd_analyst, resume_analyst, gap_analyst, question_planner, evaluator, follow_up, report_writer |
| **Knowledge files** | 1 | `rubric.md` |
| **UI screens** | 4 | Input · Gap analysis · Interview · Report |
| **Outputs per session** | 3 | On-screen report, PDF file, Gmail draft (local mode) |
| **LLM calls per session** | target ≤ 18, hard cap 24 | see A5 |
| **Test assets** | 3 JD/resume pairs + 20 golden answers | consistency test (3 runs, ±1) |

> Note: only the **2 CrewAI analysts** are "agents" in the framework sense. The other 5 are **specialised LLM nodes inside a LangGraph agent graph** — say exactly that if asked.

### A2. Agents (roles)

| # | Agent | Lives in | Input | Output (schema) | Calls |
|---|---|---|---|---|---|
| 1 | JD Analyst | CrewAI | JD text | `JDProfile` | 1 |
| 2 | Resume Analyst | CrewAI | Resume text | `ResumeProfile` | 1 |
| 3 | Gap Analyst | LangGraph `gap_analysis` | Both profiles + RAG evidence | `list[GapItem]` | 1 |
| 4 | Question Planner | LangGraph `plan_questions` | Gaps, profiles, level | `QuestionPlan` (4/3/3) | 1 |
| 5 | Evaluator | LangGraph `evaluate_answer` / `evaluate_follow_up` | Q + answer + rubric + evidence | `Evaluation` | ≤11 |
| 6 | Follow-up Interviewer | LangGraph `follow_up` | Weakest Q&A | `Question` | 1 |
| 7 | Report Writer | LangGraph `build_report` (`final_evaluation` is Python only, decision 21 Nov 2026) | Scores + evaluations | `ReportSummary` (summary, top 3 actions, sample answers) | 1 |

### A3. Tools

| Tool | Type | Purpose | External service |
|---|---|---|---|
| `save_report_pdf` | MCP tool | Markdown → PDF file | none |
| `create_gmail_draft` | MCP tool | Draft email with PDF attached | Gmail API (`gmail.compose`) |
| `retrieve_evidence` | Python (RAG) | Top-3 chunks from resume/JD/rubric | none (local) |
| `parse_resume_pdf` | Python | PDF → text | none |
| `compute_*` / `get_recommendation` | Python | Deterministic scoring | none |

### A4. Memory & storage

| What | Where | Lifetime |
|---|---|---|
| Session state (profiles, questions, answers, scores) | `InterviewState` in `InMemorySaver` | Until browser session ends |
| Session id | `st.session_state["thread_id"]` | Browser session |
| Vectors (resume, JD, rubric chunks) | Chroma in-memory | Per session |
| Report PDF | temp folder (local) / download bytes (cloud) | Deleted after session |
| Secrets | `.env` (local) / Streamlit secrets (cloud) | Permanent, never committed |
| Gmail token | `token.json` (local only) | Permanent, never committed |

### A5. LLM budget per session

| Step | Calls |
|---|---|
| extract (2 agents) | 2 |
| gap_analysis | 1 |
| plan_questions | 1 |
| evaluate_answer × 10 (skips = 0) | ≤10 |
| follow_up + evaluate_follow_up | 2 |
| final_evaluation | 0 (Python only) |
| build_report | 1 (batched) |
| **Total** | **≤ 17** (target 18, cap 24) → ~29 sessions/day on Gemini Flash-Lite (~500 RPD) |

---

## Part B — Prerequisites (summary; details in `docs/archive/08_prerequisites.md`)

| Needed by phase | Prerequisite | Owner |
|---|---|---|
| Phase 0 | Python 3.10–3.12, Git, VS Code | You |
| Phase 0 | GitHub repo `interview-arena` | You |
| Phase 1 | Google AI Studio key + exact Flash-Lite model ID | You |
| Phase 1 | Groq key, OpenRouter key | You |
| Phase 2 | Target JD text + 1 resume (personal details removed) | You |
| Phase 4 | `rubric.md` + 7 prompt files | Claude drafts, you review |
| Phase 7 | Google Cloud project, Gmail API, OAuth Desktop client, you as Test user | You |
| Phase 9 | 2 more JD/resume pairs + 20 golden answers | Claude drafts, you review |
| Phase 10 | Streamlit Community Cloud account | You |

---

## Part C — Phase-by-phase plan (~10 working days)

| Phase | Goal | Key tasks | Outputs (deliverables) | Done when |
|---|---|---|---|---|
| **0. Setup** (½ day) | Ready repo | venv, folders, `.gitignore`, `.env.example`, `requirements.txt`, first commit | Project skeleton on GitHub | `pip install -r requirements.txt` works |
| **1. LLM router** (½ day) | Free LLM access with fallback | `llm/router.py`: Gemini → Groq → OpenRouter, retry/backoff, structured-output helper | `router.py`, `scripts/test_providers.py` | All 3 providers answer; fallback works when main key is removed |
| **2. Extraction** (1 day) | JD + resume → JSON | PDF parser, `JDProfile`/`ResumeProfile` schemas, CrewAI crew (2 agents, `max_iter=1`, own fallback) | `crew/extraction_crew.py`, `schemas.py` | Valid JSON for the sample JD + resume, ≤ 2 calls |
| **3. Gap + RAG** (1 day) | Find gaps with evidence | FastEmbed + Chroma store, `retrieve_evidence`, Gap Analyst prompt, `compute_match_percent` | `rag/store.py`, gap node | Gaps list + match % look right for sample |
| **4. Questions + Evaluator** (1.5 days) | Core intelligence | `rubric.md`, Question Planner (4/3/3 validated), Evaluator (JSON, per-criterion scores + evidence, Python-weighted score, `EVALUATOR_PROVIDER` lock, injection guard, skip = 0) | 2 prompts + nodes | 10 gap-targeted questions; weak answer < 5, strong ≥ 7 |
| **5. LangGraph wiring** (1 day) | Full flow in terminal | `InterviewState`, 12 nodes, `route_next`, 4 interrupt types, checkpointer, CLI runner | `graph/`, `cli.py` | Complete interview runs in terminal, ≤ 18 calls (cap 24) |
| **6. Report** (½ day) | Final output | Python scoring + recommendation, Report Writer (1 batched call), markdown report | `report.py` | Report markdown generated for a full session |
| **7. MCP server** (1 day) | Tools via MCP | FastMCP server with 2 tools, Gmail OAuth (local), MCP client via `langchain-mcp-adapters` | `mcp_server/server.py` | PDF saved + Gmail draft with attachment appears in your Drafts |
| **8. Streamlit UI** (1.5 days) | Usable app | 4 screens, session handling (`cache_resource`, `thread_id`), resume interrupts, download button, privacy note | `app.py` | Full flow in browser, survives reruns |
| **9. Testing** (1 day) | Prove it works | 3 JD/resume pairs, 20 golden answers, consistency test, fallback test, acceptance checklist | `tests/`, results table | All PRD acceptance criteria ✅ |
| **10. Ship** (1 day) | Release-ready | README (problem, diagrams, metrics, setup), demo GIF, deploy to Streamlit Cloud (`APP_MODE=cloud`) | Public link + README | Link works; README shows metrics |

### Phase dependencies
```
0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10
                       └──────────► 8 can start after 5 (UI on top of graph)
```

### Phase outputs checklist
- [ ] P0 repo skeleton
- [ ] P1 router + provider test
- [ ] P2 extraction crew
- [ ] P3 RAG store + gap analysis
- [ ] P4 rubric, planner, evaluator
- [ ] P5 LangGraph CLI end-to-end
- [ ] P6 report generator
- [ ] P7 MCP server (PDF + Gmail draft)
- [ ] P8 Streamlit app
- [ ] P9 tests + metrics
- [ ] P10 README, GIF, live link
