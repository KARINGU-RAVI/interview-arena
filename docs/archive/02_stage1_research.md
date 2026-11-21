# Interview Arena — Stage 1: Research Report

**Date:** 21 Nov 2026 · **Rule #1:** everything must run on **free tiers only** (₹0).

---

## 1. Summary (TL;DR)

| Question | Finding | Decision |
|---|---|---|
| Is the idea unique? | **No.** Many GitHub projects already build AI mock interviewers (CrewAI, LangGraph, AutoGen versions) and many commercial tools exist. | Build it, but **differentiate** (see §3). |
| Is AutoGen still the right choice? | **AutoGen is in maintenance mode.** Microsoft Agent Framework (MAF) 1.0 is the official successor (GA April 2026). | Use **Microsoft Agent Framework** for the panel (AutoGen's successor). Mention both in interviews. |
| Can it run free? | Yes, but a multi-agent interview uses **many LLM calls** (~25–30 per session). | **Gemini Flash-Lite** as main (≈500 req/day), Groq only as small backup. Lean prompts. |
| Free hosting? | Streamlit Community Cloud is free (~1 GB RAM, sleeps when idle). Hugging Face Gradio/Docker Spaces are now paid. | **Streamlit + Streamlit Community Cloud.** |
| Embeddings on free hosting? | `sentence-transformers` pulls in PyTorch (heavy for 1 GB RAM). | Use a **lightweight ONNX embedder (FastEmbed)** or Gemini's free embedding endpoint. Decide in PRD. |

---

## 2. Competitor Landscape

### Open-source (GitHub / blogs)
| Project | Stack | What it does | Gap |
|---|---|---|---|
| ai_mock_interview (CrewAI) | CrewAI | Agents run interview phases + feedback | Single framework, no memory, no evaluation numbers |
| Multi-Agent Interview Prep (LangGraph) | LangGraph + Ollama + Streamlit | 6 agents incl. mock interview agent | No panel dynamics, no leadership-principle focus |
| Intelligent Interview Prep System | LangGraph | 6-node feedback loop (Analyst → Coach) | Prep-focused, not a live panel |
| AutoGen Interview Simulator | AutoGen + GPT-4 | Multi-agent interviewer | Paid model, no RAG over user's stories |
| AI Interview System (Medium) | LangGraph | Auto-chooses panel per role, round-wise | No scoring rubric, no persistence |

### Commercial tools
Final Round AI, Yoodli (free tier), Huru, Google Interview Warmup, Pramp, edesy (leadership-principle practice), DayOne (paid leadership-principle story review, ex-Bar-Raiser).

**Takeaway:** "AI mock interview" alone will not impress. The *how* must stand out.

---

## 3. Our Differentiators (what most projects do NOT have)

1. **Real panel, real roles** — Tech Interviewer, Hiring Manager, **Values interviewer** (company principles) and an independent **Evaluator**, each with a clear job.
2. **Multi-framework with a reason** — LangGraph (session flow + memory), CrewAI (prep pipeline), Microsoft Agent Framework (panel group chat). One shared **custom MCP server** for tools.
3. **Story Bank (RAG)** — the user's own STAR stories + resume + JD are indexed; the values interviewer probes *your* stories, and the Evaluator checks if an answer maps to the right LP.
4. **Weakness memory** — scores per skill/LP persist across sessions; next session targets weak areas.
5. **Measured evaluation** — fixed rubric (STAR completeness, LP alignment, specificity, metrics). Report **scoring consistency** in README (same answer scored N times → variance).
6. **Free-tier engineering** — call budget per session, provider fallback, lean context.

---

## 4. Framework Research

| Framework | Status (Nov 2026) | Role in our project | Why this one |
|---|---|---|---|
| **LangGraph** (+ LangChain) | Active | Orchestrator: rounds, state, human-in-the-loop, memory | Best for stateful flows and control |
| **CrewAI** | Active | Prep pipeline: JD Analyst → Resume Analyst → Question Planner | Role-based sequential tasks are its core pattern |
| **AutoGen** | **Maintenance mode** (bug/security fixes only) | — | Not recommended for new projects |
| **Microsoft Agent Framework 1.0** | GA April 2026, AutoGen + Semantic Kernel successor | Interview panel (multi-agent group chat) | Carries AutoGen's conversational patterns, supported long-term |
| **MCP** (official Python SDK / FastMCP) | Active | Own server: save report, create calendar `.ics`, Gmail draft | Shared tool layer across all frameworks |


---

## 5. Free-Tier Stack & Budget

### LLM providers (all free, no card)
| Provider | Free limit (Nov 2026, verify in console) | Role |
|---|---|---|
| Gemini **Flash-Lite** (AI Studio) | ~500 req/day, per project | **Main** |
| Gemini Flash | ~20 req/day | Final demo only |
| Groq `gpt-oss-120b` | 30 RPM, 1,000 RPD, **200K tokens/day**, 8K TPM | Backup |
| OpenRouter `:free` | 20 RPM, 50 RPD | Last fallback |

### Call budget per interview session (estimate)
| Step | Calls |
|---|---|
| Prep crew (JD, resume, question plan) | ~4 |
| 8 questions × (ask + evaluate) | ~16 |
| Follow-up questions (~4) | ~4 |
| Final report | ~2 |
| **Total** | **~26 calls, ~80–100K tokens** |

| Provider | Sessions/day possible |
|---|---|
| Gemini Flash-Lite | **~15–19** ✅ |
| Groq | ~2 (token cap) ⚠️ |
| OpenRouter | ~1–2 ⚠️ |

**Design rules from this:**
- Don't pass the full chat history to every agent — pass a **short running summary**.
- Run the Prep crew **once per JD** and cache its output.
- Retrieve only **top 3** chunks from RAG.
- Keep model IDs in `.env`; auto-fallback Gemini → Groq → OpenRouter.

### Other free components
| Need | Free choice | Note |
|---|---|---|
| Vector store | Chroma (local) | Free, simple |
| Embeddings | FastEmbed (ONNX, light) **or** Gemini embedding (free tier) | Avoid heavy PyTorch on 1 GB hosting |
| UI | Streamlit | Free, Python-only |
| Hosting | Streamlit Community Cloud | ~1 GB RAM, sleeps after idle, public apps free |
| Tools | Own MCP server + official filesystem MCP | Free |
| Gmail draft | Gmail API, `gmail.compose` scope | Free; Google Cloud OAuth setup |
| Calendar | `.ics` file | No OAuth needed |
| Memory | JSON/SQLite file per user | Free (simple file) |

### Avoid (not free anymore / paid)
- Hugging Face Gradio/Docker Spaces (paid plan since 2026)
- GitHub Models (reported retired July 2026)
- Cerebras (now $5 trial)
- Voice APIs (paid) — text-only in v1

---

## 6. Content for RAG

| Source | Plan |
|---|---|
| company leadership principles | Write **our own short notes** on the 16 LPs (paraphrased, with example question themes) — don't copy long text |
| STAR method rubric | Write our own rubric file |
| User resume + JD | Uploaded by user (PDF/text) |
| User's Story Bank | User writes 5–10 STAR stories in a simple form |

---

## 7. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Free limits cut suddenly (Gemini cut Flash to 20/day before) | App stops | 3-provider fallback; Flash-Lite default |
| Free model scoring inconsistent | Unfair feedback | Fixed rubric + structured JSON scores + consistency test |
| Mixing 3 frameworks gets messy | Delays | Clear boundaries: one framework per module, shared MCP tools |
| MAF + free LLMs compatibility | Integration issues | Use OpenAI-compatible endpoints (Gemini/Groq both support) — verify on Day 1 |
| Data privacy (free tiers may train on prompts) | Resume data exposure | Warn users; demo with dummy resume |
| Project looks "common" | Low wow-factor | Differentiators in §3 + measured results in README |

---

## 8. Open Questions for Stage 2 (PRD)

1. v1 rounds: Tech + Values interviewer only, or also Hiring Manager?
2. Number of questions per session (8? 10?).
3. Embeddings: FastEmbed (local) vs Gemini embedding (API, counts toward quota)?
4. Memory: single user (you) or multi-user?
5. Gmail draft in v1, or only report file + `.ics`?
6. Deploy publicly on Streamlit Cloud in v1, or local only?
7. Role focus: Agentic AI / software roles only, or any role?
