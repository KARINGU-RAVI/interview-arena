# 01 — Project Brief & Decision Log

## Why this project exists
Target role: **Agentic AI Engineer** (JD skills: Agentic AI, Python, LangGraph, LangChain, Claude API, Claude Code, CrewAI, AutoGen, AWS, RAG, Amazon Bedrock, **Amazon Bedrock AgentCore** — highest priority). Goal: **one strong, compact project** that can be demoed and explained in depth, built entirely on **free tiers**.

## Core idea
1. The user gives the **JD** and the **resume**.
2. The first agent **extracts** information from both.
3. Based on where the **biggest gap** is, it asks **~10 questions across all rounds, including HR**.
4. It **evaluates** the candidate's performance **according to the JD**.
5. It generates a **report that can be sent by mail**.
> Keep it **as compact as possible**; avoid anything very difficult to build.

## Problem statement
Candidates can't practise realistically: questions online are generic, they don't know which resume–JD gaps interviewers will probe, they get no structured feedback, human mock interviews are costly, and plain chatbots ask random questions with inconsistent scoring.

## Solution
An AI interview panel that reads the JD and resume, builds a gap analysis, runs a gap-targeted 10-question interview (4 Technical, 3 Project, 3 HR + 1 follow-up), scores each answer with a fixed rubric, and delivers a report as a PDF and Gmail draft.

## Final decisions
| Topic | Decision |
|---|---|
| Frameworks | LangGraph (orchestrator) + CrewAI (extraction) + own MCP server |
| RAG | Yes — FastEmbed + Chroma over resume, JD, rubric; used for evidence |
| Inputs | Resume PDF or text; JD pasted text; English only |
| Gap view | Shown before interview; user clicks Start |
| Rounds | 4 Technical · 3 Project · 3 HR; max 1 follow-up |
| Answers | Text only; no time limit; Skip allowed (0) |
| Difficulty | From the JD's level (the job's bar), not the candidate's years — decision 21 Nov 2026 |
| Scoring | 0–10 per answer via rubric; overall + recommendation in Python |
| Report | PDF + email summary; sample better answers for scores < 6 |
| Email | Gmail **draft only**, candidate as recipient; local mode only |
| Users | Candidates; no history in v1 |
| Deploy | Local first → Streamlit Community Cloud at the end |
| Main LLM | `gemini-3.5-flash-lite` (15 RPM, 250K TPM, 500 RPD) |
| Backups | Groq `openai/gpt-oss-120b` → OpenRouter `:free` |

## Ideas considered and rejected (and why)
| Idea | Why not |
|---|---|
| Hospital assistant on Bedrock | AWS account has an account-level Bedrock hold (support case); kept for v2/AgentCore |
| Four separate projects (LangGraph/CrewAI/AutoGen/Bedrock) | Too much time; one strong project preferred |
| HR policy assistant (RAG + Gmail) | Very common on GitHub; low wow-factor |
| YouTube → blog, docs-based curriculum | No strong need; messier data sources |
| AI QA browser-testing team | Too complex for timeline |
| AutoGen for the panel | AutoGen is in **maintenance mode** (successor: Microsoft Agent Framework 1.0) — deferred to v2 |
| Hugging Face Spaces hosting | Gradio/Docker Spaces became paid in 2026 |
| sentence-transformers | Pulls PyTorch; too heavy for 1 GB hosting |
| Reranker, SQLite DB, voice | Out of compact scope |

## Working conventions
- Research all alternatives proactively; think creatively.
- Challenge ideas with pros/cons instead of simply agreeing.
- Keep things compact and free; explain in simple English.
- Never ask for keys in chat.

## v2 ideas (not now)
Company leadership-principle (bar-raiser style) round · Microsoft Agent Framework panel · weakness history · voice · deploy on Bedrock AgentCore.
