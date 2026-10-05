"""The Evoque shell: one design system, rendered server-side.

`evoque_assets.CSS` is the approved stylesheet carried over verbatim from the
reference build, so appearance is settled in one place and every page here is
an arrangement of the same vocabulary -- panels, rows, spec tables, pills --
rather than its own styling. The dashboard is the full treatment; the other
pages are branches of it: same sidebar, same panel and row shapes, laid out for
whatever they are actually showing.

Nothing in this module queries the database. Pages hand it finished values.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any, Sequence

from .evoque_assets import CSS, GLOBE_JS
from .html import esc

# The nav the sidebar renders, in order. `key` matches the `active` argument
# pages pass; `count` is looked up in the counts mapping a page supplies. Profile is
# not in the list: it is the account row under it (`_me`), next to Log out.
NAV = [
    ("", "Dashboard", "home"),
    ("todos", "Ideas & To Dos", "checklist"),
    ("publications", "Publications", "file"),
    ("jobs", "Jobs", "briefcase"),
    ("competitions", "Competitions", "trophy"),
    ("queue", "Queue", "clock"),
    ("resume", "Resume", "file"),
    ("analytics", "Analytics", "chart"),
    ("runs", "Runs", "history"),
    ("answers", "Answers", "chat"),
]

ICONS = {
    "home": '<path d="M3 10.5 12 3l9 7.5M5 9.5V21h14V9.5"/>',
    "briefcase": '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
    "trophy": '<path d="M7 4h10v5a5 5 0 0 1-10 0zM5 5H3v2a3 3 0 0 0 3 3M19 5h2v2a3 3 0 0 1-3 3M9 20h6M12 14v6"/>',
    "file": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "pin": '<path d="M12 21s7-6.2 7-11a7 7 0 1 0-14 0c0 4.8 7 11 7 11Z"/><circle cx="12" cy="10" r="2.5"/>',
    "arrow": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "chev": '<path d="m6 9 6 6 6-6"/>',
    "calendar": '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
    "external": '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><path d="M15 3h6v6M10 14 21 3"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "chart": '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    "history": '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/><path d="M12 8v4l3 2"/>',
    "chat": '<path d="M21 12a8 8 0 0 1-8 8H7l-4 3v-7a8 8 0 0 1 8-8h2a8 8 0 0 1 8 4z"/>',
    "check": '<path d="m5 13 4 4L19 7"/>',
    "checklist": '<path d="M9 6h11M9 12h11M9 18h11"/><path d="m3 6 1.5 1.5L7 4.5M3 12l1.5 1.5L7 10.5M3 18l1.5 1.5L7 16.5"/>',
    "x": '<path d="M6 6l12 12M18 6 6 18"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "trash": '<path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13"/>',
}


def icon(name: str, size: int = 16, width: float = 2.0) -> str:
    body = ICONS.get(name, "")
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
        f'stroke="currentColor" stroke-width="{width}" stroke-linecap="round" '
        f'stroke-linejoin="round">{body}</svg>'
    )


# --------------------------------------------------------------------------- shell


def _brand() -> str:
    return '<a class="brand" href="/"><span class="mark"></span><b>hole</b></a>'


def _initials(name: str) -> str:
    words = name.split()
    if not words:
        return ""
    return (words[0][0] + (words[-1][0] if len(words) > 1 else "")).upper()


def _me(active: str, who: str) -> str:
    """Who is signed in, under the nav: the profile page and the way out."""
    name = who.strip()
    mark = esc(_initials(name)) or icon("user", 16)
    on = " on" if active == "profile" else ""
    return (
        '<div class="nav-me">'
        f'<a class="me-link{on}" href="/profile" title="Your profile">'
        f'<span class="avatar">{mark}</span>'
        f'<span class="me-text"><b>{esc(name) or "Profile"}</b><small>Profile</small></span></a>'
        '<form method="post" action="/logout">'
        '<button class="me-out" type="submit" title="Log out">Log out</button></form>'
        "</div>"
    )


def _nav(active: str, counts: dict[str, Any] | None = None) -> str:
    counts = counts or {}
    items = []
    for key, label, ico in NAV:
        n = counts.get(key)
        badge = f'<span class="nav-n">{_num(n)}</span>' if n else ""
        cls = "nav-item" + (" on" if key == active else "")
        items.append(
            f'<a class="{cls}" href="/{key}">{icon(ico, 15)}'
            f"<span>{esc(label)}</span>{badge}</a>"
        )
    return f'<nav class="nav">{"".join(items)}{_me(active, str(counts.get("who") or ""))}</nav>'


def _head_tools() -> str:
    return (
        '<div class="head-tools">'
        '<div class="toggle" id="themeToggle" title="Toggle theme"></div>'
        '<div class="pill"><svg class="bell" width="20" height="20" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="2"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>'
        '<span class="dot"></span></div>'
        "</div>"
    )


def page(
    *,
    title: str,
    heading: str,
    sub: str,
    active: str,
    sidebar: str,
    main: str,
    counts: dict[str, Any] | None = None,
) -> str:
    """A full document in the Evoque shell.

    `sidebar` is whatever goes under the brand and nav; `main` is everything
    under the page heading. Both are already-escaped HTML.
    """
    return (
        "<!doctype html>\n"
        '<html lang="en" data-theme="dark"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{esc(title)} · hole</title>"
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=Sora:wght@400;500;600;700;800'
        '&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">'
        f"<style>{CSS}{_EXTRA_CSS}</style></head><body>"
        '<div class="app">'
        f'<aside class="sidebar">{_brand()}{_nav(active, counts)}{sidebar}</aside>'
        '<main class="main">'
        '<div class="main-head"><div>'
        f"<h1>{esc(heading)}</h1><div class=\"sub\">{esc(sub)}</div>"
        f"</div>{_head_tools()}</div>"
        f"{main}"
        "</main></div>"
        f"<script>{SCRIPT}</script>"
        "</body></html>"
    )


def login_document(*, error: str = "") -> str:
    """The sign-in page, in the same shell as everything past it.

    Deliberately not `page()`: there is no session yet, so no sidebar, no nav,
    no counts to show. `body`'s flex-center (from CSS) does the centering; this
    just needs one card in the middle of it.
    """
    error_html = (
        f'<div style="color:var(--danger);font-size:13px;font-weight:600;'
        f'margin-top:-2px">{esc(error)}</div>'
        if error
        else ""
    )
    return (
        "<!doctype html>\n"
        '<html lang="en" data-theme="dark"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Sign in · hole</title>"
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=Sora:wght@400;500;600;700;800'
        '&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">'
        f"<style>{CSS}"
        ".brand{text-decoration:none;color:var(--text)}"
        "body{background:radial-gradient(circle at 20% 15%,rgba(232,118,58,.22),transparent 55%),"
        "radial-gradient(circle at 85% 85%,rgba(184,72,31,.24),transparent 50%),var(--panel)}"
        # Chrome/Edge paint an autofilled input with their own light
        # background no matter what `background` says; only `-webkit-box-shadow`
        # (an inset covering the field) actually overrides it, and only
        # `-webkit-text-fill-color` overrides the text color it forces to black.
        ".field input:-webkit-autofill,.field input:-webkit-autofill:hover,"
        ".field input:-webkit-autofill:focus{"
        "-webkit-box-shadow:0 0 0 1000px var(--panel-3) inset;"
        "-webkit-text-fill-color:var(--text);caret-color:var(--text)}"
        "</style></head><body>"
        '<div class="search-card" style="width:min(360px,100%);box-shadow:var(--shadow)">'
        f"{_brand()}"
        f"{error_html}"
        '<form method="post" action="/login">'
        f'{field("Username", "username")}'
        f'{field("Password", "password", kind="password")}'
        '<button class="btn-search" type="submit" style="width:100%;margin-top:4px">Sign in</button>'
        "</form></div>"
        "</body></html>"
    )


# Additions the reference build had no need for: a nav (five pages need one),
# and the layout variants the branch pages arrange their content with. Every
# value here is an existing token -- no new colors, radii or type scales.
_EXTRA_CSS = """
/* The reference sized the frame for one screenful of flights. These pages
   carry a nav and longer lists, so the frame is pinned to the viewport and the
   panels inside it scroll -- which is what `.flight-list{overflow-y:auto}` and
   `.flights{flex:1;min-height:0}` were always written to do. Without this the
   whole document scrolls instead and the globe drifts off-screen. */
