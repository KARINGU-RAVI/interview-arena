"""Manual check for Phase 7: call both MCP tools through the real MCP client (stdio).

    python scripts/try_mcp.py --pdf-only
    python scripts/try_mcp.py --to someone@example.com      # PDF + Gmail draft (local mode)

Uses the newest outputs/report_*.md (or --report PATH). The draft is created in the Gmail
account that authorised the app; this script then reads the draft back and checks its
labels (DRAFT, not SENT) to confirm nothing was sent. No LLM calls.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mcp_client import MCPToolError, get_toolbox  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", help="markdown report (default: newest outputs/report_*.md)")
    ap.add_argument("--to", help="draft recipient")
    ap.add_argument("--pdf-only", action="store_true")
    args = ap.parse_args()

    reports = sorted((ROOT / "outputs").glob("report_*.md"))
    report = Path(args.report) if args.report else (reports[-1] if reports else None)
    if report is None:
        sys.exit("No report found — run cli.py first or pass --report.")

    toolbox = get_toolbox()
    print("MCP tools:", sorted(toolbox.tools))

    pdf = toolbox.call_tool(
        "save_report_pdf", report_markdown=report.read_text(encoding="utf-8"), filename=report.stem
    )
    print("PDF:", pdf["path"])

    # Safety check of the server's own guard: attachments must come from outputs/.
    try:
        toolbox.call_tool(
            "create_gmail_draft",
            to="x@example.com",
            subject="x",
            body="x",
            attachment_path=str(ROOT / ".env"),
        )
        print("GUARD FAILED: a file outside outputs/ was accepted")
    except MCPToolError as exc:
        print("Guard OK (file outside outputs/ refused):", exc)

    if args.pdf_only or not args.to:
        sys.exit(0)

    draft = toolbox.call_tool(
        "create_gmail_draft",
        to=args.to,
        subject="Interview Arena — test report (draft only)",
        body="Test draft from scripts/try_mcp.py. The report is attached.",
        attachment_path=pdf["path"],
    )
    print("Gmail draft id:", draft["draft_id"])

    # Read the draft back (gmail.compose allows drafts.get) to prove it was NOT sent.
    from googleapiclient.discovery import build

    from mcp_server.server import _gmail_credentials

    service = build("gmail", "v1", credentials=_gmail_credentials(), cache_discovery=False)
    got = service.users().drafts().get(userId="me", id=draft["draft_id"], format="full").execute()
    labels = got["message"].get("labelIds", [])
    parts = got["message"]["payload"].get("parts", [])
    attached = [p.get("filename") for p in parts if p.get("filename")]
    print(f"Labels: {labels} | attachment(s): {attached}")
    print(
        "NOT SENT (still a draft)"
        if "DRAFT" in labels and "SENT" not in labels
        else "WARNING: unexpected labels"
    )
