"""Offline tests for Phase 7 (no Gmail, no LLM, no MCP subprocess).

Run:  python -m pytest -q tests/test_mcp.py
"""

from types import SimpleNamespace

import pytest
from pypdf import PdfReader

import config
import mcp_server.server as server
from graph import nodes


def test_filename_cannot_escape_outputs():
    assert server._safe_filename("../../etc/passwd") == "passwd.pdf"
    assert server._safe_filename("report — Ananya (test).md") == "report-Ananya-test.pdf"
    assert server._safe_filename("...") == "report.pdf"


def test_pdf_keeps_unicode_and_tables(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    md = "# Report — test\n\nScore 4.2 · No Hire\n\n| Skill | Status |\n|---|---|\n| React | matched |\n"
    out = server.save_report_pdf(md, "unicode-test")
    text = PdfReader(out["path"]).pages[0].extract_text()
    assert "—" in text and "React" in text and "| Skill |" not in text


def test_gmail_tool_refuses_files_outside_outputs(tmp_path):
    with pytest.raises(ValueError, match="outputs"):
        server.create_gmail_draft("a@b.co", "s", "b", str(config.PROJECT_ROOT / ".env"))


def test_no_send_tool_exists():
    names = {t.name for t in server.mcp._tool_manager.list_tools()}
    assert names == {"save_report_pdf", "create_gmail_draft"}
    source = (config.PROJECT_ROOT / "mcp_server" / "server.py").read_text(encoding="utf-8")
    assert ".send(" not in source  # no drafts.send / messages.send anywhere


# ---------------------------------------------------------------- deliver node branches


class FakeToolbox:
    def __init__(self):
        self.calls = []

    def call_tool(self, name, **args):
        self.calls.append(name)
        return {"path": "outputs/r.pdf"} if name == "save_report_pdf" else {"draft_id": "d123"}


class FakeStore:
    closed = False

    def close(self):
        FakeStore.closed = True


def _setup(monkeypatch, mode: str = "local"):
    fake = FakeToolbox()
    monkeypatch.setattr("mcp_client.get_toolbox", lambda: fake)
    monkeypatch.setattr(config, "APP_MODE", mode)
    FakeStore.closed = False
    runtime = SimpleNamespace(context=SimpleNamespace(store=FakeStore(), router=None))
    return fake, runtime


def _state(action: str, delivery: dict | None = None) -> dict:
    return {
        "send_action": action,
        "candidate_email": "c@example.com",
        "report_markdown": "# r",
        "report_path": "outputs/report_x.md",
        "report_summary": {},
        "overall": {"score": 5.0, "recommendation": "Lean No"},
        "match_percent": 50.0,
        "resume_profile": {"name": "TEST"},
        "jd_profile": {"role_title": "Dev"},
        "delivery": delivery or {"pdf_path": "outputs/r.pdf", "draft_id": "", "errors": []},
    }


def test_build_report_creates_pdf_straight_away(monkeypatch, tmp_path):
    import report

    fake, runtime = _setup(monkeypatch)
    runtime.context.router = SimpleNamespace(call_count=1, summary=lambda: {"calls": 1, "target": 18})
    monkeypatch.setattr(report, "write_summary", lambda state, router: None)
    monkeypatch.setattr(report, "render_markdown", lambda state, summary, rs: "# report")
    monkeypatch.setattr(report, "save_report", lambda md, name: tmp_path / "report_x.md")
    out = nodes.build_report({"resume_profile": {"name": "T"}}, runtime)
    assert fake.calls == ["save_report_pdf"] and out["delivery"]["pdf_path"] == "outputs/r.pdf"


def test_gmail_creates_one_draft_and_returns_to_send_gate(monkeypatch):
    fake, runtime = _setup(monkeypatch, "local")
    out = nodes.deliver(_state("gmail"), runtime)
    assert fake.calls == ["create_gmail_draft"] and out["delivery"]["draft_id"] == "d123"
    assert "not sent" in out["delivery"]["gmail"] and not FakeStore.closed  # session still open
    assert nodes.route_after_deliver({**_state("gmail"), **out}) == "send_gate"
    again = nodes.deliver({**_state("gmail"), **out}, runtime)  # clicked twice
    assert fake.calls == ["create_gmail_draft"] and again["delivery"]["draft_id"] == "d123"


def test_finish_cleans_up_and_ends(monkeypatch):
    fake, runtime = _setup(monkeypatch, "local")
    out = nodes.deliver(_state("finish"), runtime)
    assert fake.calls == [] and out["finished"] and FakeStore.closed
    assert out["delivery"]["pdf_path"] == "outputs/r.pdf"  # download stays available
    assert nodes.route_after_deliver({**_state("finish"), **out}) == "end"


def test_cloud_mode_never_touches_gmail(monkeypatch):
    fake, runtime = _setup(monkeypatch, "cloud")
    out = nodes.deliver(_state("gmail"), runtime)
    assert fake.calls == [] and "cloud" in out["delivery"]["gmail"]


def test_pdf_tables_get_widths_left_align_and_footer(tmp_path, monkeypatch):
    html = server._style_tables(
        "<table><thead><tr><th>#</th><th>Question</th><th>Other</th></tr></thead>"
        "<tbody><tr><td>T1</td><td>long text</td><td>x</td></tr></tbody></table>"
    )
    assert '<th width="6%">#</th>' in html and '<th width="50%">Question</th>' in html
    assert "<th>Other</th>" in html and '<td align="left">long text</td>' in html
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    out = server.save_report_pdf("# Title\n\n" + "line\n\n" * 120, "footer-test")
    pages = PdfReader(out["path"]).pages
    assert len(pages) >= 2
    assert all(f"Page {i} of {len(pages)}" in p.extract_text() for i, p in enumerate(pages, 1))