.app{height:calc(100vh - 56px);min-height:620px;grid-template-rows:minmax(0,1fr)}
/* A grid item's default min-height:auto refuses to shrink below its content,
   so without this the columns stand at their full content height and `.app`
   just clips them -- the list never gets to scroll. */
.sidebar,.main{min-height:0}
/* `min-height:0` alone lets the sidebar shrink to the grid row's height, but
   shrinking still clips whatever doesn't fit -- nav plus the job-search
   filters plus the search button run past one screenful on shorter windows.
   It needs its own scrollbar, same as `.main-scroll` gets one below. */
.sidebar{overflow-y:auto}
.flights.stack{flex:none}
/* The reference's `.flights{flex:1;min-height:0}` lets the list shrink to its own
   padding when the nav and the search card already fill a short window: the heading
   then spills out of an empty pill and the rows are gone. A floor keeps the title and
   a couple of rows; past that the sidebar scrolls. */
.sidebar .flights{min-height:200px}
.sidebar::-webkit-scrollbar{width:6px}
.sidebar::-webkit-scrollbar-thumb{background:var(--line-2);border-radius:9px}
@media(max-width:1040px){.app{height:auto;grid-template-rows:none}}
.brand{text-decoration:none;color:var(--text)}
.nav{display:flex;flex-direction:column;gap:2px;background:var(--panel-2);
  border:1px solid var(--line);border-radius:var(--radius);padding:8px}
