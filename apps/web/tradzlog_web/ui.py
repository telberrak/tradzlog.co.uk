"""TradzLog web UI design system — layout shell, tokens, and reusable HTML fragments."""

from __future__ import annotations

import json
import re
from html import escape

from tradzlog_web.auth import inject_csrf

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
.filter-bar{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:16px}
.filter-bar form{display:flex;align-items:center;gap:8px;margin:0}
.filter-bar select{width:auto;min-width:220px;min-height:34px;padding:6px 10px}
.seg{display:inline-flex;border:1px solid var(--border-subtle);border-radius:var(--radius-sm);overflow:hidden;background:var(--bg-surface)}
.seg a,.seg label{padding:7px 12px;font-size:12px;font-weight:600;color:var(--text-secondary);border-right:1px solid var(--border-subtle);cursor:pointer;user-select:none}
.seg a:last-child,.seg label:last-of-type{border-right:none}
.seg a:hover,.seg label:hover{background:var(--bg-surface-alt);color:var(--text-primary)}
.seg a.active{background:var(--accent-soft);color:var(--accent)}
.seg{position:relative}
.seg input{position:absolute;opacity:0;pointer-events:none;width:1px;height:1px;min-height:0;padding:0;margin:0}
.seg input:checked+label{background:var(--accent-soft);color:var(--accent)}
.seg input.long:checked+label{background:var(--success-soft);color:var(--success)}
.seg input.short:checked+label{background:var(--danger-soft);color:var(--danger)}
.seg input:focus-visible+label{outline:2px solid var(--accent);outline-offset:-2px}
.kpi-row{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin-bottom:14px}
.kpi-card{min-width:0}
.kpi-card .kpi{font-size:24px;overflow-wrap:anywhere}
.form-row>.field,.form-layout>*{min-width:0}
.form-row input{min-width:0}
.kpi-card .note.positive{color:var(--success)}
.kpi-card .note.negative{color:var(--danger)}
.dash-grid{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(300px,1fr);gap:14px;margin-bottom:14px}
.card-title{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px}
.card-title h2{font-size:15px;font-weight:650;margin:0}
.card-title .meta{font-size:12px;color:var(--text-muted)}
.chart{width:100%;height:auto;display:block}
.chart .grid-line{stroke:var(--border-subtle);stroke-width:1}
.chart .equity{fill:none;stroke:var(--success);stroke-width:2.2;stroke-linejoin:round}
.chart .equity.down{stroke:var(--danger)}
.chart .equity-fill{fill:var(--success-soft)}
.chart .equity-fill.down{fill:var(--danger-soft)}
.chart .dd{fill:var(--danger);opacity:.4}
.chart .baseline{stroke:var(--border-strong);stroke-dasharray:3 4}
.chart text{fill:var(--text-muted);font-size:11px;font-family:var(--font-mono)}
.cal{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:4px}
.cal .dow{font-size:10px;font-weight:600;text-transform:uppercase;color:var(--text-muted);text-align:center;padding-bottom:2px}
.cal .day{aspect-ratio:1/0.82;border-radius:6px;background:var(--bg-surface-alt);border:1px solid transparent;display:flex;flex-direction:column;justify-content:space-between;padding:4px 5px;font-size:10px;color:var(--text-muted);min-width:0;text-decoration:none}
.cal a.day:hover{border-color:var(--border-strong)}
.cal .day.pad{background:transparent}
.cal .day.today{border-color:var(--accent)}
.cal .day .amt{font-family:var(--font-mono);font-size:10px;font-weight:600;text-align:right;white-space:nowrap;overflow:hidden}
.cal .day.w1{background:var(--success-soft);color:var(--success)}
.cal .day.w2{background:var(--success);color:#fff}
.cal .day.l1{background:var(--danger-soft);color:var(--danger)}
.cal .day.l2{background:var(--danger);color:#fff}
.cal-foot{display:flex;justify-content:space-between;font-size:12px;color:var(--text-muted);margin-top:10px}
.edge{display:grid;gap:12px}
.edge-row .top{display:flex;justify-content:space-between;gap:8px;font-size:13px}
.edge-row .top .num{color:var(--text-secondary);font-size:12px}
.edge-row .track{position:relative;height:8px;margin-top:5px;background:var(--bar-track);border-radius:999px;overflow:hidden}
.edge-row .track .zero{position:absolute;top:0;bottom:0;width:1px;background:var(--border-strong)}
.edge-row .track span{position:absolute;top:0;bottom:0;border-radius:999px;background:var(--success)}
.edge-row .track span.neg{background:var(--danger)}
table.dense th,table.dense td{padding:7px 10px;white-space:nowrap}
table.dense td{font-size:13px}
table.dense td.sym a{font-weight:650;color:var(--text-primary)}
table.dense tr[data-href]{cursor:pointer}
table.dense td.wrap{white-space:normal;max-width:220px;overflow:hidden;text-overflow:ellipsis}
.table-wrap{overflow-x:auto}
.trade-head{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px}
.trade-head .sym{font-size:26px;font-weight:700;font-family:var(--font-mono)}
.trade-head .meta{color:var(--text-muted);font-size:13px}
.trade-head .actions{margin-left:auto}
.ladder text{font-family:var(--font-mono);font-size:12px}
.ladder .lbl{fill:var(--text-secondary);font-family:var(--font-sans);font-weight:600;font-size:11px}
.timeline{list-style:none;margin:0;padding:0}
.timeline li{position:relative;padding:0 0 16px 22px}
.timeline li::before{content:"";position:absolute;left:5px;top:14px;bottom:-2px;width:2px;background:var(--border-subtle)}
.timeline li:last-child::before{display:none}
.timeline .dot{position:absolute;left:0;top:4px;width:12px;height:12px;border-radius:50%;border:2px solid var(--accent);background:var(--bg-surface)}
.timeline .dot.exit{border-color:var(--warning)}
.timeline .t{font-size:12px;color:var(--text-muted)}
.timeline .d{font-size:13px;font-weight:600}
.thumbs{display:grid;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));gap:8px}
.thumbs img{width:100%;aspect-ratio:4/3;object-fit:cover;border-radius:var(--radius-sm);border:1px solid var(--border-subtle)}
.form-layout{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(280px,1fr);gap:14px;align-items:start}
.form-section{padding:16px 0;border-top:1px solid var(--border-subtle)}
.form-section:first-of-type{border-top:none;padding-top:0}
.form-section h3{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--text-muted);margin:0 0 12px}
.form-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.preview{position:sticky;top:16px}
.preview dl{display:grid;grid-template-columns:1fr auto;gap:10px 12px;margin:0}
.preview dt{color:var(--text-muted);font-size:13px}
.preview dd{margin:0;text-align:right;font-family:var(--font-mono);font-weight:600}
.preview .big{font-size:30px;font-weight:700;font-family:var(--font-mono);margin:6px 0 14px}
.hint{font-size:12px;color:var(--text-muted);margin:8px 0 0}
.hint.warn{color:var(--warning)}
@media(max-width:1100px){.kpi-row{grid-template-columns:repeat(3,minmax(0,1fr))}.dash-grid,.form-layout{grid-template-columns:1fr}.preview{position:static}}
@media(max-width:640px){.kpi-row{grid-template-columns:1fr 1fr}.kpi-card .kpi{font-size:18px}.filter-bar select{min-width:0;width:100%}}
.user-menu{position:relative}
.user-menu summary{list-style:none;cursor:pointer}
.user-menu summary::-webkit-details-marker{display:none}
.user-menu-panel{position:absolute;right:0;top:42px;z-index:20;min-width:200px;background:var(--bg-surface);border:1px solid var(--border-subtle);border-radius:var(--radius-md);box-shadow:var(--shadow-md);padding:6px;display:grid;gap:2px}
.user-menu-name{padding:8px 10px;font-weight:650;border-bottom:1px solid var(--border-subtle);margin-bottom:4px}
.user-menu-panel a,.user-menu-panel button{display:block;width:100%;text-align:left;padding:8px 10px;border-radius:6px;color:var(--text-primary);background:none;border:0;font:inherit;font-size:13px;cursor:pointer}
.user-menu-panel a:hover,.user-menu-panel button:hover{background:var(--bg-surface-alt)}
.user-menu-panel form{margin:0}
.auth-wrap{min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:24px 16px}
.auth-brand{border:none;margin:0 0 16px;padding:0}
.auth-card{width:100%;max-width:400px;padding:24px}
.auth-card h1{font-size:20px;margin:0 0 16px}
.auth-card form{display:grid;gap:14px}
.auth-card .btn{height:40px;width:100%}
.auth-card .switch{margin:16px 0 0;font-size:13px;color:var(--text-muted);text-align:center}
.form-error{background:var(--danger-soft);color:var(--danger);border:1px solid rgba(239,68,68,.3);border-radius:var(--radius-sm);padding:10px 12px;font-size:13px;margin:0 0 14px}
.form-ok{background:var(--success-soft);color:var(--success);border:1px solid rgba(16,185,129,.3);border-radius:var(--radius-sm);padding:10px 12px;font-size:13px;margin:0 0 14px}
/* custom from/to dates in the filter bar */
.date-range{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:0}
.date-range label{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text-muted)}
.date-range input[type=date]{height:30px;padding:0 8px;font-size:12px;width:auto}
.date-range.active input[type=date]{border-color:var(--accent)}
.close-form{display:flex;gap:6px;align-items:center;margin:0}
.close-form input{width:96px;height:28px;padding:0 8px;font-size:12px}
.close-form input[name=fees]{width:56px}
/* journal */
.journal-list{display:grid;gap:10px}
.journal-card{display:flex;gap:16px;text-decoration:none;color:inherit;padding:16px}
.journal-card:hover{border-color:var(--border-strong)}
.journal-card h3{font-size:15px;margin:0 0 6px}
.journal-card p{font-size:13px;color:var(--text-secondary);margin:0 0 10px;line-height:1.55}
.journal-date{flex:0 0 56px;text-align:center;border-right:1px solid var(--border-subtle);padding-right:14px}
.journal-date b{display:block;font-size:22px;line-height:1.1}
.journal-date span{font-size:11px;color:var(--text-muted);text-transform:uppercase}
.journal-tags{display:flex;flex-wrap:wrap;gap:6px}
.journal-text{white-space:pre-wrap;line-height:1.7;color:var(--text-primary)}
.facts{display:grid;grid-template-columns:auto 1fr;gap:8px 14px;margin:0;font-size:13px}
.facts dt{color:var(--text-muted)}
.facts dd{margin:0}
.side-title{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--text-muted);margin:18px 0 8px}
.lessons{margin:0;padding-left:18px;font-size:13px;line-height:1.6}
.journal-detail-main{grid-column:span 2}
@media (max-width:720px){.journal-detail-main{grid-column:auto}}
@media print{.sidebar,.topbar,.no-print,.coach-disclaimer{display:none!important}.main-area{margin:0!important}.card{break-inside:avoid;box-shadow:none}}
/* portfolio: one full-width card per account, stacked */
.account-stack{display:grid;grid-template-columns:minmax(0,1fr);gap:14px}
/* screenshots */
.upload-bar{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;align-items:end;margin-bottom:14px}
.shots{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}
.shot{margin:0;background:var(--bg-surface);border:1px solid var(--border-subtle);border-radius:var(--radius-lg);overflow:hidden}
.shot img{display:block;width:100%;aspect-ratio:16/10;object-fit:cover;background:var(--bg-surface-alt)}
.shot figcaption{display:grid;grid-template-columns:1fr auto;gap:2px 8px;padding:10px 12px;font-size:13px;align-items:center}
.shot figcaption a{color:var(--text-primary);text-decoration:none}
.shot figcaption form{grid-row:1/3;grid-column:2}
/* first-run guide on the dashboard */
.onboarding{margin-bottom:14px}
.onboarding-progress{height:6px;border-radius:999px;background:var(--bg-surface-alt);overflow:hidden;margin:0 0 16px}
.onboarding-progress span{display:block;height:100%;background:var(--accent);border-radius:999px}
.onboarding-steps{list-style:none;margin:0;padding:0;display:grid;gap:4px}
.onboarding-steps li{display:flex;gap:12px;padding:10px 4px;border-top:1px solid var(--border-subtle)}
.onboarding-steps li:first-child{border-top:0}
.onboarding-steps h3{font-size:14px;margin:2px 0 4px}
.onboarding-steps p{font-size:13px;color:var(--text-secondary);margin:0}
.onboarding-steps li.done h3{color:var(--text-muted);text-decoration:line-through}
.onboarding-steps li.done p{display:none}
.step-mark{flex:0 0 26px;height:26px;border-radius:50%;display:grid;place-items:center;font-size:12px;font-weight:700;border:1px solid var(--border-strong);color:var(--text-secondary)}
.onboarding-steps li.done .step-mark{background:var(--success-soft);border-color:rgba(16,185,129,.4);color:var(--success)}
.step-links{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
/* public site: landing, pricing, legal and error pages */
.pub-nav{position:sticky;top:0;z-index:10;display:flex;align-items:center;gap:20px;max-width:1120px;margin:0 auto;padding:14px 16px;background:var(--bg-main)}
.pub-nav .sidebar-brand{border:none;margin:0;padding:0;text-decoration:none;color:inherit}
.pub-links{display:flex;gap:18px;margin-left:12px}
.pub-links a{color:var(--text-secondary);text-decoration:none;font-size:14px}
.pub-links a:hover,.pub-links a.active{color:var(--text-primary)}
.pub-actions{margin-left:auto;display:flex;gap:8px}
.pub-main{max-width:1120px;margin:0 auto;padding:0 16px 64px}
.hero{padding:72px 0 48px;max-width:760px}
.hero .eyebrow,.pub-section .eyebrow{color:var(--accent);font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;margin:0 0 12px}
.hero h1{font-size:clamp(32px,5vw,52px);line-height:1.08;letter-spacing:-.02em;margin:0 0 18px}
.hero p.lede{font-size:18px;line-height:1.6;color:var(--text-secondary);margin:0 0 28px}
.hero .cta{display:flex;gap:10px;flex-wrap:wrap}
.hero .cta .btn,.pub-section .cta .btn{height:42px;padding:0 20px;font-size:14px}
.pub-section{padding:40px 0;border-top:1px solid var(--border-subtle)}
.pub-section h2{font-size:26px;letter-spacing:-.01em;margin:0 0 10px}
.pub-section > p{color:var(--text-secondary);max-width:680px;line-height:1.6;margin:0 0 24px}
.feature-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}
.feature{background:var(--bg-surface);border:1px solid var(--border-subtle);border-radius:var(--radius-lg);padding:20px}
.feature h3{font-size:16px;margin:0 0 8px}
.feature p,.feature li{color:var(--text-secondary);font-size:14px;line-height:1.55;margin:0}
.feature ul{margin:8px 0 0;padding-left:18px;display:grid;gap:4px}
.broker-list{display:flex;flex-wrap:wrap;gap:8px}
.broker-list span{border:1px solid var(--border-subtle);border-radius:999px;padding:6px 14px;font-size:13px;color:var(--text-secondary);background:var(--bg-surface)}
.price-card{max-width:440px;background:var(--bg-surface);border:1px solid var(--brand-border);border-radius:var(--radius-lg);padding:28px}
.price-card .amount{font-size:40px;font-weight:700;margin:8px 0 4px}
.price-card ul{padding-left:18px;color:var(--text-secondary);line-height:1.8;margin:16px 0 24px}
.prose{max-width:760px;padding:48px 0;line-height:1.7;color:var(--text-secondary)}
.prose h1{color:var(--text-primary);font-size:34px;letter-spacing:-.01em;margin:0 0 6px}
.prose h2{color:var(--text-primary);font-size:20px;margin:32px 0 8px}
.prose .updated{color:var(--text-muted);font-size:13px;margin:0 0 24px}
.prose a{color:var(--accent)}
.prose table{width:100%;border-collapse:collapse;font-size:14px;margin:8px 0}
.prose th,.prose td{text-align:left;border-bottom:1px solid var(--border-subtle);padding:8px 6px;vertical-align:top}
.notice{border:1px solid rgba(245,158,11,.35);background:var(--warning-soft);color:var(--text-primary);border-radius:var(--radius-md);padding:12px 14px;font-size:14px}
.site-footer{border-top:1px solid var(--border-subtle);margin-top:24px}
.site-footer .inner{max-width:1120px;margin:0 auto;padding:28px 16px;display:grid;gap:14px;color:var(--text-muted);font-size:13px;line-height:1.6}
.site-footer nav{display:flex;flex-wrap:wrap;gap:16px}
.site-footer a{color:var(--text-secondary);text-decoration:none}
.site-footer a:hover{color:var(--text-primary)}
.error-page{padding:96px 0;max-width:560px}
.error-page .code{font-family:var(--font-mono);color:var(--accent);font-size:14px;margin:0 0 10px}
.error-page h1{font-size:32px;margin:0 0 12px}
.error-page p{color:var(--text-secondary);line-height:1.6;margin:0 0 24px}
@media (max-width:720px){.pub-links{display:none}.hero{padding:40px 0 32px}.hero p.lede{font-size:16px}}
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

