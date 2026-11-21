"""Interview Arena MCP server (FastMCP, stdio) — exactly 2 tools, and NO send tool.

  save_report_pdf(report_markdown, filename)        -> {"path": ...}
  create_gmail_draft(to, subject, body, attachment_path) -> {"draft_id": ...}

Run by the client as a subprocess:  python mcp_server/server.py
IMPORTANT: with the stdio transport, STDOUT is the protocol channel. Nothing in this
process may print to stdout (it would corrupt the messages); logs go to stderr.

Gmail: scope gmail.compose ONLY (the narrowest scope that can create drafts). Google's
label for it is "Manage drafts and send emails" — so the guarantee that nothing is sent
comes from this code: it never calls drafts.send or messages.send.
The draft is created in the mailbox of the Google account that authorised the app
(local / owner mode, Review C2).
"""

from __future__ import annotations

import base64
import contextlib
import logging
import mimetypes
import re
import sys
from email.message import EmailMessage
from pathlib import Path

import markdown as md
from fpdf import FPDF
from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "outputs"
CREDENTIALS = ROOT / "credentials.json"
TOKEN = ROOT / "token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024

logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
log = logging.getLogger("interview_arena.mcp_server")

mcp = FastMCP("interview-arena", log_level="WARNING")

# Unicode TrueType fonts (Windows, then common Linux paths). Without one, fpdf2's core
# fonts only support Latin-1, so we transliterate instead of crashing.
FONT_CANDIDATES = [
    {
        "": "C:/Windows/Fonts/arial.ttf",
        "B": "C:/Windows/Fonts/arialbd.ttf",
        "I": "C:/Windows/Fonts/ariali.ttf",
        "BI": "C:/Windows/Fonts/arialbi.ttf",
    },
    {
        "": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "B": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "I": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
        "BI": "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
    },
]
# Column widths (% of page width) by table header label. fpdf2 reads width="NN%" from the
# first row's <th> cells. Question table: # 6, Round 12, Question 50, Score 8, Feedback 24.
# Skill-gap table: Evidence gets the most room.
COLUMN_WIDTHS = {
    "#": 6,
    "Round": 12,
    "Question": 50,
    "Score": 8,
    "Feedback": 24,
    "Skill": 24,
    "Importance": 13,
    "Status": 11,
    "Evidence": 52,
}

LATIN1_SWAPS = {
    "—": "-",
    "–": "-",
    "·": "-",
    "’": "'",
    "‘": "'",
    "“": '"',
    "”": '"',
    "…": "...",
    "•": "-",
    "→": "->",
    "≥": ">=",
    "≤": "<=",
}


def _safe_filename(filename: str) -> str:
    """Only a plain file name inside outputs/ — no folders, no '..' (path traversal)."""
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", Path(filename).stem).strip("-.") or "report"
    return f"{stem[:80]}.pdf"


class ReportPDF(FPDF):
    """FPDF with a footer on every page: "Interview Arena · Page X of Y"."""

    footer_family = "helvetica"
    separator = "-"

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font(self.footer_family, size=8)
        self.set_text_color(120)
        # {nb} is replaced by the total page count when the PDF is written.
        self.cell(0, 8, f"Interview Arena {self.separator} Page {self.page_no()} of {{nb}}", align="C")
        self.set_text_color(0)


GREEN, AMBER, RED = "#15803D", "#B45309", "#B91C1C"
STATUS_COLOURS = {"matched": GREEN, "partial": AMBER, "missing": RED}
REC_COLOURS = {"Strong Hire": GREEN, "Hire": GREEN, "Lean No": AMBER, "No Hire": RED}


def _score_colour(score: float) -> str:
    return GREEN if score >= 7 else AMBER if score >= 5 else RED


def _colour_meaning(html: str) -> str:
    """Same colour meaning as the app, always WITH the word (never colour alone):
    status cells, the recommendation, and the Score column (>=7 green, 5-6.9 amber, <5 red)."""

    def status(m: re.Match) -> str:
        word = m.group(1)
        return f'<td align="left"><font color="{STATUS_COLOURS[word]}"><b>{word}</b></font></td>'

    html = re.sub(r'<td align="left">(matched|partial|missing)</td>', status, html)

    def score(m: re.Match) -> str:
        value = float(m.group(1))
        return f'<td align="left"><font color="{_score_colour(value)}"><b>{m.group(1)}</b></font></td>'

    html = re.sub(r'<td align="left">(\d{1,2}(?:\.\d)?)</td>', score, html)

    def rec(m: re.Match) -> str:
        return f'{m.group(1)}<font color="{REC_COLOURS[m.group(2)]}"><b>{m.group(2)}</b></font>'

    return re.sub(r"(<strong>Recommendation:</strong>\s*)(Strong Hire|Hire|Lean No|No Hire)", rec, html)