.nav-item{display:flex;align-items:center;gap:11px;padding:10px 13px;border-radius:13px;
  color:var(--muted);text-decoration:none;font-size:13.5px;font-weight:600;transition:.15s}
.nav-item:hover{background:var(--panel-3);color:var(--text)}
.nav-item.on{background:var(--accent);color:#fff}
.nav-item.on svg{color:#fff}
.nav-n{margin-left:auto;font-size:11px;font-weight:700;color:var(--muted-2)}
.nav-item.on .nav-n{color:rgba(255,255,255,.85)}
/* Who is signed in, at the foot of the nav card: the profile page and Log out. */
.nav-me{display:flex;align-items:center;gap:8px;margin-top:6px;padding-top:8px;border-top:1px solid var(--line)}
.nav-me form{margin:0}
.me-link{flex:1;min-width:0;display:flex;align-items:center;gap:11px;padding:6px 8px;border-radius:13px;
  color:var(--text);text-decoration:none;transition:.15s}
.me-link:hover{background:var(--panel-3)}
.me-link.on{background:var(--accent);color:#fff}
.nav-me .avatar{width:34px;height:34px;border-radius:11px;flex-shrink:0;display:grid;place-items:center;
  font:700 12px 'Sora','Inter',sans-serif;letter-spacing:.02em;color:var(--accent)}
.me-link.on .avatar{background:rgba(255,255,255,.22);color:#fff}
.me-text{display:flex;flex-direction:column;min-width:0;line-height:1.25}
.me-text b{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.me-text small{font-size:11px;color:var(--muted-2)}
.me-link.on small{color:rgba(255,255,255,.85)}
.me-out{height:34px;padding:0 12px;border-radius:11px;border:1px solid var(--line);background:var(--panel-3);
  color:var(--muted);font:inherit;font-size:12px;font-weight:600;cursor:pointer;transition:.15s}
.me-out:hover{color:var(--text);border-color:var(--line-2)}

.main-scroll{position:absolute;inset:96px 0 0;overflow-y:auto;padding:8px 34px 34px;z-index:4}
.main-scroll::-webkit-scrollbar{width:6px}
.main-scroll::-webkit-scrollbar-thumb{background:var(--line-2);border-radius:9px}
.grid{display:grid;gap:16px}
.g2{grid-template-columns:1fr 1fr}
.g3{grid-template-columns:repeat(3,1fr)}
.panel{background:var(--panel-2);border:1px solid var(--line);border-radius:var(--radius);padding:16px}
.panel h3{font-size:15px;margin-bottom:2px}
.panel .psub{font-size:11.5px;color:var(--muted-2);margin-bottom:14px}
.stat{background:var(--panel-3);border:1px solid var(--line);border-radius:14px;padding:13px 15px}
.stat .v{font-family:'Sora';font-size:23px;font-weight:700;line-height:1}
.stat .k{font-size:11px;color:var(--muted-2);margin-top:5px}
.stat .d{font-size:11px;font-weight:600;margin-top:6px}
.up{color:#4bb573}.down{color:var(--danger)}

/* Ideas stay visually distinct from tasks: left is for open-ended capture,
   right is for action and completion. */
.idea-todo-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start}
.idea-todo-col{min-height:430px;display:flex;flex-direction:column;gap:14px}
.idea-todo-col .ev-form{margin-top:auto}
.capture-form{display:grid;gap:10px}
.capture-form textarea{min-height:76px;resize:vertical}
.idea-todo-list{display:flex;flex-direction:column;gap:8px}
.idea-card{display:flex;align-items:flex-start;gap:10px;padding:12px;border:1px solid var(--line);background:var(--panel-3);border-radius:13px}
.idea-card p{margin:1px 0 0;flex:1;font-size:13px;line-height:1.45;white-space:pre-wrap}
.idea-card.done p{text-decoration:line-through;color:var(--muted-2)}
.idea-card form{margin:0;flex:0 0 auto}
.todo-toggle,.todo-delete{border:0;background:transparent;color:var(--muted);padding:2px;cursor:pointer;line-height:1}
.todo-toggle:hover{color:var(--accent)}
.todo-delete:hover{color:var(--danger)}
.todo-toggle svg,.todo-delete svg{vertical-align:middle}
.pub-tabs{display:flex;gap:10px;margin:16px 0 12px}
.sticky-tab{display:inline-flex;padding:10px 18px;border-radius:10px 10px 4px 4px;background:#e8c96a;color:#322817;font-weight:700;font-size:13px;box-shadow:0 3px 0 rgba(0,0,0,.2);transform:rotate(-1deg)}
.sticky-tab.resource{background:#9ddbd1;transform:rotate(1deg)}.sticky-tab.idea{background:#e9a7bd}
.pub-status-bar{display:flex;gap:22px;align-items:center;padding:12px 16px;border:1px solid var(--line);border-radius:13px;background:var(--panel-3);font-size:12px}.pub-status-bar span{display:flex;align-items:center;gap:7px}.pub-status-bar b{margin-left:2px}.status-dot{width:8px;height:8px;border-radius:50%;display:inline-block;background:#9b8f86}.status-dot.in_progress{background:#e8c96a}.status-dot.completed{background:#4bb573}
.pub-card{display:flex;align-items:center;gap:10px;padding:11px 12px;margin:7px 0;border:1px solid var(--line);border-radius:11px;background:var(--panel-3);font-size:13px}.pub-card>div{flex:1;min-width:0}.pub-card b{display:block;white-space:pre-wrap}.pub-card a{display:block;font-size:11px;color:var(--muted-2);border:0}.pub-card select{background:var(--panel-2);border:1px solid var(--line);color:var(--text);border-radius:7px;padding:5px;font-size:11px}
@media(max-width:760px){.idea-todo-grid{grid-template-columns:1fr}.idea-todo-col{min-height:0}}

.trow{display:flex;align-items:center;gap:12px;padding:11px 13px;border-radius:13px;
  background:var(--panel-3);border:1px solid var(--line);text-decoration:none;color:var(--text)}
.trow+.trow{margin-top:7px}
.trow:hover{border-color:var(--accent-soft)}
.trow .co{font-family:'Sora';font-weight:700;font-size:12.5px;min-width:104px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.trow .ti{flex:1;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.trow .lo{font-size:11.5px;color:var(--muted-2);white-space:nowrap}
/* The sidebar is too narrow for the table-style row: the title takes the width
   and the company, place or deadline sits under it. */
.trow.two .ti{display:flex;flex-direction:column;gap:4px;min-width:0}
.trow.two .ti b{font-weight:600;overflow:hidden;text-overflow:ellipsis}
.trow.two .ti small{display:flex;align-items:center;gap:8px;font-size:11.5px;color:var(--muted-2);
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tag{font-size:10.5px;font-weight:700;padding:3px 8px;border-radius:7px;
  background:var(--accent-soft);color:var(--accent-2);white-space:nowrap}
.tag.mute{background:var(--panel);color:var(--muted-2)}
.tag.good{background:rgba(75,181,115,.16);color:#4bb573}
.tag.bad{background:rgba(229,72,77,.16);color:var(--danger)}

.chart{width:100%;display:block;overflow:visible}
.legend{display:flex;flex-wrap:wrap;gap:12px;margin-top:12px;font-size:11.5px;color:var(--muted)}
.legend i{width:9px;height:9px;border-radius:3px;display:inline-block;margin-right:6px}
.bars{display:flex;flex-direction:column;gap:10px}
.barrow{display:grid;grid-template-columns:96px 1fr 42px;gap:11px;align-items:center;font-size:12px}
.barrow .track{height:7px;border-radius:5px;background:var(--panel-3);overflow:hidden}
.barrow .fill{height:100%;border-radius:5px;background:linear-gradient(90deg,var(--accent),var(--accent-2))}
.barrow .n{text-align:right;color:var(--muted);font-weight:600}
.barrow .lbl{color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

.tl{position:relative;padding-left:22px}
.tl::before{content:"";position:absolute;left:4px;top:6px;bottom:6px;width:2px;background:var(--line-2)}
.tl-item{position:relative;padding:0 0 18px}
.tl-item::before{content:"";position:absolute;left:-22px;top:5px;width:10px;height:10px;
  border-radius:50%;background:var(--accent);box-shadow:0 0 0 3px var(--panel-2)}
.tl-item h4{font-size:14px;font-weight:600}
.tl-item .org{font-size:12px;color:var(--accent-2);margin-top:2px}
.tl-item .when{font-size:11px;color:var(--muted-2);margin-top:3px}
.tl-item p{font-size:12.5px;color:var(--muted);margin-top:7px;line-height:1.55}

.chips{display:flex;flex-wrap:wrap;gap:7px}
.chip{font-size:11.5px;padding:5px 11px;border-radius:20px;background:var(--panel-3);
  border:1px solid var(--line);color:var(--muted)}
.chip.ev{border-color:var(--accent-soft);color:var(--accent-2)}
.empty{color:var(--muted-2);font-size:12.5px;padding:18px 0;text-align:center}

/* Two dates per competition row, each labelled, so "register by" is never
   confused with "runs on" -- and neither needs a click to see. */
.date-chip{display:inline-flex;align-items:center;gap:6px;white-space:nowrap;
  font-size:11px;font-weight:600;color:var(--muted);background:var(--panel);
  border:1px solid var(--line);border-radius:8px;padding:4px 8px}
.date-chip b{font-size:9px;letter-spacing:.08em;color:var(--muted-2);font-weight:800}
.date-chip.soon{border-color:rgba(229,72,77,.45);color:var(--danger)}
.date-chip.soon b{color:var(--danger);opacity:.75}
.date-chip.past{opacity:.45;text-decoration:line-through}
.date-chip.none{opacity:.5}

.seg{display:flex;gap:4px;background:var(--panel-3);border:1px solid var(--line);
  border-radius:13px;padding:4px}
.seg a{flex:1;text-align:center;padding:7px 10px;border-radius:10px;font-size:12.5px;
  font-weight:600;color:var(--muted);text-decoration:none}
.seg a.on{background:var(--accent);color:#fff}
.seg a:not(.on):hover{color:var(--text)}

/* An inline form is a single button living inside a row -- verify, remove,
   approve. It must not stack or stretch like the column forms do. */
.ev-form.inline{flex-direction:row;gap:0;margin-left:auto}
.ev-form.inline .btn-search{margin:0;padding:6px 12px;font-size:11.5px;border-radius:9px;
  background:var(--panel);border:1px solid var(--line);color:var(--muted);box-shadow:none}
.ev-form.inline .btn-search:hover{border-color:var(--accent-soft);color:var(--text);filter:none}
.ev-form.inline.dan .btn-search{color:var(--danger)}
.ev-form.suggest{background:var(--panel-3);border:1px solid var(--line);
  border-radius:14px;padding:12px;margin-bottom:8px}
.ev-form.suggest .btn-search{padding:8px;font-size:12.5px}

/* Detail pages reuse the spec-table rows outside the floating card they were
   written for, so the absolute positioning has to be undone. */
.spec-flat{display:block}
.spec-flat .ac-spec:first-child{border-top:0}
.lnk{color:var(--accent-2);text-decoration:none;font-weight:600}
.lnk:hover{text-decoration:underline}
.field select{width:100%;border:0;background:transparent;color:var(--text);
  font:inherit;font-size:13px;font-weight:600;outline:none;cursor:pointer}
.field select option{background:var(--panel-2);color:var(--text)}
.trow .ev-form.inline{flex:0 0 auto}
.hm{display:flex;flex-direction:column;gap:4px;overflow-x:auto}
.hm-r{display:flex;gap:4px;align-items:center}
.hm-l{flex:0 0 92px;font-size:11px;color:var(--muted);overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.hm-h{flex:1;min-width:44px;text-align:center;font-size:10px;color:var(--muted-2);font-weight:700}
.hm-c{flex:1;min-width:44px;height:30px;border-radius:7px;display:flex;align-items:center;
  justify-content:center;font-size:11px;font-weight:600;color:var(--text)}

/* A position and the accomplishments under it read as one block, because that
   is how the graph stores them -- a bullet never floats free of its position. */
.grp{background:var(--panel-3);border:1px solid var(--line);border-radius:14px;
  overflow:hidden;margin-bottom:8px}
.grp .trow{background:none;border:0;border-radius:0}
.subrows{border-top:1px solid var(--line);padding:4px 6px 6px}
.subrow{display:flex;align-items:center;gap:10px;padding:8px 10px;font-size:12.5px;
  color:var(--muted);border-radius:10px}
.subrow+.subrow{margin-top:2px}
.subrow:hover{background:var(--panel-2)}
.subrow .ti{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.subrow .lo{font-size:11.5px;color:var(--accent-2);white-space:nowrap}
.subrow::before{content:"";width:5px;height:5px;border-radius:50%;
  background:var(--accent);flex:0 0 auto;opacity:.6}

.notices{display:flex;flex-direction:column;gap:10px;margin-bottom:16px}
.notice{border-radius:14px;padding:13px 15px;font-size:12.5px;color:var(--muted);
  background:var(--panel-2);border:1px solid var(--line);border-left-width:3px}
.notice b{display:block;font-size:13px;color:var(--text);margin-bottom:2px}
.notice ul{margin:6px 0 0 16px}
.notice.bad{border-left-color:var(--danger)}
.notice.bad b{color:var(--danger)}
.notice.warn{border-left-color:#e2b23c}
.notice.warn b{color:#e2b23c}
.notice.good{border-left-color:#4bb573}
.notice.good b{color:#4bb573}
.notice.mute{border-left-color:var(--line-2)}

.ev-form{display:flex;flex-direction:column;gap:10px}
.ev-form .btn-search{margin-top:4px}
.field.ta{cursor:auto}
.field textarea{width:100%;border:0;background:transparent;color:var(--text);
  font:inherit;font-size:13px;outline:none;resize:vertical;line-height:1.5}
.fgrid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.frow{display:flex;gap:8px;align-items:center}
.btn-sm{border:1px solid var(--line);background:var(--panel-3);color:var(--text);
  border-radius:11px;padding:8px 14px;font:inherit;font-size:12.5px;font-weight:600;cursor:pointer}
.btn-sm:hover{border-color:var(--accent-soft)}
.btn-sm.pri{background:var(--accent);border-color:var(--accent);color:#fff}
.btn-sm.dan{color:var(--danger)}
a.btn-sm{text-decoration:none;display:inline-flex;align-items:center;gap:7px}

.doc{background:var(--panel-3);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.doc+.doc{margin-top:12px}
.doc-h{padding:10px 14px;font-size:11px;font-weight:700;letter-spacing:.06em;
  color:var(--muted-2);border-bottom:1px solid var(--line);text-transform:uppercase}
.doc pre{padding:14px;margin:0;font-size:12px;line-height:1.6;color:var(--muted);
  white-space:pre-wrap;word-break:break-word;max-height:420px;overflow:auto;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.doc pre::-webkit-scrollbar{width:6px}
.doc pre::-webkit-scrollbar-thumb{background:var(--line-2);border-radius:9px}
.crumb{display:inline-flex;align-items:center;gap:7px;color:var(--muted);
  text-decoration:none;font-size:12.5px;font-weight:600;margin-bottom:14px}
.crumb:hover{color:var(--accent-2)}
@media(max-width:1040px){.main-scroll{position:static;padding:0 26px 26px}.g2,.g3{grid-template-columns:1fr}}
"""

_THEME_JS = """
(function(){var t=document.getElementById('themeToggle');if(!t)return;
var s=null;try{s=localStorage.getItem('hole-theme')}catch(e){}
if(s)document.documentElement.dataset.theme=s;
t.onclick=function(){var h=document.documentElement;
h.dataset.theme=h.dataset.theme==='dark'?'light':'dark';
try{localStorage.setItem('hole-theme',h.dataset.theme)}catch(e){}};})();
"""


# --------------------------------------------------------------------------- bits


def _num(n: Any) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n or "")


def search_card(*, action: str, q: str = "", where: str = "", extra: str = "") -> str:
    """The reference's From/To/Date/Search block.

    There is no itinerary here, so the two fields become what this app actually
    searches on -- free text and location -- and Search submits to `action`.
    """
    return (
        f'<form class="search-card" method="get" action="{esc(action)}">'
        '<div class="field-row">'
        '<div class="field"><label for="q">Role</label>'
        f'<input id="q" name="q" value="{esc(q)}" placeholder="anything" autocomplete="off"></div>'
        '<button class="swap" type="submit" title="Search">'
        + icon("search", 13, 2.5)
        + "</button>"
        '<div class="field"><label for="where">Location</label>'
        f'<input id="where" name="where" value="{esc(where)}" placeholder="anywhere" autocomplete="off"></div>'
        "</div>"
        f"{extra}"
        '<button class="btn-search" type="submit">Search</button>'
        "</form>"
    )


def list_panel(*, title: str, sub: str, rows: str, tools: str = "", stacked: bool = False) -> str:
    """`stacked` is for a sidebar with several lists in it: each keeps the height
    of its own rows rather than sharing what the first one would have filled."""
    body = rows or '<div class="empty">Nothing here yet.</div>'
    return (
        f'<div class="flights{" stack" if stacked else ""}"><div class="flights-head"><div>'
        f'<h2>{esc(title)}</h2><div class="sub">{esc(sub)}</div></div>{tools}</div>'
        f'<div class="flight-list">{body}</div></div>'
    )


def spec_panel(title: str, rows: Sequence[tuple[str, Any]], *, head: str = "") -> str:
    """The dashboard's top-right card: an optional visual over key/value rows.

    Same slot and same shape the reference used for the aircraft spec table;
    `head` is what sits above the rows -- a chart here rather than a photo.
    """
    label = (
        f'<div class="ac-airline"><span>{esc(title)}</span></div>' if title else ""
    )
    body = "".join(
        f'<div class="ac-spec"><span class="k">{esc(k)}</span>'
        f'<span class="v">{esc(v)}</span></div>'
        for k, v in rows
    )
    return f'<aside class="aircraft">{head}{label}{body}</aside>'


def stat(value: Any, label: str, delta: str = "", tone: str = "") -> str:
    d = f'<div class="d {tone}">{esc(delta)}</div>' if delta else ""
    return (
        f'<div class="stat"><div class="v">{esc(_num(value) if isinstance(value,(int,float)) else value)}</div>'
        f'<div class="k">{esc(label)}</div>{d}</div>'
    )


def panel(title: str, body: str, *, sub: str = "") -> str:
    s = f'<div class="psub">{esc(sub)}</div>' if sub else ""
    return f'<div class="panel"><h3>{esc(title)}</h3>{s}{body}</div>'


def bars(rows: Sequence[tuple[str, float]], *, unit: str = "") -> str:
    if not rows:
        return '<div class="empty">No data yet.</div>'
    top = max((v for _, v in rows), default=0) or 1
    out = []
    for label, value in rows:
        pct = max(2.0, value / top * 100)
        out.append(
            f'<div class="barrow"><span class="lbl">{esc(label)}</span>'
            f'<span class="track"><span class="fill" style="width:{pct:.1f}%"></span></span>'
            f'<span class="n">{_num(value)}{esc(unit)}</span></div>'
        )
    return f'<div class="bars">{"".join(out)}</div>'


def area_chart(
    series: Sequence[tuple[str, Sequence[float]]],
    labels: Sequence[str],
    *,
    height: int = 150,
) -> str:
    """A small multi-series area chart, inline SVG, themed with the palette.

    This is what replaces the reference's aircraft photo: the same panel slot,
    showing discovery volume over time instead.
    """
    if not series or not labels:
        return '<div class="empty">No activity recorded yet.</div>'
    w, pad = 460.0, 6.0
    top = max((max(vals) if vals else 0) for _, vals in series) or 1
    n = max(len(labels) - 1, 1)
    colors = ["var(--accent)", "var(--muted)"]
    parts = ['<svg class="chart" viewBox="0 0 460 %d" preserveAspectRatio="none">' % height]
    parts.append('<defs><linearGradient id="ag" x1="0" y1="0" x2="0" y2="1">'
                 '<stop offset="0" stop-color="#e8763a" stop-opacity=".42"/>'
                 '<stop offset="1" stop-color="#e8763a" stop-opacity="0"/></linearGradient></defs>')
    for gi in range(1, 4):
        y = pad + (height - pad * 2) * gi / 4
        parts.append(f'<line x1="0" y1="{y:.1f}" x2="{w}" y2="{y:.1f}" '
                     'stroke="rgba(255,255,255,.06)" stroke-width="1"/>')
    for si, (_name, vals) in enumerate(series):
        pts = [
            (i / n * w, height - pad - (v / top) * (height - pad * 2))
            for i, v in enumerate(vals)
        ]
        line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        if si == 0:
            parts.append(
                f'<polygon points="0,{height} {line} {w},{height}" fill="url(#ag)"/>'
            )
        parts.append(
            f'<polyline points="{line}" fill="none" stroke="{colors[si % len(colors)]}" '
            f'stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"'
            + ('' if si == 0 else ' stroke-dasharray="4 5"') + "/>"
        )
    parts.append("</svg>")
    legend = "".join(
        f'<span><i style="background:{colors[i % len(colors)]}"></i>{esc(name)}</span>'
        for i, (name, _v) in enumerate(series)
    )
    return "".join(parts) + f'<div class="legend">{legend}</div>'


def donut(good: float, rest: float, *, good_label: str, rest_label: str) -> str:
    total = (good + rest) or 1
    frac = good / total
    r, c = 52.0, 60.0
    circ = 2 * 3.141592653589793 * r
    return (
        '<svg class="chart" viewBox="0 0 120 120" style="max-height:150px">'
        f'<circle cx="{c}" cy="{c}" r="{r}" fill="none" stroke="var(--panel-3)" stroke-width="15"/>'
        f'<circle cx="{c}" cy="{c}" r="{r}" fill="none" stroke="var(--accent)" stroke-width="15"'
        f' stroke-linecap="round" stroke-dasharray="{circ*frac:.1f} {circ:.1f}"'
        f' transform="rotate(-90 {c} {c})"/>'
        f'<text x="{c}" y="{c-2}" text-anchor="middle" font-family="Sora" font-size="21"'
        ' font-weight="700" fill="var(--text)">' + f"{frac*100:.0f}%</text>"
        f'<text x="{c}" y="{c+15}" text-anchor="middle" font-size="9"'
        ' fill="var(--muted-2)">' + esc(good_label) + "</text></svg>"
        f'<div class="legend"><span><i style="background:var(--accent)"></i>{esc(good_label)} '
        f'{_num(good)}</span><span><i style="background:var(--panel-3)"></i>{esc(rest_label)} '
        f"{_num(rest)}</span></div>"
    )


# The globe script reads `zoom`, which the reference defined alongside the zoom
# buttons, above the block carried over here. Same code, same behaviour.
_ZOOM_JS = """
let zoom = 1;
const setZoom = z => { zoom = Math.max(.6, Math.min(1.8, z)); };
for (const [id, fn] of [['zin',()=>setZoom(zoom+0.15)],['zout',()=>setZoom(zoom-0.15)],
                        ['zfit',()=>setZoom(1)]]) {
  const el = document.getElementById(id); if (el) el.onclick = fn;
}
"""

# One script for every page, so it can be allow-listed by hash the way
# `assets.SITE_JS_HASH` already is. Nothing page-specific is interpolated in:
# the globe's arc endpoints arrive as a `data-arc` attribute on the canvas, and
# a page without a canvas simply never calls `initGlobe()`. That keeps
# `script-src` free of 'unsafe-inline', which is what stops a job description
# from ever executing.
SCRIPT = _THEME_JS + _ZOOM_JS + GLOBE_JS + "\nif(document.getElementById('globe'))initGlobe();\n"

SCRIPT_HASH = "sha256-" + base64.b64encode(
    hashlib.sha256(SCRIPT.encode("utf-8")).digest()
).decode("ascii")


TONE_CLASS = {"bad": "bad", "warn": "warn", "good": "good", "": "mute"}


def notices(items: Sequence[dict[str, Any]]) -> str:
    """The config-problem / autonomous-mode banners.

    Honesty first, same as the page these came from: say plainly when the
    pipeline cannot send, or when it will send on its own.
    """
    if not items:
        return ""
    out = []
    for n in items:
        tone = TONE_CLASS.get(n.get("tone", ""), "mute")
        bullets = "".join(f"<li>{esc(i)}</li>" for i in n.get("items") or [])
        out.append(
            f'<div class="notice {tone}"><b>{esc(n.get("text", ""))}</b>'
            + (f"<ul>{bullets}</ul>" if bullets else "")
            + "</div>"
        )
    return f'<div class="notices">{"".join(out)}</div>'


def field(label: str, name: str, value: Any = "", *, kind: str = "text", ph: str = "") -> str:
    return (
        f'<div class="field"><label for="f-{esc(name)}">{esc(label)}</label>'
        f'<input id="f-{esc(name)}" name="{esc(name)}" type="{esc(kind)}" '
        f'value="{esc(value if value is not None else "")}" placeholder="{esc(ph)}"></div>'
    )


def textarea(label: str, name: str, value: Any = "", *, rows: int = 4) -> str:
    return (
        f'<div class="field ta"><label for="f-{esc(name)}">{esc(label)}</label>'
        f'<textarea id="f-{esc(name)}" name="{esc(name)}" rows="{rows}">'
        f'{esc(value if value is not None else "")}</textarea></div>'
    )


def form(action: str, token: str, body: str, submit: str, *, cls: str = "") -> str:
    return (
        f'<form class="ev-form {cls}" method="post" action="{esc(action)}">'
        f'<input type="hidden" name="token" value="{esc(token)}">{body}'
        f'<button class="btn-search" type="submit">{esc(submit)}</button></form>'
    )


def kv(rows: Sequence[tuple[str, str]]) -> str:
    """Key/value rows for a detail page. Values are pre-escaped HTML."""
    return "".join(
        f'<div class="ac-spec"><span class="k">{esc(k)}</span><span class="v">{v}</span></div>'
        for k, v in rows
    )


def doc(title: str, text: str) -> str:
    """A generated document, shown as-is. Escaped: these files are model output
    built from a job description, so they are never trusted markup."""
    return (
        f'<div class="doc"><div class="doc-h">{esc(title)}</div>'
        f"<pre>{esc(text)}</pre></div>"
    )


def globe_attr(a: tuple[float, float], b: tuple[float, float]) -> str:
    """`data-arc` for the canvas: the arc's two endpoints, as real lat/lons."""
    return f'data-arc="{a[0]:.4f},{a[1]:.4f},{b[0]:.4f},{b[1]:.4f}"'