ROW_LINK_SCRIPT = """
document.addEventListener("click", function (event) {
  var row = event.target.closest("tr[data-href]");
  if (!row || event.target.closest("a,button,input,select,form")) return;
  if (event.metaKey || event.ctrlKey) { window.open(row.dataset.href, "_blank"); return; }
  window.location.href = row.dataset.href;
});
"""

NAV_ICONS: dict[str, str] = {
    "dashboard": '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>',
    "portfolio": '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 17h7"/></svg>',
    "trades": '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 17l6-6 4 4 8-8"/><path d="M14 7h7v7"/></svg>',
    "positions": '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>',
    "transactions": '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M7 4v16M3 8l4-4 4 4M17 20V4M13 16l4 4 4-4"/></svg>',
    "reports": '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 13h7M9 17h5"/></svg>',
    "journal": '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 5h16v14H4z"/><path d="M8 5v14M8 9h4"/></svg>',
    "analytics": '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19V5M10 19V9M16 19v-6M22 19V3"/></svg>',
    "coaching": '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3a7 7 0 0 1 7 7c0 3-2 5-4 6l-1 5H10l-1-5c-2-1-4-3-4-6a7 7 0 0 1 7-7z"/></svg>',
    "accounts": '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="6" width="18" height="13" rx="2"/><path d="M3 10h18M8 15h3"/></svg>',
    "settings": '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"/></svg>',
}


