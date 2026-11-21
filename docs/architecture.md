# Architecture

Interview Arena reads a resume and a job description, finds the skill gaps, runs a 10-question
interview focused on them, scores every answer with a fixed rubric, and produces a report + PDF
(optionally a Gmail draft). Diagrams are Mermaid (they render on GitHub).

## 1. Data flow

```mermaid
flowchart TD
    A["Input<br/>resume (PDF or text), JD, email"] -->|pypdf, input checks| B["Extraction<br/>JD + resume → JSON profiles"]
    B -->|CrewAI: 2 agents, Pydantic output| C["Gap analysis<br/>matched / partial / missing + match %"]
    C -->|RAG evidence: FastEmbed + Chroma| G1{{"⏸ Start gate: user reviews the gaps"}}
    G1 --> D["Question plan<br/>4 Technical · 3 Project · 3 HR"]
    D --> E["Interview loop ×10<br/>⏸ answer → score per rubric criterion"]
    E --> F["Follow-up ×1 on the weakest attempted answer<br/>⏸ answer → score"]
    F --> H["Final evaluation<br/>overall score + recommendation (Python)"]
    H --> I["Report<br/>summary, top 3 actions, sample answers + PDF"]
    I --> G2{{"⏸ Report: download PDF, send to Gmail, or finish"}}
    G2 -->|own MCP server| J["Gmail draft (local mode only)"]
    LLM[["LLM router: Gemini Flash-Lite → Groq gpt-oss-120b → Groq qwen → OpenRouter (free tiers)"]] -.-> C & D & E & F & I
```

## 2. Layers

```mermaid
flowchart TB
    subgraph L1[Streamlit UI · app.py, ui/]
      UI1[Upload] --- UI2[Skill gaps] --- UI3[Interview] --- UI4[Report]
    end
    subgraph L2[Orchestration · graph/]
      W[Background step worker] --- S[InterviewState] --- N[12 nodes] --- CP[InMemorySaver] --- INT[interrupt / Command]
    end
    subgraph L3[Agents and logic]
      CR[CrewAI: JD + Resume Analysts] --- GA[Gap Analyst] --- QP[Question Planner] --- EV[Evaluator] --- RW[Report Writer]
    end
    subgraph L4[Knowledge · rag/]
      FE[FastEmbed bge-small] --- CH[Chroma in-memory] --- JR[resume + JD + rubric chunks]
    end
    subgraph L5[Tools · mcp_server/, mcp_client.py]
      MC[MCP client] --- MS[FastMCP server, stdio] --- T1[save_report_pdf] --- T2[create_gmail_draft]
    end
    subgraph L6[Free external services]
      GM[Gemini] --- GQ[Groq] --- OR[OpenRouter] --- GML[Gmail API]
    end
    L1 --> L2 --> L3 --> L4
    L3 --> L5 --> L6
    L3 --> L6
```

## 3. The LangGraph graph (12 nodes)

```mermaid
stateDiagram-v2
    [*] --> extract
    extract --> gap_analysis
    gap_analysis --> start_gate
    start_gate --> plan_questions: ⏸ user starts
    plan_questions --> ask_question
    ask_question --> evaluate_answer: ⏸ answer / skip
    evaluate_answer --> ask_question: route_next — more questions
    evaluate_answer --> follow_up: route_next — done, an attempted answer exists
    evaluate_answer --> final_evaluation: route_next — done, nothing attempted
    follow_up --> evaluate_follow_up: question written (no pause)
    evaluate_follow_up --> final_evaluation: ⏸ answer first, then score
    final_evaluation --> build_report
    build_report --> send_gate: report + PDF ready
    send_gate --> deliver: ⏸ Gmail or Finish
    deliver --> send_gate: Gmail draft created
    deliver --> [*]: Finish
```

Rules that shape the graph:

- **No LLM call before `interrupt()` in a node.** LangGraph re-runs a node from its start when it
  resumes, so a call placed before the pause would run twice. `follow_up` only writes the question;
  `evaluate_follow_up` pauses first and scores after.
- `route_next` is a conditional **edge** (a function), not a node.
- The state holds plain data only, so `InMemorySaver` can checkpoint it at every pause. Live objects
  (LLM router, RAG store) are passed as LangGraph runtime **context** on every invoke.

## 4. One answer, end to end

```mermaid
sequenceDiagram
    actor U as User
    participant UI as Streamlit page
    participant W as Step worker (thread)
    participant G as LangGraph
    participant R as LLM router
    U->>UI: Submit answer
    UI->>W: submit(Command(resume=answer)) — refused if a step is running
    UI-->>U: spinner (page waits; refresh/rerun can't cancel the step)
    W->>G: stream(Command, thread_id, context)
    G->>R: evaluate_answer → per-criterion scores (1 call)
    R-->>G: validated JSON (Pydantic)
    G-->>W: paused at the next question (checkpointed)
    UI->>G: get_state → pending interrupt
    UI-->>U: result card + next question
```

**Why a background worker:** Streamlit stops a running script on refresh or rerun by raising an
exception at the next `st.*` call. Running the graph inside the script let that cut a step between two
nodes. In a worker thread the step always finishes at a clean, checkpointed pause; the page only waits
and redraws. A session id in the URL (`?sid=`) lets a refreshed page find its paused interview.

## 5. Data model

```mermaid
classDiagram
    class InterviewState {
      resume_text, jd_text, candidate_email
      jd_profile, resume_profile
      gaps, match_percent
      questions, current_index, answers, evaluations
      follow_up, overall
      report_summary, report_markdown, report_path
      delivery, send_action, finished, llm_calls
    }
    class JDProfile { role_title; level; required_skills; nice_to_have; responsibilities }
    class ResumeProfile { name; skills; tools; years_experience; projects; education; certifications }
    class GapItem { skill; importance; status; evidence }
    class Question { id; round; text; target_skill }
    class QuestionPlan { questions: exactly 4 / 3 / 3 }
    class Evaluation { question_id; round; score; criteria; strengths; weaknesses; feedback; skipped; error }
    class ReportSummary { summary; top_actions (3); sample_answers }
```

## 6. LLM call budget per session

| Step | Calls |
|---|---|
| extract (CrewAI, 2 agents) | 2 |
| gap_analysis | 1 |
| plan_questions (+1 retry if a rule is broken) | 1 |
| evaluate_answer ×10 (skips and "don't know" cost 0) | ≤ 10 |
| follow_up + evaluate_follow_up | 2 |
| final_evaluation (Python only) | 0 |
| build_report (one batched Report Writer call) | 1 |
| **Total** | **≤ 17** (target 18, hard cap 24) |

Gemini is limited to 12 requests/minute by one process-wide limiter (shared by CrewAI, the router
and tests). Every call goes through `llm/router.py` except CrewAI's two, which are added to the same
per-session count.
