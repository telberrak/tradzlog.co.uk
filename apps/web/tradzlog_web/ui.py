"""TradzLog web UI design system — layout shell, tokens, and reusable HTML fragments."""

from __future__ import annotations

import json
import re
from html import escape

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap');
:root{
  color-scheme:dark;
  --bg-main:#0f1115;
  --bg-surface:#151922;
  --bg-surface-alt:#1b2130;
  --bg-elevated:#202838;
  --border-subtle:#2a3242;
  --border-strong:#3b465c;
  --text-primary:#e6eaf2;
  --text-secondary:#a5adbd;
  --text-muted:#7d8799;
  --accent:#3b82f6;
  --accent-hover:#2563eb;
  --accent-soft:rgba(59,130,246,.14);
  --success:#10b981;
  --success-soft:rgba(16,185,129,.14);
  --danger:#ef4444;
  --danger-soft:rgba(239,68,68,.14);
  --warning:#f59e0b;
  --warning-soft:rgba(245,158,11,.14);
  --shadow-sm:0 1px 2px rgba(0,0,0,.35);
  --shadow-md:0 8px 24px rgba(0,0,0,.28);
  --radius-sm:8px;
  --radius-md:10px;
  --radius-lg:14px;
  --font-sans:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  --font-mono:"JetBrains Mono","IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;
  --sidebar-w:248px;
  --topbar-h:56px;
  --green:var(--success);
  --red:var(--danger);
  --blue:var(--accent);
  --amber:var(--warning);
  --neutral:var(--accent);
  --base:var(--bg-main);
  --panel:var(--bg-surface);
  --panel2:var(--bg-surface-alt);
  --line:var(--border-subtle);
  --line2:var(--border-strong);
  --text:var(--text-primary);
  --text-strong:var(--text-primary);
  --muted:var(--text-muted);
  --surface:var(--bg-surface);
  --surface2:var(--bg-surface-alt);
  --surface3:var(--bg-elevated);
  --surface4:var(--bg-surface-alt);
  --input-bg:var(--bg-elevated);
  --border-table:var(--border-subtle);
  --th-text:var(--text-secondary);
  --tr-hover:rgba(59,130,246,.08);
  --label-dim:var(--text-muted);
  --label-accent:var(--accent);
  --nav-text:var(--text-secondary);
  --nav-bg:transparent;
  --nav-active-bg:var(--accent-soft);
  --nav-active-border:var(--accent);
  --nav-active-text:var(--text-primary);
  --control-bg:var(--bg-elevated);
  --control-text:var(--text-primary);
  --brand-fg:#93c5fd;
  --brand-bg:rgba(59,130,246,.12);
  --brand-border:rgba(59,130,246,.35);
  --spark-grid:#2a3242;
  --spark-axis:#3b465c;
  --bar-track:#1b2130;
  --bar-border:#2a3242;
  --primary-bg:var(--accent);
  --primary-border:var(--accent-hover);
  --primary-text:#fff;
  --avatar-bg:var(--bg-elevated);
  --top-bg:var(--bg-surface);
  --tabbar-bg:var(--bg-surface);
}
[data-theme="light"]{
  color-scheme:light;
  --bg-main:#f4f7fb;
  --bg-surface:#ffffff;
  --bg-surface-alt:#f8fafc;
  --bg-elevated:#ffffff;
  --border-subtle:#d8e0ec;
  --border-strong:#b8c4d6;
  --text-primary:#101828;
  --text-secondary:#475467;
  --text-muted:#667085;
  --accent:#2563eb;
  --accent-hover:#1d4ed8;
  --accent-soft:rgba(37,99,235,.10);
  --success:#059669;
  --success-soft:rgba(5,150,105,.10);
  --danger:#dc2626;
  --danger-soft:rgba(220,38,38,.10);
  --warning:#d97706;
  --warning-soft:rgba(217,119,6,.10);
  --shadow-sm:0 1px 2px rgba(16,24,40,.06);
  --shadow-md:0 8px 24px rgba(16,24,40,.08);
  --spark-grid:#e4e7ec;
  --spark-axis:#98a2b3;
  --bar-track:#eef2f6;
  --bar-border:#d0d5dd;
  --tr-hover:rgba(37,99,235,.06);
  --brand-fg:#1d4ed8;
  --brand-bg:rgba(37,99,235,.08);
  --brand-border:rgba(37,99,235,.25);
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg-main);color:var(--text-primary);font-family:var(--font-sans);font-size:14px;line-height:1.45;-webkit-font-smoothing:antialiased}
a{color:var(--accent);text-decoration:none}
a:hover{color:var(--accent-hover)}
.app-shell{display:flex;min-height:100vh;background:var(--bg-main)}
.sidebar{width:var(--sidebar-w);flex-shrink:0;background:var(--bg-surface);border-right:1px solid var(--border-subtle);display:flex;flex-direction:column;padding:16px 12px;position:sticky;top:0;height:100vh;overflow-y:auto}
.sidebar-brand{display:flex;align-items:center;gap:10px;padding:8px 10px 18px;border-bottom:1px solid var(--border-subtle);margin-bottom:12px}
.sidebar-brand .logo{width:32px;height:32px;border-radius:var(--radius-sm);background:linear-gradient(135deg,var(--accent),#6366f1);display:flex;align-items:center;justify-content:center;font-weight:800;font-size:13px;color:#fff}
.sidebar-brand .name{font-weight:700;font-size:15px;letter-spacing:.04em;color:var(--text-primary)}
.sidebar-nav{display:flex;flex-direction:column;gap:4px;flex:1}
.sidebar-nav a{display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:var(--radius-sm);color:var(--nav-text);font-size:13px;font-weight:500;border:1px solid transparent;transition:background .15s,border-color .15s,color .15s}
.sidebar-nav a:hover{background:var(--bg-surface-alt);color:var(--text-primary)}
.sidebar-nav a.active{background:var(--nav-active-bg);border-color:rgba(59,130,246,.25);color:var(--nav-active-text);font-weight:600}
.sidebar-nav .icon{width:18px;height:18px;opacity:.85;flex-shrink:0}
.sidebar-footer{margin-top:auto;padding-top:12px;border-top:1px solid var(--border-subtle);font-size:12px;color:var(--text-muted)}
.main-area{flex:1;min-width:0;display:flex;flex-direction:column}
.topbar{height:var(--topbar-h);flex-shrink:0;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:0 20px;background:var(--bg-surface);border-bottom:1px solid var(--border-subtle)}
.topbar-left{display:flex;flex-direction:column;gap:2px;min-width:0}
.topbar .crumb{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--text-muted)}
.topbar h1{font-size:22px;font-weight:700;margin:0;color:var(--text-primary);line-height:1.2}
.topbar-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.page-content{padding:20px;flex:1}
.mono{font-family:var(--font-mono)}
.num{font-family:var(--font-mono);font-variant-numeric:tabular-nums}
.t-right{text-align:right}
.grid{display:grid;gap:14px}
.kpis{grid-template-columns:repeat(6,minmax(0,1fr))}
.two-col{grid-template-columns:minmax(0,1.55fr) minmax(320px,.95fr)}
.form-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
.accounts{grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}
.card,.panel{background:var(--bg-surface);border:1px solid var(--border-subtle);border-radius:var(--radius-lg);padding:16px;box-shadow:var(--shadow-sm)}
.kpi-card{background:var(--bg-surface);border:1px solid var(--border-subtle);border-radius:var(--radius-lg);padding:14px 16px;box-shadow:var(--shadow-sm)}
.kpi-card .label{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--text-muted)}
.kpi-card .kpi{font-size:28px;font-weight:700;margin-top:6px;font-family:var(--font-mono);line-height:1.1}
.kpi-card .note{font-size:12px;color:var(--text-muted);margin-top:6px}
.positive{color:var(--success)}
.negative{color:var(--danger)}
.neutral{color:var(--accent)}
.amber{color:var(--warning)}
.muted{color:var(--text-muted)}
.small{font-size:12px}
.section-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:14px;padding-bottom:10px;border-bottom:1px solid var(--border-subtle)}
.section-head h2{font-size:16px;font-weight:650;margin:4px 0 0;color:var(--text-primary)}
.section-head .label{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--text-muted)}
.section-sub{font-size:12px;color:var(--text-muted);margin-top:2px}
.btn,.pill{display:inline-flex;align-items:center;justify-content:center;gap:6px;height:34px;padding:0 14px;border-radius:var(--radius-sm);font-size:12px;font-weight:600;font-family:inherit;text-decoration:none;border:1px solid var(--border-subtle);background:var(--bg-elevated);color:var(--text-primary);cursor:pointer;transition:background .15s,border-color .15s,color .15s,box-shadow .15s;white-space:nowrap}
.btn:hover,.pill:hover{border-color:var(--border-strong);background:var(--bg-surface-alt)}
.btn-primary,.pill.primary,button.primary{background:var(--accent);border-color:var(--accent-hover);color:#fff}
.btn-primary:hover,.pill.primary:hover{background:var(--accent-hover)}
.btn-danger{background:var(--danger-soft);border-color:rgba(239,68,68,.35);color:var(--danger)}
.btn-ghost{background:transparent;border-color:transparent;color:var(--text-secondary)}
.btn-ghost:hover{background:var(--bg-surface-alt);color:var(--text-primary)}
.btn-sm{height:28px;padding:0 10px;font-size:11px}
.theme-toggle{height:34px}
.filter-tabs{display:flex;flex-wrap:wrap;gap:6px;align-items:center;padding:4px;background:var(--bg-surface-alt);border:1px solid var(--border-subtle);border-radius:var(--radius-md);width:fit-content;max-width:100%}
.filter-tabs a,.filter-tabs .tab{padding:7px 12px;border-radius:6px;font-size:12px;font-weight:600;color:var(--text-secondary);text-decoration:none;border:1px solid transparent}
.filter-tabs a.active,.filter-tabs a:hover,.filter-tabs .tab.active{background:var(--bg-surface);color:var(--text-primary);border-color:var(--border-subtle);box-shadow:var(--shadow-sm)}
.badge{display:inline-flex;align-items:center;padding:3px 8px;border-radius:999px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;border:1px solid var(--border-subtle);background:var(--bg-elevated);color:var(--text-secondary)}
.badge-long,.badge.long{background:var(--success-soft);border-color:rgba(16,185,129,.35);color:var(--success)}
.badge-short,.badge.short{background:var(--danger-soft);border-color:rgba(239,68,68,.35);color:var(--danger)}
.badge-open,.badge.open{background:var(--accent-soft);border-color:rgba(59,130,246,.35);color:var(--accent)}
.badge-closed,.badge.closed{background:var(--bg-surface-alt);color:var(--text-secondary)}
.badge-cancelled,.badge.cancelled{opacity:.75}
.badge-completed,.badge.completed{background:var(--success-soft);color:var(--success);border-color:rgba(16,185,129,.3)}
.badge-failed,.badge.failed{background:var(--danger-soft);color:var(--danger);border-color:rgba(239,68,68,.3)}
.badge-pending,.badge.pending{background:var(--warning-soft);color:var(--warning);border-color:rgba(245,158,11,.3)}
.badge-review{background:var(--warning-soft);color:var(--warning)}
.badge-high{background:var(--success-soft);color:var(--success)}
.badge-low-sample{background:var(--bg-surface-alt);color:var(--text-muted)}
.field{display:grid;gap:6px}
.field label{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;color:var(--text-secondary)}
input,select,textarea{width:100%;border:1px solid var(--border-subtle);background:var(--input-bg);color:var(--text-primary);border-radius:var(--radius-sm);padding:9px 11px;font:inherit;font-size:13px;min-height:38px;transition:border-color .15s,box-shadow .15s}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
textarea{min-height:120px;resize:vertical}
table.data-table{width:100%;border-collapse:separate;border-spacing:0;font-size:12px}
table.data-table th,table{width:100%;border-collapse:collapse}
table th,table td{padding:9px 10px;border-bottom:1px solid var(--border-subtle);text-align:left;vertical-align:middle}
table th{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;color:var(--text-secondary);background:var(--bg-surface-alt);position:sticky;top:0;z-index:1}
table tbody tr:hover{background:var(--tr-hover)}
table .num,table td.num,table th.num{font-family:var(--font-mono);font-variant-numeric:tabular-nums}
table th.num,table td.num{text-align:right}
.empty-state{border:1px dashed var(--border-strong);border-radius:var(--radius-lg);padding:28px 24px;text-align:center;background:var(--bg-surface-alt);color:var(--text-muted)}
.empty-state .empty-icon{font-size:28px;margin-bottom:10px;opacity:.7}
.empty-state h3{margin:0 0 8px;font-size:15px;color:var(--text-primary)}
.empty-state p{margin:0 0 14px;font-size:13px;max-width:420px;margin-left:auto;margin-right:auto}
.dropzone{border:2px dashed var(--border-strong);border-radius:var(--radius-lg);padding:28px;text-align:center;background:var(--bg-surface-alt);cursor:pointer;transition:border-color .15s,background .15s}
.dropzone:hover{border-color:var(--accent);background:var(--accent-soft)}
.dropzone strong{display:block;color:var(--text-primary);margin-bottom:6px}
.avatar{width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;background:var(--avatar-bg);border:1px solid var(--border-subtle);color:var(--brand-fg)}
.bar{height:8px;background:var(--bar-track);border-radius:999px;overflow:hidden;border:1px solid var(--bar-border)}
.bar span{display:block;height:100%;background:var(--success);border-radius:999px;transition:width .2s}
.bar.negative span{background:var(--danger)}
.spark{width:100%;height:200px;border-radius:var(--radius-md);background:var(--bg-surface-alt);background-image:linear-gradient(var(--spark-grid) 1px,transparent 1px),linear-gradient(90deg,var(--spark-grid) 1px,transparent 1px);background-size:24px 24px}
.spark polyline{fill:none;stroke:var(--success);stroke-width:2.5}
.spark .axis{stroke:var(--spark-axis);stroke-width:1}
.daily-chart{display:flex;flex-direction:column;gap:8px}
.daily-row{display:grid;grid-template-columns:52px 1fr 88px;gap:10px;align-items:center}
.daily-row .bar-wrap{height:22px;background:var(--bar-track);border-radius:4px;overflow:hidden;border:1px solid var(--bar-border)}
.daily-row .bar-fill{height:100%;border-radius:3px}
.daily-row .bar-fill.pos{background:var(--success)}
.daily-row .bar-fill.neg{background:var(--danger)}
.insight-card{border:1px solid var(--border-subtle);border-radius:var(--radius-md);padding:14px;margin-bottom:10px;background:var(--bg-surface-alt)}
.insight-card h4{margin:0 0 8px;font-size:13px;font-weight:650;color:var(--text-primary)}
.insight-card p{margin:0;font-size:13px;line-height:1.55;color:var(--text-secondary)}
.insight-card ul{margin:6px 0 0;padding-left:18px;color:var(--text-secondary);font-size:13px}
.journal-card{background:var(--bg-surface);border:1px solid var(--border-subtle);border-radius:var(--radius-lg);padding:16px;transition:border-color .15s,box-shadow .15s}
.journal-card:hover{border-color:var(--border-strong);box-shadow:var(--shadow-md)}
.account-card .account-type{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--text-muted)}
.account-card h2{font-size:17px;margin:4px 0 0}
.actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.page-block{margin-bottom:18px}
@media(max-width:1100px){.kpis{grid-template-columns:repeat(3,1fr)}.two-col,.form-grid{grid-template-columns:1fr}}
@media(max-width:900px){
  .app-shell{flex-direction:column}
  .sidebar{width:100%;height:auto;position:relative;flex-direction:row;flex-wrap:wrap;padding:10px}
  .sidebar-brand{border-bottom:none;margin-bottom:0;padding-bottom:0}
  .sidebar-nav{flex-direction:row;flex-wrap:wrap;flex:1}
  .sidebar-footer{display:none}
}
@media(max-width:640px){.kpis{grid-template-columns:1fr 1fr}.page-content{padding:12px}.topbar{padding:0 12px;flex-wrap:wrap;height:auto;min-height:var(--topbar-h);padding-top:10px;padding-bottom:10px}}
"""

THEME_SCRIPT = """
(function () {
  try {
    var k = "tradzlog-theme";
    var saved = localStorage.getItem(k);
    var prefersLight = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
    var initial = saved || (prefersLight ? "light" : "dark");
    document.documentElement.setAttribute("data-theme", initial);
  } catch (e) {}
})();
"""

THEME_TOGGLE_SCRIPT = """
(function () {
  var k = "tradzlog-theme";
  function label(t) { return t === "light" ? "Dark mode" : "Light mode"; }
  function apply(t) {
    document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem(k, t); } catch (e) {}
    var btn = document.getElementById("theme-toggle");
    if (btn) btn.textContent = label(t);
  }
  var cur = document.documentElement.getAttribute("data-theme") || "dark";
  var btn = document.getElementById("theme-toggle");
  if (btn) {
    btn.textContent = label(cur);
    btn.addEventListener("click", function () {
      var next = (document.documentElement.getAttribute("data-theme") || "dark") === "light" ? "dark" : "light";
      apply(next);
    });
  }
})();
"""

NAV_ICONS: dict[str, str] = {
    "dashboard": '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>',
    "portfolio": '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 17h7"/></svg>',
    "trades": '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 17l6-6 4 4 8-8"/><path d="M14 7h7v7"/></svg>',
    "positions": '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>',
    "journal": '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 5h16v14H4z"/><path d="M8 5v14M8 9h4"/></svg>',
    "analytics": '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19V5M10 19V9M16 19v-6M22 19V3"/></svg>',
    "coaching": '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3a7 7 0 0 1 7 7c0 3-2 5-4 6l-1 5H10l-1-5c-2-1-4-3-4-6a7 7 0 0 1 7-7z"/></svg>',
    "settings": '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"/></svg>',
}


def sidebar_nav(active: str) -> str:
    items = [
        ("Dashboard", "/dashboard", "dashboard"),
        ("Portfolio", "/dashboard/portfolio", "portfolio"),
        ("Trades", "/trades", "trades"),
        ("Positions", "/positions", "positions"),
        ("Journal", "/journal", "journal"),
        ("Analytics", "/analytics", "analytics"),
        ("AI Coaching", "/coaching", "coaching"),
        ("Import", "/settings/import", "settings"),
    ]
    links = []
    for label, href, key in items:
        icon = NAV_ICONS.get(key, "")
        cls = "active" if key == active else ""
        links.append(f'<a class="{cls}" href="{href}">{icon}<span>{escape(label)}</span></a>')
    return "".join(links)


def shell(
    title: str,
    active: str,
    body: str,
    account_name: str = "Demo Account",
    user_name: str = "Trader",
    *,
    show_range: bool = True,
) -> str:
    initials = escape("".join(part[:1] for part in user_name.split()[:2]).upper() or "T")
    range_pills = ""
    if show_range:
        range_pills = """
            <a class="btn btn-sm" href="/dashboard?range=1M">1M</a>
            <a class="btn btn-sm" href="/dashboard?range=3M">3M</a>
            <a class="btn btn-sm" href="/dashboard?range=YTD">YTD</a>
            <a class="btn btn-sm" href="/dashboard/portfolio">All Accounts</a>
        """
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{escape(title)} · TradzLog</title>
    <style>{CSS}</style>
    <script>{THEME_SCRIPT}</script>
  </head>
  <body>
    <div class="app-shell">
      <aside class="sidebar" aria-label="Main navigation">
        <div class="sidebar-brand">
          <div class="logo">TZ</div>
          <div class="name">TRADZLOG</div>
        </div>
        <nav class="sidebar-nav">{sidebar_nav(active)}</nav>
        <div class="sidebar-footer">Trading journal &amp; analytics</div>
      </aside>
      <div class="main-area">
        <header class="topbar">
          <div class="topbar-left">
            <div class="crumb">TradzLog / {escape(account_name)}</div>
            <h1>{escape(title)}</h1>
          </div>
          <div class="topbar-actions">
            {range_pills}
            <button type="button" class="btn theme-toggle" id="theme-toggle" aria-label="Switch color theme">Theme</button>
            <div class="avatar" aria-label="User avatar">{initials}</div>
          </div>
        </header>
        <main class="page-content">{body}</main>
      </div>
    </div>
    <script>{THEME_TOGGLE_SCRIPT}</script>
  </body>
</html>"""