def sidebar_nav(active: str) -> str:
    items = [
        ("Dashboard", "/dashboard", "dashboard"),
        ("Portfolio", "/dashboard/portfolio", "portfolio"),
        ("Trades", "/trades", "trades"),
        ("Positions", "/positions", "positions"),
        ("Transactions", "/transactions", "transactions"),
        ("Journal", "/journal", "journal"),
        ("Analytics", "/analytics", "analytics"),
        ("Reports", "/reports/performance", "reports"),
        ("AI Coaching", "/coaching", "coaching"),
        ("Accounts", "/settings/accounts", "accounts"),
        ("Import", "/settings/import", "settings"),
    ]
    links = []
    for label, href, key in items:
        icon = NAV_ICONS.get(key, "")
        cls = "active" if key == active else ""
        links.append(f'<a class="{cls}" href="{href}">{icon}<span>{escape(label)}</span></a>')
    return "".join(links)


def coaching_notice(active: str) -> str:
    """AI coaching reviews past trades; say plainly on every coaching page that it is not advice."""
    if active != "coaching":
        return ""
    return (
        '<p class="hint coach-disclaimer">AI coaching is generated automatically from your own trade history. '
        'It can be wrong, and it is not financial advice or a recommendation to trade. '
        '<a href="/legal/risk">Risk disclaimer</a></p>'
    )


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
    del show_range  # the date range now lives in each page's filter bar
    return inject_csrf(f"""<!doctype html>
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
            <a class="btn btn-primary" href="/trades/new"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" width="14" height="14" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>Log trade</a>
            <button type="button" class="btn theme-toggle" id="theme-toggle" aria-label="Switch color theme">Theme</button>
            <details class="user-menu">
              <summary class="avatar" aria-label="Account menu for {escape(user_name)}">{initials}</summary>
              <div class="user-menu-panel">
                <div class="user-menu-name">{escape(user_name)}</div>
                <a href="/settings/profile">Profile</a>
                <a href="/settings/accounts">Trading accounts</a>
                <a href="/settings/security">Security</a>
                <a href="/settings/data">Your data</a>
                <form method="post" action="/logout"><button type="submit">Sign out</button></form>
              </div>
            </details>
          </div>
        </header>
        <main class="page-content">{body}{coaching_notice(active)}</main>
      </div>
    </div>
    <script>{THEME_TOGGLE_SCRIPT}{ROW_LINK_SCRIPT}</script>
  </body>
</html>""")


