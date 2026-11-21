# 10 — UI/UX Design Spec (Interview Arena)

Written from a senior product-designer view. Goal: the app should feel like a calm, trustworthy **practice coach**, not a developer demo. Every screen answers 3 questions for the user: *Where am I? What do I do now? What happens next?*

> Implementation note for Claude Code: Streamlit 1.67. Verify every API / config key in the installed version before using it (§9). Prefer built-in components + `.streamlit/config.toml` theme. Small, scoped CSS via `st.markdown(..., unsafe_allow_html=True)` is allowed **only** for spacing/typography that the theme can't do — keep it in one `ui/styles.py`. No custom JS.

---

## 0. Bugs first (P0)

| # | Bug | Required behaviour |
|---|---|---|
| B1 | **Empty / whitespace-only answer is accepted** on Submit | Trim the answer (`.strip()`, collapse inner whitespace). If empty after trimming → do NOT resume the graph; show an inline error under the text area: *"Write an answer, or choose Skip question."* Keep focus in the text area. |
| B2 | Answers keep leading/trailing spaces | Always store and evaluate the trimmed answer. Same for pasted resume text, JD text and email. |
| B3 | Inputs on screen 1 accept whitespace-only JD/resume | Validate after trimming; show inline errors per field (not one generic error). |

Add AppTest cases for B1–B3.

---

## 1. Design foundations (tokens)

### Colour (light theme)
| Token | Value | Use |
|---|---|---|
| Primary | `#2563EB` | Primary buttons, progress, links |
| Primary-soft | `#EFF6FF` | Selected/info backgrounds |
| Text | `#0F172A` | Body/headings |
| Text-muted | `#64748B` | Helper text, labels |
| Border | `#E2E8F0` | Cards, inputs |
| Surface | `#FFFFFF` | Main background |
| Surface-alt | `#F8FAFC` | Sidebar, cards on grey |
| Success | `#16A34A` / soft `#F0FDF4` | Matched, Hire, good scores (≥7) |
| Warning | `#D97706` / soft `#FFFBEB` | Partial, Lean No, average scores (5–6.9) |
| Danger | `#DC2626` / soft `#FEF2F2` | Missing, No Hire, low scores (<5), errors |

Never use colour alone: always pair with an icon **and** a word (✅ Matched, 🟡 Partial, ❌ Missing). Text contrast must meet WCAG AA (4.5:1).

### Typography (one family, 4 sizes)
| Role | Size / weight |
|---|---|
| Page title | 28px / 700 |
| Section title | 20px / 600 |
| Question text | 20px / 600, line-height 1.4 |
| Body | 16px / 400, line-height 1.6 |
| Helper / caption | 13–14px / 400, muted |

No heading should wrap to more than 2 lines on desktop — shorten the text instead of shrinking it.

### Spacing & shape
- 8-pt spacing scale: 8 / 16 / 24 / 32 / 48.
- Content max width ~860px, centred. Cards: 1px border, radius 12px, padding 24px.
- One primary button per screen. Buttons: auto width (not full width) except on mobile.

### Theme (`.streamlit/config.toml`)
Set primaryColor, backgroundColor, secondaryBackgroundColor, textColor, font, base radius, and `client.toolbarMode = "minimal"` (verify key names for 1.67).

---

## 2. Global layout

1. **Top stepper on every screen:** `① Upload → ② Skill gaps → ③ Interview → ④ Report`. Current step highlighted, done steps ticked. (Build with columns + small text; no JS.)
2. **Sidebar:** logo + name; "Start a new interview" (secondary); collapsed "Session details" (AI calls x/18, model, mode, session id). Add a tiny "How it works" expander (3 bullets) for first-time visitors.
3. **Feedback messages:** use `st.toast` for transient success ("Answer saved"), inline callouts for things the user must read. Never stack more than one callout.
4. **Loading:** every LLM step uses `st.status` with human steps and expected time, e.g. *"Reading your resume… → Understanding the job… → Finding skill gaps… (about 15 s)"*.
5. **Errors:** friendly, specific, with an action: *"Our AI providers are busy. Your progress is saved — [Try again]."* Never show stack traces.

---

## 3. Screen specs