def _style_tables(html: str) -> str:
    """Set column widths from the header labels and LEFT-align every body cell
    (fpdf2 would otherwise justify long text like the evidence quotes)."""

    def th(match: re.Match) -> str:
        label = match.group(1).strip()
        width = COLUMN_WIDTHS.get(label)
        return f'<th width="{width}%">{label}</th>' if width else match.group(0)

    html = re.sub(r"<th>(.*?)</th>", th, html)
    return html.replace("<td>", '<td align="left">')


MIN_SPACE_UNDER_HEADING_MM = 40  # a heading with less room below it moves to the next page
MIN_SPACE_FOR_MAJOR_MM = 70  # heading + table header + first ~3 rows (spec §5)
MAJOR_SECTIONS = ("Skill gaps", "Question by question", "Sample better answers")


def _write_sections(pdf: FPDF, html: str) -> None:
    """Write the report section by section so headings are never orphaned at a page bottom.

    - Any <h2>/<h3> with less than 40 mm left below it starts a new page.
    - A major section (Skill gaps, Question by question, Sample answers) needs room for its
      heading and first rows (70 mm); otherwise it starts on a new page.
    """
    for section in re.split(r"(?=<h[23]>)", html):
        if not section.strip():
            continue
        heading = re.match(r"<h([23])>(.*?)</h[23]>", section)
        if heading and pdf.y > pdf.t_margin + 1:  # not already at the top of a page
            needed = (
                MIN_SPACE_FOR_MAJOR_MM
                if heading.group(2).startswith(MAJOR_SECTIONS)
                else MIN_SPACE_UNDER_HEADING_MM
            )
            if pdf.page_break_trigger - pdf.y < needed:
                pdf.add_page()
        pdf.write_html(section)


def _setup_font(pdf: FPDF) -> bool:
    """Register a Unicode font if one exists. Returns True if Unicode is available."""
    for files in FONT_CANDIDATES:
        if Path(files[""]).exists():
            for style, path in files.items():
                pdf.add_font("Body", style, path if Path(path).exists() else files[""])
            pdf.set_font("Body", size=10)
            pdf.footer_family, pdf.separator = "Body", "·"
            return True
    pdf.set_font("helvetica", size=10)
    return False


@mcp.tool()
def save_report_pdf(report_markdown: str, filename: str) -> dict:
    """Convert the interview report (markdown) to a PDF saved in outputs/. Returns {"path"}."""
    html = _colour_meaning(_style_tables(md.markdown(report_markdown, extensions=["tables"])))
    pdf = ReportPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)  # leave room for the footer
    unicode_font = _setup_font(pdf)  # fonts first: the footer of page 1 needs them
    pdf.add_page()
    if not unicode_font:
        for bad, good in LATIN1_SWAPS.items():
            html = html.replace(bad, good)
        html = html.encode("latin-1", "replace").decode("latin-1")  # anything else -> "?"
    _write_sections(pdf, html)
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / _safe_filename(filename)
    pdf.output(str(path))
    return {"path": str(path)}


def _gmail_credentials():
    """Load token.json; refresh it; or run the browser OAuth flow once (saves token.json)."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES) if TOKEN.exists() else None
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        if not CREDENTIALS.exists():
            raise FileNotFoundError("credentials.json not found — see docs/archive/08_prerequisites.md §3.")
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS), SCOPES)
        # The flow prints a "visit this URL" line to stdout — that would break the MCP
        # protocol, so send it to stderr instead.
        with contextlib.redirect_stdout(sys.stderr):
            creds = flow.run_local_server(port=0, timeout_seconds=300)
    TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return creds


@mcp.tool()
def create_gmail_draft(to: str, subject: str, body: str, attachment_path: str) -> dict:
    """Create a Gmail DRAFT (never sends) with the file attached. Returns {"draft_id"}."""
    from googleapiclient.discovery import build

    attachment = Path(attachment_path).resolve()
    if OUTPUT_DIR.resolve() not in attachment.parents:
        raise ValueError("Only files from the outputs/ folder can be attached.")
    if not attachment.is_file() or attachment.stat().st_size > MAX_ATTACHMENT_BYTES:
        raise ValueError("Attachment missing or larger than 10 MB.")

    msg = EmailMessage()
    msg["To"], msg["Subject"] = to, subject
    msg.set_content(body)
    ctype, _ = mimetypes.guess_type(attachment.name)
    maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
    msg.add_attachment(attachment.read_bytes(), maintype=maintype, subtype=subtype, filename=attachment.name)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    service = build("gmail", "v1", credentials=_gmail_credentials(), cache_discovery=False)
    draft = service.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
    return {"draft_id": draft["id"]}


if __name__ == "__main__":
    mcp.run(transport="stdio")