def auth_page(title: str, body: str) -> str:
    """Centred single-card layout for sign-in and sign-up (no app navigation)."""
    return inject_csrf(f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{escape(title)} · TradzLog</title>
    <style>{CSS}</style>
    <script>{THEME_SCRIPT}</script>
  </head>
  <body>
    <main class="auth-wrap">
      <div class="sidebar-brand auth-brand"><div class="logo">TZ</div><div class="name">TRADZLOG</div></div>
      <section class="card auth-card">
        <h1>{escape(title)}</h1>
        {body}
      </section>
    </main>
  </body>
</html>""")

RISK_NOTICE = (
    "TradzLog is a journal and analytics tool. Nothing on this site is financial advice, and past results "
    "do not predict future returns. Trading carries a high risk of losing money."
)


def public_page(title: str, body: str, *, active: str = "", signed_in: bool = False, description: str = "") -> str:
    """Layout for pages visitors can see without signing in: top navigation, content, legal footer."""
    links = "".join(
        f'<a class="{"active" if key == active else ""}" href="{href}">{label}</a>'
        for label, href, key in (("Features", "/features", "features"), ("Pricing", "/pricing", "pricing"), ("Security", "/security", "security"))
    )
    actions = (
        '<a class="btn btn-primary" href="/dashboard">Open dashboard</a>'
        if signed_in
        else '<a class="btn btn-ghost" href="/login">Sign in</a><a class="btn btn-primary" href="/signup">Get started</a>'
    )
    meta = f'<meta name="description" content="{escape(description)}" />' if description else ""
    return inject_csrf(f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{escape(title)} · TradzLog</title>
    {meta}
    <style>{CSS}</style>
    <script>{THEME_SCRIPT}</script>
  </head>
  <body>
    <header class="pub-nav">
      <a class="sidebar-brand" href="/"><div class="logo">TZ</div><div class="name">TRADZLOG</div></a>
      <nav class="pub-links" aria-label="Site">{links}</nav>
      <div class="pub-actions">{actions}</div>
    </header>
    <main class="pub-main">{body}</main>
    <footer class="site-footer">
      <div class="inner">
        <nav aria-label="Legal">
          <a href="/legal/terms">Terms</a><a href="/legal/privacy">Privacy</a><a href="/legal/cookies">Cookies</a>
          <a href="/legal/risk">Risk disclaimer</a><a href="/security">Security</a>
        </nav>
        <p style="margin:0">{escape(RISK_NOTICE)}</p>
      </div>
    </footer>
  </body>
</html>""")


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
