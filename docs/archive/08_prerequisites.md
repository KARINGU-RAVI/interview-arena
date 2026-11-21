# Interview Arena — Prerequisites Checklist

**Rule:** ₹0. Never add a card or link a billing account anywhere. Never paste keys into chat.

Legend: 🧑 = you do it · ⏱ = rough time

---

## 1. Your machine

| # | Item | How to check / do | ⏱ | Done |
|---|---|---|---|---|
| 1.1 | **Python 3.11** (3.10–3.12 OK) | `python --version` | 2 min | [ ] |
| 1.2 | **Git** | `git --version` | 2 min | [ ] |
| 1.3 | **VS Code** (or any editor) | — | — | [ ] |
| 1.4 | **RAM ≥ 8 GB** | Task Manager → Performance | 1 min | [ ] |
| 1.5 | Free disk ≥ 3 GB | for libraries + embedding model | 1 min | [ ] |

---

## 2. Accounts & API keys (all free, no card)

| # | Item | Steps | ⏱ | Done |
|---|---|---|---|---|
| 2.1 | **Google AI Studio key** (main LLM) | aistudio.google.com → Get API key → create in a **new project** → check **Rate limit** page: note the exact **Flash-Lite model ID** and RPD. Confirm **no billing linked**. | 5 min | [ ] |
| 2.2 | **Groq key** (backup) | console.groq.com → sign up → API Keys → create | 3 min | [ ] |
| 2.3 | **OpenRouter key** (last fallback) | openrouter.ai → sign up → Keys → create. Don't buy credits. | 3 min | [ ] |
| 2.4 | **GitHub account** + new repo `interview-arena` (public) | github.com → New repository → add `.gitignore` (Python) | 3 min | [ ] |
| 2.5 | **Streamlit Community Cloud** account | share.streamlit.io → sign in with GitHub (needed only on Day 10) | 2 min | [ ] |

---

## 3. Google Cloud for Gmail drafts (local mode only)

| # | Step | ⏱ | Done |
|---|---|---|---|
| 3.1 | console.cloud.google.com → **New project** `interview-arena` (no billing) | 2 min | [ ] |
| 3.2 | APIs & Services → Library → enable **Gmail API** | 1 min | [ ] |
| 3.3 | OAuth consent screen → **External** → app name, your email → add scope `gmail.compose` → add **yourself as Test user** | 5 min | [ ] |
| 3.4 | Credentials → Create credentials → **OAuth client ID** → type **Desktop app** → download `credentials.json` | 2 min | [ ] |
| 3.5 | Keep `credentials.json` in project root; it must be in `.gitignore` | — | [ ] |

> Needed only on Day 8. You can do it later.

---

## 4. Project setup (Day 1 — I'll give exact commands)

```
interview-arena/
  .venv/              (virtual environment)
  .env                (your keys — never committed)
  .env.example        (same names, no values — committed)
  .gitignore          (.env, credentials.json, token.json, .venv, *.pdf, chroma/)
```

### `.env` keys you will fill
```
GOOGLE_API_KEY=
GROQ_API_KEY=
OPENROUTER_API_KEY=
MAIN_MODEL=            # exact Flash-Lite ID from AI Studio
BACKUP_MODEL_1=openai/gpt-oss-120b
BACKUP_MODEL_2=        # an OpenRouter :free model with tool support
APP_MODE=local         # local | cloud
```

### Libraries (`requirements.txt`, all free/open-source)
| Purpose | Package |
|---|---|
| Orchestration | `langgraph`, `langchain`, `langchain-core` |
| LLM clients | `langchain-google-genai`, `langchain-groq`, `langchain-openai` (for OpenRouter) |
| Extraction | `crewai` |
| Validation | `pydantic` |
| RAG | `fastembed`, `chromadb` |
| PDF in / out | `pypdf`, `fpdf2`, `markdown` |
| MCP | `mcp`, `langchain-mcp-adapters` |
| Gmail | `google-api-python-client`, `google-auth-oauthlib` |
| UI | `streamlit` |
| Config | `python-dotenv` |

---

## 5. Test data (you prepare, I help)

| # | Item | Notes | Done |
|---|---|---|---|
| 5.1 | **JD #1**: target Agentic AI Engineer JD (text) | Prepared | [ ] |
| 5.2 | **JD #2, #3**: one frontend role, one full-stack role | Copy from any job post | [ ] |
| 5.3 | **Resume #1**: your resume with phone/address removed (PDF) | Or a dummy resume | [ ] |
| 5.4 | **Resume #2**: a dummy resume (I can generate) | — | [ ] |
| 5.5 | **Golden answers**: 10 strong + 10 weak answers to sample questions | I'll draft; you review | [ ] |

---

## 6. Knowledge files (I'll draft, you review)

| File | Content |
|---|---|
| `data/rubric.md` | Scoring rubric (Technical/Project and HR tables from PRD §11) |
| `prompts/*.md` | Extraction, gap, question plan, evaluation, report prompts |

---

## 7. Day-1 readiness check

- [ ] `python --version` shows 3.10–3.12
- [ ] 3 keys saved in `.env` (not shared anywhere)
- [ ] Exact Gemini Flash-Lite model ID noted
- [ ] GitHub repo created
- [ ] Target JD + one resume ready

When all five are ticked → we start Day 1 (setup + LLM router + provider test).
