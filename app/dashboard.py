"""
app/dashboard.py
================
Mounts two endpoints:

  GET /dashboard   — serves the self-contained HTML demo dashboard.
  GET /audit/all   — returns all audit_log rows (newest-first) as JSON;
                     used by the dashboard's JS to populate the audit table.

Both live in a FastAPI APIRouter so main.py can include them cleanly.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

from app import db

router = APIRouter()

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "dashboard.html"


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def serve_dashboard():
    """Serve the single-page demo dashboard HTML."""
    return HTMLResponse(content=_TEMPLATE_PATH.read_text(encoding="utf-8"))


@router.get("/audit/all")
def get_all_audit():
    """
    Return every row in audit_log, ordered newest-first.
    Used by the dashboard JS; also useful for quick ad-hoc inspection.
    """
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return JSONResponse(content=[dict(r) for r in rows])