### Screen 1 — Upload
- Title: **"Practice an interview for any job"**; subtitle (1 line): *"Upload your resume and paste a job description. We'll find your skill gaps and interview you on them."*
- **Two cards side by side** (stack on mobile): **Your resume** (PDF drop zone + "or paste text" toggle) | **Job description** (text area with live counter `1,240 / 8,000`).
- Email field: label "Email for your report (optional in cloud mode)". Validate format on submit only.
- **"Try with sample data"** tertiary button → fills a fictional resume + JD (pair 2). Critical for first-time visitors testing the public link.
- Privacy note as small muted text with a lock icon (not a big warning box): *"Use a resume without phone/address. Free AI providers may use inputs to improve their models."*
- Primary button: **"Analyse my fit →"** (disabled-looking state handled via validation message if fields empty).

### Screen 2 — Skill gaps
- Title **"Skill gap analysis"**; subtitle: role + level chip (e.g. `Full Stack Developer · Mid`).
- **3 summary cards in a row:** Match % (big) with "4 of 9 required skills" · ✅ Matched n · 🟡 Partial n · ❌ Missing n.
- **"Your biggest gaps"**: chips for missing/partial *required* skills (max 6) — this is what the interview will focus on.
- Full table in an expander "See all skills and evidence" — all rows visible, evidence wraps, sorted missing → partial → matched, required first.
- Info line (not a big box): *"10 questions · about 15 minutes · 4 Technical, 3 Project, 3 HR. You can skip any question."*
- Primary: **"Start interview →"**; secondary: "Change inputs".

### Screen 3 — Interview
- **Segmented progress** showing the 3 rounds: `Technical ■■■■ | Project ■□□ | HR □□□` + text "Question 5 of 10".
- **Round chip** coloured by round + **focus chip** (target skill or project), e.g. `PROJECT` `Focus: Task Board App`.
- Question text 20px/600 inside a card. Optional collapsed "Tips for this answer" (Technical: *concept → why → trade-off*; Project: *what you built → how → result*; HR: *STAR*).
- Text area: 6 rows, placeholder *"Explain your thinking in 3–6 sentences…"*, live **word count** with a gentle hint: *"Good answers are usually 60–150 words."*
- Actions row: left **"Skip question"** (tertiary/text), right **"Submit answer →"** (primary, auto width). Ctrl+Enter hint as caption.
- After submit: show the previous result as a **small coloured result card** at the top of the next question (score pill + one-line feedback), dismissible — not grey text. Skipped: neutral grey pill "Skipped".
- Never lose the typed text on rerun errors.

### Screen 4 — Report
- **Hero card:** big overall score `6.4 / 10` + coloured recommendation badge + match %. One-line summary under it.
- **Round scores** as 3 small cards each with a thin progress bar (Technical / Project / HR) and weight in caption.
- **Actions bar** directly under the hero (sticky feel): **"Download PDF"** (primary) · **"Send to my Gmail"** (secondary, local mode only) · "Start a new interview" (tertiary). After Gmail: success toast + "✅ Draft created" label; both buttons stay available.
- **"Your top 3 next steps"** as a checklist (checkboxes are only visual).
- **Strengths / To improve** two columns (from evaluator strengths/weaknesses).
- **Question-by-question:** one `st.expander` per question, title = `T1 · Technical · 7.2/10 ●` (coloured dot), body = your answer, feedback, stronger answer. Collapsed by default; low scores first option.
- Follow-up shown as "Second chance on T2: 3.9 → 6.5 (+2.6)".
- Do **not** repeat the report header block in the app (it's in the PDF).
- Footer caption: "Scores are calculated from a fixed rubric · AI calls 15 · model …".

---

## 4. Microcopy rules
- Supportive coach tone; never shame ("No Hire" is the system label — pair it with *"Here's how to close the gap"*).
- Buttons are verbs: Analyse, Start, Submit, Skip, Download, Send.
- Replace jargon: "LLM calls" → "AI calls"; "evaluator" → "scoring".

## 5. PDF report (match the app)
- Title "Interview Report" + role as subtitle (no 2-line titles).
- Same colour meaning as the app for status/recommendation (with words).
- No orphan headings: start "Question by question" / "Sample answers" on a new page if the first rows won't fit.
- Keep the footer "Interview Arena · Page X of Y".

## 6. Accessibility & responsive
- Colour + icon + text for every status; AA contrast; visible focus.
- Every input has a visible label; errors are next to the field.
- Check at 1440px, 1024px and 390px widths; columns stack on mobile; no horizontal scroll.

## 7. Done when
- B1–B3 fixed with tests.
- All 4 screens match this spec; screenshots at 1440px and 390px reviewed by the maintainer.
- AppTest end-to-end still passes; offline tests pass.