def kpi_card(label: str, value: str, value_tone: str = "neutral", note: str = "") -> str:
    return (
        f'<article class="kpi-card">'
        f'<div class="label">{escape(label)}</div>'
        f'<div class="kpi {value_tone}">{escape(value)}</div>'
        f'<div class="note">{escape(note)}</div>'
        f"</article>"
    )


def empty_state(title: str, description: str, cta_href: str = "", cta_label: str = "", icon: str = "◇") -> str:
    cta = f'<a class="btn btn-primary" href="{escape(cta_href)}">{escape(cta_label)}</a>' if cta_href else ""
    return f"""<section class="empty-state page-block">
      <div class="empty-icon" aria-hidden="true">{escape(icon)}</div>
      <h3>{escape(title)}</h3>
      <p>{escape(description)}</p>
      {cta}
    </section>"""


def filter_tabs(items: list[tuple[str, str, bool]]) -> str:
    links = "".join(
        f'<a class="{"active" if active else ""}" href="{href}">{escape(label)}</a>' for label, href, active in items
    )
    return f'<nav class="filter-tabs" aria-label="Filters">{links}</nav>'


def side_badge(side: str) -> str:
    normalized = (side or "").upper()
    css = "long" if normalized == "LONG" else "short" if normalized == "SHORT" else ""
    return f'<span class="badge badge-{css}">{escape(normalized or side)}</span>'


