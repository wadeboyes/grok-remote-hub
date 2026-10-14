"""Mobile-first Hub UI: touch targets, working-session home, composer/modals."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def test_html_home_working_sessions() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="home-sessions"' in html
    assert 'id="home-sessions-empty"' in html
    assert 'id="home-sessions-heading"' in html
    assert "Working" in html
    # Existing empty-state contract (sidebar copy + New session)
    assert "No session selected" in html
    assert "Pick a chat from the sidebar" in html
    assert 'id="btn-empty-new"' in html
    assert 'id="btn-empty-sessions"' in html
    assert "btn-block" in html
    # Cache-bust so phones pick up the redesign
    assert "app.css?v=" in html
    assert "app.js?v=" in html
    assert "20261014e" in html
    assert "html.embed-chat #empty-main" in html
    assert "inter-latin-400.woff2" in html
    # Pretty home: cards + top New; no render-blocking Google Fonts
    assert 'id="btn-topbar-new"' in html
    assert "empty-hero" in html
    assert "sheet-handle" in html
    assert "is-home" in html
    assert "system-ui" in html
    assert "fonts.googleapis.com/css" not in html
    assert "fonts.gstatic.com" not in html
    assert "<style>" in html
    assert ".hidden, [hidden]" in html or ".hidden,[hidden]" in html
    assert (STATIC / "fonts" / "inter-latin-400.woff2").is_file()
    assert (STATIC / "fonts" / "inter-latin-600.woff2").is_file()


def test_html_modal_primary_actions_intact() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="btn-ask-user-cancel"' in html
    assert 'id="btn-ask-user-submit"' in html
    assert 'id="btn-plan-approve"' in html
    assert 'id="btn-plan-request-changes"' in html
    assert 'id="btn-entry-start-new"' in html
    assert "Start new session" in html
    assert "Use this folder" in html
    assert 'id="composer-input"' in html
    assert 'id="btn-send"' in html
    assert 'id="btn-stop"' in html
    assert 'id="btn-attach"' in html


def test_js_home_sessions_render() -> None:
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "function isWorkingSession" in js
    assert "function compareSessionsNewest" in js
    assert "function homeWorkingSessions" in js
    assert "function renderHomeSessions" in js
    assert "function setAppHomeMode" in js
    assert "renderHomeSessions()" in js
    assert "home-session-row" in js
    assert "is-home" in js
    assert 'textContent = "Resume"' in js or 'cta.textContent' in js
    # Rail sort still used for home + list
    render_idx = js.find("function renderSessions")
    assert render_idx >= 0
    render_chunk = js[render_idx : render_idx + 9000]
    assert "compareSessionsNewest" in render_chunk
    assert "renderHomeSessions()" in render_chunk
    # showEmptyMain rebuilds the home list host
    empty_idx = js.find("function showEmptyMain")
    empty_chunk = js[empty_idx : empty_idx + 2200]
    assert "home-sessions" in empty_chunk
    assert "btn-block" in empty_chunk
    # Exposed for tests
    hooks_idx = js.find("window.__hubTestHooks")
    hooks = js[hooks_idx : hooks_idx + 2800]
    assert "renderHomeSessions" in hooks
    assert "homeWorkingSessions" in hooks
    assert "setAppHomeMode" in hooks


def test_css_mobile_touch_targets() -> None:
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert "--touch: 44px" in css
    assert "--touch-lg: 48px" in css
    assert ".home-session-row" in css
    assert ".home-session-cta" in css
    assert ".btn-block" in css
    assert "@media (max-width: 899px)" in css
    assert "--font-ui: Inter, system-ui" in css
    assert "inter-latin-400.woff2" in css
    assert "#app.is-home .composer-shell" in css
    assert ".sheet-handle" in css
    assert "appearance: none" in css

    mobile = css[css.find("/* Mobile-first touch") :]
    assert mobile, "mobile-first touch section missing"
    assert "min-height: var(--touch)" in mobile
    assert "min-height: var(--touch-lg)" in mobile
    assert "#btn-send" in mobile
    assert "#btn-stop" in mobile
    assert "#btn-attach" in mobile
    assert "#btn-plan-approve" in mobile
    assert "#btn-ask-user-submit" in mobile
    assert "#btn-entry-start-new" in mobile
    assert "flex-direction: column" in mobile
    assert ".composer-actions" in mobile
    assert ".modal-actions" in mobile
    assert "width: 100%" in mobile
    # Composer stacks on phone (override the old row-locked 480px rule)
    narrow = css[css.find("/* Narrow phones") : css.find("/* Narrow phones") + 900]
    assert "flex-direction: column" in narrow
    assert "flex-direction: row" not in narrow


def test_css_desktop_rail_width_unchanged() -> None:
    """Desktop rail stays compact; mobile drawer still uses the 899px breakpoint."""
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert "--rail-w: 300px" in css
    assert "@media (max-width: 899px)" in css
    # First (desktop) .btn rule must not force 44px globally
    btn_idx = css.find("\n.btn {")
    assert btn_idx >= 0
    btn_chunk = css[btn_idx : btn_idx + 400]
    assert "min-height: var(--touch)" not in btn_chunk


def test_static_only_no_security_or_acp_churn() -> None:
    """This redesign is CSS/JS/HTML; do not silently edit token or auto-approve."""
    server = (ROOT / "hub" / "server.py").read_text(encoding="utf-8")
    assert "hub_token" in server
    # Auto-approve stay-out: agent serve still always-approve
    supervisor = (ROOT / "hub" / "agent_supervisor.py").read_text(encoding="utf-8")
    assert "always-approve" in supervisor or "always_approve" in supervisor