def status_badge(status: str) -> str:
    normalized = (status or "").upper()
    mapping = {
        "OPEN": "open",
        "CLOSED": "closed",
        "CANCELLED": "cancelled",
        "COMPLETED": "completed",
        "FAILED": "failed",
        "PENDING": "pending",
    }
    css = mapping.get(normalized, "closed")
    return f'<span class="badge badge-{css}">{escape(normalized or status)}</span>'


def coach_quality_badge(average_r: float | str, trades: int) -> str:
    r_val = float(average_r or 0)
    if trades < 5:
        return '<span class="badge badge-low-sample">Low sample</span>'
    if r_val > 0.5:
        return '<span class="badge badge-high">High expectancy</span>'
    if r_val > 0:
        return '<span class="badge">Stable</span>'
    return '<span class="badge badge-review">Needs review</span>'


def format_insight_content(content: str) -> str:
    text = (content or "").strip()
    if not text:
        return '<p class="muted">No insight content available.</p>'

    if text.startswith("AI coaching is wired but") or "Payload prepared for Claude" in text:
        return (
            '<div class="insight-card">'
            "<h4>Coaching unavailable</h4>"
            "<p>Configure <strong>ANTHROPIC_API_KEY</strong> to generate AI pattern analysis. "
            "Your performance data is ready.</p>"
            "</div>"
        )

    parsed: object | None = None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if fence:
            try:
                parsed = json.loads(fence.group(1))
            except json.JSONDecodeError:
                parsed = None

    if isinstance(parsed, dict):
        return _render_insight_object(parsed)
    if isinstance(parsed, list):
        return "".join(_render_insight_object(item) for item in parsed if isinstance(item, dict))

    sections = _parse_insight_sections(text)
    if sections:
        return "".join(
            f'<div class="insight-card"><h4>{escape(title)}</h4><p>{escape(body)}</p></div>'
            for title, body in sections
        )

    return f'<div class="insight-card"><p>{escape(text)}</p></div>'


def _render_insight_object(data: dict[str, object]) -> str:
    blocks: list[str] = []
    title = str(data.get("title") or data.get("headline") or "Insight")
    summary = data.get("summary") or data.get("content") or data.get("insight")
    if summary:
        blocks.append(f'<div class="insight-card"><h4>{escape(str(title))}</h4><p>{escape(str(summary))}</p></div>')

    for key, heading in (
        ("patterns", "Behaviour patterns"),
        ("behaviourPatterns", "Behaviour patterns"),
        ("strengths", "Strengths"),
        ("weaknesses", "Weaknesses"),
        ("recommendedActions", "Recommended actions"),
        ("actions", "Next actions"),
        ("riskWarning", "Risk warning"),
        ("riskWarnings", "Risk warnings"),
        ("journalPrompt", "Suggested journal prompt"),
        ("journalPrompts", "Suggested journal prompts"),
    ):
        value = data.get(key)
        if not value:
            continue
        if isinstance(value, list):
            items = "".join(f"<li>{escape(str(item))}</li>" for item in value)
            blocks.append(f'<div class="insight-card"><h4>{escape(heading)}</h4><ul>{items}</ul></div>')
        else:
            blocks.append(f'<div class="insight-card"><h4>{escape(heading)}</h4><p>{escape(str(value))}</p></div>')

    insights = data.get("insights")
    if isinstance(insights, list):
        for item in insights:
            if isinstance(item, dict):
                blocks.append(_render_insight_object(item))

    if not blocks:
        plain_parts = [f"{k}: {v}" for k, v in data.items() if v and k not in {"insights"}]
        body = escape("\n".join(plain_parts[:8]))
        blocks.append(f'<div class="insight-card"><h4>{escape(str(title))}</h4><p>{body}</p></div>')
    return "".join(blocks)


def _parse_insight_sections(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    current_title = "Key insight"
    current_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.endswith(":") and len(stripped) < 64:
            if current_lines:
                sections.append((current_title, "\n".join(current_lines).strip()))
                current_lines = []
            current_title = stripped[:-1]
        elif stripped:
            current_lines.append(stripped)
    if current_lines:
        sections.append((current_title, "\n".join(current_lines).strip()))
    return sections


def insight_cards_html(insights: list[object]) -> str:
    cards = []
    for insight in insights:
        title = escape(getattr(insight, "title", "Insight"))
        generated = getattr(insight, "generated_at", None)
        date_label = generated.strftime("%Y-%m-%d") if generated else ""
        type_label = escape(getattr(getattr(insight, "type", None), "value", "PATTERN"))
        body = format_insight_content(str(getattr(insight, "content", "")))
        cards.append(
            f"""<article class="panel page-block">
              <div class="section-head">
                <div>
                  <div class="label">{type_label}</div>
                  <h2>{title}</h2>
                  <div class="section-sub">Generated {escape(date_label)}</div>
                </div>
              </div>
              {body}
            </article>"""
        )
    return "".join(cards) or empty_state(
        "No AI insights yet",
        "Generate a pattern analysis to receive data-driven coaching on your trading book.",
        "/coaching",
        "Generate insights",
        "✦",
    )
