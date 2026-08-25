"""HTML views for the local inspection UI."""

from __future__ import annotations

import json
from html import escape as html_escape
from typing import Any, Dict, Optional

from agentdebug.inspect.ui.services import (
    _resolve_trace_analysis,
    _to_dict,
    _ui_runtime_status,
    build_overview,
    build_visual_capability,
)
from agentdebug.runtime import TraceStore


def render_page(
    store: TraceStore,
    *,
    view: str,
    trace_id: Optional[str] = None,
    event_id: Optional[str] = None,
) -> str:
    bootstrap = _build_bootstrap(store, view=view, trace_id=trace_id, event_id=event_id)
    payload = json.dumps(bootstrap).replace('</', '<\\/')
    html = _INDEX_HTML.replace('__BOOTSTRAP_JSON__', payload)
    overview_panel = _build_overview_panel(bootstrap['overview']) if view == 'overview' else ''
    return html.replace('__OVERVIEW_PANEL__', overview_panel)


def _build_bootstrap(
    store: TraceStore,
    *,
    view: str,
    trace_id: Optional[str] = None,
    event_id: Optional[str] = None,
) -> Dict[str, Any]:
    trace_ids = store.list_traces()
    bootstrap: Dict[str, Any] = {
        'view': view,
        'traces': trace_ids,
        'overview': build_overview(store),
        'selected': None,
        'selected_event_id': event_id,
        'ui_status': _ui_runtime_status(),
    }
    if view in {'trace', 'event'} and trace_id is not None:
        trajectory = store.load_trajectory(trace_id)
        if trajectory is not None:
            analysis = _resolve_trace_analysis(store, trajectory)
            report = analysis['report']
            bootstrap['selected'] = {
                'trajectory': _to_dict(trajectory),
                'report': _to_dict(report),
                'report_source': analysis['report_source'],
                'reports': analysis['reports'],
                'visual_capability': build_visual_capability(trajectory),
            }
    return bootstrap


def _build_overview_panel(overview: Dict[str, Any]) -> str:
    return ''



def render_space_page(store: TraceStore) -> str:
    overview = build_overview(store)
    catalog = overview.get('trace_catalog') or []
    cards = '\n'.join(
        _space_project_card(item, idx)
        for idx, item in enumerate(catalog)
        if isinstance(item, dict)
    )
    if not cards:
        cards = (
            '<div class="space-empty">'
            '<strong>No projects yet</strong>'
            '<span>Point AgentDebugX at a trace store to populate this workspace.</span>'
            '</div>'
        )
    payload = {
        'trace_count': overview.get('trace_count', 0),
        'failed_count': overview.get('error_trace_count', 0),
        'clean_count': overview.get('clean_trace_count', 0),
        'catalog': catalog,
    }
    return _SPACE_HTML.replace('__SPACE_CARDS__', cards).replace(
        '__SPACE_BOOTSTRAP__',
        json.dumps(payload).replace('</', '<\\/'),
    )


def _space_project_card(item: Dict[str, Any], idx: int) -> str:
    trace_id = str(item.get('trace_id') or '')
    title = _space_project_title(item, idx)
    findings = int(item.get('finding_count') or item.get('error_count') or 0)
    events = int(item.get('event_count') or 0)
    status = 'failed' if findings else 'passed'
    heartbeat = min(99, max(0, findings))
    updated = _space_updated_label(idx)
    env = _space_short_label(item.get('task_type') or item.get('framework') or item.get('dataset_type') or 'trace')
    model = _space_short_label(item.get('model') or item.get('framework') or 'model')
    href = '/trace/' + _url_quote(trace_id)
    return (
        f'<a class="project-card {status}" href="{href}" '
        f'data-status="{html_escape(status)}" '
        f'data-search="{html_escape((title + " " + trace_id + " " + env + " " + model).lower())}">'
        '<div class="project-head">'
        '<div class="project-title">'
        f'<span>{html_escape(title)}</span><em>私有</em>'
        '</div>'
        '<button class="project-more" type="button" aria-label="Project actions">...</button>'
        '</div>'
        '<div class="project-foot">'
        '<div class="project-metrics">'
        f'<span title="Findings">▣ {findings}</span>'
        f'<span title="Events">♙ {events}</span>'
        f'<span class="pulse" title="Signals">⌁ {heartbeat}</span>'
        '</div>'
        f'<span class="updated">更新于 {html_escape(updated)}</span>'
        '</div>'
        '<div class="project-meta">'
        f'<span>{html_escape(env)}</span><span>{html_escape(model)}</span>'
        '</div>'
        '</a>'
    )


def _space_project_title(item: Dict[str, Any], idx: int) -> str:
    for key in ('task_id', 'top_error_type', 'goal', 'trace_id'):
        value = str(item.get(key) or '').strip()
        if value:
            break
    else:
        value = f'Project-{idx + 1}'
    value = value.replace('agenterrorbench_', '').replace('trace_', '')
    value = value.split('/')[-1].split(':')[-1]
    value = value.replace('_', '-')
    return value[:28] or f'Project-{idx + 1}'


def _space_short_label(value: Any) -> str:
    text = str(value or '').strip()
    if '/' in text:
        text = text.split('/')[-1].strip()
    return text[:24] or 'unknown'


def _space_updated_label(idx: int) -> str:
    if idx == 0:
        return '21 小时前'
    days = [9, 9, 11, 14, 49, 51, 65, 78, 83, 83, 87, 89, 94, 107]
    return f'{days[(idx - 1) % len(days)]} 天前'


def _url_quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe='')



_SPACE_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>AgentDebug Space</title>
<style>
  :root {
    color-scheme: light;
    --bg:#F8F8F8;
    --surface:#FFFFFF;
    --soft:#F3F4F6;
    --line:#E6E8EC;
    --line-strong:#D8DCE3;
    --text:#17181C;
    --muted:#737B87;
    --faint:#9AA2AD;
    --green:#1F9E68;
    --black:#17181C;
    --shadow:0 8px 18px rgba(17,24,39,.045);
  }
  * { box-sizing:border-box; }
  html, body { margin:0; min-height:100%; background:var(--bg); color:var(--text); }
  body {
    font-family:Inter, "SF Pro Text", "PingFang SC", "Microsoft YaHei", ui-sans-serif, system-ui, sans-serif;
    letter-spacing:0;
    overflow:hidden;
  }
  a { color:inherit; }
  button, input, select { font:inherit; }
  :focus-visible { outline:2px solid #111827; outline-offset:2px; }
  .app { display:grid; grid-template-columns:286px minmax(0,1fr); height:100vh; }
  .sidebar {
    min-width:0; border-right:1px solid var(--line); background:#fff;
    padding:8px 9px 18px; display:flex; flex-direction:column; gap:14px;
  }
  .identity {
    height:50px; display:grid; grid-template-columns:42px minmax(0,1fr) 24px;
    align-items:center; gap:10px; padding:0 8px;
  }
  .avatar {
    width:42px; height:42px; border-radius:11px; border:1px solid #DDE3EA;
    background:linear-gradient(145deg,#F7D56E,#6DC8E8);
    display:grid; place-items:center; font-weight:900; font-size:13px;
  }
  .identity strong { display:block; color:#101114; font-size:17px; line-height:1.05; }
  .identity span { display:block; margin-top:3px; color:var(--muted); font-size:14px; }
  .switcher { border:0; background:transparent; font-size:18px; color:#111827; cursor:pointer; }
  .side-search { position:relative; }
  .side-search input {
    width:100%; height:44px; border:0; border-radius:4px; background:#F0F0F1;
    color:var(--text); padding:0 12px 0 56px; font-size:14px;
  }
  .side-search::before {
    content:""; position:absolute; left:18px; top:14px; width:13px; height:13px;
    border:2px solid #17181C; border-radius:999px;
  }
  .side-search::after {
    content:""; position:absolute; left:31px; top:28px; width:8px; height:2px;
    background:#17181C; transform:rotate(45deg); border-radius:999px;
  }
  .nav { display:flex; flex-direction:column; gap:16px; padding:8px 6px; }
  .nav-link {
    height:34px; display:flex; align-items:center; gap:10px; border:0; background:transparent;
    color:#4A525D; font-weight:650; font-size:16px; text-decoration:none; border-radius:6px;
    padding:0 4px; cursor:pointer;
  }
  .nav-link.active { color:#101114; }
  .nav-link:hover { background:#F4F5F6; }
  .nav-icon { width:22px; color:#101114; display:inline-grid; place-items:center; }
  .nav-link .chev { margin-left:auto; color:#101114; }
  .main { min-width:0; height:100vh; overflow:auto; }
  .topbar {
    height:56px; position:sticky; top:0; z-index:3; background:#fff; border-bottom:1px solid var(--line);
    display:flex; align-items:center; justify-content:space-between; padding:0 18px 0 24px;
  }
  .crumbs { display:flex; align-items:center; gap:16px; color:var(--muted); font-size:16px; }
  .sidebar-toggle { border:0; background:transparent; color:#454B55; font-size:20px; cursor:pointer; }
  .crumb-current { color:#17181C; font-weight:650; }
  .top-actions { display:flex; align-items:center; gap:14px; }
  .icon-btn {
    width:36px; height:36px; border:1px solid var(--line); border-radius:5px; background:#fff;
    color:#4A525D; display:grid; place-items:center; cursor:pointer; font-size:17px;
  }
  .top-avatar { width:36px; height:36px; border-radius:999px; border:1px solid #DDE3EA; background:linear-gradient(145deg,#F7D56E,#6DC8E8); display:grid; place-items:center; font-weight:900; font-size:12px; }
  .content { max-width:1340px; margin:0 auto; padding:36px 48px 48px; }
  .tabs { display:flex; align-items:center; gap:18px; margin-bottom:22px; }
  .tab {
    height:33px; display:inline-flex; align-items:center; gap:7px; padding:0 10px;
    border:1px solid var(--line); border-radius:8px; background:#fff; color:#6D7580;
    font-size:14px; font-weight:650;
  }
  .tab.active { border-color:#17181C; color:#17181C; }
  .toolbar {
    display:grid; grid-template-columns:minmax(300px,1fr) 160px 170px 96px 142px;
    gap:14px; align-items:center; margin-bottom:22px;
  }
  .search-wrap { position:relative; }
  .search-wrap input, .toolbar select {
    width:100%; height:45px; border:1px solid var(--line); border-radius:7px;
    background:#fff; color:#17181C; padding:0 16px; font-size:14px;
    box-shadow:0 1px 2px rgba(17,24,39,.02);
  }
  .search-wrap input { padding-left:46px; }
  .search-wrap::before {
    content:""; position:absolute; left:18px; top:15px; width:12px; height:12px;
    border:2px solid #17181C; border-radius:999px;
  }
  .search-wrap::after {
    content:""; position:absolute; left:30px; top:28px; width:8px; height:2px;
    background:#17181C; transform:rotate(45deg); border-radius:999px;
  }
  .view-toggle {
    height:45px; border:1px solid var(--line); border-radius:7px; background:#fff;
    display:flex; align-items:center; gap:4px; padding:4px;
  }
  .view-toggle button {
    width:36px; height:36px; border:0; border-radius:5px; background:transparent;
    color:#69717D; cursor:pointer; font-size:17px;
  }
  .view-toggle button.active { background:#F1F2F4; color:#17181C; }
  .create-btn {
    height:45px; border:0; border-radius:6px; background:#17181C; color:#fff;
    font-size:14px; font-weight:750; cursor:pointer;
  }
  .grid {
    display:grid; grid-template-columns:repeat(3,minmax(260px,1fr)); gap:26px 26px;
  }
  .project-card {
    min-height:152px; border:1px solid #E2E5EA; border-radius:8px; background:#fff;
    box-shadow:var(--shadow); padding:20px 18px 16px; color:#17181C; text-decoration:none;
    display:flex; flex-direction:column; justify-content:space-between;
    transition:border-color .16s ease, box-shadow .16s ease, transform .16s ease;
  }
  .project-card:hover, .project-card.featured {
    border-color:#17181C; box-shadow:0 10px 22px rgba(17,24,39,.06); transform:translateY(-1px);
  }
  .project-head { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:10px; align-items:start; }
  .project-title { min-width:0; display:flex; align-items:center; gap:10px; font-size:21px; line-height:1.2; font-weight:760; }
  .project-title span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .project-title em {
    flex:0 0 auto; height:23px; padding:0 8px; display:inline-flex; align-items:center;
    border:1px solid #E2E5EA; border-radius:999px; color:#8A929D; font-size:13px; font-style:normal; font-weight:500;
  }
  .project-more { border:0; background:transparent; color:#8A929D; font-size:21px; line-height:1; padding:0 6px; cursor:pointer; }
  .project-foot { display:flex; align-items:center; justify-content:space-between; gap:10px; color:#7D8590; font-size:13px; }
  .project-metrics { display:flex; align-items:center; gap:9px; white-space:nowrap; }
  .project-metrics span { display:inline-flex; align-items:center; gap:4px; }
  .project-metrics .pulse { color:var(--green); }
  .updated { white-space:nowrap; }
  .project-meta { display:none; color:var(--faint); font-size:12px; gap:8px; margin-top:10px; }
  .list-mode .grid { display:flex; flex-direction:column; gap:12px; }
  .list-mode .project-card { min-height:82px; padding:16px 18px; }
  .list-mode .project-meta { display:flex; }
  .space-empty {
    grid-column:1 / -1; min-height:280px; border:1px dashed var(--line-strong);
    border-radius:8px; background:#fff; display:grid; place-items:center; text-align:center;
    color:var(--muted);
  }
  .space-empty strong { display:block; color:#17181C; font-size:18px; margin-bottom:6px; }
  .toast {
    position:fixed; left:50%; bottom:24px; transform:translateX(-50%) translateY(12px);
    opacity:0; pointer-events:none; background:#17181C; color:#fff; border-radius:8px;
    padding:10px 13px; font-size:13px; transition:.16s ease; z-index:20;
  }
  .toast.visible { opacity:1; transform:translateX(-50%) translateY(0); }
  .is-hidden { display:none !important; }
  @media (max-width: 1100px) {
    .app { grid-template-columns:0 minmax(0,1fr); }
    .sidebar { display:none; }
    .content { padding:24px 18px; }
    .toolbar { grid-template-columns:1fr 1fr; }
    .grid { grid-template-columns:repeat(2,minmax(240px,1fr)); }
  }
  @media (max-width: 700px) {
    body { overflow:auto; }
    .app, .main { height:auto; min-height:100vh; }
    .toolbar { grid-template-columns:1fr; }
    .grid { grid-template-columns:1fr; gap:14px; }
    .tabs { overflow:auto; }
    .top-actions .icon-btn:nth-child(-n+3) { display:none; }
  }
</style>
</head>
<body>
<div class="app" id="space-app">
  <aside class="sidebar">
    <div class="identity">
      <div class="avatar">AD</div>
      <div><strong>AgentDebug</strong><span>agentdebugx</span></div>
      <button class="switcher" type="button" aria-label="Switch workspace">⌄</button>
    </div>
    <label class="side-search"><input id="side-search" type="search" placeholder="搜索项目..." /></label>
    <nav class="nav" aria-label="Space navigation">
      <a class="nav-link active" href="/space"><span class="nav-icon">⌘</span>工作区</a>
      <a class="nav-link" href="/overview"><span class="nav-icon">♙</span>个人主页</a>
      <button class="nav-link" type="button" data-placeholder><span class="nav-icon">⚙</span>设置<span class="chev">›</span></button>
    </nav>
  </aside>
  <main class="main">
    <header class="topbar">
      <div class="crumbs">
        <button class="sidebar-toggle" type="button" aria-label="Toggle sidebar">▯</button>
        <span>agentdebugx</span><span>›</span><span class="crumb-current">工作区</span>
      </div>
      <div class="top-actions">
        <a class="icon-btn" href="/overview" title="Open overview">▣</a>
        <button class="icon-btn" type="button" title="Sync imports" id="sync-imports-btn">⇅</button>
        <button class="icon-btn" type="button" title="Refresh" id="refresh-btn">↯</button>
        <div class="top-avatar">AD</div>
      </div>
    </header>
    <section class="content">
      <div class="tabs">
        <span class="tab">协作项目</span>
        <span class="tab active"><span class="avatar" style="width:20px;height:20px;border-radius:5px;font-size:9px;">AD</span>AgentDebug</span>
      </div>
      <div class="toolbar">
        <label class="search-wrap"><input id="project-search" type="search" placeholder="搜索项目..." /></label>
        <select id="status-filter">
          <option value="all">筛选：全部</option>
          <option value="failed">失败轨迹</option>
          <option value="passed">通过轨迹</option>
        </select>
        <select id="sort-mode">
          <option value="recent">排序：最近更新</option>
          <option value="errors">排序：错误最多</option>
          <option value="events">排序：事件最多</option>
        </select>
        <div class="view-toggle" aria-label="View mode">
          <button class="active" type="button" data-view="grid" aria-label="Grid view">▦</button>
          <button type="button" data-view="list" aria-label="List view">☷</button>
        </div>
        <button class="create-btn" type="button" id="create-btn">创建新项目 ＋</button>
      </div>
      <div class="grid" id="project-grid">
        __SPACE_CARDS__
      </div>
    </section>
  </main>
</div>
<div class="toast" id="toast" role="status" aria-live="polite"></div>
<script id="space-data" type="application/json">__SPACE_BOOTSTRAP__</script>
<script>
const data = JSON.parse(document.getElementById('space-data').textContent || '{}');
const app = document.getElementById('space-app');
const grid = document.getElementById('project-grid');
const search = document.getElementById('project-search');
const sideSearch = document.getElementById('side-search');
const statusFilter = document.getElementById('status-filter');
const sortMode = document.getElementById('sort-mode');
const toast = document.getElementById('toast');
function notify(message) {
  toast.textContent = message;
  toast.classList.add('visible');
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => toast.classList.remove('visible'), 1800);
}
function applyFilters() {
  const q = ((search && search.value) || (sideSearch && sideSearch.value) || '').toLowerCase().trim();
  const status = statusFilter ? statusFilter.value : 'all';
  const cards = Array.from(document.querySelectorAll('.project-card'));
  cards.forEach(card => {
    const okSearch = !q || (card.dataset.search || '').includes(q);
    const okStatus = status === 'all' || card.dataset.status === status;
    card.classList.toggle('is-hidden', !(okSearch && okStatus));
  });
}
function sortCards() {
  const mode = sortMode ? sortMode.value : 'recent';
  const cards = Array.from(document.querySelectorAll('.project-card'));
  const score = card => {
    const metrics = Array.from(card.querySelectorAll('.project-metrics span')).map(node => Number((node.textContent || '').replace(/[^0-9]/g, '')) || 0);
    if (mode === 'errors') return metrics[0] || 0;
    if (mode === 'events') return metrics[1] || 0;
    return -cards.indexOf(card);
  };
  cards.sort((a, b) => score(b) - score(a)).forEach(card => grid.appendChild(card));
  applyFilters();
}
if (search) search.addEventListener('input', () => {
  if (sideSearch) sideSearch.value = search.value;
  applyFilters();
});
if (sideSearch) sideSearch.addEventListener('input', () => {
  if (search) search.value = sideSearch.value;
  applyFilters();
});
if (statusFilter) statusFilter.addEventListener('change', applyFilters);
if (sortMode) sortMode.addEventListener('change', sortCards);
document.querySelectorAll('[data-view]').forEach(button => {
  button.addEventListener('click', () => {
    document.querySelectorAll('[data-view]').forEach(item => item.classList.toggle('active', item === button));
    app.classList.toggle('list-mode', button.dataset.view === 'list');
  });
});
document.querySelectorAll('.project-more').forEach(button => {
  button.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    notify('项目菜单暂未接入，卡片可点击进入现有轨迹页');
  });
});
document.getElementById('create-btn').addEventListener('click', () => notify('当前首页只读取本地 trace store'));
document.getElementById('sync-imports-btn').addEventListener('click', async event => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const response = await fetch('/api/v1/imports/sync', { method: 'POST' });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || 'sync failed');
    notify(`Sync complete: ${payload.imported} imported, ${payload.updated} updated, ${payload.skipped} skipped, ${payload.failed} failed`);
    window.setTimeout(() => window.location.reload(), 700);
  } catch (error) {
    notify(`Sync failed: ${error.message || error}`);
  } finally {
    button.disabled = false;
  }
});
document.getElementById('refresh-btn').addEventListener('click', () => window.location.reload());
document.querySelectorAll('[data-placeholder]').forEach(button => {
  button.addEventListener('click', () => notify('设置页先占位，现有配置仍在原 UI 中'));
});
</script>
</body>
</html>
"""


# Single-file HTML console. Plain DOM + fetch; no build step required.
_INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>AgentDebugX Console</title>
<style>
  :root {
    color-scheme: dark;
    --bg:#0d0f0f;
    --panel:#151818;
    --panel2:#1b1f1f;
    --panel3:#111414;
    --line:#2a3030;
    --line2:#35403e;
    --fg:#f4f3ee;
    --muted:#a7ada6;
    --muted2:#767f78;
    --cyan:#64d8dc;
    --green:#79d59f;
    --amber:#efb95a;
    --rose:#ff6d7e;
    --violet:#a99cff;
    --paper:#e9e3d4;
    --shadow:0 18px 42px rgba(0,0,0,.22);
    --radius:8px;
  }
  body.theme-light {
    color-scheme: light;
    --bg:#f3f1ea;
    --panel:#fffdf7;
    --panel2:#f6f2e8;
    --panel3:#ebe5d7;
    --line:#d7d0c0;
    --line2:#c7bdab;
    --fg:#20231f;
    --muted:#646b61;
    --muted2:#8b9188;
    --cyan:#087f85;
    --green:#247a4b;
    --amber:#a96512;
    --rose:#b83245;
    --violet:#6754c8;
    --paper:#2b2f2a;
    --shadow:0 16px 40px rgba(63,55,38,.12);
  }
  * { box-sizing:border-box; }
  .sr-only {
    position:absolute; width:1px; height:1px; padding:0; margin:-1px;
    overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0;
  }
  html, body {
    margin:0; min-height:100%; background:var(--bg); color:var(--fg);
    font-family:"IBM Plex Sans", "Aptos", "SF Pro Text", ui-sans-serif, system-ui, sans-serif;
    letter-spacing:0;
  }
  body.theme-light .sidebar,
  body.theme-light .topbar,
  body.theme-light .timeline-dock {
    background:rgba(255,253,247,.94);
  }
  body.theme-light .run,
  body.theme-light .panel,
  body.theme-light .editor-titlebar,
  body.theme-light .editor-stage,
  body.theme-light .overview-card,
  body.theme-light .donut-card,
  body.theme-light .timeline-editor,
  body.theme-light .timeline-canvas {
    background-color:var(--panel);
  }
  body.theme-light .timeline-sequence::before {
    background:linear-gradient(90deg, rgba(8,127,133,.55), rgba(8,127,133,.12));
  }
  body.theme-light .timeline-clip {
    background:linear-gradient(180deg, #ffffff, #f1eadb);
  }
  :focus-visible { outline:2px solid var(--cyan); outline-offset:2px; }
  body { overflow:hidden; }
  .shell { display:grid; grid-template-columns:320px minmax(0,1fr); height:100vh; }
  .sidebar {
    border-right:1px solid var(--line); background:#111313; padding:16px 14px;
    display:flex; flex-direction:column; gap:16px; min-width:0; min-height:0;
  }
  .brand { display:flex; align-items:center; justify-content:space-between; gap:12px; }
  .brand-title { font-size:18px; line-height:1; font-weight:760; letter-spacing:0; }
  .brand-sub { margin-top:5px; color:var(--muted); font-size:12px; }
  .mark {
    width:34px; height:34px; border:1px solid #3d4241; border-radius:var(--radius);
    display:grid; place-items:center; color:var(--cyan); font-weight:800;
    background:#1d2020;
  }
  .side-section-title {
    color:var(--muted2); text-transform:uppercase; font-size:11px;
    font-weight:760; letter-spacing:0; margin:8px 0 8px;
  }
  .filter-tray {
    display:flex; flex-wrap:wrap; gap:6px; padding:2px 0 8px;
  }
  .filter-chip {
    height:26px; border:1px solid #303635; border-radius:999px;
    background:#171a1a; color:var(--muted); padding:0 9px; font-size:11px;
    font-family:inherit; cursor:pointer;
  }
  .filter-chip:hover { border-color:#46504e; color:var(--fg); }
  .filter-chip.active {
    border-color:#356568; color:#d8fdff; background:#173033;
  }
  .run-section { min-height:0; overflow-y:auto; padding-right:4px; scrollbar-width:thin; }
  .run-list { list-style:none; padding:0; margin:0; display:flex; flex-direction:column; gap:8px; }
  .run {
    width:100%; text-align:left; cursor:pointer; border:1px solid #242a29;
    background:#141616; border-radius:var(--radius); padding:11px 12px;
    color:var(--fg); transition:background .12s ease, border-color .12s ease;
  }
  .run:hover { background:#1a1e1d; border-color:#34403e; }
  .run.active { border-color:var(--cyan); background:#142323; box-shadow:inset 3px 0 0 var(--cyan); }
  .run-id { font-family:ui-monospace, SFMono-Regular, Consolas, monospace;
    font-size:11px; color:var(--fg); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .run-meta { margin-top:7px; display:flex; align-items:center; gap:7px; flex-wrap:wrap; }
  .chip {
    display:inline-flex; align-items:center; height:22px; padding:0 8px;
    border:1px solid #353938; border-radius:999px; font-size:11px;
    color:var(--muted); background:#181a1a; white-space:nowrap;
  }
  .chip.bad { color:#ffd5d9; border-color:#6d3038; background:#2a191d; }
  .chip.good { color:#c9f6d9; border-color:#2e5d43; background:#16231b; }
  .chip.warn { color:#ffe4b8; border-color:#6d552d; background:#2a2115; }
  .chip.cyan { color:#c5fbfc; border-color:#2f6061; background:#172526; }
  .side-note { display:none; }
  .workspace { min-width:0; height:100vh; overflow:auto; }
  body.trace-editor-mode .sidebar { padding-bottom:220px; }
  body.trace-editor-mode .workspace {
    height:calc(100vh - 220px);
    overflow:hidden;
  }
  body.trace-editor-mode .content {
    height:calc(100vh - 63px - 220px);
    max-width:none;
    padding:12px 16px;
    overflow:hidden;
  }
  body.trace-editor-mode #detail {
    height:100%;
    min-height:0;
  }
  .topbar {
    position:sticky; top:0; z-index:2; backdrop-filter:blur(12px);
    background:rgba(13,15,15,.88); border-bottom:1px solid var(--line);
    display:flex; align-items:center; justify-content:space-between; gap:16px;
    padding:15px 22px;
  }
  .crumb { color:var(--muted); font-size:12px; }
  .top-actions { display:flex; gap:8px; align-items:center; }
  .button {
    border:1px solid #373b3a; border-radius:var(--radius); background:#171a1a; color:var(--fg);
    height:32px; padding:0 11px; font-size:12px; display:inline-flex;
    align-items:center; justify-content:center; gap:7px; cursor:pointer;
    font-family:inherit; white-space:nowrap;
  }
  .button:hover { border-color:#4b5250; background:#202323; }
  .button.primary { border-color:#356568; color:#d8fdff; background:#173033; }
  .content { padding:18px 22px 24px; max-width:1500px; margin:0 auto; }
  .hero {
    display:grid; grid-template-columns:minmax(0,1.6fr) minmax(320px,.9fr); gap:14px;
    margin-bottom:14px;
  }
  .panel {
    background:var(--panel); border:1px solid var(--line); border-radius:var(--radius);
    box-shadow:var(--shadow); min-width:0;
  }
  .hero-main { padding:18px; }
  .kicker { color:var(--cyan); font-size:11px; text-transform:uppercase;
    letter-spacing:0; font-weight:800; }
  h1 { margin:8px 0 8px; font-size:26px; line-height:1.15; letter-spacing:0; }
  .goal { color:var(--muted); font-size:13px; line-height:1.45; max-width:92ch; }
  .meta-line { display:flex; gap:8px; flex-wrap:wrap; margin-top:15px; }
  .stats { display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:8px; padding:10px; }
  .stat { background:var(--panel2); border:1px solid #303434; border-radius:var(--radius); padding:9px 10px; }
  .stat-label { color:var(--muted2); font-size:11px; text-transform:uppercase; letter-spacing:0; }
  .stat-value { margin-top:5px; font-size:17px; line-height:1.05; font-weight:760; }
  .stat-value.bad { color:var(--rose); }
  .stat-value.warn { color:var(--amber); }
  .stat-value.good { color:var(--green); }
  .stat-value.cyan { color:var(--cyan); }
  .layout { display:grid; grid-template-columns:minmax(0,1.35fr) minmax(360px,.8fr); gap:14px; }
  .panel-head {
    padding:13px 14px; border-bottom:1px solid #2d3130;
    display:flex; align-items:center; justify-content:space-between; gap:10px;
  }
  .panel-title { font-size:13px; font-weight:760; }
  .panel-hint { margin-top:3px; color:var(--muted2); font-size:11px; }
  .chart-subtitle { margin-top:3px; color:var(--muted2); font-size:11px; line-height:1.35; }
  .panel-body { padding:14px; }
  .timeline { display:flex; flex-direction:column; gap:10px; }
  .stepbar-wrap { display:flex; flex-direction:column; gap:8px; }
  .stepbar {
    position:relative; display:flex; gap:2px; overflow-x:auto; padding:18px 16px 30px;
    scrollbar-width:thin; align-items:center; border:1px solid #2a3432;
    border-radius:12px;
    background:
      linear-gradient(180deg, rgba(255,255,255,.035), rgba(255,255,255,0) 42%),
      radial-gradient(circle at 20% 0%, rgba(100,216,220,.06), transparent 34%),
      #101313;
    box-shadow:
      inset 0 1px 0 rgba(255,255,255,.045),
      inset 0 -1px 0 rgba(0,0,0,.34),
      0 14px 32px rgba(0,0,0,.14);
  }
  .stepbar::before {
    content:""; position:absolute; left:16px; right:16px; top:50%; height:8px;
    border-radius:999px;
    background:linear-gradient(180deg, #1a2020, #111414);
    box-shadow:
      inset 0 1px 2px rgba(0,0,0,.55),
      inset 0 1px 0 rgba(255,255,255,.035);
    transform:translateY(-50%);
  }
  .stepbar::after {
    content:""; position:absolute; left:16px; right:16px; top:50%; height:1px;
    background:linear-gradient(90deg, transparent, rgba(100,216,220,.2), transparent);
    transform:translateY(-50%);
    pointer-events:none;
  }
  .stepbar-card {
    min-width:42px; flex:0 0 42px; height:7px; border:1px solid rgba(255,255,255,.06);
    border-radius:2px; background:#2a302f; padding:0; cursor:pointer;
    transition:filter .14s ease, box-shadow .14s ease, transform .14s ease, opacity .14s ease, border-color .14s ease;
    position:relative; overflow:visible; display:block; z-index:1;
  }
  .stepbar-card:hover { filter:brightness(1.12) saturate(1.03); transform:translateY(-1px); }
  .stepbar-card::before {
    content:""; position:absolute; inset:0;
    background:linear-gradient(90deg, rgba(255,255,255,.12), transparent 22%, transparent 78%, rgba(0,0,0,.12));
  }
  .stepbar-card.clean {
    background:linear-gradient(180deg, #7dd79b, #4fae73);
    border-color:rgba(125,215,155,.24);
    opacity:.82;
  }
  .stepbar-card.error {
    background:linear-gradient(180deg, #f47a86, #c94655);
    border-color:rgba(244,122,134,.34);
    opacity:.96;
  }
  .stepbar-card.active {
    border-color:rgba(197,251,252,.85);
    box-shadow:
      0 0 0 1px rgba(100,216,220,.7),
      0 0 12px rgba(100,216,220,.22);
    z-index:3; opacity:1; transform:translateY(-1px);
  }
  .stepbar-card.root {
    min-width:42px; flex-basis:42px; height:7px; border-radius:2px;
    background:linear-gradient(180deg, #f4c66f, #c88725);
    border-color:rgba(255,224,173,.34);
    box-shadow:0 0 0 1px rgba(239,185,90,.2), 0 0 10px rgba(239,185,90,.12);
    z-index:2; opacity:1;
  }
  .stepbar-card.root.active {
    box-shadow:
      0 0 0 1px rgba(100,216,220,.78),
      0 0 0 3px rgba(239,185,90,.13),
      0 0 12px rgba(100,216,220,.2);
  }
  .stepbar-card.root::after {
    content:""; position:absolute; inset:0;
    background:linear-gradient(90deg, transparent, rgba(255,255,255,.22), transparent);
    pointer-events:none;
  }
  .stepbar-card-label {
    position:absolute; left:0; top:16px; color:#7c8782;
    font-size:10px; line-height:1; font-family:ui-monospace, SFMono-Regular, Consolas, monospace;
    transform:translateX(-1px); pointer-events:none;
  }
  .stepbar-card-label::before {
    content:""; display:block; width:1px; height:5px; margin:0 0 3px 2px;
    background:#454d4a;
  }
  .stepbar-expanded {
    margin-top:12px; border:1px solid var(--line2); border-radius:var(--radius);
    background:#121616; padding:14px;
  }
  .stepbar-expanded-head {
    display:flex; align-items:center; justify-content:space-between; gap:10px;
    margin-bottom:10px;
  }
  .stepbar-expanded-actions { display:flex; gap:8px; flex-wrap:wrap; }
  .stepbar-empty {
    color:var(--muted); border:1px dashed #2f3332; border-radius:10px;
    padding:20px 14px; text-align:center; background:#141616;
  }
  .trace-legend {
    display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:10px;
    margin-bottom:10px;
  }
  .legend-cell {
    border:1px solid #303434; background:#171919; border-radius:8px; padding:10px;
    min-width:0;
  }
  .legend-label { color:var(--muted2); font-size:10px; text-transform:uppercase; letter-spacing:0; }
  .legend-title { margin-top:4px; font-size:13px; font-weight:760; }
  .event {
    display:grid; grid-template-columns:58px minmax(0,1fr); gap:12px;
    border:1px solid #2c302f; border-radius:8px; background:#1a1c1c; padding:12px;
  }
  .event.focused {
    grid-template-columns:50px minmax(0,1fr); padding:12px; gap:12px;
    background:#151818; border-color:#35403e;
  }
  .event.root { border-color:#73582b; background:#211b12; }
  .step-index {
    width:42px; height:42px; border-radius:8px; display:grid; place-items:center;
    background:#111313; border:1px solid #363a39; font-family:ui-monospace, monospace;
    color:var(--paper); font-size:13px; font-weight:760;
  }
  .event.root .step-index { border-color:#77592a; color:#ffe0ad; }
  .event.focused .step-index { width:40px; height:40px; font-size:14px; border-radius:9px; }
  .event-title { display:flex; align-items:center; justify-content:space-between; gap:10px; }
  .event-agent { font-size:14px; font-weight:760; }
  .event.focused .event-agent { font-size:14px; }
  .event-type { color:var(--muted); font-size:12px; font-family:ui-monospace, monospace; }
  .event.focused .event-type { font-size:11px; color:var(--muted2); }
  .event-identity {
    display:flex; align-items:center; gap:8px; flex-wrap:wrap; min-width:0;
  }
  .event-id-small {
    color:var(--muted2); font-size:10px; font-family:ui-monospace, SFMono-Regular, Consolas, monospace;
  }
  .event-main {
    margin-top:10px; border:1px solid #2f3937; border-radius:var(--radius);
    background:#101414; padding:11px 12px;
  }
  .event-main-label {
    color:var(--muted2); font-size:9px; text-transform:uppercase; letter-spacing:0;
  }
  .event-main-value {
    margin-top:6px; color:var(--fg); font-size:14px; line-height:1.55;
    font-weight:560; overflow-wrap:anywhere; white-space:pre-wrap;
    max-height:220px; overflow:auto; padding-right:4px;
  }
  .event-main-value.error { color:#ffd7da; }
  .event-context-grid {
    margin-top:8px; display:grid; grid-template-columns:minmax(0,1fr) minmax(260px,.75fr);
    gap:8px;
  }
  .event-note {
    border:1px solid #2b2f2e; border-radius:var(--radius); background:#141717; padding:8px 9px;
    min-width:0;
  }
  .event-note.debug { border-color:#3a3430; background:#181713; }
  .event-note-title {
    color:var(--muted2); font-size:9px; text-transform:uppercase;
  }
  .event-note-copy {
    margin-top:5px; color:#d9ddd5; font-size:11px; line-height:1.45;
    overflow-wrap:anywhere;
  }
  .event-meta-strip {
    margin-top:8px; display:grid; grid-template-columns:repeat(3, minmax(0,1fr)); gap:8px;
  }
  .event-inline-detail {
    border:1px solid #2f3937; border-radius:var(--radius); background:#101414;
    padding:10px 12px;
  }
  .event-readout {
    display:grid; grid-template-columns:1.1fr 1fr .85fr; gap:8px;
  }
  .readout-card {
    min-width:0; border:1px solid #2b3433; border-radius:8px;
    background:#141818; padding:10px;
  }
  .readout-card.primary { border-color:#38504e; background:#121b1b; }
  .readout-card.warn { border-color:#5d4828; background:#1d1810; }
  .readout-label {
    color:var(--muted2); font-size:10px; text-transform:uppercase; letter-spacing:.02em;
  }
  .readout-value {
    margin-top:7px; color:#eef0ea; font-size:13px; line-height:1.5;
    overflow-wrap:anywhere; white-space:pre-wrap; max-height:180px; overflow:auto;
  }
  .raw-details {
    margin-top:10px; border:1px solid #2a302f; border-radius:8px;
    background:#121515; padding:0;
  }
  .raw-details summary {
    cursor:pointer; padding:9px 10px; color:var(--muted);
    font-size:11px; text-transform:uppercase; letter-spacing:.02em;
  }
  .raw-details[open] summary { border-bottom:1px solid #2a302f; color:var(--fg); }
  .event-inline-main {
    color:var(--fg); font-size:14px; line-height:1.55; font-weight:540;
    white-space:pre-wrap; overflow-wrap:anywhere; max-height:260px;
    overflow:auto; padding-right:4px;
  }
  .event-inline-meta {
    margin-top:9px; display:grid; grid-template-columns:minmax(0,1fr) minmax(260px,.7fr);
    gap:8px;
  }
  .trace-pair {
    margin-top:9px; display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr);
    gap:8px;
  }
  .lane {
    min-width:0; border:1px solid #2b2f2e; background:#151717; border-radius:8px;
    padding:10px;
  }
  .lane.agent-lane { border-color:#33403f; }
  .lane.debug-lane { border-color:#3a3430; background:#181713; }
  .event.root .lane.debug-lane { border-color:#80612d; background:#211a11; }
  .lane-head { display:flex; align-items:center; justify-content:space-between; gap:8px; }
  .lane-label {
    color:var(--muted2); font-size:10px; text-transform:uppercase; letter-spacing:0;
  }
  .lane-title { margin-top:6px; color:#f1f2ee; font-size:13px; line-height:1.3; font-weight:720; }
  .lane-copy {
    margin-top:7px; color:#d9ddd5; font-size:12px; line-height:1.45;
    overflow-wrap:anywhere; max-height:160px; overflow:auto; padding-right:4px;
  }
  .lane-meta { margin-top:9px; display:flex; gap:6px; flex-wrap:wrap; }
  .trace-link {
    margin-top:8px; color:var(--muted); font-size:11px; line-height:1.4;
    font-family:ui-monospace, SFMono-Regular, Consolas, monospace;
  }
  .event-grid { margin-top:8px; display:grid; grid-template-columns:1fr 1fr; gap:8px; }
  .field {
    min-width:0; border:1px solid #2b2f2e; background:#141717; border-radius:var(--radius); padding:8px 9px;
    max-height:120px; overflow:auto;
  }
  .field-label { color:var(--muted2); font-size:10px; text-transform:uppercase; letter-spacing:0; }
  .field-value {
    margin-top:5px; color:#d9ddd5; font-size:12px; line-height:1.4;
    overflow-wrap:anywhere;
  }
  .field.error { border-color:#66333a; background:#211619; }
  .field.error .field-value { color:#ffd7da; }
  .findings { display:flex; flex-direction:column; gap:10px; }
  .compact-findings .panel-body {
    max-height:520px; overflow-y:auto; scrollbar-width:thin;
  }
  .finding { border:1px solid #303434; border-radius:8px; padding:12px; background:#1a1c1c; }
  .finding.clickable { cursor:pointer; transition:border-color .14s ease, background .14s ease, transform .14s ease; }
  .finding.clickable:hover { border-color:var(--cyan); background:#162020; transform:translateY(-1px); }
  .finding-title { display:flex; align-items:center; justify-content:space-between; gap:10px; }
  .subnav {
    display:flex; gap:8px; flex-wrap:wrap; margin-bottom:14px;
  }
  .subnav-link {
    display:inline-flex; align-items:center; height:30px; padding:0 10px;
    border:1px solid #353938; border-radius:999px; font-size:11px;
    color:var(--muted); background:#181a1a; white-space:nowrap; text-decoration:none;
  }
  .subnav-link:hover { border-color:#4b5250; background:#202323; color:var(--fg); }
  .section-stack { display:flex; flex-direction:column; gap:14px; }
  .trace-header {
    display:grid; grid-template-columns:minmax(0,1fr) minmax(360px,.55fr); gap:12px;
    margin-bottom:12px;
  }
  .trace-title-panel {
    border:1px solid #2d3130; border-radius:8px; background:#171818;
    padding:14px; min-width:0;
  }
  .trace-title-panel h1 { margin:6px 0 6px; font-size:20px; line-height:1.2; }
  .trace-kpi-strip {
    display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:8px;
    border:1px solid #2d3130; border-radius:8px; background:#171818;
    padding:10px;
  }
  .timeline-priority {
    border:1px solid #3d4745; border-radius:8px; background:#151818;
    box-shadow:0 18px 48px rgba(0,0,0,.24);
  }
  .timeline-priority .panel-head { background:#171b1b; }
  .trace-detail-grid {
    display:grid; grid-template-columns:minmax(300px,.75fr) minmax(0,1.25fr); gap:12px;
  }
  .trace-workbench {
    display:grid; grid-template-columns:minmax(0,1fr) 360px; gap:14px;
    align-items:start;
  }
  .trace-main-column { min-width:0; display:flex; flex-direction:column; gap:12px; }
  .editor-workbench {
    height:100%;
    min-height:0;
    display:block;
    overflow:hidden;
  }
  .editor-main {
    width:100%; height:100%; min-width:0; min-height:0; overflow:hidden;
    display:flex; flex-direction:column;
  }
  .editor-titlebar {
    position:absolute; left:0; right:0; top:0; height:64px;
    border:1px solid #303837; border-radius:10px; background:#141818;
    padding:10px 12px; display:grid; grid-template-columns:minmax(0,1fr) auto;
    gap:14px; align-items:center;
    min-height:0; overflow:hidden;
  }
  .editor-titlebar h1 {
    margin:3px 0 5px; font-size:16px; line-height:1.15;
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  }
  .editor-title-meta { display:flex; gap:6px; flex-wrap:wrap; }
  .editor-titlebar .trace-kpi-strip {
    grid-template-columns:repeat(4, 74px); gap:5px; padding:6px;
  }
  .editor-titlebar .kpi { padding:5px 6px; }
  .editor-titlebar .kpi-label { font-size:8px; }
  .editor-titlebar .kpi-value { font-size:12px; }
  .editor-stage {
    position:relative;
    height:100%; min-height:0; overflow:hidden; border:1px solid #303b39; border-radius:14px;
    background:
      radial-gradient(circle at 18% 0%, rgba(100,216,220,.16), transparent 34%),
      radial-gradient(circle at 84% 12%, rgba(239,185,90,.09), transparent 30%),
      linear-gradient(180deg, rgba(255,255,255,.055), rgba(255,255,255,0) 28%),
      #111515;
    box-shadow:0 26px 76px rgba(0,0,0,.34), inset 0 1px 0 rgba(255,255,255,.06);
    display:grid; grid-template-rows:58px minmax(0,1fr);
  }
  .editor-stage-head {
    min-width:0; padding:13px 16px; border-bottom:1px solid #2b3432;
    display:flex; align-items:center; justify-content:space-between; gap:10px;
    background:linear-gradient(180deg, rgba(24,29,29,.94), rgba(17,21,21,.82));
  }
  .editor-stage-title { min-width:0; }
  .editor-stage-title .panel-title { font-size:14px; }
  .editor-stage-body {
    min-height:0; overflow:auto; padding:16px; scrollbar-width:thin;
  }
  .editor-empty {
    min-height:320px; display:grid; place-items:center; text-align:center;
    color:var(--muted); border:1px dashed #31403d; border-radius:10px;
    background:rgba(10,13,13,.34);
  }
  .editor-empty strong { display:block; margin-bottom:6px; color:var(--fg); font-size:16px; }
  .trace-visual-toggle {
    display:inline-flex; gap:3px; padding:3px; border:1px solid #35413f;
    border-radius:9px; background:#0d1111;
  }
  .trace-visual-toggle button {
    min-width:62px; height:28px; border:0; border-radius:6px; background:transparent;
    color:var(--muted); font-size:11px; font-weight:760; cursor:pointer;
  }
  .trace-visual-toggle button.active { color:#e9fffe; background:#234542; }
  .trace-visual-toggle button:disabled { opacity:.36; cursor:not-allowed; }
  .visual-inspector { display:grid; gap:14px; }
  .visual-comparison { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
  .visual-pane {
    min-width:0; border:1px solid #34413f; border-radius:12px;
    background:#0d1111; overflow:hidden;
  }
  .visual-pane-head {
    display:flex; justify-content:space-between; gap:8px; align-items:center;
    padding:10px 12px; border-bottom:1px solid #293331; background:#121717;
  }
  .visual-pane-title { font-size:12px; font-weight:820; color:#e9efeb; }
  .visual-pane-source { font-size:10px; color:var(--muted); }
  .visual-stage {
    position:relative; min-height:300px; display:grid; place-items:center;
    overflow:auto; background:#080b0b; padding:12px;
  }
  .visual-stage img {
    display:block; max-width:100%; max-height:66vh; width:auto; height:auto;
    object-fit:contain; border-radius:5px;
  }
  .visual-image-wrap { position:relative; display:inline-block; line-height:0; }
  .visual-gallery-image { display:none; }
  .visual-gallery-image.active { display:inline-block; }
  .visual-thumbnails {
    min-height:39px; display:flex; gap:6px; align-items:center; padding:7px 10px;
    border-top:1px solid #293331; overflow-x:auto;
  }
  .visual-thumb {
    height:25px; min-width:32px; border:1px solid #34413f; border-radius:6px;
    background:#151b1a; color:var(--muted); font-size:10px; cursor:pointer;
  }
  .visual-thumb.active { border-color:var(--cyan); color:#e9fffe; background:#203b39; }
  .visual-media-caption {
    min-height:34px; padding:8px 10px; color:var(--muted); font-size:10px;
    border-top:1px solid #202826; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  }
  .visual-click-marker {
    position:absolute; display:none; width:24px; height:24px; margin:-12px 0 0 -12px;
    border:3px solid #ff4949; border-radius:50%; box-shadow:0 0 0 2px rgba(0,0,0,.65);
    pointer-events:none;
  }
  .visual-click-marker::after {
    content:''; position:absolute; left:8px; top:8px; width:2px; height:2px;
    border-radius:50%; background:#ff4949;
  }
  .visual-nav { display:flex; align-items:center; justify-content:space-between; gap:10px; }
  .visual-facts { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; }
  .visual-copy-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
  .visual-copy-card {
    min-width:0; border:1px solid #303837; border-radius:10px; background:#121616;
    padding:12px;
  }
  .visual-copy-card.wide { grid-column:1 / -1; }
  .visual-copy-card p {
    margin:7px 0 0; color:#dce3df; white-space:pre-wrap; overflow-wrap:anywhere;
    line-height:1.5; max-height:220px; overflow:auto;
  }
  @media (max-width: 900px) {
    .visual-comparison, .visual-copy-grid { grid-template-columns:1fr; }
    .visual-copy-card.wide { grid-column:auto; }
    .visual-facts { grid-template-columns:repeat(2,minmax(0,1fr)); }
  }
  .editor-event-hero {
    display:grid; grid-template-columns:72px minmax(0,1fr) auto; gap:14px;
    align-items:start; margin-bottom:12px;
  }
  .editor-event-index {
    width:58px; height:58px; border-radius:14px; display:grid; place-items:center;
    border:1px solid #3c4946; background:#0f1212;
    font-family:ui-monospace, SFMono-Regular, Consolas, monospace;
    color:#eef0ea; font-size:20px; font-weight:850;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.05);
  }
  .editor-event-hero.root .editor-event-index {
    border-color:#8c692f; color:#ffe0ad; background:#211910;
  }
  .editor-event-name {
    font-size:22px; line-height:1.15; font-weight:830; white-space:nowrap;
    overflow:hidden; text-overflow:ellipsis;
  }
  .editor-event-sub {
    margin-top:5px; color:var(--muted); font-family:ui-monospace, SFMono-Regular, Consolas, monospace;
    font-size:12px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  }
  .editor-stage .event-readout {
    grid-template-columns:minmax(0,1.18fr) minmax(280px,.82fr);
  }
  .editor-stage .readout-card.primary { min-height:220px; }
  .editor-stage .readout-card:nth-child(3) { grid-column:1 / -1; }
  .editor-stage .readout-value { max-height:300px; font-size:14px; }
  .event-inspector {
    min-height:100%; display:grid; grid-template-rows:auto auto auto minmax(0,1fr);
    gap:12px;
  }
  .event-focus-banner {
    border:1px solid rgba(100,216,220,.45); border-radius:15px;
    background:
      linear-gradient(135deg, rgba(100,216,220,.16), transparent 46%),
      linear-gradient(180deg, rgba(255,255,255,.045), rgba(255,255,255,0)),
      #101616;
    padding:18px;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.06), 0 18px 48px rgba(0,0,0,.22);
  }
  .event-focus-kicker {
    color:var(--cyan); font-size:11px; font-weight:860; letter-spacing:.12em;
    text-transform:uppercase;
  }
  .event-focus-title {
    margin-top:8px; color:var(--fg); font-size:32px; line-height:1.08;
    font-weight:900; letter-spacing:-.035em;
  }
  .event-focus-copy {
    margin-top:12px; max-height:140px; overflow:auto; padding-right:6px;
    color:#eef0ea; font-size:18px; line-height:1.48; font-weight:650;
    white-space:pre-wrap; overflow-wrap:anywhere; scrollbar-width:thin;
  }
  .event-inspector-summary {
    display:grid; grid-template-columns:72px minmax(0,1fr) auto; gap:15px; align-items:center;
    border:1px solid #34413f; border-radius:13px;
    background:
      linear-gradient(135deg, rgba(100,216,220,.1), transparent 44%),
      #121616;
    padding:14px;
  }
  .event-inspector-summary.root {
    border-color:#80612d;
    background:
      linear-gradient(135deg, rgba(239,185,90,.18), transparent 44%),
      #211a11;
  }
  .event-inspector-index {
    width:56px; height:56px; border-radius:14px; display:grid; place-items:center;
    border:1px solid #3d5553; background:#0e1111; color:var(--paper);
    font:880 24px/1 ui-monospace, SFMono-Regular, Consolas, monospace;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.05), 0 12px 28px rgba(0,0,0,.22);
  }
  .event-inspector-title { font-size:24px; font-weight:860; line-height:1.1; letter-spacing:-.02em; }
  .event-inspector-sub {
    margin-top:7px; color:var(--muted); font-size:12px;
    font-family:ui-monospace, SFMono-Regular, Consolas, monospace;
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  }
  .inspector-tabs {
    display:flex; gap:6px; flex-wrap:wrap; padding:0 2px;
  }
  .inspector-tab {
    height:30px; border:1px solid #333d3b; border-radius:999px;
    background:#151919; color:var(--muted); padding:0 10px;
    font:inherit; font-size:11px; cursor:pointer;
  }
  .inspector-tab.active { border-color:var(--cyan); color:#d8fdff; background:#173033; }
  .inspector-pane {
    display:none; min-height:0; overflow:auto; border:1px solid #2f3937;
    border-radius:12px; background:#101414; padding:14px; scrollbar-width:thin;
  }
  .inspector-pane.active { display:block; }
  .inspector-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }
  .inspector-card {
    min-width:0; border:1px solid #2b3433; border-radius:9px;
    background:#141818; padding:13px;
  }
  .inspector-card.wide { grid-column:1 / -1; }
  .inspector-label {
    color:var(--muted2); font-size:10px; text-transform:uppercase; letter-spacing:.04em;
  }
  .inspector-value {
    margin-top:8px; color:#eef0ea; font-size:15px; line-height:1.55;
    overflow-wrap:anywhere; white-space:pre-wrap;
  }
  .raw-pre {
    margin:0; color:#e8ece6; font-size:12px; line-height:1.55;
    font-family:ui-monospace, SFMono-Regular, Consolas, monospace;
    white-space:pre-wrap; overflow-wrap:anywhere;
  }
  .timeline-dock {
    position:fixed; left:0; right:0; bottom:0; height:220px; z-index:40;
    min-height:0; border:1px solid #354240; border-left:0; border-right:0; border-bottom:0;
    border-radius:0; overflow:hidden;
    background:
      linear-gradient(180deg, rgba(255,255,255,.035), transparent 20%),
      radial-gradient(circle at 50% 0%, rgba(100,216,220,.08), transparent 36%),
      #111515;
    box-shadow:0 -22px 54px rgba(0,0,0,.38), inset 0 1px 0 rgba(255,255,255,.06);
    display:grid; grid-template-rows:40px minmax(0,1fr);
  }
  .timeline-dock .panel-head {
    padding:7px 14px; background:linear-gradient(180deg, #1a2020, #151919);
  }
  .timeline-dock .panel-body { min-height:0; padding:0; overflow:hidden; }
  .timeline-editor {
    height:100%; min-height:0; display:grid; grid-template-columns:86px minmax(0,1fr);
    grid-template-rows:34px minmax(0,1fr); background:#101414;
  }
  .timeline-corner {
    border-right:1px solid #293331; border-bottom:1px solid #293331;
    background:#141818;
  }
  .timeline-ruler {
    position:relative; min-width:0; overflow:hidden; border-bottom:1px solid #293331;
    background:
      repeating-linear-gradient(90deg, transparent 0 95px, rgba(255,255,255,.08) 96px, transparent 97px),
      #141818;
  }
  .timeline-ruler-inner {
    min-width:max-content; height:100%; display:flex; gap:10px; align-items:end;
    padding:0 16px 6px;
  }
  .timeline-tick {
    flex:0 0 92px; color:#838d88; font-size:10px;
    font-family:ui-monospace, SFMono-Regular, Consolas, monospace;
  }
  .timeline-track-labels {
    border-right:1px solid #293331; background:#121616;
    display:grid; grid-template-rows:1fr 1fr; min-height:0;
  }
  .track-label {
    padding:12px 10px; border-bottom:1px solid #222c2a;
    color:#96a19b; font-size:10px; text-transform:uppercase; letter-spacing:.04em;
    display:flex; align-items:center;
  }
  .track-label:last-child { border-bottom:0; }
  .timeline-track-scroll {
    min-width:0; min-height:0; overflow:auto; scrollbar-width:thin;
    background:
      repeating-linear-gradient(90deg, rgba(255,255,255,.025) 0 1px, transparent 1px 96px),
      linear-gradient(180deg, rgba(255,255,255,.018), transparent),
      #0f1313;
  }
  .timeline-track-stack {
    min-width:max-content; min-height:100%; display:grid; grid-template-rows:1fr 1fr;
    padding:12px 16px 14px; position:relative;
  }
  .timeline-track-row {
    position:relative; min-height:88px; display:flex; align-items:center;
    padding:0 0 0 0;
  }
  .timeline-track-row + .timeline-track-row { border-top:1px solid #202827; }
  .timeline-track-row::before {
    content:""; position:absolute; left:0; right:0; top:50%; height:2px;
    background:linear-gradient(90deg, rgba(100,216,220,.18), rgba(100,216,220,.04));
    box-shadow:0 0 14px rgba(100,216,220,.08);
  }
  .clip-wrap {
    position:relative; flex:0 0 auto; display:flex; align-items:center; z-index:1;
  }
  .clip-connector {
    width:16px; height:2px; background:#43504d; box-shadow:0 0 8px rgba(100,216,220,.08);
  }
  .clip-connector::after {
    content:""; display:block; float:right; width:0; height:0; margin-top:-3px;
    border-left:6px solid #43504d; border-top:4px solid transparent; border-bottom:4px solid transparent;
  }
  .clip-button {
    flex:0 0 112px; width:112px; height:62px; border:1px solid #293433; border-radius:9px;
    background:#151919; color:var(--fg); cursor:pointer; display:grid;
    grid-template-rows:auto 1fr auto; align-items:center; padding:8px 9px;
    text-align:left; font-family:inherit; transition:border-color .14s ease, background .14s ease, transform .14s ease, box-shadow .14s ease;
  }
  .clip-button.clean { background:linear-gradient(180deg, #152019, #111817); border-color:#2b4f38; }
  .clip-button.error { background:linear-gradient(180deg, #2a171b, #191113); border-color:#68323a; }
  .clip-button.root { background:linear-gradient(180deg, #2b2112, #17130e); border-color:#80612d; }
  .clip-button:hover { border-color:#5a6a67; transform:translateY(-1px); }
  .clip-button.active {
    border-color:var(--cyan); box-shadow:0 0 0 1px rgba(100,216,220,.55), 0 0 18px rgba(100,216,220,.18);
  }
  .clip-button-index {
    display:flex; align-items:center; justify-content:space-between; gap:8px;
    font-family:ui-monospace, SFMono-Regular, Consolas, monospace; font-size:13px; font-weight:820;
  }
  .clip-button-role {
    margin-top:5px; color:#e4e9e4; font-size:12px; font-weight:760;
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  }
  .clip-button-foot {
    color:#7f8984; font-size:9px; text-transform:uppercase; letter-spacing:.04em;
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  }
  .signal-clip .clip-button {
    height:46px; width:112px; grid-template-rows:auto auto; opacity:.9;
  }
  .signal-clip .clip-button.clean { opacity:.36; }
  .playhead {
    position:absolute; top:0; bottom:0; width:2px; background:var(--cyan);
    box-shadow:0 0 16px rgba(100,216,220,.45); z-index:3; pointer-events:none;
  }
  .playhead::before {
    content:""; position:absolute; left:50%; top:-8px; transform:translateX(-50%);
    width:14px; height:14px; border-radius:4px; background:var(--cyan);
    box-shadow:0 0 14px rgba(100,216,220,.38);
  }
  .timeline-editor {
    height:100%; min-height:0; display:flex; flex-direction:column;
    background:
      radial-gradient(circle at 24% 0%, rgba(100,216,220,.1), transparent 34%),
      linear-gradient(180deg, #121717 0%, #0d1111 100%);
  }
  .timeline-ruler-band {
    flex:0 0 28px; border-bottom:1px solid #25302e; display:flex; align-items:end;
    gap:0; padding:0 24px 5px; min-width:max-content;
    background:
      repeating-linear-gradient(90deg, transparent 0 111px, rgba(255,255,255,.1) 112px, transparent 113px),
      #141818;
  }
  .timeline-ruler-mark {
    flex:0 0 128px; color:#7f8a85; font-size:10px;
    font-family:ui-monospace, SFMono-Regular, Consolas, monospace;
  }
  .timeline-canvas {
    position:relative; flex:1; min-height:0; overflow:auto; padding:14px 24px 14px;
    scrollbar-width:thin;
    background:
      linear-gradient(90deg, rgba(100,216,220,.06) 0 1px, transparent 1px 128px),
      linear-gradient(180deg, rgba(255,255,255,.018), transparent),
      #0f1313;
    background-size:128px 100%, auto;
  }
  .timeline-lane-title {
    position:sticky; left:0; z-index:4; width:max-content; margin-bottom:8px;
    display:flex; gap:8px; align-items:center; color:#d7dfd8;
    font-size:11px; text-transform:uppercase; letter-spacing:.08em;
  }
  .timeline-lane-title::before {
    content:""; width:8px; height:8px; border-radius:999px; background:var(--cyan);
    box-shadow:0 0 14px rgba(100,216,220,.45);
  }
  .timeline-sequence {
    position:relative; min-width:max-content; height:96px; display:flex; align-items:center;
    padding:0 0 0 0;
  }
  .timeline-sequence::before {
    content:""; position:absolute; left:0; right:0; top:50%; height:3px;
    border-radius:999px;
    background:linear-gradient(90deg, rgba(100,216,220,.7), rgba(100,216,220,.16));
    box-shadow:0 0 18px rgba(100,216,220,.18);
  }
  .timeline-clip-node {
    position:relative; z-index:2; display:flex; align-items:center; flex:0 0 auto;
  }
  .timeline-link {
    width:22px; height:3px; background:linear-gradient(90deg, #4e5d59, #2f3937);
    box-shadow:0 0 10px rgba(100,216,220,.1);
  }
  .timeline-link::after {
    content:""; display:block; float:right; width:7px; height:7px; margin-top:-2px;
    border-right:2px solid #4e5d59; border-top:2px solid #4e5d59;
    transform:rotate(45deg);
  }
  .timeline-clip {
    width:120px; height:56px; border-radius:10px; border:1px solid #33413e;
    background:linear-gradient(180deg, #1a201f, #121616); color:var(--fg);
    cursor:pointer; padding:7px 9px; font-family:inherit; text-align:left;
    display:grid; grid-template-rows:auto 1fr auto; gap:3px;
    box-shadow:0 12px 30px rgba(0,0,0,.22), inset 0 1px 0 rgba(255,255,255,.045);
    transition:transform .14s ease, border-color .14s ease, box-shadow .14s ease, filter .14s ease;
  }
  .timeline-clip:hover { transform:translateY(-2px); border-color:#62736f; filter:brightness(1.06); }
  .timeline-clip.clean { border-color:#315f42; background:linear-gradient(180deg, #17251c, #111817); }
  .timeline-clip.error { border-color:#793842; background:linear-gradient(180deg, #331920, #1b1114); }
  .timeline-clip.root { border-color:#9a742f; background:linear-gradient(180deg, #332612, #1b150d); }
  .timeline-clip.active {
    border-color:var(--cyan);
    box-shadow:0 0 0 1px rgba(100,216,220,.7), 0 0 28px rgba(100,216,220,.22), inset 0 1px 0 rgba(255,255,255,.08);
  }
  .timeline-clip-top { display:flex; align-items:center; justify-content:space-between; gap:8px; }
  .timeline-clip-num {
    font-family:ui-monospace, SFMono-Regular, Consolas, monospace; font-size:13px; font-weight:850;
  }
  .timeline-clip-role {
    font-size:11px; font-weight:790; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  }
  .timeline-clip-foot {
    color:#8c9892; font-size:9px; text-transform:uppercase; letter-spacing:.05em;
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  }
  .timeline-playhead {
    position:absolute; top:34px; bottom:10px; width:2px; background:var(--cyan);
    box-shadow:0 0 18px rgba(100,216,220,.5); z-index:3; pointer-events:none;
  }
  .timeline-playhead::before {
    content:""; position:absolute; left:50%; top:-13px; transform:translateX(-50%);
    width:20px; height:20px; border-radius:6px; background:var(--cyan);
    clip-path:polygon(50% 100%, 0 0, 100% 0);
    box-shadow:0 0 18px rgba(100,216,220,.48);
  }
  .inspector-rail {
    min-width:0; min-height:0; height:100%;
    overflow:auto; display:flex; flex-direction:column; gap:12px; scrollbar-width:thin;
  }
  .inspector-rail .panel { box-shadow:none; }
  .trace-rail {
    min-width:0; position:sticky; top:78px; max-height:calc(100vh - 96px);
    overflow:auto; display:flex; flex-direction:column; gap:12px; scrollbar-width:thin;
  }
  .trace-rail .panel { box-shadow:none; }
  .trajectory-list { display:flex; flex-direction:column; gap:8px; }
  .trajectory-event {
    border:1px solid #29302f; border-radius:var(--radius); background:#141717;
    overflow:hidden; transition:border-color .16s ease, background .16s ease, transform .16s ease;
  }
  .trajectory-event:hover { border-color:#3a4543; background:#171b1b; }
  .trajectory-event.open { border-color:var(--cyan); background:#142020; }
  .trajectory-event.focus-pulse { animation:focusPulse 900ms ease-out 1; }
  .editor-stage.focus-pulse { animation:focusPulse 900ms ease-out 1; }
  @keyframes focusPulse {
    0% { box-shadow:0 0 0 0 rgba(100,216,220,.4); transform:translateY(-1px); }
    100% { box-shadow:0 0 0 10px rgba(100,216,220,0); transform:translateY(0); }
  }
  .trajectory-summary {
    width:100%; border:0; background:transparent; color:var(--fg); cursor:pointer;
    display:grid; grid-template-columns:52px minmax(0,1fr) auto; gap:10px;
    align-items:center; padding:11px 12px; text-align:left; font-family:inherit;
  }
  .trajectory-summary:hover { background:#182020; }
  .trajectory-summary-main { min-width:0; }
  .trajectory-title {
    display:flex; align-items:center; gap:8px; min-width:0; flex-wrap:wrap;
    font-size:13px; font-weight:760;
  }
  .trajectory-copy {
    margin-top:5px; color:#d8ddd7; font-size:12px; line-height:1.4;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
  }
  .trajectory-detail { padding:0 12px 12px 12px; }
  .trajectory-detail .event { box-shadow:none; }
  .compact-findings .finding { padding:10px; }
  .compact-findings .suggestion,
  .compact-findings .evidence { display:none; }
  .timeline-list { display:flex; flex-direction:column; gap:8px; }
  .timeline-row {
    display:grid; grid-template-columns:52px minmax(0,1fr) 88px; gap:10px;
    align-items:center; border:1px solid #2d3130; border-radius:8px;
    background:#181a1a; padding:10px 12px; min-width:0;
  }
  .timeline-row.root { border-color:#80612d; background:#201a12; }
  .timeline-step {
    width:36px; height:36px; border-radius:8px; display:grid; place-items:center;
    background:#111313; border:1px solid #363a39; font-family:ui-monospace, monospace;
    color:var(--paper); font-size:12px; font-weight:760;
  }
  .timeline-main { min-width:0; }
  .timeline-head { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
  .timeline-summary {
    margin-top:6px; color:#d9ddd5; font-size:12px; line-height:1.45;
    overflow:hidden; text-overflow:ellipsis; display:-webkit-box; -webkit-line-clamp:2;
    -webkit-box-orient:vertical;
  }
  .timeline-open {
    display:inline-flex; align-items:center; justify-content:center;
    border:1px solid #3b403f; border-radius:8px; height:32px; color:var(--paper);
    background:#161818; text-decoration:none; font-size:12px;
  }
  .timeline-open:hover { border-color:#4b5250; background:#202323; }
  .event-nav {
    display:flex; gap:8px; flex-wrap:wrap; margin-top:10px;
  }
  .event-nav a {
    display:inline-flex; align-items:center; height:30px; padding:0 10px;
    border:1px solid #353938; border-radius:999px; font-size:11px;
    color:var(--muted); background:#181a1a; text-decoration:none;
  }
  .event-nav a:hover { border-color:#4b5250; background:#202323; color:var(--fg); }
  .mode { font-family:ui-monospace, SFMono-Regular, Consolas, monospace; font-size:12px; color:var(--cyan); }
  .confidence { color:var(--paper); font-size:12px; font-weight:760; }
  .suggestion { margin-top:9px; color:#d9ddd5; font-size:12px; line-height:1.45; }
  .evidence { margin-top:9px; color:var(--muted); font-size:11px; line-height:1.45; }
  .rail { display:flex; flex-direction:column; gap:14px; }
  .root-card { border-left:4px solid var(--amber); }
  .root-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; margin-top:10px; }
  .audit-card { border-left:4px solid var(--rose); }
  .audit-note {
    color:#d9ddd5; font-size:12px; line-height:1.5; overflow-wrap:anywhere;
  }
  .mini { border:1px solid #303434; border-radius:8px; padding:9px; background:#171919; min-width:0; }
  .mini-label { color:var(--muted2); font-size:10px; text-transform:uppercase; letter-spacing:0; }
  .mini-value { margin-top:6px; font-size:13px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .flow { display:grid; gap:8px; }
  .flow-item {
    display:grid; grid-template-columns:26px minmax(0,1fr); gap:9px; align-items:start;
    color:#d9ddd5; font-size:12px;
  }
  .flow-dot {
    width:22px; height:22px; border-radius:7px; display:grid; place-items:center;
    background:#202525; border:1px solid #38403e; color:var(--cyan); font-size:11px;
  }
  .dashboard-grid { display:grid; grid-template-columns:minmax(0,1fr) minmax(320px,.9fr); gap:14px; }
  .overview-header {
    display:grid; grid-template-columns:minmax(0,1fr) minmax(360px,.65fr); gap:14px;
    margin-bottom:14px;
  }
  .overview-title {
    border:1px solid var(--line); border-radius:var(--radius); background:#151818;
    padding:14px 16px;
  }
  .overview-title h1 { margin:6px 0 6px; font-size:22px; line-height:1.2; }
  .overview-health {
    display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:8px;
    border:1px solid var(--line); border-radius:var(--radius); background:#151818;
    padding:10px;
  }
  .health-card {
    border:1px solid #2a2f2e; border-radius:var(--radius); background:#111414;
    padding:10px;
  }
  .health-label { color:var(--muted2); font-size:9px; text-transform:uppercase; }
  .health-value { margin-top:5px; font-size:24px; line-height:1; font-weight:800; }
  .health-value.bad { color:var(--rose); }
  .health-value.warn { color:var(--amber); }
  .health-value.good { color:var(--green); }
  .health-value.cyan { color:var(--cyan); }
  .overview-analysis-grid {
    display:grid; grid-template-columns:minmax(0,1.25fr) minmax(320px,.75fr); gap:14px;
  }
  .overview-side-stack { display:flex; flex-direction:column; gap:14px; min-width:0; }
  .insight-grid { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:14px; }
  .stage-bar {
    display:flex; height:18px; border:1px solid #2d3130; border-radius:999px;
    overflow:hidden; background:#111414; margin:10px 0 8px;
  }
  .stage-segment { min-width:2px; height:100%; }
  .stage-segment.early { background:var(--rose); }
  .stage-segment.middle { background:var(--amber); }
  .stage-segment.late { background:var(--cyan); }
  .stage-segment.none { background:#4d5551; }
  .heatmap { display:grid; gap:6px; overflow:auto; }
  .heatmap-row { display:grid; grid-template-columns:120px repeat(var(--cols), minmax(48px,1fr)); gap:6px; align-items:center; }
  .heatmap-label { color:#d9ddd5; font-size:11px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .heatmap-cell {
    height:30px; border:1px solid #29302f; border-radius:6px; display:grid; place-items:center;
    font-size:11px; color:var(--paper); font-family:ui-monospace, SFMono-Regular, Consolas, monospace;
  }
  .priority-list { display:flex; flex-direction:column; gap:8px; }
  .priority-item {
    display:grid; grid-template-columns:minmax(0,1fr) auto; gap:10px; align-items:start;
    border:1px solid #29302f; border-radius:var(--radius); background:#141717;
    padding:10px; text-decoration:none; color:var(--fg);
  }
  .priority-item:hover { border-color:#3a4543; background:#171b1b; }
  .priority-title { color:var(--cyan); font-size:12px; font-family:ui-monospace, SFMono-Regular, Consolas, monospace; }
  .priority-copy { margin-top:4px; color:var(--muted); font-size:11px; line-height:1.4; }
  .mini-chart { width:100%; height:180px; display:block; }
  .axis-label { fill:#747b73; font-size:10px; font-family:ui-monospace, SFMono-Regular, Consolas, monospace; }
  .chart-dot { fill:var(--cyan); opacity:.82; }
  .chart-dot.warn { fill:var(--amber); }
  .chart-dot.bad { fill:var(--rose); }
  .chart-grid {
    display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:14px;
  }
  .donut-card { border:1px solid #303434; border-radius:var(--radius); background:#151818; padding:16px; min-width:0; overflow:hidden; }
  .donut-card.primary { padding:18px 22px 22px; min-height:470px; display:flex; flex-direction:column; }
  .donut-card.secondary { opacity:.9; min-height:220px; }
  .donut-label { color:var(--muted2); font-size:10px; text-transform:uppercase; }
  .donut-shell {
    margin-top:10px; display:grid; grid-template-columns:minmax(180px, 260px) minmax(0,1fr);
    gap:16px; align-items:center;
  }
  .donut-card.primary .donut-shell {
    flex:1; grid-template-columns:minmax(360px, 430px) minmax(260px, .78fr);
    gap:28px; align-items:center; justify-content:center;
  }
  .donut-card.secondary .donut-shell { grid-template-columns:minmax(130px, 160px) minmax(0,1fr); gap:12px; }
  .donut-figure {
    position:relative; width:min(100%, 260px); aspect-ratio:1; margin:0 auto;
  }
  .donut-card.primary .donut-figure { width:min(100%, 430px); }
  .donut-card.secondary .donut-figure { width:min(100%, 160px); }
  .donut-figure svg { width:100%; height:100%; display:block; transform:rotate(-90deg); overflow:visible; }
  .donut-segment {
    cursor:default;
    opacity:.9;
    transition:opacity .16s ease, stroke-width .16s ease, filter .16s ease;
  }
  .donut-segment:hover {
    opacity:1;
    stroke-width:22;
    filter:drop-shadow(0 0 8px rgba(107,214,216,.2));
  }
  .donut-center {
    position:absolute; inset:0; display:grid; place-items:center; text-align:center;
    pointer-events:none;
  }
  .donut-center strong { display:block; font-size:40px; line-height:1; }
  .donut-center span { display:block; margin-top:6px; color:var(--muted); font-size:13px; }
  .donut-card.primary .donut-center strong { font-size:52px; }
  .donut-card.primary .donut-center span { font-size:14px; }
  .donut-card.secondary .donut-center strong { font-size:28px; }
  .donut-card.secondary .donut-center span { font-size:11px; }
  .legend-stack { display:flex; flex-direction:column; gap:8px; min-width:0; }
  .donut-card.primary .legend-stack { justify-content:center; max-width:420px; }
  .legend-row {
    display:grid; grid-template-columns:12px minmax(0,1fr) auto; gap:8px; align-items:center;
    font-size:12px;
    border-bottom:1px solid #222827; padding-bottom:6px;
    cursor:default;
    transition:background .14s ease, border-color .14s ease;
  }
  .legend-row:hover { background:rgba(255,255,255,.025); border-color:#303938; }
  .legend-row:last-child { border-bottom:0; padding-bottom:0; }
  .legend-swatch {
    width:12px; height:12px; border-radius:999px; border:1px solid rgba(255,255,255,.08);
  }
  .legend-name { color:#d9ddd5; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .legend-value { color:var(--paper); font-family:ui-monospace, monospace; }
  .chart-tooltip {
    position:fixed; left:0; top:0; z-index:80; pointer-events:none;
    max-width:260px; padding:9px 10px; border:1px solid rgba(107,214,216,.35);
    border-radius:12px; background:rgba(14,17,17,.94); color:var(--paper);
    box-shadow:0 14px 40px rgba(0,0,0,.42), inset 0 1px 0 rgba(255,255,255,.05);
    font-size:12px; line-height:1.35; opacity:0; transform:translate(12px, 12px) scale(.98);
    transition:opacity .12s ease, transform .12s ease; backdrop-filter:blur(10px);
  }
  .chart-tooltip.visible { opacity:1; transform:translate(12px, 12px) scale(1); }
  .chart-tooltip strong { display:block; margin-bottom:2px; color:#fff; font-size:13px; }
  .chart-tooltip span { display:block; margin-top:3px; color:var(--muted); }
  .timeline-tooltip {
    display:grid; gap:6px; min-width:128px;
  }
  .timeline-tooltip-main {
    display:flex; align-items:center; justify-content:space-between; gap:10px;
  }
  .timeline-tooltip-index {
    color:#fff; font-size:18px; line-height:1; font-weight:850;
    font-family:ui-monospace, SFMono-Regular, Consolas, monospace;
  }
  .timeline-tooltip-type { color:#dce4de; font-size:13px; font-weight:760; }
  .timeline-error-tooltip, .timeline-branch-tooltip { min-width:260px; gap:8px; }
  .timeline-tooltip-evidence {
    color:#B9C7CA; font-size:12px; line-height:1.42;
    padding-top:2px;
  }
  .timeline-tooltip-meta {
    display:flex; align-items:center; justify-content:space-between; gap:10px;
    color:#82949B; font-size:10px; font-weight:780; text-transform:uppercase; letter-spacing:.06em;
  }
  .timeline-tooltip-status {
    width:max-content; height:24px; padding:0 9px; display:inline-flex; align-items:center;
    border-radius:999px; font-size:12px; font-weight:820; text-transform:uppercase;
  }
  .timeline-tooltip-status.ok { color:#c9f6d9; border:1px solid #2e5d43; background:#16231b; }
  .timeline-tooltip-status.error { color:#ffd5d9; border:1px solid #6d3038; background:#2a191d; }
  .timeline-tooltip-status.root { color:#ffe4b8; border:1px solid #6d552d; background:#2a2115; }
  .dist-list { display:flex; flex-direction:column; gap:10px; }
  .dist-row { display:flex; flex-direction:column; gap:6px; }
  .dist-head {
    display:flex; align-items:center; justify-content:space-between; gap:8px;
    font-size:12px;
  }
  .dist-name { color:#d9ddd5; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .dist-track {
    height:10px; border-radius:999px; overflow:hidden; background:#111313; border:1px solid #2d3130;
  }
  .dist-fill {
    height:100%; border-radius:999px; background:linear-gradient(90deg, var(--cyan), var(--green));
  }
  .overview-summary-grid {
    display:grid; grid-template-columns:repeat(3, minmax(0,1fr)); gap:8px;
  }
  .overview-hero { display:grid; grid-template-columns:minmax(0,1.65fr) minmax(300px,.85fr); gap:14px; margin-bottom:14px; }
  .overview-card {
    border:1px solid #2d3130; border-radius:10px; background:#171818;
    box-shadow:var(--shadow);
  }
  .mini.compact { padding:8px 9px; }
  .mini.compact .mini-label { font-size:9px; }
  .mini.compact .mini-value { margin-top:4px; font-size:12px; }
  .kpi-strip {
    display:grid; grid-template-columns:repeat(6, minmax(86px,1fr)); gap:6px;
    border:1px solid #2d3130; border-radius:10px; background:#151717;
    padding:7px; margin-bottom:12px;
  }
  .kpi {
    min-width:0; border:1px solid #2a2e2d; border-radius:8px;
    background:#191b1b; padding:7px 8px;
  }
  .kpi-label {
    color:var(--muted2); font-size:9px; text-transform:uppercase;
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  }
  .kpi-value {
    margin-top:3px; font-size:14px; line-height:1; font-weight:760;
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  }
  .kpi-value.bad { color:var(--rose); }
  .kpi-value.warn { color:var(--amber); }
  .kpi-value.good { color:var(--green); }
  .kpi-value.cyan { color:var(--cyan); }
  .empty {
    color:var(--muted); padding:26px 18px; text-align:center;
    border:1px dashed #2f3735; border-radius:10px; background:#121515;
  }
  .empty::before {
    content:""; display:block; width:34px; height:34px; margin:0 auto 10px;
    border-radius:10px; border:1px solid #35403e;
    background:linear-gradient(135deg, #172222, #111414);
  }
  .loading-card {
    border:1px solid #2d3433; border-radius:10px; background:#141717;
    padding:16px; display:grid; gap:10px;
  }
  .skeleton-line {
    height:12px; border-radius:999px;
    background:linear-gradient(90deg, #1a1f1e 0%, #26302e 45%, #1a1f1e 90%);
    background-size:220% 100%; animation:skeletonShift 1.2s ease-in-out infinite;
  }
  .skeleton-line.short { width:42%; }
  .skeleton-line.mid { width:68%; }
  @keyframes skeletonShift {
    0% { background-position:120% 0; }
    100% { background-position:-120% 0; }
  }
  /* Professional debugger refresh: restrained Sentry/DevTools density. */
  :root {
    --bg:#0D1011;
    --panel:#121617;
    --panel2:#171C1E;
    --panel3:#101314;
    --line:#273033;
    --line2:#323C40;
    --fg:#E7ECEC;
    --muted:#98A3A5;
    --muted2:#6F7A7C;
    --cyan:#42C7CC;
    --green:#62B982;
    --amber:#D7A956;
    --rose:#D96C75;
    --paper:#E7ECEC;
    --shadow:none;
    --radius:6px;
  }
  html, body { font-family:Inter, "SF Pro Text", ui-sans-serif, system-ui, sans-serif; }
  .shell { grid-template-columns:220px minmax(0,1fr); }
  .sidebar { background:#0f1314; padding:14px 12px; gap:14px; }
  .brand-title { font-size:16px; font-weight:650; }
  .brand-sub { font-size:12px; color:var(--muted); }
  .mark { display:none; }
  .side-section-title { font-size:11px; letter-spacing:.02em; }
  .filter-chip {
    border-radius:6px; height:28px; padding:0 10px; background:transparent;
    border-color:var(--line); font-size:12px;
  }
  .filter-chip.active { background:#152124; border-color:#2d595d; color:#c6f5f6; }
  .run {
    border:0; border-left:2px solid transparent; border-radius:6px;
    background:transparent; padding:9px 10px;
  }
  .run:hover { background:#151a1c; border-color:transparent; }
  .run.active {
    border-color:var(--cyan); background:#141c1e; box-shadow:none;
  }
  .run-id { font-size:12px; font-weight:600; font-family:inherit; color:var(--fg); }
  .run-meta { margin-top:5px; gap:6px; }
  .chip {
    height:20px; padding:0 7px; border-radius:999px;
    font-size:11px; background:transparent; border-color:var(--line2);
  }
  .chip.good { color:#b7e6c8; border-color:#2f6044; background:transparent; }
  .chip.bad { color:#efb6bc; border-color:#704046; background:transparent; }
  .chip.warn { color:#efd19b; border-color:#6d572f; background:transparent; }
  .chip.cyan { color:#adeff1; border-color:#2b6266; background:transparent; }
  .workspace { background:var(--bg); overflow:hidden; }
  .topbar {
    height:52px; padding:8px 16px; background:#0f1314; backdrop-filter:none;
    border-bottom:1px solid var(--line);
  }
  .top-actions .button { height:30px; border-radius:6px; }
  .top-actions #theme-btn,
  .top-actions #hub-btn { display:none; }
  .button { background:#141819; border-color:var(--line2); font-size:12px; }
  .button.primary, #analyze-btn { background:#173033; border-color:#2c686c; color:#d5fbfc; }
  .content { max-width:none; padding:16px; height:calc(100vh - 52px); overflow:auto; }
  .panel, .overview-card, .donut-card {
    background:var(--panel); border-color:var(--line); border-radius:6px; box-shadow:none;
  }
  .panel-head { padding:10px 12px; border-color:var(--line); }
  .panel-title { font-size:14px; font-weight:600; }
  .panel-hint, .chart-subtitle { font-size:12px; color:var(--muted2); }
  body.trace-editor-mode::after { content:none; display:none; }
  body.trace-editor-mode .workspace { height:calc(100vh - 236px); }
  body.trace-editor-mode .content {
    height:calc(100vh - 52px - 236px); padding:12px 14px; overflow:hidden;
  }
  body.trace-editor-mode .sidebar { padding-bottom:236px; }
  .project-overview { display:flex; flex-direction:column; gap:12px; }
  .overview-top {
    display:flex; align-items:flex-end; justify-content:space-between; gap:16px;
  }
  .overview-top h1 { margin:4px 0 3px; font-size:20px; font-weight:650; }
  .overview-top .goal { max-width:760px; }
  .kpi-row { display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:10px; }
  .triage-kpi {
    min-height:86px; padding:12px; border:1px solid var(--line);
    border-radius:6px; background:
      radial-gradient(circle at 18% 0%, rgba(66,199,204,.12), transparent 38%),
      var(--panel);
    cursor:pointer; position:relative; overflow:hidden;
    transition:transform .18s ease, border-color .18s ease, background .18s ease;
  }
  .triage-kpi::after {
    content:""; position:absolute; inset:auto 12px 0; height:2px; border-radius:999px;
    background:linear-gradient(90deg, transparent, rgba(66,199,204,.8), transparent);
    opacity:.45;
  }
  .triage-kpi:hover { transform:translateY(-2px); border-color:rgba(66,199,204,.45); }
  .triage-kpi.bad { background:radial-gradient(circle at 16% 0%, rgba(217,108,117,.16), transparent 40%), var(--panel); }
  .triage-kpi.warn { background:radial-gradient(circle at 16% 0%, rgba(215,169,86,.15), transparent 40%), var(--panel); }
  .triage-kpi.good { background:radial-gradient(circle at 16% 0%, rgba(98,185,130,.14), transparent 40%), var(--panel); }
  .triage-kpi-label { color:var(--muted); font-size:12px; }
  .triage-kpi-value {
    margin-top:10px; font-size:28px; line-height:1; font-weight:650;
    animation:kpiRise .42s ease both;
  }
  .triage-kpi-sub { margin-top:6px; color:var(--muted2); font-size:12px; }
  @keyframes kpiRise {
    from { opacity:.35; transform:translateY(6px); }
    to { opacity:1; transform:none; }
  }
  .triage-grid { display:grid; grid-template-columns:minmax(0,1fr) 310px; gap:12px; min-height:0; }
  .runs-toolbar {
    display:grid; grid-template-columns:minmax(220px,1fr) repeat(5, minmax(110px,auto));
    gap:8px; padding:10px 12px; border-bottom:1px solid var(--line);
  }
  .runs-input, .runs-select {
    height:30px; border:1px solid var(--line2); border-radius:6px;
    background:#0f1314; color:var(--fg); padding:0 9px; font:inherit; font-size:12px;
  }
  .runs-table-wrap { overflow:auto; max-height:calc(100vh - 330px); }
  .runs-table { width:100%; border-collapse:collapse; font-size:12px; }
  .runs-table th {
    position:sticky; top:0; z-index:1; text-align:left; color:var(--muted);
    font-weight:600; padding:9px 10px; background:#121617; border-bottom:1px solid var(--line);
  }
  .runs-table td { padding:10px; border-bottom:1px solid #1f272a; vertical-align:middle; }
  .runs-table tr { cursor:pointer; }
  .runs-table tr:hover td { background:#151b1d; }
  .status-dot {
    display:inline-flex; align-items:center; gap:6px; white-space:nowrap;
  }
  .status-dot::before {
    content:""; width:7px; height:7px; border-radius:999px; background:var(--green);
  }
  .status-dot.failed::before { background:var(--rose); }
  .status-dot.warning::before { background:var(--amber); }
  .mono { font-family:ui-monospace, SFMono-Regular, Consolas, monospace; }
  .issue-list { display:flex; flex-direction:column; gap:10px; }
  .issue-row { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:10px; font-size:12px; }
  .issue-name { color:var(--fg); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .issue-bar { grid-column:1 / -1; height:6px; border-radius:999px; background:#0f1314; overflow:hidden; }
  .issue-fill { height:100%; background:#59666a; border-radius:999px; }
  .trend-panel { min-height:180px; }
  .trend-line { width:100%; height:146px; display:block; overflow:visible; }
  .trend-area { fill:url(#failureTrendFill); opacity:.86; }
  .trend-path { fill:none; stroke:var(--rose); stroke-width:2.4; filter:drop-shadow(0 0 10px rgba(217,108,117,.18)); }
  .trend-point { cursor:pointer; stroke:#101314; stroke-width:2; transition:r .14s ease, filter .14s ease; }
  .trend-point:hover { r:5.5; filter:drop-shadow(0 0 10px rgba(66,199,204,.45)); }
  .overview-visual-grid {
    display:grid; grid-template-columns:minmax(0,1.15fr) minmax(320px,.85fr); gap:12px;
  }
  .overview-command-grid {
    display:grid; grid-template-columns:minmax(0,1fr) 360px; gap:12px; align-items:start;
  }
  .overview-main-stack { display:flex; flex-direction:column; gap:12px; min-width:0; }
  .overview-sidebar-stack { display:flex; flex-direction:column; gap:12px; min-width:0; position:sticky; top:0; }
  .overview-filterbar {
    display:flex; align-items:center; gap:8px; flex-wrap:wrap;
    border:1px solid var(--line); border-radius:6px; background:var(--panel);
    padding:10px 12px;
  }
  .overview-filterbar .runs-input { flex:1; min-width:220px; }
  .overview-filterbar .runs-select { min-width:150px; }
  .intel-overview {
    display:flex; flex-direction:column; gap:14px;
    background:
      radial-gradient(circle at 20% -10%, rgba(72,215,230,.11), transparent 30%),
      radial-gradient(circle at 86% 8%, rgba(240,113,103,.1), transparent 28%),
      var(--bg);
  }
  .intel-hero {
    display:grid; grid-template-columns:minmax(280px,.34fr) minmax(0,1fr); gap:14px;
    align-items:stretch;
  }
  .health-hero {
    min-height:330px; border:1px solid #1A2A31; border-radius:12px;
    background:
      radial-gradient(circle at 50% 24%, rgba(240,113,103,.16), transparent 36%),
      linear-gradient(180deg, rgba(255,255,255,.045), rgba(255,255,255,0)),
      #0B151B;
    box-shadow:0 28px 80px rgba(0,0,0,.24), inset 0 1px 0 rgba(255,255,255,.05);
    padding:18px; display:flex; flex-direction:column; justify-content:space-between;
  }
  .health-topline { display:flex; align-items:center; justify-content:space-between; gap:10px; }
  .health-status { color:#F07167; font-size:13px; font-weight:850; text-transform:uppercase; letter-spacing:.08em; }
  .health-ring {
    width:190px; height:190px; margin:12px auto 8px; border-radius:999px;
    display:grid; place-items:center; position:relative;
    background:conic-gradient(var(--rose) var(--health-rate, 0%), rgba(255,255,255,.07) 0);
    box-shadow:0 0 42px rgba(240,113,103,.14);
  }
  .health-ring::before {
    content:""; position:absolute; inset:18px; border-radius:999px; background:#0B151B;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.06);
  }
  .health-ring-center { position:relative; text-align:center; }
  .health-ring-center strong { display:block; font-size:40px; line-height:1; letter-spacing:-.04em; color:#EDF5F7; }
  .health-ring-center span { display:block; margin-top:7px; color:#A7B7BD; font-size:12px; }
  .health-metrics { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
  .health-mini {
    border:1px solid #1A2A31; border-radius:9px; background:#081217; padding:10px;
  }
  .health-mini-label { color:#70858D; font-size:10px; text-transform:uppercase; letter-spacing:.06em; }
  .health-mini-value { margin-top:5px; color:#EDF5F7; font-size:16px; font-weight:850; }
  .intel-panel {
    border:1px solid #1A2A31; border-radius:12px;
    background:linear-gradient(180deg, rgba(255,255,255,.035), rgba(255,255,255,0)), #0B151B;
    box-shadow:0 18px 54px rgba(0,0,0,.2), inset 0 1px 0 rgba(255,255,255,.045);
    overflow:hidden;
  }
  .intel-panel .panel-head { background:rgba(16,29,36,.72); }
  .intel-chart-body { padding:16px; }
  .failure-stack-chart { width:100%; height:280px; display:block; overflow:visible; }
  .stack-area { opacity:.74; cursor:pointer; transition:opacity .15s ease, filter .15s ease; }
  .stack-area:hover { opacity:.95; filter:drop-shadow(0 0 12px rgba(72,215,230,.2)); }
  .stack-line { fill:none; stroke:rgba(237,245,247,.35); stroke-width:1.2; }
  .stack-marker { cursor:pointer; stroke:#071015; stroke-width:2; filter:drop-shadow(0 0 8px rgba(240,113,103,.28)); }
  .chart-legend { display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }
  .legend-pill {
    display:inline-flex; align-items:center; gap:6px; height:24px; padding:0 9px;
    border:1px solid #1A2A31; border-radius:999px; background:#081217;
    color:#A7B7BD; font-size:11px; cursor:pointer;
  }
  .legend-pill::before { content:""; width:8px; height:8px; border-radius:999px; background:var(--legend-color); }
  .pattern-grid { display:grid; grid-template-columns:minmax(0,1.15fr) minmax(360px,.85fr); gap:14px; }
  .treemap {
    min-height:300px; display:grid; grid-template-columns:repeat(6,1fr); grid-auto-rows:74px;
    gap:8px;
  }
  .treemap-node {
    border:1px solid rgba(255,255,255,.08); border-radius:10px; padding:12px;
    background:linear-gradient(145deg, color-mix(in srgb, var(--node-color) 32%, #0B151B), #0B151B);
    color:#EDF5F7; cursor:pointer; overflow:hidden; display:flex; flex-direction:column; justify-content:space-between;
    transition:transform .16s ease, border-color .16s ease, filter .16s ease;
  }
  .treemap-node:hover { transform:translateY(-2px); border-color:rgba(72,215,230,.46); filter:brightness(1.08); }
  .treemap-node.major { grid-column:span 4; grid-row:span 2; }
  .treemap-node.medium { grid-column:span 3; }
  .treemap-mode { font-weight:850; font-size:14px; line-height:1.25; overflow:hidden; text-overflow:ellipsis; }
  .treemap-meta { color:#D6E2E5; font-size:12px; }
  .root-intel { display:grid; grid-template-columns:minmax(0,1fr); gap:14px; }
  .root-dist { display:flex; flex-direction:column; gap:10px; }
  .root-dist-row {
    display:grid; grid-template-columns:minmax(0,1fr) 48px 48px; gap:10px; align-items:center;
    cursor:pointer;
  }
  .root-dist-name { color:#EDF5F7; font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .root-dist-track { grid-column:1 / -1; height:8px; border-radius:999px; background:#071015; overflow:hidden; }
  .root-dist-fill { height:100%; border-radius:999px; background:linear-gradient(90deg, var(--rose), var(--amber)); }
  .severity-matrix {
    display:grid; grid-template-columns:112px repeat(4,minmax(52px,1fr)); gap:6px; align-items:stretch;
  }
  .severity-cell {
    min-height:34px; border:1px solid #1A2A31; border-radius:8px; background:#081217;
    display:grid; place-items:center; color:#A7B7BD; font-size:11px; cursor:pointer;
  }
  .severity-cell.head { color:#70858D; text-transform:uppercase; letter-spacing:.05em; background:transparent; border-color:transparent; }
  .severity-cell.hot { background:rgba(240,113,103,.22); color:#FFD2D0; border-color:rgba(240,113,103,.34); }
  .severity-cell.warm { background:rgba(241,185,88,.18); color:#FFE0A6; border-color:rgba(241,185,88,.3); }
  .performance-matrix { display:grid; gap:8px; overflow:auto; }
  .perf-table { min-width:760px; display:grid; gap:7px; }
  .perf-row { display:grid; grid-template-columns:150px repeat(var(--env-count), minmax(180px,1fr)); gap:7px; }
  .perf-axis { color:#A7B7BD; font-size:12px; display:flex; align-items:center; }
  .perf-cell {
    min-height:86px; border:1px solid #1A2A31; border-radius:10px; background:#081217;
    padding:12px; position:relative; overflow:hidden; cursor:pointer;
  }
  .perf-cell::before {
    content:""; position:absolute; inset:0; opacity:var(--risk-alpha, .05);
    background:radial-gradient(circle at 18% 10%, var(--risk-color, #48D7E6), transparent 55%);
  }
  .perf-cell.empty { background:repeating-linear-gradient(135deg, #081217 0 8px, #0B151B 8px 16px); cursor:default; }
  .perf-main, .perf-sub, .perf-dot { position:relative; z-index:1; }
  .perf-main { color:#EDF5F7; font-size:18px; font-weight:900; }
  .perf-sub { margin-top:8px; color:#A7B7BD; font-size:12px; line-height:1.4; }
  .perf-dot { position:absolute; right:10px; top:10px; width:9px; height:9px; border-radius:999px; background:var(--risk-color, #48D7E6); box-shadow:0 0 16px var(--risk-color, #48D7E6); }
  .critical-runs { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:10px; }
  .critical-run {
    border:1px solid #1A2A31; border-radius:10px; background:#081217; padding:12px;
    color:inherit; text-decoration:none; cursor:pointer; transition:transform .15s ease, border-color .15s ease;
  }
  .critical-run:hover { transform:translateY(-2px); border-color:rgba(72,215,230,.46); }
  .critical-title { color:#EDF5F7; font-size:13px; font-weight:850; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .critical-meta { margin-top:5px; color:#A7B7BD; font-size:12px; }
  .critical-mini { margin-top:11px; display:flex; gap:2px; height:22px; align-items:center; overflow:hidden; }
  .critical-mini .sequence-segment.root { border-radius:2px; transform:rotate(45deg) scale(.75); background:#F59F5B; }
  .visual-stack { display:flex; flex-direction:column; gap:12px; min-width:0; }
  .breakdown-list { display:flex; flex-direction:column; gap:9px; }
  .breakdown-row {
    display:grid; grid-template-columns:minmax(0,1fr) 48px; gap:10px; align-items:center;
    font-size:12px;
    border:1px solid transparent; border-radius:6px; padding:6px; margin:-6px;
    cursor:pointer; transition:background .15s ease, border-color .15s ease;
  }
  .breakdown-row:hover { background:#151b1d; border-color:var(--line); }
  .breakdown-bar { grid-column:1 / -1; height:9px; border-radius:999px; background:#0f1314; overflow:hidden; }
  .breakdown-fill { height:100%; border-radius:999px; background:linear-gradient(90deg, var(--rose), var(--amber)); }
  .heatmap-grid { overflow:auto; }
  .heatmap-table { min-width:520px; display:grid; gap:5px; }
  .heatmap-header, .heatmap-row-v2 {
    display:grid; grid-template-columns:140px repeat(var(--env-count), minmax(72px,1fr)); gap:5px;
    align-items:center;
  }
  .heatmap-axis { color:var(--muted); font-size:11px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .heat-cell {
    min-height:38px; border:1px solid var(--line); border-radius:6px;
    display:flex; align-items:center; justify-content:center; position:relative; overflow:hidden;
    font:12px ui-monospace, SFMono-Regular, Consolas, monospace;
    color:#dde5e5; background:#111617;
    cursor:pointer; transition:transform .15s ease, border-color .15s ease;
  }
  .heat-cell::before {
    content:""; position:absolute; left:0; top:0; bottom:0; width:var(--heat-width, 0%);
    background:linear-gradient(90deg, rgba(217,108,117,.28), rgba(215,169,86,.18));
  }
  .heat-cell span { position:relative; z-index:1; font-weight:750; }
  .heat-cell:hover { transform:translateY(-1px); border-color:rgba(66,199,204,.45); }
  .heat-cell.hot { background:#3a2024; border-color:#6b3940; color:#ffd0d5; }
  .heat-cell.warm { background:#332716; border-color:#67512c; color:#f1d7a6; }
  .recent-sequences { display:flex; flex-direction:column; gap:8px; }
  .sequence-card {
    display:grid; grid-template-columns:minmax(0,1fr) 172px; gap:12px;
    align-items:center; border:1px solid var(--line); border-radius:6px;
    background:#101415; padding:9px 10px; text-decoration:none; color:var(--fg);
  }
  .sequence-card:hover { border-color:#3b4b4e; background:#13191b; }
  .sequence-title { font-size:12px; font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .sequence-meta { margin-top:4px; color:var(--muted); font-size:11px; }
  .sequence-mini { display:flex; gap:2px; align-items:center; justify-content:flex-end; min-width:0; }
  .sequence-segment { flex:1 1 4px; min-width:3px; max-width:10px; height:20px; border-radius:2px; background:#303a3d; }
  .sequence-segment.error { background:var(--rose); }
  .sequence-segment.root { background:var(--amber); }
  .runs-support-grid { display:grid; grid-template-columns:minmax(0,1fr) 310px; gap:12px; }
  .editor-workbench {
    height:100%; display:grid; grid-template-columns:minmax(0,1fr) 320px; gap:12px;
  }
  .editor-main { overflow:hidden; }
  .editor-stage {
    position:relative; height:100%; border-radius:6px; border-color:var(--line);
    background:var(--panel); box-shadow:none; grid-template-rows:64px minmax(0,1fr);
  }
  .editor-stage-head {
    padding:10px 12px; background:var(--panel); border-color:var(--line);
  }
  .editor-stage-title .panel-title { font-size:15px; }
  .editor-stage-body { padding:0; overflow:hidden; }
  .event-inspector { min-height:100%; display:grid; grid-template-rows:auto auto minmax(0,1fr); gap:0; }
  .event-focus-banner { display:none; }
  .event-inspector-summary {
    min-height:68px; border:0; border-bottom:1px solid var(--line); border-radius:0;
    background:var(--panel); grid-template-columns:minmax(0,1fr) auto; padding:10px 12px;
  }
  .event-inspector-summary.root { background:var(--panel); border-color:var(--line); }
  .event-inspector-index { display:none; }
  .event-inspector-title { font-size:15px; font-weight:650; letter-spacing:0; }
  .event-inspector-sub { font-size:12px; margin-top:4px; color:var(--muted); }
  .inspector-tabs {
    gap:18px; padding:0 12px; height:38px; align-items:end;
    border-bottom:1px solid var(--line); background:#101415;
  }
  .inspector-tab {
    height:38px; padding:0; border:0; border-radius:0; background:transparent;
    color:var(--muted); font-size:12px; border-bottom:2px solid transparent;
  }
  .inspector-tab.active { background:transparent; color:var(--fg); border-color:var(--cyan); }
  .inspector-pane {
    border:0; border-radius:0; background:var(--panel); padding:12px;
    overflow:auto;
  }
  .inspector-grid { grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }
  .inspector-card { border-color:var(--line); border-radius:6px; background:#101415; padding:10px; }
  .inspector-label { font-size:11px; letter-spacing:.02em; }
  .inspector-value { font-size:13px; line-height:1.5; font-weight:400; }
  .summary-block {
    border:1px solid var(--line); border-radius:6px; background:#101415; padding:10px;
  }
  .summary-block.wide { grid-column:1 / -1; }
  .summary-block h3 { margin:0 0 6px; font-size:13px; font-weight:650; }
  .summary-block p { margin:0; color:#d6dddd; font-size:13px; line-height:1.5; white-space:pre-wrap; overflow-wrap:anywhere; }
  .delta-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
  .context-row {
    width:100%; display:grid; grid-template-columns:42px minmax(0,1fr) 70px;
    gap:8px; align-items:center; border:0; border-bottom:1px solid #222a2d;
    background:transparent; color:var(--fg); padding:8px 0; text-align:left; cursor:pointer;
    font:inherit; font-size:12px;
  }
  .context-row.selected { color:var(--cyan); }
  .diagnosis-panel {
    min-height:0; overflow:auto; border:1px solid var(--line); border-radius:6px;
    background:var(--panel); scrollbar-width:thin;
  }
  .diagnosis-section { padding:12px; border-bottom:1px solid var(--line); }
  .diagnosis-section:last-child { border-bottom:0; }
  .diagnosis-label { color:var(--muted); font-size:12px; font-weight:600; }
  .diagnosis-title { margin-top:7px; font-size:15px; line-height:1.35; font-weight:650; }
  .diagnosis-copy { margin-top:7px; color:#cfd7d8; font-size:13px; line-height:1.5; }
  .evidence-list { margin:8px 0 0; padding-left:16px; color:#cfd7d8; font-size:13px; line-height:1.5; }
  .related-chain { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }
  .related-link {
    height:24px; border:1px solid var(--line2); border-radius:6px; background:#101415;
    color:var(--fg); padding:0 8px; font:inherit; font-size:12px; cursor:pointer;
  }
  .related-link:hover { border-color:var(--cyan); }
  .timeline-dock {
    height:260px; background:#0f1314; border-color:var(--line); box-shadow:none;
    grid-template-rows:40px minmax(0,1fr);
  }
  .timeline-dock .panel-head { padding:8px 12px; background:#0f1314; }
  .timeline-toolbar { display:flex; align-items:center; gap:8px; }
  .timeline-tool {
    height:26px; border:1px solid var(--line2); border-radius:6px; background:#121617;
    color:var(--fg); padding:0 8px; font:inherit; font-size:12px; cursor:pointer;
  }
  .timeline-tool:hover { border-color:var(--cyan); }
  .timeline-editor {
    height:100%; display:grid; grid-template-columns:92px minmax(0,1fr);
    grid-template-rows:28px 30px 46px 28px auto; background:#0f1314;
  }
  .timeline-minimap-label, .track-label {
    border-right:1px solid var(--line); border-bottom:1px solid var(--line);
    color:var(--muted); font-size:11px; display:flex; align-items:center; padding:0 10px;
    background:#111617; text-transform:uppercase; letter-spacing:.03em;
  }
  .timeline-minimap {
    border-bottom:1px solid var(--line); overflow-x:auto; overflow-y:hidden; display:flex; align-items:center;
    gap:2px; padding:0 10px; background:#111617; scrollbar-width:none;
  }
  .timeline-minimap::-webkit-scrollbar { display:none; }
  .mini-segment {
    height:10px; width:calc(48px * var(--zoom, 1)); min-width:28px; max-width:86px;
    flex:0 0 auto; border:0; border-radius:1px; background:#2b3437; padding:0; cursor:pointer;
  }
  .mini-segment.error { background:var(--rose); }
  .mini-segment.root { background:var(--amber); }
  .mini-segment.active { outline:1px solid var(--cyan); }
  .timeline-track {
    position:relative; min-width:0; overflow-x:auto; overflow-y:hidden;
    border-bottom:1px solid #1c2427; scrollbar-width:none;
    background:
      repeating-linear-gradient(90deg, rgba(255,255,255,.028) 0 1px, transparent 1px calc((48px * var(--zoom, 1)) + 4px)),
      #0d1011;
  }
  .timeline-track::-webkit-scrollbar { display:none; }
  .timeline-track.marker-lane .track-clip {
    height:16px; border-radius:999px; color:transparent; padding:0;
  }
  .timeline-track.marker-lane .track-clip.error,
  .timeline-track.marker-lane .track-clip.root {
    color:#fff; font-size:9px; font-weight:700; text-align:center;
  }
  .timeline-track.main-sequence .track-clip {
    height:30px; font-size:12px; border-radius:4px;
  }
  .track-strip {
    position:relative; min-width:max-content; height:100%; display:flex; align-items:center; gap:4px; padding:0 10px;
  }
  .track-strip::before {
    content:""; position:absolute; left:10px; right:10px; top:50%; height:1px; background:#2a3336;
  }
  .track-clip {
    position:relative; z-index:1; width:calc(48px * var(--zoom, 1)); min-width:28px; max-width:86px;
    height:22px; border:1px solid #303b3e; border-top-width:2px; border-radius:3px;
    background:linear-gradient(180deg, #171d1f, #131719); color:#dce4e4; font:11px ui-monospace, SFMono-Regular, Consolas, monospace;
    cursor:pointer; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; padding:0 5px;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.025);
  }
  .track-clip:hover { border-color:#56666a; }
  .track-clip.ok { border-top-color:#4b5c5f; }
  .track-clip.error { border-top-color:var(--rose); }
  .track-clip.root { border-top-color:var(--amber); }
  .track-clip.active { border-color:var(--cyan); box-shadow:0 0 0 1px rgba(66,199,204,.28); }
  .playhead {
    position:absolute; top:0; bottom:0; width:1px; background:var(--cyan); z-index:6; pointer-events:none;
    box-shadow:0 0 0 1px rgba(66,199,204,.18);
  }
  .playhead::before {
    content:attr(data-step); position:absolute; top:-18px; transform:translateX(-50%);
    color:var(--cyan); font-size:10px; font-family:ui-monospace, SFMono-Regular, Consolas, monospace;
  }
  .timeline-advanced-tracks {
    grid-column:1 / -1; overflow:auto; border-top:1px solid var(--line); background:#0f1314;
  }
  .timeline-advanced-tracks summary {
    height:28px; display:flex; align-items:center; padding:0 10px;
    color:var(--muted); font-size:12px; cursor:pointer; border-bottom:1px solid var(--line);
  }
  .advanced-track-grid {
    display:grid; grid-template-columns:92px minmax(0,1fr);
    grid-auto-rows:28px; min-height:0;
  }
  /* Reference clone: cinematic trace editor from 1.png. */
  :root {
    --bg:#060B10;
    --panel:#101820;
    --panel2:#151F29;
    --panel3:#0B1118;
    --line:#1E2C36;
    --line2:#2A3A46;
    --fg:#E7EEF5;
    --muted:#9CAAB5;
    --muted2:#63727E;
    --cyan:#72E8F4;
    --green:#6EDB98;
    --amber:#F5BE55;
    --rose:#F26D5E;
    --violet:#A884FF;
    --shadow:0 24px 80px rgba(0,0,0,.46);
    --radius:14px;
  }
  html, body {
    font-family:"SF Pro Display", "SF Pro Text", "Aptos", ui-sans-serif, system-ui, sans-serif;
    background:
      radial-gradient(circle at 18% -8%, rgba(67,213,229,.16), transparent 26%),
      radial-gradient(circle at 78% 4%, rgba(242,109,94,.09), transparent 24%),
      linear-gradient(135deg, #05080d 0%, #071017 46%, #04070b 100%);
  }
  body::before {
    content:""; position:fixed; inset:0; pointer-events:none; z-index:-1;
    background:
      linear-gradient(rgba(255,255,255,.025) 1px, transparent 1px),
      linear-gradient(90deg, rgba(255,255,255,.018) 1px, transparent 1px);
    background-size:44px 44px;
    mask-image:radial-gradient(circle at center, black 0%, transparent 82%);
  }
  .shell {
    grid-template-columns:74px 430px minmax(0,1fr);
    height:100vh; padding:0; background:rgba(2,6,10,.48);
  }
  .icon-rail {
    grid-column:1; min-height:0; border-right:1px solid rgba(117,232,244,.08);
    background:linear-gradient(180deg, rgba(8,14,22,.95), rgba(7,13,20,.86));
    display:flex; flex-direction:column; align-items:center; gap:22px; padding:18px 12px;
    box-shadow:inset -1px 0 0 rgba(255,255,255,.03);
  }
  .rail-logo {
    width:42px; height:42px; border-radius:14px; display:grid; place-items:center;
    color:var(--cyan); font-weight:900; letter-spacing:-.04em;
    background:linear-gradient(145deg, rgba(114,232,244,.18), rgba(114,232,244,.05));
    border:1px solid rgba(114,232,244,.28); box-shadow:0 0 30px rgba(114,232,244,.1);
  }
  .rail-nav { display:flex; flex-direction:column; gap:16px; margin-top:18px; }
  .rail-btn {
    width:38px; height:38px; border:0; border-radius:12px; color:#8FA2B0;
    background:transparent; cursor:pointer; display:grid; place-items:center;
    transition:background .18s ease, color .18s ease, transform .18s ease, box-shadow .18s ease;
  }
  .rail-btn:hover, .rail-btn.active {
    color:#DDFDFF; background:rgba(114,232,244,.1);
    box-shadow:inset 0 0 0 1px rgba(114,232,244,.14), 0 12px 34px rgba(0,0,0,.28);
    transform:translateY(-1px);
  }
  .rail-btn span {
    display:block;
    transform:translateY(-1px);
  }
  .rail-user {
    border:0;
    cursor:pointer;
    margin-top:auto; width:40px; height:40px; border-radius:999px;
    background:linear-gradient(145deg, #263647, #121b25); border:1px solid rgba(255,255,255,.14);
    position:relative; box-shadow:0 16px 38px rgba(0,0,0,.32);
    transition:transform .18s ease, border-color .18s ease, box-shadow .18s ease;
  }
  .rail-user img {
    width:100%; height:100%; display:block; border-radius:inherit;
    object-fit:cover; object-position:center;
  }
  .rail-user:hover {
    transform:translateY(-1px);
    border-color:rgba(114,232,244,.32);
    box-shadow:0 18px 44px rgba(0,0,0,.38), 0 0 28px rgba(114,232,244,.1);
  }
  .rail-user::after {
    content:""; position:absolute; right:1px; bottom:1px; width:10px; height:10px;
    border-radius:999px; background:var(--green); box-shadow:0 0 0 3px #081018;
  }
  .offline-popover {
    position:fixed; left:72px; bottom:24px; z-index:130; width:278px;
    border:1px solid rgba(114,232,244,.26); border-radius:14px;
    background:
      radial-gradient(circle at 12% 0%, rgba(114,232,244,.14), transparent 34%),
      linear-gradient(145deg, rgba(18,27,36,.98), rgba(8,14,20,.98));
    box-shadow:0 24px 70px rgba(0,0,0,.42), inset 0 1px 0 rgba(255,255,255,.05);
    padding:14px; opacity:0; transform:translateY(10px) scale(.98);
    pointer-events:none; transition:opacity .2s ease, transform .2s ease;
  }
  .offline-popover.visible { opacity:1; transform:none; pointer-events:auto; }
  .offline-popover::before {
    content:""; position:absolute; left:-7px; bottom:21px; width:12px; height:12px;
    transform:rotate(45deg); background:#101a24;
    border-left:1px solid rgba(114,232,244,.26); border-bottom:1px solid rgba(114,232,244,.26);
  }
  .offline-title { display:flex; align-items:center; gap:9px; font-weight:820; color:#EAF7FA; }
  .offline-dot {
    width:10px; height:10px; border-radius:999px; background:var(--green);
    box-shadow:0 0 0 4px rgba(110,219,152,.12), 0 0 18px rgba(110,219,152,.42);
  }
  .offline-copy { margin-top:9px; color:#AAB8C2; font-size:13px; line-height:1.45; }
  .offline-meta { margin-top:12px; display:flex; flex-wrap:wrap; gap:7px; }
  .case-toolbar {
    display:flex; align-items:center; justify-content:space-between; gap:12px;
    margin-bottom:12px; flex-wrap:wrap;
  }
  .case-workbench { display:grid; grid-template-columns:minmax(0,1fr) 360px; gap:12px; align-items:start; }
  .case-controls {
    display:grid; grid-template-columns:minmax(220px,1fr) repeat(3, minmax(120px,auto)) auto;
    gap:8px; padding:10px 12px; border-bottom:1px solid var(--line);
  }
  .case-list-scroll { max-height:calc(100vh - 284px); overflow:auto; padding-right:4px; }
  .case-detail-panel { position:sticky; top:0; }
  .case-detail-hero {
    border:1px solid rgba(66,199,204,.22); border-radius:10px;
    background:
      radial-gradient(circle at 18% 0%, rgba(66,199,204,.15), transparent 42%),
      linear-gradient(145deg, rgba(18,27,36,.9), rgba(10,16,22,.92));
    padding:14px;
  }
  .case-detail-title { color:#EFF7FC; font-size:16px; font-weight:850; line-height:1.25; }
  .case-detail-summary { margin-top:10px; color:#AAB8C2; font-size:13px; line-height:1.5; }
  .case-detail-actions { margin-top:12px; display:flex; gap:8px; flex-wrap:wrap; }
  .case-grid {
    display:grid; grid-template-columns:repeat(auto-fill, minmax(360px, 1fr));
    gap:12px;
  }
  .case-card {
    border:1px solid rgba(151,170,181,.13); border-radius:12px;
    background:linear-gradient(145deg, rgba(18,27,36,.9), rgba(10,16,22,.9));
    box-shadow:var(--shadow), inset 0 1px 0 rgba(255,255,255,.04);
    padding:16px; min-width:0; text-align:left; color:inherit; cursor:pointer;
    position:relative; transition:transform .18s ease, border-color .18s ease, box-shadow .18s ease;
  }
  .case-card:hover, .case-card.selected {
    transform:translateY(-2px); border-color:rgba(66,199,204,.4);
    box-shadow:0 18px 52px rgba(0,0,0,.28), 0 0 0 1px rgba(66,199,204,.08);
  }
  .case-card-head {
    display:grid; grid-template-columns:minmax(0,1fr) auto; gap:10px; align-items:start;
  }
  .case-card-title {
    color:#EFF7FC; font-weight:820; font-size:15px;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
  }
  .case-delete {
    height:28px; border-radius:8px; border:1px solid rgba(242,109,94,.34);
    background:rgba(242,109,94,.1); color:#ffd5d9; font:inherit;
    font-size:12px; font-weight:760; cursor:pointer; padding:0 9px;
  }
  .case-delete:hover {
    background:rgba(242,109,94,.18); border-color:rgba(242,109,94,.58);
  }
  .case-card-summary {
    margin-top:10px; color:#AAB8C2; font-size:13px; line-height:1.48;
    display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden;
  }
  .case-meta-grid {
    display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; margin-top:12px;
  }
  .case-mini-timeline {
    display:flex; gap:2px; height:26px; align-items:center; margin-top:12px;
    padding:5px; border:1px solid rgba(151,170,181,.12); border-radius:8px;
    background:rgba(8,14,20,.42); overflow:hidden;
  }
  .case-mini-seg {
    flex:1; min-width:3px; height:14px; border-radius:2px; background:#263238;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.05);
  }
  .case-mini-seg.error { background:linear-gradient(180deg, #e17b83, #6a2730); }
  .case-mini-seg.root { background:linear-gradient(180deg, #efc26c, #714a17); }
  .case-mini-seg.ok { background:linear-gradient(180deg, #68bf8a, #244f38); }
  .case-empty-side {
    border:1px dashed rgba(151,170,181,.18); border-radius:10px; padding:18px;
    color:var(--muted); background:#101415; text-align:center;
  }
  .case-meta {
    border:1px solid rgba(151,170,181,.12); border-radius:10px;
    background:rgba(8,14,20,.55); padding:8px;
  }
  .case-meta-label { color:#7F8E99; font-size:10px; text-transform:uppercase; letter-spacing:.06em; }
  .case-meta-value {
    margin-top:4px; color:#EAF4FA; font-weight:760; font-size:12px;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
  }
  .case-library-shell { display:flex; flex-direction:column; gap:14px; }
  .case-summary-strip {
    display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:10px;
  }
  .case-summary-tile {
    min-height:72px; border:1px solid rgba(151,170,181,.13); border-radius:12px;
    background:linear-gradient(145deg, rgba(18,27,36,.86), rgba(10,16,22,.88));
    padding:12px; cursor:pointer; transition:transform .16s ease, border-color .16s ease;
  }
  .case-summary-tile:hover { transform:translateY(-2px); border-color:rgba(66,199,204,.42); }
  .case-summary-label { color:#8EA0AA; font-size:10px; text-transform:uppercase; letter-spacing:.08em; }
  .case-summary-value { margin-top:6px; color:#EFF7FC; font-size:24px; line-height:1; font-weight:880; }
  .case-summary-sub { margin-top:5px; color:#7F8E99; font-size:11px; }
  .case-library-grid {
    display:grid; grid-template-columns:260px minmax(0,1fr) 380px; gap:12px; align-items:start;
  }
  .pattern-nav {
    position:sticky; top:0; border:1px solid rgba(151,170,181,.13); border-radius:12px;
    background:linear-gradient(145deg, rgba(18,27,36,.9), rgba(10,16,22,.9));
    overflow:hidden;
  }
  .pattern-nav-head { padding:14px; border-bottom:1px solid rgba(151,170,181,.12); }
  .pattern-nav-list { padding:10px; display:flex; flex-direction:column; gap:6px; }
  .pattern-nav-item {
    width:100%; min-height:34px; border:1px solid transparent; border-radius:9px;
    background:transparent; color:#AAB8C2; display:grid; grid-template-columns:minmax(0,1fr) auto;
    gap:8px; align-items:center; text-align:left; padding:7px 9px; font:inherit; cursor:pointer;
  }
  .pattern-nav-item:hover, .pattern-nav-item.active {
    color:#EFF7FC; background:rgba(66,199,204,.08); border-color:rgba(66,199,204,.22);
    box-shadow:inset 3px 0 0 var(--cyan);
  }
  .pattern-nav-item.sub { margin-left:12px; width:calc(100% - 12px); font-size:12px; }
  .pattern-nav-count { color:#7F8E99; font-family:ui-monospace, SFMono-Regular, Consolas, monospace; }
  .tag-cloud { display:flex; flex-wrap:wrap; gap:6px; padding:0 10px 12px; }
  .tag-pill {
    border:1px solid rgba(151,170,181,.14); background:rgba(8,14,20,.5); color:#AAB8C2;
    border-radius:999px; padding:5px 8px; font-size:11px; cursor:pointer;
  }
  .case-workspace { min-width:0; display:flex; flex-direction:column; gap:12px; }
  .pattern-summary {
    border:1px solid rgba(66,199,204,.18); border-radius:12px;
    background:
      radial-gradient(circle at 12% 0%, rgba(66,199,204,.13), transparent 36%),
      linear-gradient(145deg, rgba(18,27,36,.9), rgba(10,16,22,.9));
    padding:14px; display:grid; grid-template-columns:minmax(0,1fr) auto; gap:14px; align-items:start;
  }
  .pattern-summary-title { color:#EFF7FC; font-size:16px; font-weight:880; }
  .pattern-summary-copy { margin-top:7px; color:#AAB8C2; line-height:1.5; font-size:13px; }
  .pattern-summary-actions { display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end; }
  .case-view-toggle { display:inline-flex; gap:4px; padding:3px; border:1px solid rgba(151,170,181,.14); border-radius:10px; background:rgba(8,14,20,.5); }
  .case-view-toggle button {
    border:0; border-radius:7px; background:transparent; color:#8EA0AA; height:28px; padding:0 10px; font:inherit; cursor:pointer;
  }
  .case-view-toggle button.active { background:rgba(66,199,204,.14); color:#D9FBFC; }
  .case-id-kicker { color:var(--cyan); font-size:11px; font-weight:800; letter-spacing:.04em; text-transform:uppercase; }
  .case-card-actions { position:relative; }
  .case-menu-btn {
    width:30px; height:30px; border-radius:9px; border:1px solid rgba(151,170,181,.15);
    background:rgba(8,14,20,.5); color:#AAB8C2; cursor:pointer; font-weight:900;
  }
  .case-menu {
    display:none; position:absolute; right:0; top:34px; z-index:8; min-width:190px;
    border:1px solid rgba(151,170,181,.18); border-radius:10px;
    background:#0B1117; box-shadow:0 18px 42px rgba(0,0,0,.34); padding:6px;
  }
  .case-card-actions:hover .case-menu, .case-menu.open { display:flex; flex-direction:column; gap:3px; }
  .case-menu button {
    border:0; border-radius:7px; background:transparent; color:#AAB8C2; text-align:left;
    height:30px; padding:0 9px; font:inherit; cursor:pointer;
  }
  .case-menu button:hover { background:rgba(66,199,204,.09); color:#EFF7FC; }
  .case-menu .danger { color:#FFB8BE; border-top:1px solid rgba(151,170,181,.12); margin-top:3px; }
  .case-tags { display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; }
  .case-tag { border:1px solid rgba(66,199,204,.18); border-radius:999px; color:#AEEFF2; padding:3px 7px; font-size:10px; background:rgba(66,199,204,.06); }
  .case-status-row { display:flex; align-items:center; gap:7px; flex-wrap:wrap; margin-top:9px; }
  .case-table-wrap { display:none; overflow:auto; border:1px solid rgba(151,170,181,.13); border-radius:12px; }
  .case-table-wrap.active { display:block; }
  .case-grid.hidden { display:none; }
  .case-table { width:100%; border-collapse:collapse; font-size:12px; min-width:980px; }
  .case-table th {
    position:sticky; top:0; z-index:1; text-align:left; color:#8EA0AA; background:#101720;
    padding:10px; border-bottom:1px solid rgba(151,170,181,.12);
  }
  .case-table td { padding:10px; border-bottom:1px solid rgba(151,170,181,.1); color:#DCE7EA; vertical-align:middle; }
  .case-table tr { cursor:pointer; }
  .case-table tr:hover td, .case-table tr.selected td { background:rgba(66,199,204,.07); }
  .case-detail-section {
    margin-top:12px; border:1px solid rgba(151,170,181,.12); border-radius:10px;
    background:rgba(8,14,20,.34); padding:11px;
  }
  .case-detail-section-title { color:#8EA0AA; font-size:10px; text-transform:uppercase; letter-spacing:.08em; margin-bottom:8px; }
  .case-detail-text { color:#DCE7EA; font-size:13px; line-height:1.5; }
  body.theme-light .case-card {
    background:linear-gradient(145deg, rgba(255,255,255,.96), rgba(243,248,246,.94)) !important;
    border-color:rgba(124,143,137,.22) !important;
    box-shadow:0 18px 52px rgba(41,54,51,.12), inset 0 1px 0 rgba(255,255,255,.78) !important;
  }
  body.theme-light .case-card-title,
  body.theme-light .case-meta-value { color:#17211F !important; }
  body.theme-light .case-card-summary { color:#61706C !important; }
  body.theme-light .case-delete {
    background:#FCEBED !important;
    border-color:#E9A8B0 !important;
    color:#A42E3D !important;
  }
  body.theme-light .case-meta {
    background:#FFFFFF !important;
    border-color:#DDE5E2 !important;
  }
  .sidebar {
    grid-column:2; border-right:1px solid rgba(114,232,244,.08);
    background:linear-gradient(180deg, rgba(15,23,31,.94), rgba(10,17,24,.9));
    padding:22px 20px; gap:18px; box-shadow:inset -1px 0 0 rgba(255,255,255,.03);
  }
  .brand { display:none; }
  .run-section { overflow:hidden; display:flex; flex-direction:column; min-height:0; }
  .side-section-title {
    margin:0 0 16px; color:#E6EEF5; font-size:18px; font-weight:760;
    letter-spacing:-.02em; text-transform:none;
  }
  .run-search-shell {
    display:grid; grid-template-columns:minmax(0,1fr) 44px; gap:10px; margin-bottom:14px;
  }
  .run-search {
    height:42px; border-radius:11px; border:1px solid rgba(142,162,176,.16);
    background:rgba(6,11,16,.56); color:var(--fg); padding:0 14px;
    font:inherit; font-size:13px; box-shadow:inset 0 1px 0 rgba(255,255,255,.035);
  }
  .run-filter-btn {
    height:42px; border-radius:11px; border:1px solid rgba(142,162,176,.16);
    background:rgba(13,21,29,.76); color:#B8C7D0; cursor:pointer;
    transition:transform .18s ease, border-color .18s ease, background .18s ease;
  }
  .run-filter-btn:hover { transform:translateY(-1px); border-color:rgba(114,232,244,.35); background:rgba(20,33,43,.9); }
  .filter-tray { gap:8px; padding:0 0 14px; }
  .filter-chip {
    height:30px; padding:0 12px; border-radius:999px;
    background:rgba(255,255,255,.03); border-color:rgba(255,255,255,.09);
    color:#A7B7C2; transition:all .18s ease;
  }
  .filter-chip:hover { transform:translateY(-1px); border-color:rgba(114,232,244,.28); color:#E9FBFF; }
  .filter-chip.active { background:rgba(114,232,244,.13); border-color:rgba(114,232,244,.45); color:#D6FCFF; }
  body.run-compact .run {
    min-height:70px !important;
    padding:12px 16px !important;
  }
  body.run-compact .run .run-meta:last-child { display:none; }
  .run-list { gap:10px; overflow:auto; padding-right:6px; scrollbar-width:thin; }
  .run {
    min-height:92px; border:1px solid rgba(151,170,181,.12); border-left:1px solid rgba(151,170,181,.12);
    border-radius:12px; padding:14px 16px; background:linear-gradient(145deg, rgba(18,27,36,.82), rgba(10,16,22,.74));
    box-shadow:inset 0 1px 0 rgba(255,255,255,.035);
    transition:transform .22s cubic-bezier(.2,.8,.2,1), border-color .22s ease, background .22s ease, box-shadow .22s ease;
  }
  .run:hover {
    transform:translateY(-2px); background:linear-gradient(145deg, rgba(23,35,46,.92), rgba(12,21,29,.82));
    border-color:rgba(114,232,244,.24); box-shadow:0 18px 45px rgba(0,0,0,.22), inset 0 1px 0 rgba(255,255,255,.05);
  }
  .run.active {
    border-color:rgba(114,232,244,.64); background:linear-gradient(145deg, rgba(19,45,55,.82), rgba(12,25,33,.9));
    box-shadow:0 0 0 1px rgba(114,232,244,.18), 0 18px 52px rgba(0,0,0,.3), inset 4px 0 0 var(--cyan);
  }
  .run-id { font:700 13px/1.25 ui-monospace, SFMono-Regular, Consolas, monospace; color:#EAF4FA; }
  .run-meta { margin-top:12px; gap:8px; }
  .chip {
    height:24px; padding:0 9px; border-radius:8px; font-size:12px; font-weight:640;
    background:rgba(255,255,255,.03); border-color:rgba(255,255,255,.12);
  }
  .chip.bad { color:#FFBDB6; background:rgba(242,109,94,.12); border-color:rgba(242,109,94,.33); }
  .chip.good { color:#C8F8D8; background:rgba(110,219,152,.11); border-color:rgba(110,219,152,.3); }
  .chip.warn { color:#FFE0A8; background:rgba(245,190,85,.12); border-color:rgba(245,190,85,.34); }
  .chip.cyan { color:#D6FCFF; background:rgba(114,232,244,.1); border-color:rgba(114,232,244,.32); }
  .workspace { grid-column:3; background:transparent; }
  .topbar {
    height:64px; padding:0 22px; background:rgba(7,12,18,.82);
    border-bottom:1px solid rgba(114,232,244,.08); backdrop-filter:blur(18px);
  }
  .crumb { color:#AAB8C2; font-weight:640; }
  .crumb::before { content:"Projects  ›  checkout-agent  ›  Debug Runs  ›  "; color:#6F808D; font-weight:580; }
  .top-actions { gap:12px; }
  .button, .timeline-tool {
    height:38px; border-radius:10px; padding:0 15px; background:rgba(17,26,35,.75);
    border-color:rgba(151,170,181,.16); color:#E6EEF5; font-weight:680;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.04);
    transition:transform .18s ease, border-color .18s ease, background .18s ease, box-shadow .18s ease;
  }
  .button:hover, .timeline-tool:hover {
    transform:translateY(-1px); border-color:rgba(114,232,244,.36);
    background:rgba(22,35,47,.92); box-shadow:0 14px 34px rgba(0,0,0,.28), inset 0 1px 0 rgba(255,255,255,.06);
  }
  .button.primary, #analyze-btn { background:rgba(18,39,48,.9); border-color:rgba(114,232,244,.3); color:#D9FBFF; }
  .top-actions #theme-btn, .top-actions #hub-btn { display:inline-flex; }
  #hub-btn { width:auto; min-width:112px; padding:0 13px; font-size:13px; gap:7px; }
  #hub-btn::before { display:none; }
  #hub-btn svg { width:15px; height:15px; fill:none; stroke:currentColor; stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round; }
  .content { height:calc(100vh - 64px); padding:0 14px 14px; overflow:hidden; }
  body.trace-editor-mode .workspace { height:calc(100vh - 276px); }
  body.trace-editor-mode .content { height:calc(100vh - 64px - 276px); padding:14px; overflow:hidden; }
  body.trace-editor-mode .sidebar { padding-bottom:276px; }
  .editor-workbench { grid-template-columns:minmax(0,1fr) 520px; gap:10px; animation:panelIn .36s ease-out both; }
  @keyframes panelIn { from { opacity:0; transform:translateY(8px); } to { opacity:1; transform:none; } }
  .editor-stage, .diagnosis-panel, .timeline-dock, .panel, .overview-card, .donut-card {
    border-radius:12px; border-color:rgba(151,170,181,.12);
    background:linear-gradient(145deg, rgba(18,27,36,.9), rgba(10,16,22,.9));
    box-shadow:var(--shadow), inset 0 1px 0 rgba(255,255,255,.04);
  }
  .editor-stage {
    grid-template-rows:58px minmax(0,1fr);
    background:
      radial-gradient(circle at 24% 0%, rgba(114,232,244,.08), transparent 34%),
      linear-gradient(145deg, rgba(18,27,36,.92), rgba(10,16,22,.94));
  }
  .editor-stage-head, .panel-head {
    min-height:58px; padding:0 22px; border-color:rgba(151,170,181,.12);
    background:linear-gradient(180deg, rgba(21,31,40,.7), rgba(12,18,25,.3));
  }
  .panel-title { font-size:17px; font-weight:780; letter-spacing:-.02em; color:#EFF7FC; }
  .panel-hint { display:none; }
  .event-inspector-summary {
    min-height:100px; padding:18px 22px; border-color:rgba(151,170,181,.1);
    background:linear-gradient(135deg, rgba(255,255,255,.035), rgba(114,232,244,.035));
    grid-template-columns:minmax(0,1fr) auto;
  }
  .event-inspector-summary.root { background:linear-gradient(135deg, rgba(245,190,85,.1), rgba(255,255,255,.02)); }
  .event-head-left { min-width:0; display:flex; align-items:center; gap:18px; }
  .event-alert-dot {
    flex:0 0 auto; width:36px; height:36px; border-radius:999px; display:grid; place-items:center;
    color:var(--rose); border:2px solid rgba(242,109,94,.8); font-weight:900;
    box-shadow:0 0 24px rgba(242,109,94,.13);
  }
  .status-ok .event-alert-dot {
    color:var(--green); border-color:rgba(110,219,152,.75);
    box-shadow:0 0 22px rgba(110,219,152,.12);
  }
  .status-root .event-alert-dot {
    color:var(--amber); border-color:rgba(245,190,85,.78);
    box-shadow:0 0 22px rgba(245,190,85,.14);
  }
  .event-head-right {
    display:grid; grid-template-columns:auto 82px 92px; align-items:center; gap:26px;
  }
  .event-metric strong { display:block; color:#F0F7FA; font-size:16px; font-weight:760; }
  .event-metric span { display:block; margin-top:4px; color:#8D9BA6; font-size:12px; }
  .event-inspector-title { font-size:30px; font-weight:850; letter-spacing:-.035em; }
  .event-number { color:#F0F7FA; margin-right:18px; }
  .event-chevron { color:#71818D; margin:0 10px; }
  .event-inspector-sub { color:#9FAEB9; font-size:14px; }
  .inspector-tabs {
    height:54px; padding:0 22px; gap:34px; background:rgba(8,13,19,.5);
    border-color:rgba(151,170,181,.1);
  }
  .inspector-tab {
    height:54px; color:#8595A1; font-weight:760; font-size:14px;
    transition:color .2s ease, border-color .2s ease, transform .2s ease;
  }
  .inspector-tab:hover { color:#E8F6FB; transform:translateY(-1px); }
  .inspector-tab.active { color:var(--cyan); border-color:var(--cyan); }
  .inspector-pane { padding:18px 22px; background:transparent; animation:fadeSlide .22s ease-out both; }
  @keyframes fadeSlide { from { opacity:0; transform:translateY(5px); } to { opacity:1; transform:none; } }
  .inspector-grid { grid-template-columns:390px minmax(0,1fr); gap:14px; }
  .inspector-pane[data-pane="summary"] .inspector-grid {
    display:flex;
    flex-direction:column;
    gap:12px;
  }
  .inspector-pane[data-pane="summary"] .inspector-card { display:none; }
  .summary-primary,
  .summary-observation,
  .summary-plan {
    grid-column:auto;
    grid-row:auto;
    width:100%;
  }
  .summary-primary { min-height:180px; }
  .summary-observation, .summary-plan { min-height:96px; }
  .summary-block, .inspector-card {
    border-radius:10px; border-color:rgba(151,170,181,.12);
    background:rgba(8,14,20,.58); box-shadow:inset 0 1px 0 rgba(255,255,255,.035);
  }
  .summary-block { padding:18px; }
  .summary-block h3 { color:#F0F7FA; font-size:16px; margin-bottom:10px; }
  .summary-block p, .inspector-value { color:#D4DEE5; font-size:15px; line-height:1.58; }
  .summary-detail-block {
    min-height:0;
    background:rgba(8,14,20,.42);
  }
  .summary-detail-block + .summary-detail-block { margin-top:12px; }
  .detail-field {
    padding:12px 0;
    border-top:1px solid rgba(151,170,181,.1);
  }
  .detail-field:first-of-type { border-top:0; padding-top:4px; }
  .detail-field.danger {
    padding:12px;
    border:1px solid rgba(242,109,94,.28);
    border-radius:10px;
    background:rgba(242,109,94,.08);
  }
  .detail-field p {
    margin:6px 0 0;
    white-space:pre-wrap;
    overflow-wrap:anywhere;
  }
  .context-stack {
    display:flex;
    flex-direction:column;
    gap:6px;
  }
  .diagnosis-panel {
    border-left:1px solid rgba(245,190,85,.45);
    background:
      radial-gradient(circle at 8% 0%, rgba(245,190,85,.1), transparent 28%),
      linear-gradient(145deg, rgba(18,27,36,.93), rgba(10,16,22,.96));
  }
  .diagnosis-panel.compact-clean {
    border-left-color:rgba(110,219,152,.34);
    background:
      radial-gradient(circle at 8% 0%, rgba(110,219,152,.08), transparent 28%),
      linear-gradient(145deg, rgba(16,25,31,.9), rgba(9,15,21,.94));
  }
  .diagnosis-panel.compact-clean .diagnosis-section {
    padding-top:16px !important;
    padding-bottom:16px !important;
  }
  .diagnosis-next-step { border-top:1px solid rgba(151,170,181,.1); }
  .diagnosis-hero {
    background:
      radial-gradient(circle at 6% 10%, rgba(245,190,85,.16), transparent 35%),
      linear-gradient(135deg, rgba(245,190,85,.08), rgba(255,255,255,.02));
  }
  .diagnosis-facts {
    display:grid;
    grid-template-columns:repeat(3, minmax(0, 1fr));
    gap:10px;
    padding-top:14px !important;
    padding-bottom:14px !important;
  }
  .diagnosis-facts .mini {
    min-width:0;
    border:1px solid rgba(151,170,181,.12);
    border-radius:10px;
    background:rgba(8,14,20,.58);
    padding:10px 12px;
  }
  .diagnosis-facts .mini-label {
    font-size:11px;
    color:#7F8E99;
    text-transform:uppercase;
    letter-spacing:.06em;
  }
  .diagnosis-facts .mini-value {
    margin-top:5px;
    color:#EAF4FA;
    font-size:13px;
    font-weight:720;
    overflow:hidden;
    text-overflow:ellipsis;
    white-space:nowrap;
  }
  .timeline-toolbar-quiet { gap:8px !important; }
  .timeline-tool-group {
    display:inline-flex;
    align-items:center;
    gap:4px;
    padding:3px;
    border:1px solid rgba(151,170,181,.12);
    border-radius:12px;
    background:rgba(7,12,18,.42);
  }
  .timeline-tool-primary {
    border-color:rgba(114,232,244,.28) !important;
    color:#DDFBFF !important;
    background:rgba(15,35,44,.72) !important;
  }
  .inspector-actions { display:flex; flex-wrap:wrap; gap:8px; margin:0 0 12px; }
  .summary-block summary {
    cursor:pointer; list-style:none; color:#F0F7FA; font-size:16px; font-weight:760;
  }
  .summary-block summary::-webkit-details-marker { display:none; }
  .summary-block summary::after { content:"collapse"; float:right; color:#7f8e99; font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.06em; }
  .summary-block:not([open]) summary::after { content:"expand"; }
  .timeline-track.hidden-track, .timeline-minimap.hidden-track,
  .timeline-overview.hidden-track { display:none !important; }
  .track-label.hidden-track, .timeline-minimap-label.hidden-track,
  .timeline-overview-label.hidden-track { opacity:.36; }
  .toast {
    position:fixed; right:22px; bottom:calc(var(--timeline-h) + 34px); z-index:120;
    max-width:320px; padding:12px 14px; border-radius:12px;
    background:rgba(13,22,30,.94); border:1px solid rgba(114,232,244,.26);
    color:#EAF7FA; box-shadow:0 18px 50px rgba(0,0,0,.34);
    opacity:0; transform:translateY(10px); pointer-events:none;
    transition:opacity .22s ease, transform .22s ease;
  }
  .toast.visible { opacity:1; transform:none; }
  .diagnosis-section { padding:20px 24px; border-color:rgba(151,170,181,.1); }
  .diagnosis-label { color:#A8B6C0; font-size:13px; text-transform:none; }
  .diagnosis-title { font-size:17px; color:#EEF6FA; }
  .diagnosis-copy, .evidence-list { color:#C6D2DB; font-size:15px; }
  .related-link {
    height:32px; border-radius:9px; background:rgba(12,19,26,.74);
    border-color:rgba(151,170,181,.13); transition:all .18s ease;
  }
  .related-link:hover { color:#EAFEFF; background:rgba(114,232,244,.1); transform:translateY(-1px); }
  .debug-resume-btn {
    border-color:rgba(114,232,244,.38) !important;
    color:#E8FEFF !important;
    background:
      radial-gradient(circle at 18% 0%, rgba(114,232,244,.2), transparent 52%),
      rgba(16,48,58,.82) !important;
  }
  .debug-resume-btn:hover {
    border-color:rgba(114,232,244,.66) !important;
    box-shadow:0 14px 34px rgba(0,0,0,.26), 0 0 24px rgba(114,232,244,.13) !important;
  }
  .report-select {
    min-height:34px; max-width:260px; padding:0 30px 0 10px;
    border:1px solid var(--line); border-radius:6px;
    background:var(--surface-2); color:var(--text); font:inherit;
  }
  button:disabled, select:disabled { opacity:.48; cursor:not-allowed; }
  .continuation-modal {
    position:fixed; inset:0; z-index:160; display:grid; place-items:center;
    padding:28px; background:rgba(2,7,11,.64); backdrop-filter:blur(10px);
    opacity:0; pointer-events:none; transition:opacity .22s ease;
  }
  .continuation-modal.visible { opacity:1; pointer-events:auto; }
  .continuation-shell {
    width:min(1160px, 100%); max-height:min(780px, calc(100vh - 56px));
    display:grid; grid-template-rows:auto minmax(0,1fr) auto;
    border:1px solid rgba(114,232,244,.28); border-radius:18px; overflow:hidden;
    background:
      radial-gradient(circle at 18% 0%, rgba(114,232,244,.16), transparent 34%),
      linear-gradient(145deg, rgba(18,29,38,.98), rgba(7,13,20,.98));
    box-shadow:0 34px 100px rgba(0,0,0,.52), inset 0 1px 0 rgba(255,255,255,.06);
    transform:translateY(10px) scale(.985); transition:transform .22s ease;
  }
  .continuation-modal.visible .continuation-shell { transform:none; }
  .continuation-head {
    min-height:72px; padding:18px 20px; display:flex; align-items:flex-start;
    justify-content:space-between; gap:16px; border-bottom:1px solid rgba(151,170,181,.12);
    background:linear-gradient(180deg, rgba(255,255,255,.035), transparent);
  }
  .continuation-kicker {
    color:var(--cyan); font-size:11px; font-weight:860; text-transform:uppercase; letter-spacing:.09em;
  }
  .continuation-title { margin-top:5px; color:#EFF7FC; font-size:22px; font-weight:880; letter-spacing:-.03em; }
  .continuation-sub { margin-top:5px; color:#AAB8C2; font-size:13px; }
  .continuation-close {
    width:36px; height:36px; border-radius:10px; border:1px solid rgba(151,170,181,.16);
    background:rgba(8,14,20,.55); color:#C8D6DF; cursor:pointer; font-size:18px;
  }
  .continuation-body {
    overflow:auto; padding:18px 20px; display:grid; grid-template-columns:540px minmax(0,1fr);
    gap:14px;
  }
  .continuation-card {
    border:1px solid rgba(151,170,181,.14); border-radius:14px; padding:14px;
    background:rgba(8,14,20,.46); box-shadow:inset 0 1px 0 rgba(255,255,255,.035);
  }
  .continuation-card.primary { border-color:rgba(114,232,244,.26); background:rgba(12,35,43,.46); }
  .continuation-label {
    color:#8797A2; font-size:10px; text-transform:uppercase; letter-spacing:.08em; font-weight:760;
  }
  .continuation-value { margin-top:7px; color:#EAF4FA; font-size:14px; line-height:1.48; overflow-wrap:anywhere; }
  .continuation-metrics { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; margin-top:12px; }
  .continuation-builder { display:flex; flex-direction:column; gap:12px; }
  .composer-section {
    border:1px solid rgba(151,170,181,.13); border-radius:12px; padding:15px;
    background:rgba(8,14,20,.36);
  }
  .composer-section.locked {
    border-color:rgba(114,232,244,.22);
    background:rgba(12,35,43,.28);
  }
  .composer-row {
    display:flex; align-items:flex-start; justify-content:space-between; gap:10px;
  }
  .composer-check {
    display:flex; align-items:center; gap:10px; color:#EAF4FA; font-size:15px; font-weight:820;
  }
  .composer-check input { width:17px; height:17px; accent-color:var(--cyan); }
  .composer-help { margin-top:5px; color:#84939E; font-size:12px; line-height:1.45; }
  .composer-textarea,
  .composer-input,
  .composer-select {
    width:100%; margin-top:9px; border:1px solid rgba(151,170,181,.16); border-radius:10px;
    background:rgba(5,10,15,.56); color:#EAF4FA; font:12px/1.5 ui-monospace, SFMono-Regular, Consolas, monospace;
    padding:10px; outline:none; transition:border-color .16s ease, box-shadow .16s ease, background .16s ease;
  }
  .composer-textarea:focus,
  .composer-input:focus,
  .composer-select:focus {
    border-color:rgba(114,232,244,.48);
    box-shadow:0 0 0 3px rgba(114,232,244,.09);
  }
  .composer-input { height:46px; font-family:inherit; font-size:14px; font-weight:760; }
  .composer-textarea.small { min-height:92px; resize:vertical; }
  .composer-textarea.prompt { min-height:132px; resize:vertical; }
  .composer-select { min-height:82px; }
  .composer-select[disabled],
  .composer-textarea[disabled] {
    display:none;
  }
  .composer-compact-options {
    display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:10px;
  }
  .composer-compact-options .composer-section { min-height:58px; }
  .composer-pill-row { display:flex; flex-wrap:wrap; gap:6px; margin-top:9px; }
  .composer-pill {
    border:1px solid rgba(114,232,244,.22); color:#D6FCFF; background:rgba(114,232,244,.08);
    border-radius:999px; padding:4px 8px; font-size:11px; font-weight:760;
  }
  .composer-pill.locked { border-color:rgba(110,219,152,.24); color:#C8F8D8; background:rgba(110,219,152,.08); }
  .continuation-prompt {
    width:100%; min-height:500px; margin:0; resize:vertical; white-space:pre-wrap; overflow:auto;
    border:0; background:transparent; outline:none;
    color:#DDE8ED; font:12px/1.55 ui-monospace, SFMono-Regular, Consolas, monospace;
  }
  .continuation-inline-status {
    margin:0 20px 0 20px; padding:12px 14px; border-top:1px solid rgba(151,170,181,.12);
    color:#9fdae6; font-size:13px; line-height:1.5;
    background:rgba(8,14,20,.28);
  }
  .continuation-actions {
    padding:14px 20px; display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap;
    border-top:1px solid rgba(151,170,181,.12); background:rgba(8,14,20,.45);
  }
  .continuation-action-group { display:flex; gap:8px; flex-wrap:wrap; }
  .workflow-modal-shell { width:min(760px, 100%); }
  .workflow-modal-body { display:flex; flex-direction:column; gap:14px; padding:20px; overflow:auto; }
  .workflow-copy { margin:0; color:#AAB8C2; font-size:13px; line-height:1.55; }
  .workflow-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
  .workflow-grid .wide { grid-column:1 / -1; }
  .password-field { position:relative; width:100%; margin-top:9px; }
  .password-field.wide { grid-column:1 / -1; }
  .password-field .composer-input { margin-top:0; padding-right:48px; }
  .password-toggle {
    position:absolute; top:50%; right:6px; width:36px; height:34px; padding:0;
    display:grid; place-items:center; transform:translateY(-50%); border:0; border-radius:7px;
    background:transparent; color:#91A2AD; cursor:pointer;
  }
  .password-toggle:hover { color:#E4FDFF; background:rgba(114,232,244,.09); }
  .password-toggle:focus-visible { outline:2px solid var(--cyan); outline-offset:1px; }
  .password-toggle svg { width:18px; height:18px; fill:none; stroke:currentColor; stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round; }
  .settings-modal-shell { width:min(620px, 100%); }
  .settings-fields { display:flex; flex-direction:column; gap:12px; }
  .settings-field { display:block; }
  .settings-field .composer-input { margin-top:7px; }
  .settings-field .password-field { margin-top:7px; }
  .top-actions .llm-settings-button { display:inline-flex; align-items:center; gap:7px; }
  .llm-settings-button svg { width:15px; height:15px; fill:none; stroke:currentColor; stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round; }
  .mode-switch {
    display:inline-grid; grid-auto-flow:column; grid-auto-columns:1fr; gap:4px; padding:4px;
    border:1px solid rgba(151,170,181,.14); border-radius:10px; background:rgba(5,10,15,.5);
  }
  .mode-switch button {
    min-height:36px; padding:0 14px; border:0; border-radius:7px; cursor:pointer;
    background:transparent; color:#9EADB8; font-weight:760;
  }
  .mode-switch button.active { background:rgba(114,232,244,.14); color:#E8FEFF; }
  .rerun-mode-panel[hidden] { display:none !important; }
  .upload-drop {
    min-height:150px; display:grid; place-items:center; padding:24px; text-align:center;
    border:1px dashed rgba(114,232,244,.36); border-radius:12px; background:rgba(12,35,43,.24);
  }
  .upload-drop.dragging { border-color:var(--cyan); background:rgba(114,232,244,.1); }
  .session-modal {
    position:fixed; inset:0; z-index:155; display:grid; place-items:center;
    padding:28px; background:rgba(2,7,11,.58); backdrop-filter:blur(9px);
    opacity:0; pointer-events:none; transition:opacity .22s ease;
  }
  .session-modal.visible { opacity:1; pointer-events:auto; }
  .session-shell {
    width:min(1180px, 100%); max-height:min(760px, calc(100vh - 56px));
    display:grid; grid-template-rows:auto minmax(0,1fr);
    border:1px solid rgba(114,232,244,.24); border-radius:18px; overflow:hidden;
    background:
      radial-gradient(circle at 15% 0%, rgba(114,232,244,.14), transparent 34%),
      linear-gradient(145deg, rgba(15,26,35,.98), rgba(6,12,19,.98));
    box-shadow:0 34px 100px rgba(0,0,0,.48), inset 0 1px 0 rgba(255,255,255,.06);
    transform:translateY(10px) scale(.985); transition:transform .22s ease;
  }
  .session-modal.visible .session-shell { transform:none; }
  .session-head {
    padding:18px 22px; display:flex; align-items:flex-start; justify-content:space-between;
    gap:16px; border-bottom:1px solid rgba(151,170,181,.12);
    background:linear-gradient(180deg, rgba(255,255,255,.035), transparent);
  }
  .session-kicker {
    color:var(--cyan); font-size:11px; font-weight:860; text-transform:uppercase; letter-spacing:.09em;
  }
  .session-title { margin-top:5px; color:#F1F8FC; font-size:22px; font-weight:880; letter-spacing:-.03em; }
  .session-sub { margin-top:5px; color:#9FB0BC; font-size:13px; }
  .session-close {
    width:36px; height:36px; border-radius:10px; border:1px solid rgba(151,170,181,.16);
    background:rgba(8,14,20,.55); color:#C8D6DF; cursor:pointer; font-size:18px;
  }
  .session-body {
    min-height:0; overflow:auto; padding:18px 22px; display:grid;
    grid-template-columns:minmax(340px, .85fr) minmax(0, 1.15fr); gap:14px;
  }
  .session-list {
    min-height:0; display:flex; flex-direction:column; gap:10px;
  }
  .session-card {
    text-align:left; border:1px solid rgba(151,170,181,.14); border-radius:14px;
    padding:14px; background:rgba(8,14,20,.5); color:#EAF4FA; cursor:pointer;
    transition:transform .18s ease, border-color .18s ease, background .18s ease;
  }
  .session-card:hover,
  .session-card.active {
    transform:translateY(-1px); border-color:rgba(114,232,244,.44);
    background:rgba(14,35,43,.68);
  }
  .session-card-title {
    display:flex; align-items:center; justify-content:space-between; gap:10px;
    font-weight:840; color:#F2FAFD;
  }
  .session-card-meta { margin-top:7px; color:#97A8B4; font-size:12px; line-height:1.45; }
  .session-eval-grid {
    margin-top:10px; display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px;
  }
  .session-mini {
    border:1px solid rgba(151,170,181,.12); border-radius:10px; padding:8px;
    background:rgba(255,255,255,.025);
  }
  .session-mini span {
    display:block; color:#81929F; font-size:10px; text-transform:uppercase; letter-spacing:.07em;
  }
  .session-mini strong { display:block; margin-top:4px; color:#EFF8FC; font-size:15px; }
  .session-detail {
    min-height:0; border:1px solid rgba(151,170,181,.14); border-radius:16px;
    overflow:hidden; background:rgba(8,14,20,.46);
  }
  .session-detail-head {
    padding:16px 18px; border-bottom:1px solid rgba(151,170,181,.12);
    display:flex; align-items:flex-start; justify-content:space-between; gap:12px;
  }
  .session-detail-body { padding:16px 18px; display:grid; gap:14px; }
  .session-result.resolved,
  .session-result.improved { border-color:rgba(110,219,152,.34); color:#CFF9DE; background:rgba(110,219,152,.09); }
  .session-result.unchanged,
  .session-result.unknown { border-color:rgba(151,170,181,.2); color:#D6E2E8; background:rgba(151,170,181,.08); }
  .session-result.worse { border-color:rgba(242,109,94,.34); color:#FFD8D6; background:rgba(242,109,94,.1); }
  .session-actions { display:flex; flex-wrap:wrap; gap:8px; }
  .compare-grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
  .compare-column {
    min-height:260px; border:1px solid rgba(151,170,181,.13); border-radius:14px;
    padding:14px; background:rgba(4,9,14,.36);
  }
  .compare-title { color:#F0F8FC; font-size:15px; font-weight:820; margin-bottom:10px; }
  .compare-event {
    border:1px solid rgba(151,170,181,.12); border-radius:10px; padding:9px 10px;
    background:rgba(255,255,255,.025); margin-bottom:8px;
  }
  .compare-event.error { border-color:rgba(242,109,94,.32); background:rgba(242,109,94,.08); }
  .compare-event.root { border-color:rgba(245,190,85,.34); background:rgba(245,190,85,.08); }
  .compare-event.ok { border-color:rgba(110,219,152,.22); }
  .compare-event-title { display:flex; justify-content:space-between; gap:8px; color:#EAF4FA; font-weight:760; }
  .compare-event-copy { margin-top:5px; color:#AAB8C2; font-size:12px; line-height:1.45; }
  .compare-summary {
    border:1px solid rgba(114,232,244,.18); border-radius:14px; padding:12px;
    background:rgba(114,232,244,.06); color:#CFEFF3; font-size:13px; line-height:1.5;
  }
  .timeline-dock {
    height:276px; padding:0; background:
      radial-gradient(circle at 38% 0%, rgba(114,232,244,.09), transparent 36%),
      linear-gradient(180deg, rgba(13,21,29,.96), rgba(7,12,18,.98));
    border-color:rgba(151,170,181,.13);
  }
  .timeline-toolbar { gap:10px; }
  .timeline-legend-dot {
    width:15px; height:15px; border-radius:999px; display:inline-grid; place-items:center;
    border:2px solid currentColor; color:var(--violet); margin-left:6px;
  }
  .timeline-legend-dot.warning { color:var(--amber); border-radius:4px; transform:rotate(45deg) scale(.82); }
  .timeline-legend-dot.info { color:var(--green); }
  .timeline-legend-dot.error { color:var(--rose); }
  .timeline-editor {
    grid-template-columns:134px minmax(0,1fr); grid-template-rows:34px 32px 32px 32px 32px 32px 32px minmax(58px,1fr);
    background:rgba(5,9,14,.28);
  }
  .timeline-minimap-label, .track-label, .timeline-overview-label {
    padding:0 20px; background:rgba(9,15,21,.7); color:#AAB8C2;
    border-color:rgba(151,170,181,.1); font-weight:720;
    border-right:1px solid rgba(151,170,181,.1); border-bottom:1px solid rgba(151,170,181,.1);
    display:flex; align-items:center; text-transform:none;
  }
  .timeline-track {
    background:
      repeating-linear-gradient(90deg, rgba(114,232,244,.05) 0 1px, transparent 1px 96px),
      rgba(5,9,14,.54);
    border-color:rgba(151,170,181,.08);
  }
  .track-strip { gap:8px; padding:0 18px; }
  .track-strip::before { left:18px; right:18px; background:rgba(114,232,244,.14); }
  .track-clip {
    width:calc(76px * var(--zoom, 1)); min-width:52px; max-width:118px;
    border-radius:8px; border-color:rgba(151,170,181,.12); background:rgba(16,25,34,.82);
    color:#C8D7E0; transition:transform .18s ease, border-color .18s ease, background .18s ease, box-shadow .18s ease;
  }
  .timeline-track.reasoning-lane .track-clip {
    height:9px; border-radius:2px; color:transparent; padding:0;
    background:linear-gradient(180deg, rgba(110,219,152,.45), rgba(76,151,106,.42));
    border-color:rgba(110,219,152,.18); border-top-width:1px;
  }
  .timeline-track.action-lane .track-clip,
  .timeline-track.observation-lane .track-clip {
    height:24px; border-radius:7px; font-size:11px; background:rgba(18,28,37,.78);
  }
  .timeline-track.detector-lane .track-clip {
    height:24px; border-radius:7px; font-size:11px; padding:0 5px;
    color:#C8D7E0; border-top-width:2px;
  }
  .timeline-track.detector-lane .track-clip::before {
    content:""; display:none;
  }
  .timeline-track.detector-lane .track-clip.error::before,
  .timeline-track.detector-lane .track-clip.root::before { content:""; display:none; }
  .timeline-track.marker-lane .track-clip.error,
  .timeline-track.marker-lane .track-clip.root {
    width:32px; min-width:32px; max-width:32px; border-radius:999px;
  }
  .timeline-overview {
    min-width:0; overflow-x:auto; overflow-y:hidden; padding:12px 18px;
    border-bottom:1px solid rgba(151,170,181,.1); background:rgba(8,13,19,.72);
    scrollbar-width:thin;
  }
  .overview-strip {
    min-width:max-content; height:42px; display:flex; align-items:center; gap:7px;
    border:1px solid rgba(151,170,181,.14); border-radius:10px; padding:0 14px;
    background:linear-gradient(180deg, rgba(13,22,30,.72), rgba(7,13,19,.78));
  }
  .overview-dot {
    width:7px; height:7px; flex:0 0 auto; border:0; border-radius:2px;
    background:rgba(114,232,244,.32); cursor:pointer;
    transition:transform .16s ease, background .16s ease, box-shadow .16s ease;
  }
  .overview-dot.error { background:rgba(242,109,94,.72); }
  .overview-dot.root { background:rgba(245,190,85,.88); }
  .overview-dot:hover, .overview-dot.active {
    transform:scale(1.8); box-shadow:0 0 18px currentColor;
  }
  .track-clip:hover { transform:translateY(-1px); border-color:rgba(114,232,244,.35); }
  .track-clip.ok { border-top-color:rgba(110,219,152,.8); }
  .track-clip.error { border-top-color:var(--rose); background:rgba(63,28,32,.7); }
  .track-clip.root { border-top-color:var(--amber); background:rgba(64,45,19,.78); }
  .track-clip.active {
    border-color:var(--cyan); box-shadow:0 0 0 1px rgba(114,232,244,.36), 0 0 28px rgba(114,232,244,.16);
  }
  .mini-segment {
    width:calc(76px * var(--zoom, 1)); min-width:52px; max-width:118px; height:12px;
    border-radius:999px; opacity:.72; transition:transform .18s ease, opacity .18s ease, box-shadow .18s ease;
  }
  .mini-segment:hover { transform:scaleY(1.35); opacity:1; }
  .playhead {
    width:2px; background:var(--cyan); box-shadow:0 0 18px rgba(114,232,244,.58);
  }
  .playhead::before {
    top:-24px; padding:3px 7px; border-radius:7px; background:rgba(114,232,244,.16);
    border:1px solid rgba(114,232,244,.34); color:#D9FCFF;
  }
  .timeline-advanced-tracks summary { color:#9EACB7; background:rgba(9,15,21,.72); }
  .advanced-track-grid { grid-template-columns:134px minmax(0,1fr); }
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation:none !important; transition:none !important; scroll-behavior:auto !important; }
  }
  /* 1.png pixel-pass: lock major regions to the product mockup proportions. */
  :root {
    --topbar-h:74px;
    --rail-w:0px;
    --navigator-w:430px;
    --diagnosis-w:clamp(440px, 34vw, 720px);
    --timeline-h:clamp(320px, 34vh, 460px);
    --gutter:12px;
  }
  .shell {
    grid-template-columns:var(--rail-w) var(--navigator-w) minmax(0,1fr) !important;
    padding-top:var(--topbar-h);
  }
  .topbar {
    position:fixed !important; z-index:90; top:0; left:0; right:0;
    height:var(--topbar-h) !important; padding:0 26px 0 270px !important;
    background:linear-gradient(180deg, rgba(9,15,23,.96), rgba(6,11,17,.92)) !important;
    border-bottom:1px solid rgba(114,232,244,.12) !important;
    box-shadow:0 14px 42px rgba(0,0,0,.28), inset 0 1px 0 rgba(255,255,255,.045);
  }
  .topbar::before {
    content:"AgentDebugX"; position:absolute; left:82px; top:0; height:var(--topbar-h);
    display:flex; align-items:center; color:#EEF7FC; font-size:24px; font-weight:850;
    letter-spacing:-.035em;
  }
  .topbar::after {
    display:none;
  }
  .top-brand-avatar {
    position:absolute; z-index:2; left:24px; top:16px; width:42px; height:42px;
    border:1px solid rgba(114,232,244,.34); border-radius:12px; background:#08131f;
    box-shadow:0 0 24px rgba(114,232,244,.08); padding:0; cursor:pointer;
  }
  .top-brand-avatar img { width:100%; height:100%; display:block; border-radius:inherit; }
  .top-brand-avatar::after {
    content:""; position:absolute; right:-2px; bottom:-2px; width:9px; height:9px;
    border-radius:999px; background:var(--green); box-shadow:0 0 0 3px #081018;
  }
  .top-brand-avatar:hover { border-color:rgba(114,232,244,.7); background:#0C1A26; }
  .top-brand-avatar:focus-visible { outline:2px solid var(--cyan); outline-offset:3px; }
  .top-brand-avatar[aria-expanded="true"] { border-color:var(--cyan); background:rgba(114,232,244,.1); }
  .offline-popover {
    left:24px; top:calc(var(--topbar-h) + 10px); bottom:auto; width:292px;
    transform:translateY(-8px) scale(.98);
  }
  .offline-popover::before {
    left:20px; top:-7px; bottom:auto; border:0;
    border-left:1px solid rgba(114,232,244,.26); border-top:1px solid rgba(114,232,244,.26);
  }
  .workspace-launcher {
    height:34px; padding:0 11px;
    display:inline-flex; align-items:center; gap:7px; border:1px solid var(--line2);
    border-radius:8px; background:#101923; color:#D6E1E8; font:650 13px/1 inherit; cursor:pointer;
  }
  .workspace-launcher svg { width:15px; height:15px; fill:none; stroke:currentColor; stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round; }
  .workspace-launcher:hover, .workspace-launcher.drawer-active { border-color:var(--cyan); color:#DDFDFF; background:rgba(114,232,244,.1); }
  .workspace-launcher:focus-visible { outline:2px solid var(--cyan); outline-offset:2px; }
  .topbar .crumb { font-size:14px; color:#A7B7C2; }
  .topbar .crumb::before { content:"" !important; }
  .topbar .brand-sub { margin-top:4px; font-size:13px; color:#7E8D98; }
  .icon-rail, .sidebar, .workspace { height:calc(100vh - var(--topbar-h)); }
  .icon-rail {
    padding-top:34px !important; background:linear-gradient(180deg, rgba(7,13,21,.98), rgba(5,10,16,.94)) !important;
  }
  .rail-logo { display:none; }
  .rail-nav { margin-top:0; gap:22px; }
  .rail-btn { font-size:19px; }
  .sidebar {
    padding:30px 22px 22px !important;
    background:linear-gradient(180deg, rgba(14,22,30,.97), rgba(9,16,23,.95)) !important;
  }
  .side-section-title { font-size:20px !important; margin-bottom:18px !important; }
  .side-section-head {
    min-height:34px; display:flex; align-items:flex-start; justify-content:space-between; gap:12px;
  }
  .side-section-head .side-section-title { margin-bottom:18px !important; }
  .run-save-case {
    position:absolute; top:14px; right:14px; z-index:1;
    width:30px; height:30px; display:grid; place-items:center; padding:0;
    border:1px solid rgba(142,162,176,.2); border-radius:8px; color:#AFC0CB;
    background:rgba(11,18,25,.7); cursor:pointer;
    transition:border-color .15s ease, color .15s ease, background .15s ease;
  }
  .run-save-case svg {
    width:15px; height:15px; fill:none; stroke:currentColor; stroke-width:1.8;
    stroke-linecap:round; stroke-linejoin:round;
  }
  .run-save-case:hover:not(:disabled) { color:#E4FDFF; border-color:rgba(114,232,244,.52); background:rgba(114,232,244,.1); }
  .run-save-case:focus-visible { outline:2px solid var(--cyan); outline-offset:2px; }
  .run-save-case:disabled { opacity:.32; cursor:not-allowed; }
  .run-search-shell { grid-template-columns:minmax(0,1fr) 52px; gap:12px; margin-bottom:20px; }
  .run-search, .run-filter-btn { height:46px; border-radius:10px; }
  .run-list { gap:12px; }
  .run {
    position:relative; min-height:114px !important; padding:18px 54px 18px 20px !important; border-radius:10px !important;
    background:linear-gradient(145deg, rgba(19,29,39,.84), rgba(10,17,24,.86)) !important;
  }
  .run.active {
    background:linear-gradient(145deg, rgba(22,54,65,.8), rgba(12,25,34,.94)) !important;
    box-shadow:0 0 0 1px rgba(114,232,244,.36), inset 4px 0 0 var(--cyan), 0 22px 64px rgba(0,0,0,.34) !important;
  }
  .run-id { font-size:14px !important; }
  .run-meta { color:#9FAEB9; font-size:13px; }
  .workspace { padding:0; }
  .content {
    height:calc(100vh - var(--topbar-h) - var(--timeline-h) - var(--gutter) * 2) !important;
    padding:var(--gutter) 14px 0 !important;
  }
  body.trace-editor-mode .workspace {
    height:calc(100vh - var(--topbar-h)) !important;
  }
  body.trace-editor-mode .content {
    height:calc(100vh - var(--topbar-h) - var(--timeline-h) - var(--gutter) * 2) !important;
    padding:var(--gutter) 14px 0 !important;
  }
  body.trace-editor-mode #detail { height:100% !important; }
  body.trace-editor-mode .sidebar { padding-bottom:22px !important; }
  .editor-workbench {
    grid-template-columns:minmax(0,1fr) 612px !important;
    gap:12px !important; height:100%;
  }
  .editor-stage, .diagnosis-panel {
    border-radius:10px !important; min-height:0;
  }
  .editor-stage { grid-template-rows:64px 1fr !important; }
  .editor-stage-head, .diagnosis-panel .diagnosis-section:first-child {
    min-height:64px;
  }
  .event-inspector-summary {
    min-height:120px !important; padding:20px 28px !important;
  }
  .event-inspector-title {
    font-size:29px !important; display:flex; align-items:center; gap:12px; flex-wrap:wrap;
  }
  .event-head-right { grid-template-columns:74px 84px 100px !important; gap:26px !important; }
  .inspector-tabs { height:58px !important; padding:0 28px !important; gap:42px !important; }
  .inspector-pane { padding:20px 28px !important; }
  .summary-primary { min-height:250px; }
  .summary-observation, .summary-plan { min-height:120px; }
  .diagnosis-panel { border-left:1px solid rgba(245,190,85,.72) !important; }
  .diagnosis-section { padding:22px 28px !important; }
  .diagnosis-title { font-size:18px !important; }
  .timeline-dock {
    position:fixed !important; z-index:60;
    left:calc(var(--rail-w) + var(--navigator-w) + 12px) !important;
    right:14px !important; bottom:14px !important; height:var(--timeline-h) !important;
    grid-template-rows:66px minmax(0,1fr) !important;
    border-radius:10px !important;
  }
  body.trace-editor-mode .timeline-dock {
    position:fixed !important;
    left:calc(var(--rail-w) + var(--navigator-w) + 12px) !important;
    right:14px !important;
    bottom:14px !important;
    height:var(--timeline-h) !important;
    margin:0 !important;
  }
  .timeline-dock .panel-head { height:66px; min-height:66px; padding:0 28px !important; }
  .timeline-editor {
    grid-template-columns:142px minmax(0,1fr) !important;
    grid-template-rows:46px minmax(56px, 72px) !important;
    height:100% !important;
  }
  .timeline-editor.has-branches {
    grid-template-rows:44px minmax(56px, 72px) minmax(178px, 1fr) !important;
  }
  .timeline-minimap-label, .track-label, .timeline-overview-label {
    padding:0 24px !important; font-size:13px !important;
  }
  .timeline-minimap,
  .timeline-track {
    min-height:0;
  }
  .timeline-editor.unified {
    display:grid !important;
    grid-template-columns:142px minmax(0,1fr) !important;
    grid-template-rows:1fr !important;
    overflow:hidden !important;
  }
  .timeline-fixed-labels {
    display:grid;
    grid-template-rows:minmax(56px, 72px) minmax(178px, 1fr);
    min-height:0;
    border-right:1px solid rgba(70,94,104,.5);
    background:rgba(8,14,20,.82);
  }
  .timeline-editor.unified:not(.has-branches) .timeline-fixed-labels {
    grid-template-rows:1fr;
  }
  .timeline-fixed-labels .track-label {
    border-right:0 !important;
  }
  .timeline-unified-scroll {
    min-width:0;
    min-height:0;
    overflow:auto;
    scrollbar-color:rgba(114,232,244,.42) rgba(12,18,24,.72);
    scrollbar-width:thin;
    background:
      radial-gradient(circle at 34% 8%, rgba(91,231,246,.045), transparent 34%),
      rgba(5,10,15,.5);
  }
  .timeline-unified-grid {
    min-width:max-content;
    min-height:100%;
    display:grid;
    grid-template-rows:minmax(56px, 72px) minmax(178px, 1fr);
  }
  .timeline-editor.unified:not(.has-branches) .timeline-unified-grid {
    grid-template-rows:1fr;
  }
  .timeline-editor.unified .timeline-track {
    overflow:visible !important;
    border-bottom:1px solid rgba(48,68,76,.52);
  }
  .timeline-editor.unified .track-strip,
  .timeline-editor.unified .branch-track-stack {
    min-width:max-content;
  }
  .track-strip { gap:10px !important; padding:0 22px !important; }
  .track-clip {
    width:calc(86px * var(--zoom, 1)) !important; min-width:70px !important; max-width:132px !important;
  }
  .timeline-track.event-lane .track-clip {
    height:28px !important;
    border-radius:8px !important;
    color:#DDE8ED !important;
    border:1px solid rgba(103, 224, 245, .18) !important;
    border-top:2px solid rgba(103, 224, 245, .55) !important;
    background:linear-gradient(180deg, rgba(20,30,40,.96), rgba(11,18,27,.96)) !important;
    font-size:12px !important;
    font-weight:820 !important;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.04) !important;
  }
  .timeline-track.event-lane .track-clip.ok {
    border-color:rgba(103, 224, 245, .18) !important;
    border-top-color:rgba(103, 224, 245, .55) !important;
    background:linear-gradient(180deg, rgba(20,30,40,.96), rgba(11,18,27,.96)) !important;
  }
  .timeline-track.event-lane .track-clip.error {
    color:#FFD8DE !important;
    border-color:rgba(214, 93, 115, .4) !important;
    border-top-color:rgba(255, 108, 132, .88) !important;
    background:linear-gradient(180deg, rgba(57,25,34,.96), rgba(30,16,21,.96)) !important;
  }
  .timeline-track.event-lane .track-clip.root {
    color:#FFE8BF !important;
    border-color:rgba(227, 177, 86, .42) !important;
    border-top-color:rgba(255, 196, 90, .92) !important;
    background:linear-gradient(180deg, rgba(67,48,19,.96), rgba(33,24,11,.96)) !important;
  }
  .track-gap {
    position:relative; z-index:1;
    width:calc(86px * var(--zoom, 1)) !important; min-width:70px !important; max-width:132px !important;
    height:28px !important; flex:0 0 auto;
  }
  .debug-branch-label {
    color:#73E7F3 !important;
    background:linear-gradient(90deg, rgba(91,231,246,.08), rgba(8,13,18,.02)) !important;
    font-weight:900 !important;
    letter-spacing:.02em;
  }
  .debug-branch-lane {
    overflow:visible !important;
    min-height:178px;
    background:
      linear-gradient(180deg, rgba(89,225,238,.04), transparent),
      repeating-linear-gradient(90deg, rgba(95,231,244,.08) 0 1px, transparent 1px 96px),
      rgba(5,10,15,.64) !important;
  }
  .branch-track-stack {
    min-width:max-content;
    display:flex;
    flex-direction:column;
    gap:14px;
    padding:18px 22px 18px 22px;
  }
  .branch-gap {
    position:relative; z-index:1; flex:0 0 auto;
    width:calc(86px * var(--zoom, 1)) !important; min-width:70px !important; max-width:132px !important;
    height:30px !important;
  }
  .branch-sequence-row {
    min-width:max-content;
    display:flex;
    align-items:center;
    gap:10px;
    position:relative;
    min-height:42px;
  }
  .branch-sequence-row.mode-plan { --branch-color:#72E8F4; }
  .branch-sequence-row.mode-simulate { --branch-color:#A884FF; }
  .branch-sequence-row.mode-live { --branch-color:#6EDB98; }
  .branch-sequence-row.mode-plan .branch-origin-chip { border-style:dashed; }
  .branch-sequence-row.mode-simulate .branch-origin-chip { background:linear-gradient(180deg, rgba(58,40,92,.98), rgba(20,15,34,.98)); }
  .branch-sequence-row.mode-live .branch-origin-chip { background:linear-gradient(180deg, rgba(29,70,47,.98), rgba(10,28,19,.98)); }
  .branch-sequence-row::before {
    content:"";
    position:absolute;
    left:calc((var(--branch-start, 0) * ((86px * var(--zoom, 1)) + 10px)) + 36px);
    right:0;
    top:50%;
    height:1px;
    background:linear-gradient(90deg, color-mix(in srgb, var(--branch-color, #72E8F4) 62%, transparent), color-mix(in srgb, var(--branch-color, #72E8F4) 24%, transparent));
    transform:translateY(-50%);
    pointer-events:none;
  }
  .branch-origin-chip {
    position:absolute;
    z-index:3;
    left:calc(var(--branch-start, 0) * ((86px * var(--zoom, 1)) + 10px));
    top:-13px;
    height:18px;
    min-width:58px;
    padding:0 8px;
    border-radius:999px;
    border:1px solid color-mix(in srgb, var(--branch-color, #72E8F4) 58%, transparent);
    background:linear-gradient(180deg, color-mix(in srgb, var(--branch-color, #72E8F4) 20%, rgba(17,23,30,.98)), rgba(8,14,22,.98));
    color:#F4FEFF;
    font-size:10px;
    font-weight:860;
    font-family:inherit;
    box-shadow:0 8px 20px rgba(0,0,0,.24), 0 0 16px color-mix(in srgb, var(--branch-color, #72E8F4) 18%, transparent);
    cursor:pointer;
  }
  .branch-origin-chip::after {
    content:"";
    position:absolute;
    left:50%;
    top:100%;
    width:1px;
    height:15px;
    background:linear-gradient(180deg, color-mix(in srgb, var(--branch-color, #72E8F4) 86%, transparent), color-mix(in srgb, var(--branch-color, #72E8F4) 10%, transparent));
  }
  .debug-branch-track-clip {
    position:relative;
    z-index:2;
    height:30px !important;
    width:calc(86px * var(--zoom, 1)) !important;
    min-width:70px !important;
    max-width:132px !important;
    border-radius:10px !important;
    font-size:12px !important;
    font-weight:850 !important;
    letter-spacing:-.01em;
    text-align:center;
    box-shadow:0 10px 24px rgba(0,0,0,.16), inset 0 1px 0 rgba(255,255,255,.035);
  }
  .debug-branch-track-clip.ok {
    color:#D7F8E3 !important;
    border-color:rgba(110,219,152,.42) !important;
    border-top-color:rgba(110,219,152,.9) !important;
    background:linear-gradient(180deg, rgba(22,49,34,.96), rgba(10,25,18,.96)) !important;
  }
  .debug-branch-track-clip.error {
    color:#FFD8DE !important;
    border-color:rgba(214,93,115,.44) !important;
    border-top-color:rgba(255,108,132,.9) !important;
    background:linear-gradient(180deg, rgba(57,25,34,.96), rgba(30,16,21,.96)) !important;
  }
  .debug-branch-track-clip.root {
    color:#FFE8BF !important;
    border-color:rgba(227,177,86,.46) !important;
    border-top-color:rgba(255,196,90,.94) !important;
    background:linear-gradient(180deg, rgba(67,48,19,.96), rgba(33,24,11,.96)) !important;
  }
  .debug-branch-track-clip:hover {
    transform:translateY(-1px);
  }
  .debug-branch-track-clip.mode-plan {
    color:#D8FBFF !important; border-style:dashed !important; border-color:rgba(114,232,244,.58) !important;
    background:rgba(19,43,50,.92) !important;
  }
  .debug-branch-track-clip.mode-simulate {
    box-shadow:inset 3px 0 0 #A884FF, inset 0 1px 0 rgba(255,255,255,.04) !important;
  }
  .debug-branch-track-clip.mode-live {
    box-shadow:inset 3px 0 0 #6EDB98, inset 0 1px 0 rgba(255,255,255,.04) !important;
  }
  .debug-branch-more {
    margin-left:4px; color:#8AA5AF; font-size:11px; font-weight:820;
  }
  .mini-segment {
    width:calc(86px * var(--zoom, 1)) !important; min-width:70px !important; max-width:132px !important;
  }
  .overview-strip { height:58px !important; }
  /* Alignment guard: match 1.png's stable editor geometry instead of letting
     long event titles push Diagnosis out of view. */
  .content, #detail {
    width:100% !important; max-width:none !important; min-width:0 !important; margin:0 !important;
  }
  .editor-workbench, .editor-main, .editor-stage, .event-inspector,
  .event-head-left, .diagnosis-panel {
    min-width:0 !important;
  }
  .editor-workbench {
    position:relative !important;
    display:block !important;
    width:100% !important;
    height:100% !important;
  }
  .editor-main {
    width:calc(100% - var(--diagnosis-w) - 16px) !important;
    height:100% !important;
    min-width:0 !important;
  }
  .editor-stage { overflow:hidden !important; }
  .editor-stage-body {
    min-height:0 !important;
    overflow:hidden !important;
  }
  .event-inspector {
    height:100% !important;
    min-height:0 !important;
    display:grid !important;
    grid-template-rows:auto auto minmax(0, 1fr) !important;
    overflow:hidden !important;
  }
  .diagnosis-panel {
    position:fixed !important;
    z-index:58 !important;
    top:calc(var(--topbar-h) + var(--gutter)) !important;
    right:14px !important;
    bottom:calc(var(--timeline-h) + var(--gutter) * 2) !important;
    width:var(--diagnosis-w) !important;
    display:block !important;
    visibility:visible !important;
    overflow:auto !important;
  }
  .event-inspector-summary {
    display:grid !important;
    grid-template-columns:minmax(0, 1fr) auto !important;
    column-gap:24px !important;
    align-items:center !important;
    overflow:hidden !important;
  }
  .event-head-left {
    display:grid !important;
    grid-template-columns:40px minmax(0, 1fr) !important;
    gap:18px !important;
    align-items:center !important;
  }
  .event-head-left > div:last-child { min-width:0 !important; }
  .event-alert-dot { width:38px !important; height:38px !important; }
  .event-inspector-title {
    display:block !important;
    max-width:100% !important;
    white-space:nowrap !important;
    overflow:hidden !important;
    text-overflow:ellipsis !important;
    line-height:1.08 !important;
  }
  .event-number, .event-chevron { display:inline !important; }
  .event-inspector-sub {
    max-width:100% !important;
    white-space:nowrap !important;
    overflow:hidden !important;
    text-overflow:ellipsis !important;
  }
  .event-head-right {
    min-width:292px !important;
    grid-template-columns:78px 78px 98px !important;
    gap:18px !important;
    justify-content:end !important;
  }
  .event-head-right .chip {
    justify-self:end;
    max-width:78px;
    overflow:hidden;
    text-overflow:ellipsis;
    white-space:nowrap;
  }
  .event-metric { min-width:0; }
  .event-metric strong, .event-metric span {
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
  }
  .inspector-pane {
    min-height:0 !important;
    max-height:100% !important;
    overflow:auto !important;
  }
  .inspector-pane.active {
    height:100% !important;
    overflow-y:auto !important;
  }
  .summary-block p, .inspector-value, .diagnosis-copy {
    overflow-wrap:anywhere;
  }
  @media (max-width: 1760px) {
    :root { --diagnosis-w:clamp(360px, 31vw, 500px); }
    .editor-workbench {
      display:block !important;
    }
    .event-inspector-title { font-size:24px !important; }
    .event-head-right {
      min-width:230px !important;
      grid-template-columns:66px 68px 78px !important;
      gap:12px !important;
    }
    .event-metric strong { font-size:14px !important; }
    .event-metric span { font-size:11px !important; }
    .inspector-pane[data-pane="summary"] .inspector-grid {
      display:flex !important;
      flex-direction:column !important;
    }
    .summary-primary,
    .summary-observation,
    .summary-plan {
      grid-column:auto !important;
      width:100% !important;
    }
  }
  @media (max-width: 1500px) {
    :root { --navigator-w:340px; --rail-w:0px; --diagnosis-w:340px; --timeline-h:min(210px, 22vh); }
    .topbar { padding-left:270px !important; }
    .topbar::before { left:82px; font-size:21px; }
    .editor-workbench { display:block !important; }
    .event-head-right { display:none !important; }
  }
  body.hub-mode .shell,
  body.overview-mode .shell {
    grid-template-columns:var(--rail-w) minmax(0, 1fr) !important;
  }
  body.hub-mode .sidebar,
  body.overview-mode .sidebar {
    display:none !important;
  }
  body.hub-mode .workspace,
  body.overview-mode .workspace {
    grid-column:2 !important;
    height:calc(100vh - var(--topbar-h)) !important;
  }
  body.hub-mode .topbar,
  body.overview-mode .topbar {
    padding-left:380px !important;
  }
  body.hub-mode .content,
  body.overview-mode .content {
    height:calc(100vh - var(--topbar-h)) !important;
    max-width:none !important;
    overflow:auto !important;
    padding:18px 24px 28px !important;
  }
  body.hub-mode #detail,
  body.overview-mode #detail {
    height:auto !important;
    min-height:calc(100vh - var(--topbar-h) - 48px);
  }
  .workspace-drawer-scrim {
    position:fixed; z-index:140; top:var(--topbar-h); right:0; bottom:0; left:var(--rail-w);
    border:0; padding:0; background:rgba(1,5,9,.62); opacity:0; visibility:hidden;
    transition:opacity .18s ease, visibility .18s ease; cursor:pointer;
  }
  .workspace-drawer-scrim.visible { opacity:1; visibility:visible; }
  .workspace-drawer {
    position:fixed; z-index:150; top:var(--topbar-h); bottom:0; display:grid;
    grid-template-rows:58px minmax(0,1fr); overflow:hidden;
    background:#081018; border-color:rgba(114,232,244,.18);
    box-shadow:0 28px 80px rgba(0,0,0,.52); visibility:hidden;
    transition:transform .18s cubic-bezier(.2,.8,.2,1), visibility .18s ease;
  }
  .workspace-drawer.left {
    left:var(--rail-w); width:min(78vw, 1120px); border-right:1px solid rgba(114,232,244,.2);
    transform:translateX(-102%);
  }
  .workspace-drawer.right {
    right:0; width:min(72vw, 1040px); border-left:1px solid rgba(114,232,244,.2);
    transform:translateX(102%);
  }
  .workspace-drawer.visible { transform:translateX(0); visibility:visible; }
  .workspace-drawer-head {
    min-width:0; display:flex; align-items:center; justify-content:space-between; gap:16px;
    padding:0 18px; border-bottom:1px solid rgba(114,232,244,.14); background:#0b141d;
  }
  .workspace-drawer-heading { min-width:0; display:flex; align-items:baseline; gap:12px; }
  .workspace-drawer-title { color:var(--fg); font-size:16px; font-weight:760; }
  .workspace-drawer-subtitle { color:var(--muted); font-size:12px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .workspace-drawer-close {
    width:32px; height:32px; flex:0 0 32px; display:grid; place-items:center; padding:0;
    border:1px solid var(--line2); border-radius:8px; background:#101923; color:var(--fg);
    font:20px/1 inherit; cursor:pointer;
  }
  .workspace-drawer-close:hover { border-color:var(--cyan); }
  .workspace-drawer-close:focus-visible { outline:2px solid var(--cyan); outline-offset:2px; }
  .workspace-drawer-content { min-height:0; overflow:auto; padding:18px 20px 28px; scrollbar-width:thin; }
  .workspace-drawer-content .project-overview { min-height:100%; }
  .workspace-drawer.right .case-library-grid { grid-template-columns:220px minmax(0,1fr); }
  .workspace-drawer.right .case-detail-panel { grid-column:1 / -1; position:static; }
  .workspace-drawer.right .pattern-summary { grid-template-columns:minmax(0,1fr); }
  .workspace-drawer.right .pattern-summary-actions { justify-content:flex-start; }
  .workspace-drawer.right .case-controls { grid-template-columns:minmax(200px,1fr) repeat(2,minmax(130px,auto)); }
  .workspace-drawer.right .case-list-scroll { max-height:none; }
  .discussion-shell { min-height:100%; display:grid; grid-template-columns:220px minmax(0,1fr); gap:14px; }
  .discussion-sessions {
    min-width:0; border:1px solid var(--line); border-radius:10px; background:rgba(8,14,20,.48);
    padding:10px; display:flex; flex-direction:column; gap:8px;
  }
  .discussion-session {
    width:100%; border:1px solid var(--line); border-radius:8px; background:#101820;
    color:var(--muted); padding:9px; text-align:left; cursor:pointer;
  }
  .discussion-session.active { border-color:var(--cyan); color:var(--fg); background:#132a2c; }
  .discussion-main { min-width:0; display:grid; grid-template-rows:auto minmax(280px,1fr) auto; gap:10px; }
  .discussion-meta { display:flex; gap:7px; flex-wrap:wrap; align-items:center; }
  .discussion-messages {
    min-height:320px; max-height:calc(100vh - 280px); overflow:auto; padding:12px;
    border:1px solid var(--line); border-radius:10px; background:#070d12;
    display:flex; flex-direction:column; gap:10px;
  }
  .discussion-message {
    max-width:88%; padding:10px 12px; border:1px solid var(--line); border-radius:10px;
    white-space:pre-wrap; overflow-wrap:anywhere; line-height:1.5;
  }
  .discussion-message.user { align-self:flex-end; background:#173034; border-color:#285158; }
  .discussion-message.assistant { align-self:flex-start; background:#121a22; }
  .discussion-citations { display:flex; gap:5px; flex-wrap:wrap; margin-top:8px; }
  .discussion-composer { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:8px; }
  .discussion-composer textarea {
    min-height:76px; resize:vertical; border:1px solid var(--line2); border-radius:9px;
    background:#0b131a; color:var(--fg); padding:10px; font:inherit;
  }
  .discussion-draft {
    margin-top:8px; padding:10px; border:1px solid #6d552d; border-radius:8px;
    background:#211a10; color:#ffe4b8;
  }
  body.drawer-open .timeline-dock { pointer-events:none; }
  #hub-btn.drawer-active { border-color:var(--cyan); color:#DDFDFF; background:rgba(114,232,244,.12); }
  body.theme-light .workspace-drawer { background:#F7FAF8; border-color:#D3DEDA; box-shadow:0 28px 70px rgba(41,54,51,.24); }
  body.theme-light .workspace-drawer-head { background:#FFFFFF; border-color:#DDE7E3; }
  body.theme-light .workspace-drawer-close { background:#F4F8F6; color:#243632; border-color:#D3DEDA; }
  body.theme-light .workspace-drawer-scrim { background:rgba(29,43,39,.34); }
  @media (max-width: 980px) {
    .workspace-drawer.left, .workspace-drawer.right { left:0; right:0; width:100vw; }
    .workspace-drawer-scrim { left:0; }
  }
  @media (max-width: 760px) {
    .workspace-drawer-content { padding:14px; }
    .workspace-drawer.right .case-summary-strip { grid-template-columns:repeat(2,minmax(0,1fr)); }
    .workspace-drawer.right .case-library-grid { grid-template-columns:minmax(0,1fr); }
    .workspace-drawer.right .case-detail-panel { grid-column:auto; }
    .workspace-drawer.right .pattern-nav { position:static; }
    .workspace-drawer.right .case-controls { grid-template-columns:minmax(0,1fr); }
    .discussion-shell { grid-template-columns:1fr; }
    .discussion-sessions { max-height:150px; overflow:auto; }
  }
  /* Daylight theme is intentionally designed, not an inverted dark theme. */
  body.theme-light {
    --bg:#F7FAF8;
    --panel:#FFFFFF;
    --panel2:#FBFCFA;
    --panel3:#F0F5F2;
    --line:#E5ECE9;
    --line2:#D3DEDA;
    --fg:#17211F;
    --muted:#61706C;
    --muted2:#899591;
    --cyan:#0C7C84;
    --green:#237A53;
    --amber:#A86B18;
    --rose:#B93A48;
    --violet:#6555B7;
    --paper:#13211F;
    --shadow:0 14px 34px rgba(52,68,63,.075);
    background:
      radial-gradient(circle at 20% -10%, rgba(12,124,132,.12), transparent 34%),
      radial-gradient(circle at 92% 0%, rgba(168,107,24,.08), transparent 28%),
      linear-gradient(135deg, #FBFAF5 0%, #F1F7F4 48%, #FCFAF3 100%) !important;
    color:var(--fg) !important;
  }
  body.theme-light::before {
    background:
      linear-gradient(rgba(21,34,31,.035) 1px, transparent 1px),
      linear-gradient(90deg, rgba(21,34,31,.028) 1px, transparent 1px) !important;
    background-size:44px 44px !important;
  }
  body.theme-light .topbar {
    background:linear-gradient(180deg, rgba(255,255,255,.96), rgba(250,252,250,.92)) !important;
    border-bottom:1px solid rgba(183,197,192,.42) !important;
    box-shadow:0 8px 22px rgba(52,68,63,.045), inset 0 1px 0 rgba(255,255,255,.9) !important;
  }
  body.theme-light .topbar::before { color:#15211F !important; }
  body.theme-light .topbar::after {
    border-color:rgba(12,124,132,.45) !important;
    background:
      radial-gradient(circle at 64% 34%, rgba(12,124,132,.9) 0 3px, transparent 4px),
      linear-gradient(145deg, rgba(12,124,132,.14), rgba(255,255,255,.8)) !important;
    box-shadow:0 10px 28px rgba(12,124,132,.13) !important;
  }
  body.theme-light .topbar .crumb { color:#3E4C49 !important; }
  body.theme-light .topbar .brand-sub { color:#6D7B77 !important; }
  body.theme-light .icon-rail {
    background:linear-gradient(180deg, rgba(249,252,250,.98), rgba(239,246,243,.96)) !important;
    border-right:1px solid rgba(190,203,199,.44) !important;
    box-shadow:inset -1px 0 0 rgba(255,255,255,.7) !important;
  }
  body.theme-light .rail-btn {
    color:#64736F !important;
    background:transparent !important;
  }
  body.theme-light .rail-btn:hover,
  body.theme-light .rail-btn.active {
    color:#0A6670 !important;
    background:rgba(12,124,132,.1) !important;
    box-shadow:inset 0 0 0 1px rgba(12,124,132,.18), 0 14px 30px rgba(41,54,51,.1) !important;
  }
  body.theme-light .rail-user {
    background:linear-gradient(145deg, #FFFFFF, #DDE8E5) !important;
    border-color:rgba(93,113,108,.26) !important;
    box-shadow:0 16px 34px rgba(41,54,51,.14) !important;
  }
  body.theme-light .rail-user::after {
    background:#24A366 !important;
    box-shadow:0 0 0 3px #EFF5F3, 0 0 16px rgba(36,163,102,.34) !important;
  }
  body.theme-light .sidebar {
    background:linear-gradient(180deg, rgba(255,255,255,.92), rgba(244,249,247,.94)) !important;
    border-right:1px solid rgba(190,203,199,.42) !important;
    box-shadow:inset -1px 0 0 rgba(255,255,255,.74) !important;
  }
  body.theme-light .side-section-title,
  body.theme-light .panel-title { color:#17211F !important; }
  body.theme-light .run-search,
  body.theme-light .run-filter-btn,
  body.theme-light .button,
  body.theme-light .timeline-tool {
    background:rgba(255,255,255,.74) !important;
    border-color:rgba(124,143,137,.26) !important;
    color:#20302D !important;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.85) !important;
  }
  body.theme-light .button:hover,
  body.theme-light .timeline-tool:hover,
  body.theme-light .run-filter-btn:hover {
    background:#FFFFFF !important;
    border-color:rgba(12,124,132,.36) !important;
    box-shadow:0 12px 28px rgba(41,54,51,.1), inset 0 1px 0 rgba(255,255,255,.9) !important;
  }
  body.theme-light .button.primary,
  body.theme-light #analyze-btn {
    background:#0D727A !important;
    border-color:#0D727A !important;
    color:#FFFFFF !important;
  }
  body.theme-light .run {
    background:linear-gradient(145deg, rgba(255,255,255,.94), rgba(240,247,244,.9)) !important;
    border-color:rgba(124,143,137,.2) !important;
    box-shadow:0 12px 28px rgba(41,54,51,.08), inset 0 1px 0 rgba(255,255,255,.78) !important;
  }
  body.theme-light .run:hover {
    background:linear-gradient(145deg, #FFFFFF, #F2F8F6) !important;
    border-color:rgba(12,124,132,.28) !important;
  }
  body.theme-light .run.active {
    background:linear-gradient(145deg, rgba(228,247,246,.95), rgba(246,252,250,.96)) !important;
    border-color:rgba(12,124,132,.55) !important;
    box-shadow:0 0 0 1px rgba(12,124,132,.16), inset 4px 0 0 #0D8089, 0 18px 42px rgba(41,54,51,.12) !important;
  }
  body.theme-light .run-id { color:#17211F !important; }
  body.theme-light .run-meta,
  body.theme-light .event-type,
  body.theme-light .panel-hint,
  body.theme-light .chart-subtitle,
  body.theme-light .goal,
  body.theme-light .diagnosis-copy,
  body.theme-light .evidence-list { color:#61706C !important; }
  body.theme-light .chip {
    background:#F1F5F3 !important;
    border-color:#D4DFDB !important;
    color:#53635F !important;
  }
  body.theme-light .chip.good {
    background:#E8F6EE !important;
    border-color:#A8D7BE !important;
    color:#17633F !important;
  }
  body.theme-light .chip.bad {
    background:#FCEBED !important;
    border-color:#E9A8B0 !important;
    color:#A42E3D !important;
  }
  body.theme-light .chip.warn {
    background:#FFF4DB !important;
    border-color:#E8C987 !important;
    color:#8A5310 !important;
  }
  body.theme-light .chip.cyan {
    background:#E6F6F7 !important;
    border-color:#A6DDE1 !important;
    color:#086D75 !important;
  }
  body.theme-light .editor-stage,
  body.theme-light .diagnosis-panel,
  body.theme-light .timeline-dock,
  body.theme-light .panel,
  body.theme-light .overview-card,
  body.theme-light .donut-card {
    background:linear-gradient(145deg, rgba(255,255,255,.96), rgba(243,248,246,.94)) !important;
    border-color:rgba(190,203,199,.42) !important;
    box-shadow:0 12px 32px rgba(52,68,63,.07), inset 0 1px 0 rgba(255,255,255,.86) !important;
  }
  body.theme-light .editor-stage-head,
  body.theme-light .panel-head,
  body.theme-light .inspector-tabs {
    background:linear-gradient(180deg, rgba(255,255,255,.82), rgba(247,250,248,.56)) !important;
    border-color:rgba(205,216,212,.48) !important;
  }
  body.theme-light .event-inspector-summary {
    background:linear-gradient(135deg, rgba(12,124,132,.08), rgba(255,255,255,.72)) !important;
    border-color:rgba(124,143,137,.18) !important;
  }
  body.theme-light .event-inspector-summary.root {
    background:linear-gradient(135deg, rgba(168,107,24,.12), rgba(255,255,255,.72)) !important;
  }
  body.theme-light .event-inspector-title,
  body.theme-light .event-number,
  body.theme-light .summary-block h3,
  body.theme-light .summary-block summary,
  body.theme-light .diagnosis-title,
  body.theme-light h1 { color:#17211F !important; }
  body.theme-light .summary-block p,
  body.theme-light .inspector-value,
  body.theme-light .diagnosis-copy,
  body.theme-light .raw-pre {
    color:#243632 !important;
  }
  body.theme-light .event-inspector-sub,
  body.theme-light .diagnosis-label,
  body.theme-light .inspector-label { color:#6F7E79 !important; }
  body.theme-light .inspector-tab { color:#667672 !important; }
  body.theme-light .inspector-tab.active {
    color:#087780 !important;
    border-color:#0D8089 !important;
  }
  body.theme-light .summary-block,
  body.theme-light .inspector-card,
  body.theme-light .readout-card,
  body.theme-light .diagnosis-facts .mini {
    background:#FFFFFF !important;
    border-color:#DDE5E2 !important;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.9) !important;
  }
  body.theme-light .inspector-pane {
    background:linear-gradient(180deg, rgba(255,255,255,.97), rgba(248,251,249,.96)) !important;
    color:#243632 !important;
  }
  body.theme-light .raw-pre {
    background:#F5F8F7 !important;
    border:1px solid #DDE5E2 !important;
    border-radius:10px !important;
    padding:14px !important;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.95) !important;
  }
  body.theme-light .summary-detail-block {
    background:#F8FBFA !important;
  }
  body.theme-light .detail-field {
    border-color:#DDE5E2 !important;
  }
  body.theme-light .detail-field.danger {
    background:#FFF1F3 !important;
    border-color:#E9A8B0 !important;
  }
  body.theme-light .timeline-tool-group {
    background:#F4F8F7 !important;
    border-color:#DDE5E2 !important;
  }
  body.theme-light .timeline-tool-primary {
    background:#E5F6F8 !important;
    border-color:#A6DDE1 !important;
    color:#0A6870 !important;
  }
  body.theme-light .diagnosis-panel.compact-clean {
    border-left-color:#8BD0A8 !important;
    background:linear-gradient(145deg, rgba(255,255,255,.97), rgba(245,251,248,.95)) !important;
  }
  body.theme-light .inspector-actions .timeline-tool,
  body.theme-light .inspector-actions .chip {
    color:#21312D !important;
  }
  body.theme-light .readout-card.warn {
    background:#FFF8E9 !important;
    border-color:#E8C987 !important;
  }
  body.theme-light .readout-card.primary {
    background:#ECF8F7 !important;
    border-color:#B5E1E3 !important;
  }
  body.theme-light .timeline-editor,
  body.theme-light .timeline-track,
  body.theme-light .timeline-minimap,
  body.theme-light .timeline-overview,
  body.theme-light .timeline-minimap-label,
  body.theme-light .track-label,
  body.theme-light .timeline-overview-label {
    background:#F7FBF9 !important;
    border-color:#DDE5E2 !important;
    color:#50615C !important;
  }
  body.theme-light .timeline-track {
    background:
      repeating-linear-gradient(90deg, rgba(12,124,132,.07) 0 1px, transparent 1px 96px),
      #F7FBF9 !important;
  }
  body.theme-light .track-strip::before {
    background:rgba(12,124,132,.18) !important;
  }
  body.theme-light .track-clip {
    background:linear-gradient(180deg, #FFFFFF, #EFF5F3) !important;
    border-color:#CBD8D4 !important;
    color:#243632 !important;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.9) !important;
  }
  body.theme-light .track-clip.error {
    background:#FBEDEF !important;
    border-top-color:#C94C5B !important;
  }
  body.theme-light .track-clip.root {
    background:#FFF4DB !important;
    border-top-color:#B6781C !important;
  }
  body.theme-light .track-clip.active {
    border-color:#0D8089 !important;
    box-shadow:0 0 0 1px rgba(12,124,132,.3), 0 0 22px rgba(12,124,132,.14) !important;
  }
  body.theme-light .debug-branch-label,
  body.theme-light .debug-branch-lane {
    background:#F3FAF9 !important;
    border-color:#DDE5E2 !important;
    color:#087780 !important;
  }
  body.theme-light .debug-branch-track-clip {
    box-shadow:0 8px 18px rgba(12,124,132,.08), inset 0 1px 0 rgba(255,255,255,.92) !important;
  }
  body.theme-light .debug-resume-btn {
    background:linear-gradient(145deg, #0D727A, #0A6870) !important;
    border-color:#0D727A !important;
    color:#FFFFFF !important;
    box-shadow:0 10px 26px rgba(12,124,132,.16) !important;
  }
  body.theme-light .continuation-modal {
    background:rgba(239,246,243,.68) !important;
  }
  body.theme-light .continuation-shell {
    background:
      radial-gradient(circle at 18% 0%, rgba(12,124,132,.11), transparent 36%),
      linear-gradient(145deg, rgba(255,255,255,.99), rgba(245,250,248,.98)) !important;
    border-color:rgba(12,124,132,.22) !important;
    box-shadow:0 28px 80px rgba(52,68,63,.16), inset 0 1px 0 rgba(255,255,255,.92) !important;
  }
  body.theme-light .continuation-head,
  body.theme-light .continuation-actions {
    background:rgba(255,255,255,.66) !important;
    border-color:#E1EAE7 !important;
  }
  body.theme-light .continuation-inline-status {
    background:rgba(236,248,247,.78) !important;
    border-color:#D9E7E3 !important;
    color:#0D727A !important;
  }
  body.theme-light .continuation-title,
  body.theme-light .continuation-value,
  body.theme-light .continuation-prompt {
    color:#17211F !important;
  }
  body.theme-light .continuation-sub,
  body.theme-light .continuation-label {
    color:#61706C !important;
  }
  body.theme-light .continuation-card {
    background:#FFFFFF !important;
    border-color:#E1EAE7 !important;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.92) !important;
  }
  body.theme-light .continuation-card.primary {
    background:#ECF8F7 !important;
    border-color:#B5E1E3 !important;
  }
  body.theme-light .composer-section {
    background:#FFFFFF !important;
    border-color:#E1EAE7 !important;
  }
  body.theme-light .composer-section.locked {
    background:#ECF8F7 !important;
    border-color:#B5E1E3 !important;
  }
  body.theme-light .composer-check {
    color:#17211F !important;
  }
  body.theme-light .composer-help {
    color:#61706C !important;
  }
  body.theme-light .composer-textarea,
  body.theme-light .composer-input,
  body.theme-light .composer-select {
    background:#FAFCFA !important;
    border-color:#D8E4E0 !important;
    color:#17211F !important;
  }
  body.theme-light .composer-pill {
    background:#E6F6F7 !important;
    border-color:#A6DDE1 !important;
    color:#086D75 !important;
  }
  body.theme-light .composer-pill.locked {
    background:#E8F6EE !important;
    border-color:#A8D7BE !important;
    color:#17633F !important;
  }
  body.theme-light .continuation-close {
    background:#FFFFFF !important;
    border-color:#D8E4E0 !important;
    color:#243632 !important;
  }
  body.theme-light .session-modal {
    background:rgba(239,246,243,.68) !important;
  }
  body.theme-light .session-shell,
  body.theme-light .session-detail,
  body.theme-light .compare-column {
    background:
      radial-gradient(circle at 18% 0%, rgba(12,124,132,.08), transparent 36%),
      linear-gradient(145deg, rgba(255,255,255,.99), rgba(245,250,248,.98)) !important;
    border-color:#DDE5E2 !important;
    box-shadow:0 28px 80px rgba(52,68,63,.14), inset 0 1px 0 rgba(255,255,255,.92) !important;
  }
  body.theme-light .session-head,
  body.theme-light .session-detail-head {
    background:rgba(255,255,255,.66) !important;
    border-color:#E1EAE7 !important;
  }
  body.theme-light .session-title,
  body.theme-light .session-card-title,
  body.theme-light .session-mini strong,
  body.theme-light .compare-title,
  body.theme-light .compare-event-title {
    color:#17211F !important;
  }
  body.theme-light .session-sub,
  body.theme-light .session-card-meta,
  body.theme-light .compare-event-copy,
  body.theme-light .session-mini span {
    color:#61706C !important;
  }
  body.theme-light .session-card,
  body.theme-light .session-mini,
  body.theme-light .compare-event {
    background:#FFFFFF !important;
    border-color:#E1EAE7 !important;
    color:#243632 !important;
  }
  body.theme-light .session-card:hover,
  body.theme-light .session-card.active {
    background:#ECF8F7 !important;
    border-color:#B5E1E3 !important;
  }
  body.theme-light .compare-summary {
    background:#ECF8F7 !important;
    border-color:#B5E1E3 !important;
    color:#24423F !important;
  }
  body.theme-light .session-close {
    background:#FFFFFF !important;
    border-color:#D8E4E0 !important;
    color:#243632 !important;
  }
  body.theme-light .mini-segment.ok,
  body.theme-light .mini-segment { background:#A9B8B4 !important; }
  body.theme-light .mini-segment.error { background:#C94C5B !important; }
  body.theme-light .mini-segment.root { background:#B6781C !important; }
  body.theme-light .playhead {
    background:#0D8089 !important;
    box-shadow:0 0 16px rgba(12,124,132,.32) !important;
  }
  body.theme-light .playhead::before {
    background:#E6F6F7 !important;
    border-color:#A6DDE1 !important;
    color:#086D75 !important;
  }
  body.theme-light .offline-popover,
  body.theme-light .toast,
  body.theme-light .chart-tooltip {
    background:linear-gradient(145deg, rgba(255,255,255,.98), rgba(241,247,245,.98)) !important;
    border-color:rgba(12,124,132,.24) !important;
    color:#17211F !important;
    box-shadow:0 20px 52px rgba(41,54,51,.16) !important;
  }
  body.theme-light .intel-overview {
    background:
      radial-gradient(circle at 14% -8%, rgba(12,124,132,.08), transparent 31%),
      radial-gradient(circle at 86% 0%, rgba(180,90,46,.055), transparent 28%),
      linear-gradient(180deg, #FAFCFA, #F2F7F4) !important;
  }
  body.theme-light .shell {
    background:rgba(247,250,248,.76) !important;
  }
  body.theme-light .workspace {
    background:linear-gradient(180deg, rgba(250,252,250,.92), rgba(242,247,244,.94)) !important;
  }
  body.theme-light .content {
    background:transparent !important;
  }
  body.theme-light .overview-top,
  body.theme-light .overview-filterbar,
  body.theme-light .runs-toolbar,
  body.theme-light .case-controls {
    background:rgba(255,255,255,.64) !important;
    border-color:rgba(205,216,212,.48) !important;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.92) !important;
  }
  body.theme-light .overview-top {
    background:transparent !important;
    border-color:transparent !important;
    box-shadow:none !important;
  }
  body.theme-light .runs-input,
  body.theme-light .runs-select {
    background:#FFFFFF !important;
    border-color:#DCE6E2 !important;
    color:#1F302D !important;
  }
  body.theme-light .runs-input::placeholder { color:#8A9995 !important; }
  body.theme-light .health-hero,
  body.theme-light .intel-panel {
    background:
      linear-gradient(145deg, rgba(255,255,255,.98), rgba(247,250,248,.96)) !important;
    border-color:rgba(205,216,212,.62) !important;
    box-shadow:0 12px 34px rgba(52,68,63,.065), inset 0 1px 0 rgba(255,255,255,.92) !important;
  }
  body.theme-light .intel-panel .panel-head {
    background:linear-gradient(180deg, rgba(255,255,255,.9), rgba(249,251,250,.7)) !important;
    border-color:rgba(219,227,224,.72) !important;
  }
  body.theme-light .health-status { color:#B33442 !important; }
  body.theme-light .health-ring {
    background:conic-gradient(#C94C5B var(--health-rate, 0%), #E3ECE9 0) !important;
    box-shadow:0 18px 42px rgba(201,76,91,.14) !important;
  }
  body.theme-light .health-ring::before {
    background:linear-gradient(145deg, #FFFFFF, #F0F7F4) !important;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.95), 0 10px 26px rgba(41,54,51,.08) !important;
  }
  body.theme-light .health-ring-center strong,
  body.theme-light .health-mini-value,
  body.theme-light .treemap-mode,
  body.theme-light .root-dist-name,
  body.theme-light .perf-main,
  body.theme-light .critical-title,
  body.theme-light .issue-name,
  body.theme-light .sequence-title {
    color:#17211F !important;
  }
  body.theme-light .health-ring-center span,
  body.theme-light .health-mini-label,
  body.theme-light .treemap-meta,
  body.theme-light .perf-axis,
  body.theme-light .perf-sub,
  body.theme-light .critical-meta,
  body.theme-light .sequence-meta,
  body.theme-light .root-dist-row,
  body.theme-light .severity-cell,
  body.theme-light .legend-pill {
    color:#62736F !important;
  }
  body.theme-light .health-mini,
  body.theme-light .legend-pill,
  body.theme-light .severity-cell,
  body.theme-light .perf-cell,
  body.theme-light .critical-run,
  body.theme-light .sequence-card {
    background:#FFFFFF !important;
    border-color:#E2EAE7 !important;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.9) !important;
  }
  body.theme-light .perf-cell::before {
    opacity:var(--risk-alpha, .08) !important;
    background:radial-gradient(circle at 16% 12%, var(--risk-color, #0D8089), transparent 58%) !important;
  }
  body.theme-light .perf-cell.empty {
    background:repeating-linear-gradient(135deg, #F3F8F6 0 8px, #EAF2EF 8px 16px) !important;
  }
  body.theme-light .treemap-node {
    border-color:rgba(205,216,212,.68) !important;
    background:
      linear-gradient(145deg, color-mix(in srgb, var(--node-color) 13%, #FFFFFF), #F8FBF9) !important;
    color:#17211F !important;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.86) !important;
  }
  body.theme-light .root-dist-track,
  body.theme-light .breakdown-bar,
  body.theme-light .issue-bar {
    background:#E8EFEC !important;
  }
  body.theme-light .root-dist-fill,
  body.theme-light .breakdown-fill,
  body.theme-light .issue-fill {
    background:linear-gradient(90deg, #B95059, #C8862B) !important;
    opacity:.86 !important;
  }
  body.theme-light .stack-line {
    stroke:rgba(111,126,121,.28) !important;
  }
  body.theme-light .stack-marker,
  body.theme-light .trend-point {
    stroke:#FAFCFA !important;
    stroke-width:2.4 !important;
    filter:drop-shadow(0 3px 7px rgba(52,68,63,.18)) !important;
  }
  body.theme-light .stack-area,
  body.theme-light .trend-area {
    opacity:.62 !important;
  }
  body.theme-light .chart-grid {
    stroke:rgba(111,126,121,.16) !important;
  }
  body.theme-light .axis-label {
    fill:#7B8985 !important;
  }
  body.theme-light .severity-cell.hot {
    background:#FCEBED !important;
    color:#A42E3D !important;
    border-color:#E9A8B0 !important;
  }
  body.theme-light .severity-cell.warm {
    background:#FFF4DB !important;
    color:#8A5310 !important;
    border-color:#E8C987 !important;
  }
  body.theme-light .runs-table th {
    background:#EEF6F3 !important;
    color:#52635F !important;
    border-color:#D8E4E0 !important;
  }
  body.theme-light .runs-table td {
    border-color:#E2EAE7 !important;
    color:#243632 !important;
  }
  body.theme-light .runs-table tr:hover td {
    background:#F1F8F5 !important;
  }
  body.theme-light .case-detail-hero,
  body.theme-light .case-empty-side {
    background:
      radial-gradient(circle at 18% 0%, rgba(12,124,132,.1), transparent 42%),
      linear-gradient(145deg, #FFFFFF, #F1F8F5) !important;
    border-color:rgba(12,124,132,.2) !important;
    color:#61706C !important;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.9) !important;
  }
  body.theme-light .case-detail-title { color:#17211F !important; }
  body.theme-light .case-detail-summary,
  body.theme-light .case-meta-label { color:#61706C !important; }
  body.theme-light .case-mini-timeline {
    background:#F4F9F7 !important;
    border-color:#D8E4E0 !important;
  }
  body.theme-light .case-summary-tile,
  body.theme-light .pattern-nav,
  body.theme-light .pattern-summary,
  body.theme-light .case-detail-section,
  body.theme-light .case-table-wrap {
    background:linear-gradient(145deg, rgba(255,255,255,.96), rgba(243,248,246,.94)) !important;
    border-color:rgba(124,143,137,.22) !important;
    box-shadow:0 14px 34px rgba(41,54,51,.08), inset 0 1px 0 rgba(255,255,255,.78) !important;
  }
  body.theme-light .case-summary-value,
  body.theme-light .pattern-summary-title,
  body.theme-light .case-detail-text,
  body.theme-light .case-table td {
    color:#17211F !important;
  }
  body.theme-light .case-summary-label,
  body.theme-light .case-summary-sub,
  body.theme-light .pattern-nav-item,
  body.theme-light .pattern-nav-count,
  body.theme-light .case-detail-section-title,
  body.theme-light .case-table th {
    color:#61706C !important;
  }
  body.theme-light .pattern-nav-item:hover,
  body.theme-light .pattern-nav-item.active {
    color:#0A6670 !important;
    background:rgba(12,124,132,.08) !important;
    border-color:rgba(12,124,132,.2) !important;
  }
  body.theme-light .tag-pill,
  body.theme-light .case-tag,
  body.theme-light .case-view-toggle {
    background:#F4F9F7 !important;
    border-color:#D8E4E0 !important;
    color:#42615D !important;
  }
  body.theme-light .case-view-toggle button { color:#61706C !important; }
  body.theme-light .case-view-toggle button.active {
    background:#E6F6F7 !important;
    color:#086D75 !important;
  }
  body.theme-light .case-menu {
    background:#FFFFFF !important;
    border-color:#D8E4E0 !important;
    box-shadow:0 18px 42px rgba(41,54,51,.16) !important;
  }
  body.theme-light .case-menu button { color:#243632 !important; }
  body.theme-light .case-menu button:hover { background:#F1F8F5 !important; }
  body.theme-light .case-table th { background:#EEF6F3 !important; }
  body.theme-light .case-table td { border-color:#E2EAE7 !important; }
  body.theme-light .case-table tr:hover td,
  body.theme-light .case-table tr.selected td { background:#F1F8F5 !important; }
  body.theme-light .offline-popover::before {
    background:#F4F9F7 !important;
    border-color:rgba(12,124,132,.24) !important;
  }
  body.theme-light .offline-copy { color:#61706C !important; }

  /* Trace upper layout reset:
     keep Timeline docked at the bottom, but make the upper editor a real
     two-column workbench so the inspector and diagnosis panel no longer drift. */
  body.trace-editor-mode #detail {
    height:100% !important;
    min-height:0 !important;
    overflow:visible !important;
  }
  body.trace-editor-mode .editor-workbench {
    position:relative !important;
    display:grid !important;
    grid-template-columns:minmax(620px, 1fr) clamp(360px, 31vw, 500px) !important;
    gap:14px !important;
    align-items:stretch !important;
    width:100% !important;
    height:100% !important;
    min-height:0 !important;
    animation:none !important;
  }
  body.trace-editor-mode .editor-main {
    width:auto !important;
    height:100% !important;
    min-width:0 !important;
    min-height:0 !important;
    overflow:hidden !important;
  }
  body.trace-editor-mode .editor-stage,
  body.trace-editor-mode .diagnosis-panel {
    height:100% !important;
    min-height:0 !important;
    max-height:100% !important;
    align-self:stretch !important;
    border-radius:12px !important;
  }
  body.trace-editor-mode .diagnosis-panel {
    position:relative !important;
    inset:auto !important;
    top:auto !important;
    right:auto !important;
    bottom:auto !important;
    left:auto !important;
    z-index:1 !important;
    width:auto !important;
    display:flex !important;
    flex-direction:column !important;
    overflow:hidden !important;
  }
  body.trace-editor-mode .diagnosis-panel .diagnosis-section {
    padding:18px 22px !important;
  }
  body.trace-editor-mode .diagnosis-panel .diagnosis-section:first-child {
    min-height:104px !important;
    display:flex !important;
    flex-direction:column !important;
    justify-content:center !important;
  }
  .diagnosis-hero-head {
    display:flex; align-items:flex-start; justify-content:space-between; gap:14px;
  }
  .diagnosis-hero-copy { min-width:0; }
  .diagnosis-hero .workspace-launcher { flex:0 0 auto; }
  body.trace-editor-mode .diagnosis-panel .diagnosis-section:not(:first-child) {
    overflow:visible !important;
  }
  body.trace-editor-mode .editor-stage {
    display:grid !important;
    grid-template-rows:58px minmax(0, 1fr) !important;
    overflow:hidden !important;
  }
  body.trace-editor-mode .editor-stage-head {
    min-height:58px !important;
    height:58px !important;
    padding:0 22px !important;
    display:grid !important;
    grid-template-columns:minmax(132px, 156px) minmax(0, 1fr) !important;
    align-items:center !important;
    justify-content:space-between !important;
    gap:18px !important;
  }
  body.trace-editor-mode .editor-stage-title {
    min-width:0 !important;
    width:auto !important;
  }
  body.trace-editor-mode .editor-stage-title .panel-title {
    font-size:15px !important;
    letter-spacing:-.01em !important;
  }
  body.trace-editor-mode .editor-stage-title .panel-hint {
    max-width:560px !important;
    white-space:nowrap !important;
    overflow:hidden !important;
    text-overflow:ellipsis !important;
  }
  body.trace-editor-mode .editor-stage-head .lane-meta {
    min-width:0 !important;
    flex-wrap:nowrap !important;
    gap:8px !important;
    margin-left:auto !important;
    justify-content:flex-end !important;
    width:100% !important;
  }
  body.trace-editor-mode .editor-stage-head .report-select {
    min-width:140px !important;
    width:min(260px, 28vw) !important;
    max-width:260px !important;
  }
  body.trace-editor-mode .editor-stage-body {
    min-height:0 !important;
    overflow:hidden !important;
  }
  body.trace-editor-mode .event-inspector {
    height:100% !important;
    min-height:0 !important;
    display:grid !important;
    grid-template-rows:auto 48px minmax(0, 1fr) !important;
    overflow:hidden !important;
  }
  body.trace-editor-mode .event-inspector-summary {
    min-height:108px !important;
    padding:18px 22px !important;
    display:grid !important;
    grid-template-columns:minmax(0, 1fr) auto !important;
    column-gap:18px !important;
    align-items:center !important;
  }
  body.trace-editor-mode .event-head-left {
    grid-template-columns:40px minmax(0, 1fr) !important;
    gap:16px !important;
  }
  body.trace-editor-mode .event-alert-dot {
    width:38px !important;
    height:38px !important;
  }
  body.trace-editor-mode .event-inspector-title {
    display:block !important;
    font-size:26px !important;
    line-height:1.05 !important;
    max-width:100% !important;
    white-space:nowrap !important;
    overflow:hidden !important;
    text-overflow:ellipsis !important;
  }
  body.trace-editor-mode .event-inspector-sub {
    font-size:13px !important;
    margin-top:6px !important;
    max-width:100% !important;
    white-space:nowrap !important;
    overflow:hidden !important;
    text-overflow:ellipsis !important;
  }
  body.trace-editor-mode .event-head-right {
    min-width:auto !important;
    grid-template-columns:none !important;
    display:flex !important;
    align-items:center !important;
    justify-content:flex-end !important;
    gap:8px !important;
  }
  body.trace-editor-mode .event-head-right .event-metric {
    display:none !important;
  }
  body.trace-editor-mode .inspector-tabs {
    height:48px !important;
    padding:0 22px !important;
    gap:26px !important;
  }
  body.trace-editor-mode .inspector-tab {
    height:48px !important;
    font-size:13px !important;
  }
  body.trace-editor-mode .inspector-pane {
    min-height:0 !important;
    height:100% !important;
    padding:16px 22px !important;
    overflow-y:auto !important;
  }
  body.trace-editor-mode .summary-block,
  body.trace-editor-mode .inspector-card,
  body.trace-editor-mode .detail-field {
    border-radius:12px !important;
  }
  body.trace-editor-mode .summary-primary,
  body.trace-editor-mode .summary-observation,
  body.trace-editor-mode .summary-plan {
    min-height:0 !important;
    height:auto !important;
    align-self:start !important;
  }
  body.trace-editor-mode .timeline-dock {
    position:fixed !important;
    z-index:60 !important;
    left:calc(var(--rail-w) + var(--navigator-w) + 12px) !important;
    right:14px !important;
    bottom:14px !important;
    height:var(--timeline-h) !important;
    margin:0 !important;
  }
  @media (max-width: 1760px) {
    body.trace-editor-mode .editor-workbench {
      display:grid !important;
      grid-template-columns:minmax(520px, 1fr) clamp(340px, 30vw, 460px) !important;
    }
    body.trace-editor-mode .event-inspector-title { font-size:23px !important; }
    body.trace-editor-mode .editor-stage-head .lane-meta .chip:nth-child(2),
    body.trace-editor-mode .editor-stage-head .lane-meta .chip:nth-child(3) {
      display:none !important;
    }
    body.trace-editor-mode .editor-stage-head .report-select + .chip {
      display:none !important;
    }
  }
  @media (max-width: 1500px) {
    .top-actions #theme-btn { display:none !important; }
    body.trace-editor-mode .editor-workbench {
      display:grid !important;
      grid-template-columns:minmax(440px, 1fr) 340px !important;
    }
    body.trace-editor-mode .editor-stage-title .panel-hint {
      display:none !important;
    }
    body.trace-editor-mode .editor-stage-head {
      grid-template-columns:118px minmax(0, 1fr) !important;
      gap:12px !important;
    }
    body.trace-editor-mode .editor-stage-head > .lane-meta > .chip {
      display:none !important;
    }
    body.trace-editor-mode .editor-stage-head .report-select {
      min-width:120px !important;
      width:min(220px, 24vw) !important;
    }
    body.trace-editor-mode .event-head-right {
      display:flex !important;
    }
  }
  @media (max-width: 1280px) {
    .shell { grid-template-columns:var(--rail-w) var(--navigator-w) minmax(0,1fr); }
    .editor-workbench { grid-template-columns:minmax(0,1fr) 380px; }
    .editor-head-right { gap:14px; }
    .event-head-right { grid-template-columns:auto 72px 82px; gap:14px; }
  }
  code { font-family:ui-monospace, SFMono-Regular, Consolas, monospace; font-size:11px; }
  @media (max-width: 980px) {
    body { overflow:auto; }
    .shell, .hero, .layout { grid-template-columns:1fr; height:auto; }
    .sidebar { border-right:0; border-bottom:1px solid var(--line); }
    .workspace { height:auto; }
    .topbar { position:static; }
    .trace-legend, .trace-pair { grid-template-columns:1fr; }
    .stepbar-card { min-width:110px; flex-basis:110px; }
    .dashboard-grid, .chart-grid, .overview-summary-grid, .donut-shell, .overview-hero, .overview-header, .overview-analysis-grid, .insight-grid, .event-readout { grid-template-columns:1fr; }
    .kpi-strip { grid-template-columns:repeat(4, minmax(0,1fr)); }
    .trace-workbench { grid-template-columns:1fr; }
    .editor-workbench { grid-template-columns:1fr; min-height:auto; }
    .editor-main { min-height:820px; }
    .editor-titlebar { grid-template-columns:1fr; }
    .inspector-rail { max-height:none; overflow:visible; }
    .trace-rail { position:static; max-height:none; overflow:visible; }
    .trace-header, .trace-detail-grid { grid-template-columns:1fr; }
    .trace-kpi-strip { grid-template-columns:repeat(2, minmax(0,1fr)); }
  }
  @media (max-width: 640px) {
    .topbar { display:grid; grid-template-columns:1fr; align-items:start; padding:14px 16px; }
    .top-actions { width:100%; display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); }
    .button { width:100%; min-width:0; padding:0 8px; overflow:hidden; text-overflow:ellipsis; }
    .content { padding:22px 16px; }
    h1 { font-size:27px; line-height:1.1; }
    .stats { grid-template-columns:repeat(2,minmax(0,1fr)); }
    .kpi-strip { grid-template-columns:repeat(2, minmax(0,1fr)); }
    .root-grid { grid-template-columns:1fr; }
    .event { grid-template-columns:46px minmax(0,1fr); padding:10px; }
    .event.focused { grid-template-columns:42px minmax(0,1fr); padding:10px; }
    .step-index { width:38px; height:38px; }
    .event.focused .step-index { width:44px; height:44px; font-size:15px; }
    .event-main-value { font-size:16px; }
    .event-context-grid, .event-meta-strip, .event-readout { grid-template-columns:1fr; }
    .event-inline-meta { grid-template-columns:1fr; }
    .event-grid { grid-template-columns:1fr; }
    .editor-event-hero { grid-template-columns:52px minmax(0,1fr); }
    .editor-event-hero .lane-meta { grid-column:1 / -1; justify-content:flex-start !important; }
    .editor-stage .event-readout { grid-template-columns:1fr; }
    .editor-stage .readout-card:nth-child(3) { grid-column:auto; }
    .timeline-row { grid-template-columns:46px minmax(0,1fr); }
    .timeline-open { grid-column:1 / -1; }
    .stepbar-card { min-width:96px; flex-basis:96px; padding:9px; }
    .stepbar-expanded-head { display:grid; grid-template-columns:1fr; }
  }
</style>
</head>
<body>
<div class="shell">
  <aside class="sidebar">
    <div class="brand">
      <div>
        <div class="brand-title">AgentDebugX</div>
        <div class="brand-sub">Local agent failure console</div>
      </div>
      <div class="mark">AX</div>
    </div>
    <div class="run-section">
      <div class="side-section-head">
        <div class="side-section-title">Run Navigator</div>
        <button class="workspace-launcher" id="overview-btn" type="button" aria-label="Open Overview panel" title="Open Overview panel" aria-expanded="false" aria-controls="overview-drawer"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"></rect><path d="M9 4v16"></path></svg><span>Overview</span></button>
      </div>
      <div class="run-search-shell">
      <input class="run-search" id="run-search" type="search" placeholder="Search runs..." aria-label="Search runs" />
        <button class="run-filter-btn" type="button" aria-label="Filter runs">≡</button>
      </div>
      <div class="filter-tray" id="trace-filters"></div>
      <ul class="run-list" id="trace-list"></ul>
    </div>
    <div class="side-note">
      Error Hub ready. Scrub locally, package a failure bundle, then publish to
      a Git or Hugging Face dataset backend for team review.
    </div>
  </aside>
  <section class="workspace">
    <div class="topbar">
      <button class="top-brand-avatar" id="offline-status-btn" type="button" aria-label="Show Local UI status" title="Local UI status" aria-haspopup="dialog" aria-expanded="false" aria-controls="offline-popover"><img src="/assets/robot-avatar.svg" alt="" /></button>
      <div>
        <div class="crumb">Project / checkout-agent / debug session</div>
        <div class="brand-sub" id="trace-count">Loading traces</div>
      </div>
      <div class="top-actions">
        <button class="button" id="theme-btn" type="button">Theme</button>
        <button class="button llm-settings-button" id="llm-settings-btn" type="button" aria-label="Open LLM settings" title="Configure the shared LLM connection"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"></path><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.56V21h-4v-.08A1.7 1.7 0 0 0 9 19.37a1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.63 15 1.7 1.7 0 0 0 3.08 14H3v-4h.08A1.7 1.7 0 0 0 4.63 9a1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.63 1.7 1.7 0 0 0 10 3.08V3h4v.08A1.7 1.7 0 0 0 15 4.63a1.7 1.7 0 0 0 1.88-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 19.37 9 1.7 1.7 0 0 0 20.92 10H21v4h-.08A1.7 1.7 0 0 0 19.4 15Z"></path></svg><span>LLM Settings</span></button>
        <button class="button" id="upload-btn" type="button">Upload Trace</button>
        <button class="button" id="analyze-btn" type="button">Refresh View</button>
      </div>
    </div>
    <div class="content">
      __OVERVIEW_PANEL__
      <div id="detail">
        <div class="empty">Select a trace from the left to inspect its timeline.</div>
      </div>
    </div>
  </section>
</div>
<div class="offline-popover" id="offline-popover" role="dialog" aria-label="Local UI status" aria-hidden="true">
  <div class="offline-title"><span class="offline-dot" aria-hidden="true"></span><span id="runtime-status-title">Local UI</span></div>
  <div class="offline-copy" id="runtime-status-copy">Traces stay in the local store.</div>
  <div class="offline-meta" id="runtime-status-meta"></div>
</div>
<button class="workspace-drawer-scrim" id="workspace-drawer-scrim" type="button" aria-label="Close open panel"></button>
<aside class="workspace-drawer left" id="overview-drawer" role="dialog" aria-modal="true" aria-hidden="true" aria-label="Overview panel">
  <div class="workspace-drawer-head"><div class="workspace-drawer-heading"><div class="workspace-drawer-title">Overview</div><div class="workspace-drawer-subtitle">Project health and run triage</div></div><button class="workspace-drawer-close" type="button" data-close-drawer aria-label="Close Overview">×</button></div>
  <div class="workspace-drawer-content" id="overview-drawer-content"></div>
</aside>
<aside class="workspace-drawer right" id="hub-drawer" role="dialog" aria-modal="true" aria-hidden="true" aria-label="Error Hub panel">
  <div class="workspace-drawer-head"><div class="workspace-drawer-heading"><div class="workspace-drawer-title">Error Hub</div><div class="workspace-drawer-subtitle">Saved cases and reusable failure patterns</div></div><button class="workspace-drawer-close" type="button" data-close-drawer aria-label="Close Error Hub">×</button></div>
  <div class="workspace-drawer-content" id="hub-drawer-content"></div>
</aside>
<aside class="workspace-drawer right" id="discussion-drawer" role="dialog" aria-modal="true" aria-hidden="true" aria-label="Discuss with Debugger">
  <div class="workspace-drawer-head"><div class="workspace-drawer-heading"><div class="workspace-drawer-title">Discuss with Debugger</div><div class="workspace-drawer-subtitle">Trace-grounded, read-only analysis</div></div><button class="workspace-drawer-close" type="button" data-close-drawer aria-label="Close discussion">×</button></div>
  <div class="workspace-drawer-content" id="discussion-drawer-content"></div>
</aside>
<div id="chart-tooltip" class="chart-tooltip" role="status" aria-live="polite"></div>
<div id="toast" class="toast" role="status" aria-live="polite"></div>
<script>
const BOOTSTRAP = __BOOTSTRAP_JSON__;
const UI_STATUS = (BOOTSTRAP && BOOTSTRAP.ui_status) || {rerun: {configured: false, checkpoint_policy: 'from_start'}};
let CURRENT_TRACE_ID = null;
let CURRENT_TRACE_DATA = null;
let CURRENT_VIEW = (BOOTSTRAP && BOOTSTRAP.view) || 'overview';
let ACTIVE_DRAWER = null;
let DRAWER_RETURN_FOCUS = null;
let CURRENT_DISCUSSION_ID = null;
let CURRENT_EXPANDED_EVENT_ID = null;
let ACTIVE_TRACE_FILTER = 'all';
let TIMELINE_ZOOM = 1;
let TIMELINE_AXIS_MODE = 'Step Count';
const HIDDEN_TRACKS = new Set();
const DEBUG_BRANCH_STORAGE_KEY = 'agentdebugx-debug-branches-v1';
const DEBUG_BACKEND_STORAGE_KEY = 'agentdebugx-debug-backend-v1';
const LLM_SETTINGS_STORAGE_KEY = 'agentdebugx-llm-settings-v1';
const LLM_API_KEY_SESSION_KEY = 'agentdebugx-llm-api-key-v1';
const TRACE_VIEW_MODE_PREFIX = 'agentdebugx-trace-view-mode:';
const VISUAL_LAYOUT_PREFIX = 'agentdebugx-visual-layout:';
const DEBUG_BRANCH_SYNCED = new Set();
let DEBUG_BRANCHES = loadDebugBranches();
let CURRENT_BRANCH_EVENT_MAP = new Map();
let TRACE_CATALOG = ((BOOTSTRAP && BOOTSTRAP.overview && BOOTSTRAP.overview.trace_catalog) || []).map(item => ({
  trace_id: item.trace_id,
  task_id: item.task_id || '',
  goal: item.goal || '',
  framework: item.framework || '',
  model: item.model || '',
  task_type: item.task_type || '',
  dataset_type: item.dataset_type || '',
  event_count: Number(item.event_count || 0),
  finding_count: Number(item.finding_count || 0),
  error_count: Number(item.error_count || item.finding_count || 0),
  status: item.status || '',
  first_error_step: item.first_error_step,
  root_cause_step_index: item.root_cause_step_index,
  root_cause_found: Boolean(item.root_cause_found),
  duration_ms: Number(item.duration_ms || 0),
  summary: item.summary || '',
  mini_timeline: Array.isArray(item.mini_timeline) ? item.mini_timeline : [],
  top_error_type: item.top_error_type || '',
  top_family: item.top_family || ''
}));
const RUN_SCROLL_KEY = 'agentdebugx.recentRuns.scrollTop';
const CASE_DB_FILENAME = 'typical_error_cases.jsonl';
async function api(path) {
  const r = await fetch(path);
  if (!r.ok) {
    let detail = '';
    try {
      const payload = await r.json();
      detail = payload && payload.detail ? ': ' + payload.detail : '';
    } catch (e) {
      detail = '';
    }
    throw new Error('HTTP ' + r.status + detail);
  }
  return r.json();
}
async function discussionApi(path, options) {
  const response = await fetch(path, options || {});
  if (!response.ok) {
    let detail = '';
    try {
      const payload = await response.json();
      detail = payload?.detail ? ': ' + payload.detail : '';
    } catch (_e) {
    }
    throw new Error('HTTP ' + response.status + detail);
  }
  return response.status === 204 ? {} : response.json();
}
function cssEscape(value) {
  if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(String(value));
  return String(value).replace(/"/g, '');
}
function fmt(v) {
  if (v === null || v === undefined) return '';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}
function truncate(s, n) { s = fmt(s); return s.length > n ? s.slice(0, n) + '...' : s; }
function escapeHtml(s) {
  return String(s).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'})[c]);
}
function familyClass(family) {
  if (family === 'system' || family === 'action') return 'bad';
  if (family === 'planning' || family === 'verification') return 'warn';
  if (family === 'memory' || family === 'multiagent') return 'cyan';
  return 'good';
}
function eventProblem(ev) {
  const payload = (fmt(ev.error) + ' ' + fmt(ev.output) + ' ' + fmt(ev.metadata)).toLowerCase();
  return Boolean(ev.error || payload.includes('missing context') || payload.includes('premature') || payload.includes('loop') || payload.includes('handoff'));
}
async function loadTraceList(selectFirst) {
  const data = await api('/api/v1/traces');
  if (!TRACE_CATALOG.length) TRACE_CATALOG = data.traces.map(tid => ({trace_id: tid, framework: '', dataset_type: '', finding_count: 1}));
  renderTraceList(data.traces, CURRENT_TRACE_ID);
  const firstVisible = document.querySelector('.run[data-tid]');
  if (selectFirst && CURRENT_VIEW !== 'overview' && firstVisible) selectTrace(firstVisible.dataset.tid, firstVisible);
  if (data.traces.length === 0) {
    document.getElementById('detail').innerHTML = '<div class="empty">No traces in store.</div>';
  }
}
async function loadOverview() {
  try {
    CURRENT_VIEW = 'overview';
    CURRENT_TRACE_ID = null;
    CURRENT_TRACE_DATA = null;
    document.body.classList.add('hub-mode', 'overview-mode');
    document.body.classList.remove('trace-editor-mode');
    const detail = document.getElementById('detail');
    if (detail) detail.innerHTML = loadingState('Loading overview');
    const overview = await api('/api/v1/overview');
    BOOTSTRAP.overview = overview;
    if (Array.isArray(overview.trace_catalog)) {
      TRACE_CATALOG = overview.trace_catalog.map(item => ({
        trace_id: item.trace_id,
        task_id: item.task_id || '',
        goal: item.goal || '',
        framework: item.framework || '',
        model: item.model || '',
        task_type: item.task_type || '',
        dataset_type: item.dataset_type || '',
        event_count: Number(item.event_count || 0),
        finding_count: Number(item.finding_count || 0),
        error_count: Number(item.error_count || item.finding_count || 0),
        status: item.status || '',
        first_error_step: item.first_error_step,
        root_cause_step_index: item.root_cause_step_index,
        root_cause_found: Boolean(item.root_cause_found),
        duration_ms: Number(item.duration_ms || 0),
        summary: item.summary || '',
        mini_timeline: Array.isArray(item.mini_timeline) ? item.mini_timeline : [],
        top_error_type: item.top_error_type || '',
        top_family: item.top_family || ''
      }));
    }
    renderTraceList((BOOTSTRAP && BOOTSTRAP.traces) || TRACE_CATALOG.map(item => item.trace_id), null);
    renderOverview(overview);
  } catch (e) {
    document.getElementById('detail').innerHTML = '<div class="empty">' + escapeHtml(e.message || e) + '</div>';
  }
}
async function loadOverviewDrawer() {
  const target = document.getElementById('overview-drawer-content');
  if (!target) return;
  target.innerHTML = loadingState('Loading project overview');
  try {
    const overview = await api('/api/v1/overview');
    BOOTSTRAP.overview = overview;
    if (Array.isArray(overview.trace_catalog)) TRACE_CATALOG = overview.trace_catalog;
    renderOverview(overview, target);
  } catch (e) {
    target.innerHTML = '<div class="empty">' + escapeHtml(e.message || e) + '</div>';
  }
}
async function loadHubDrawer() {
  const target = document.getElementById('hub-drawer-content');
  if (!target) return;
  target.innerHTML = loadingState('Loading Error Hub');
  try {
    const payload = await api('/api/v1/cases');
    renderCasesPage(payload, target);
  } catch (e) {
    target.innerHTML = '<div class="empty">' + escapeHtml(e.message || e) + '</div>';
  }
}
async function loadDiscussionDrawer() {
  const target = document.getElementById('discussion-drawer-content');
  if (!target || !CURRENT_TRACE_ID) return;
  target.innerHTML = loadingState('Loading debugger discussions');
  try {
    const payload = await discussionApi('/api/v1/traces/' + encodeURIComponent(CURRENT_TRACE_ID) + '/discussions');
    const sessions = payload.sessions || [];
    if (!CURRENT_DISCUSSION_ID && sessions.length) CURRENT_DISCUSSION_ID = sessions[0].session_id;
    let active = null;
    if (CURRENT_DISCUSSION_ID) {
      try {
        active = await discussionApi('/api/v1/traces/' + encodeURIComponent(CURRENT_TRACE_ID) + '/discussions/' + encodeURIComponent(CURRENT_DISCUSSION_ID));
      } catch (_e) {
        CURRENT_DISCUSSION_ID = null;
      }
    }
    renderDiscussionDrawer(target, sessions, active);
  } catch (e) {
    target.innerHTML = '<div class="empty">' + escapeHtml(e.message || e) + '</div>';
  }
}
function renderDiscussionDrawer(target, sessions, activePayload) {
  const active = activePayload?.session || activePayload || null;
  const messages = activePayload?.messages || active?.messages || [];
  let html = '<div class="discussion-shell"><aside class="discussion-sessions">';
  html += '<button class="button primary" type="button" data-new-discussion>New discussion</button>';
  sessions.forEach(session => {
    const selected = session.session_id === CURRENT_DISCUSSION_ID;
    html += '<button class="discussion-session ' + (selected ? 'active' : '') + '" type="button" data-discussion-id="' + escapeHtml(session.session_id || '') + '"><strong>' + escapeHtml(session.model || 'Debugger') + '</strong><br><span>' + escapeHtml(session.created_at || '') + '</span></button>';
  });
  if (!sessions.length) html += '<div class="panel-hint">No saved discussions for this trace.</div>';
  html += '</aside><section class="discussion-main">';
  if (!active) {
    html += '<div class="editor-empty"><div><strong>Start a trace-grounded discussion</strong><span>The debugger will use the selected report and read-only event tools.</span></div></div></section></div>';
    target.innerHTML = html;
    bindDiscussionDrawer();
    return;
  }
  html += '<div class="discussion-meta"><span class="chip cyan">' + escapeHtml(active.model || 'configured model') + '</span><span class="chip">' + escapeHtml(active.report_source || 'report snapshot') + '</span>';
  if (active.stale_report) html += '<span class="chip warn">Pinned to earlier analysis</span>';
  html += '<button class="timeline-tool" type="button" data-delete-discussion>Delete</button></div>';
  html += '<div class="discussion-messages" id="discussion-messages">';
  messages.forEach(message => {
    const role = message.role === 'user' ? 'user' : 'assistant';
    html += '<div class="discussion-message ' + role + '"><div>' + escapeHtml(message.content || '') + '</div>';
    if (Array.isArray(message.citations) && message.citations.length) {
      html += '<div class="discussion-citations">';
      message.citations.forEach(citation => {
        const eventId = typeof citation === 'string' ? citation : citation?.event_id;
        if (!eventId) return;
        html += '<button class="related-link" type="button" data-discussion-event="' + escapeHtml(eventId) + '">' + escapeHtml(eventId) + '</button>';
      });
      html += '</div>';
    }
    if (message.proposal) {
      html += '<div class="discussion-draft"><strong>Report revision draft</strong><pre class="raw-pre">' + escapeHtml(JSON.stringify(message.proposal, null, 2)) + '</pre><button class="timeline-tool" type="button" data-export-discussion-draft="' + escapeHtml(message.message_id || '') + '">Export draft</button></div>';
    }
    html += '</div>';
  });
  if (!messages.length) html += '<div class="panel-hint">Ask about a suspicious event, root cause, evidence, or an alternate diagnosis.</div>';
  html += '</div><form class="discussion-composer" id="discussion-form"><textarea id="discussion-input" maxlength="8000" placeholder="Ask the debugger about this trace..." required></textarea><button class="button primary" type="submit">Send</button></form></section></div>';
  target.innerHTML = html;
  target.querySelector('#discussion-messages')?.scrollTo({top: 999999});
  bindDiscussionDrawer(active, messages);
}
function bindDiscussionDrawer(active, messages) {
  document.querySelector('[data-new-discussion]')?.addEventListener('click', async () => {
    try {
      const settings = readLLMSettings();
      const payload = await discussionApi('/api/v1/traces/' + encodeURIComponent(CURRENT_TRACE_ID) + '/discussions', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({report_id: activeStoredReportId(), model: settings.model || ''})
      });
      CURRENT_DISCUSSION_ID = payload.session?.session_id || payload.session_id;
      await loadDiscussionDrawer();
    } catch (error) {
      notify('Discussion creation failed: ' + (error.message || error));
    }
  });
  document.querySelectorAll('[data-discussion-id]').forEach(button => {
    button.onclick = async () => {
      CURRENT_DISCUSSION_ID = button.dataset.discussionId || null;
      await loadDiscussionDrawer();
    };
  });
  document.querySelector('[data-delete-discussion]')?.addEventListener('click', async () => {
    if (!CURRENT_DISCUSSION_ID || !window.confirm('Delete this discussion?')) return;
    try {
      await discussionApi('/api/v1/traces/' + encodeURIComponent(CURRENT_TRACE_ID) + '/discussions/' + encodeURIComponent(CURRENT_DISCUSSION_ID), {method: 'DELETE'});
      CURRENT_DISCUSSION_ID = null;
      await loadDiscussionDrawer();
    } catch (error) {
      notify('Delete failed: ' + (error.message || error));
    }
  });
  document.querySelectorAll('[data-discussion-event]').forEach(button => {
    button.onclick = () => {
      const eventId = button.dataset.discussionEvent || '';
      if (!CURRENT_TRACE_DATA?.trajectory?.events?.some(event => event.event_id === eventId)) return;
      CURRENT_EXPANDED_EVENT_ID = eventId;
      renderTrace(CURRENT_TRACE_DATA.trajectory, CURRENT_TRACE_DATA.report);
      positionTimelinePlayhead(eventId);
    };
  });
  document.querySelectorAll('[data-export-discussion-draft]').forEach(button => {
    button.onclick = () => {
      const message = (messages || []).find(item => String(item.message_id) === String(button.dataset.exportDiscussionDraft));
      if (!message?.proposal) return;
      downloadBlob('report-revision-draft-' + CURRENT_TRACE_ID + '.json', JSON.stringify(message.proposal, null, 2), 'application/json');
    };
  });
  const form = document.getElementById('discussion-form');
  if (form && active) {
    form.onsubmit = async event => {
      event.preventDefault();
      const input = document.getElementById('discussion-input');
      const message = String(input?.value || '').trim();
      if (!message) return;
      const submit = form.querySelector('button[type="submit"]');
      submit.disabled = true;
      try {
        const settings = readLLMSettings();
        await discussionApi('/api/v1/traces/' + encodeURIComponent(CURRENT_TRACE_ID) + '/discussions/' + encodeURIComponent(CURRENT_DISCUSSION_ID) + '/messages', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            message,
            expected_version: active.version,
            client_message_id: window.crypto?.randomUUID?.() || ('msg-' + Date.now()),
            base_url: settings.base_url || '',
            api_key: settings.api_key || '',
            model: settings.model || active.model || ''
          })
        });
        await loadDiscussionDrawer();
      } catch (error) {
        await loadDiscussionDrawer();
        notify('Debugger discussion failed: ' + (error.message || error));
      }
    };
  }
}
function openWorkspaceDrawer(kind, trigger) {
  const resolved = ['hub', 'discussion'].includes(kind) ? kind : 'overview';
  const drawer = document.getElementById(resolved + '-drawer');
  const scrim = document.getElementById('workspace-drawer-scrim');
  if (!drawer || !scrim) return;
  if (ACTIVE_DRAWER === resolved) {
    closeWorkspaceDrawer();
    return;
  }
  if (DRAWER_RETURN_FOCUS) DRAWER_RETURN_FOCUS.setAttribute('aria-expanded', 'false');
  DRAWER_RETURN_FOCUS = trigger || null;
  ACTIVE_DRAWER = resolved;
  document.querySelectorAll('.workspace-drawer').forEach(other => {
    if (other !== drawer) {
      other.classList.remove('visible');
      other.setAttribute('aria-hidden', 'true');
    }
  });
  drawer.classList.add('visible');
  drawer.setAttribute('aria-hidden', 'false');
  scrim.classList.add('visible');
  document.body.classList.add('drawer-open');
  document.getElementById('hub-btn')?.classList.toggle('drawer-active', resolved === 'hub');
  document.getElementById('hub-btn')?.setAttribute('aria-expanded', resolved === 'hub' ? 'true' : 'false');
  document.getElementById('overview-btn')?.classList.toggle('drawer-active', resolved === 'overview');
  document.getElementById('overview-btn')?.setAttribute('aria-expanded', resolved === 'overview' ? 'true' : 'false');
  document.getElementById('discussion-btn')?.classList.toggle('drawer-active', resolved === 'discussion');
  document.getElementById('discussion-btn')?.setAttribute('aria-expanded', resolved === 'discussion' ? 'true' : 'false');
  setRailMode(resolved === 'overview' ? 'overview' : 'trace');
  window.requestAnimationFrame(() => drawer.querySelector('[data-close-drawer]')?.focus());
  if (resolved === 'overview') loadOverviewDrawer();
  else if (resolved === 'hub') loadHubDrawer();
  else loadDiscussionDrawer();
}
function closeWorkspaceDrawer(restoreFocus = true) {
  if (!ACTIVE_DRAWER) return;
  document.querySelectorAll('.workspace-drawer').forEach(drawer => {
    drawer.classList.remove('visible');
    drawer.setAttribute('aria-hidden', 'true');
  });
  document.getElementById('workspace-drawer-scrim')?.classList.remove('visible');
  document.getElementById('hub-btn')?.classList.remove('drawer-active');
  document.getElementById('hub-btn')?.setAttribute('aria-expanded', 'false');
  document.getElementById('overview-btn')?.classList.remove('drawer-active');
  document.getElementById('overview-btn')?.setAttribute('aria-expanded', 'false');
  document.getElementById('discussion-btn')?.classList.remove('drawer-active');
  document.getElementById('discussion-btn')?.setAttribute('aria-expanded', 'false');
  document.body.classList.remove('drawer-open');
  ACTIVE_DRAWER = null;
  setRailMode('trace');
  const returnFocus = DRAWER_RETURN_FOCUS;
  DRAWER_RETURN_FOCUS = null;
  if (restoreFocus) returnFocus?.focus();
}
async function loadCases() {
  try {
    CURRENT_VIEW = 'cases';
    CURRENT_TRACE_ID = null;
    CURRENT_TRACE_DATA = null;
    document.body.classList.add('hub-mode', 'cases-mode');
    document.body.classList.remove('trace-editor-mode', 'overview-mode');
    const detail = document.getElementById('detail');
    if (detail) detail.innerHTML = loadingState('Loading case database');
    const payload = await api('/api/v1/cases');
    renderCasesPage(payload);
  } catch (e) {
    document.getElementById('detail').innerHTML = '<div class="empty">' + escapeHtml(e.message || e) + '</div>';
  }
}
function renderTraceList(traceIds, selectedId) {
  const ul = document.getElementById('trace-list');
  const runSection = document.querySelector('.run-section');
  const previousScroll = runSection ? runSection.scrollTop : 0;
  const q = (document.getElementById('run-search')?.value || '').toLowerCase().trim();
  ul.innerHTML = '';
  document.getElementById('trace-count').textContent = traceIds.length + ' trace' + (traceIds.length === 1 ? '' : 's') + ' in local store';
  renderTraceFilters();
  const catalogById = new Map(TRACE_CATALOG.map(item => [item.trace_id, item]));
  const visible = traceIds
    .map(tid => catalogById.get(tid) || {trace_id: tid, framework: '', dataset_type: '', finding_count: 1})
    .filter(traceMatchesFilter)
    .filter(item => !q || ((item.trace_id || '') + ' ' + readableTaskName(item) + ' ' + (item.model || '') + ' ' + (item.task_type || '')).toLowerCase().includes(q));
  visible.forEach((item) => {
    const tid = item.trace_id;
    const li = document.createElement('li');
    li.className = 'run' + (CURRENT_VIEW === 'trace' && tid === selectedId ? ' active' : '');
    const statusClass = Number(item.finding_count || 0) ? 'bad' : 'good';
    const statusLabel = Number(item.finding_count || 0) ? 'Failed' : 'Passed';
    const dataset = shortDatasetLabel(item.task_type || item.dataset_type || item.framework || 'trace');
    li.title = tid + '\\n' + dataset + ' · ' + shortModelLabel(item.model || item.framework || 'model') + '\\n' + (item.event_count || 0) + ' steps · ' + (item.error_count || item.finding_count || 0) + ' errors';
    li.innerHTML = '<button class="run-save-case" type="button" data-save-case aria-label="Save ' + escapeHtml(tid) + ' as case" title="Save this trace as case"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h12a1 1 0 0 1 1 1v17l-7-4-7 4V4a1 1 0 0 1 1-1Z"></path></svg></button>' +
      '<div class="run-id">' + escapeHtml(runCardTitle(item)) + '</div>' +
      '<div class="run-meta"><span>' + escapeHtml(dataset) + '</span><span>•</span><span>' + escapeHtml(shortModelLabel(item.model || item.framework || 'model')) + '</span><span class="chip ' + statusClass + '">' + statusLabel + '</span></div>' +
      '<div class="run-meta"><span>' + escapeHtml(item.event_count || 0) + ' steps</span><span>•</span><span>' + escapeHtml(item.error_count || item.finding_count || 0) + ' errors</span></div>';
    const saveCaseButton = li.querySelector('[data-save-case]');
    saveCaseButton.dataset.traceId = tid;
    saveCaseButton.onclick = event => {
      event.preventDefault();
      event.stopPropagation();
      saveTraceCase(tid, saveCaseButton);
    };
    li.dataset.tid = tid;
    li.onclick = async () => {
      saveRunScroll();
      const loaded = await selectTrace(tid, li);
      if (loaded) {
        history.pushState({view: 'trace', traceId: tid}, '', '/trace/' + encodeURIComponent(tid));
      }
    };
    ul.appendChild(li);
  });
  if (!visible.length) {
    const empty = document.createElement('li');
    empty.className = 'empty';
    empty.style.padding = '18px 8px';
    empty.textContent = 'No traces match this filter.';
    ul.appendChild(empty);
  }
  restoreRunScroll(previousScroll);
}
function saveRunScroll() {
  const runSection = document.querySelector('.run-section');
  if (!runSection) return;
  sessionStorage.setItem(RUN_SCROLL_KEY, String(runSection.scrollTop || 0));
}
function restoreRunScroll(fallback) {
  const runSection = document.querySelector('.run-section');
  if (!runSection) return;
  const stored = Number(sessionStorage.getItem(RUN_SCROLL_KEY) || fallback || 0);
  window.requestAnimationFrame(() => { runSection.scrollTop = stored; });
}
function renderTraceFilters() {
  const tray = document.getElementById('trace-filters');
  if (!tray) return;
  const filters = [
    ['all', 'All'],
    ['error', 'Errors'],
    ['clean', 'Clean'],
    ['root-early', 'Early RCA']
  ];
  tray.innerHTML = filters.map(([key, label]) =>
    '<button class="filter-chip ' + (ACTIVE_TRACE_FILTER === key ? 'active' : '') + '" type="button" data-filter="' + key + '">' + label + '</button>'
  ).join('');
  tray.querySelectorAll('.filter-chip').forEach(btn => {
    btn.onclick = () => {
      ACTIVE_TRACE_FILTER = btn.dataset.filter || 'all';
      renderTraceList((BOOTSTRAP && BOOTSTRAP.traces) || TRACE_CATALOG.map(item => item.trace_id), CURRENT_TRACE_ID);
    };
  });
}
function traceMatchesFilter(item) {
  const text = ((item.trace_id || '') + ' ' + (item.framework || '') + ' ' + (item.dataset_type || '') + ' ' + (item.top_family || '')).toLowerCase();
  if (ACTIVE_TRACE_FILTER === 'all') return true;
  if (ACTIVE_TRACE_FILTER === 'error') return Number(item.finding_count || 0) > 0;
  if (ACTIVE_TRACE_FILTER === 'clean') return Number(item.finding_count || 0) === 0;
  if (ACTIVE_TRACE_FILTER === 'root-early') return Number(item.first_error_step || item.root_cause_step_index || 9999) <= 5;
  return text.includes(ACTIVE_TRACE_FILTER);
}
function shortDatasetLabel(value) {
  const text = String(value || 'trace').toLowerCase();
  if (text.includes('alfworld')) return 'ALFWorld';
  if (text.includes('webshop')) return 'WebShop';
  return String(value || 'trace').split('/').pop().trim() || 'trace';
}
function readableTaskName(item) {
  const source = item.goal || item.task_id || item.summary || item.trace_id || 'Run';
  let text = String(source)
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  text = text.replace(/^(aeb|trace|demo)\s+/i, '');
  return truncate(text || 'Run', 42);
}
function runCardTitle(item) {
  const source = item.trace_id || item.task_id || item.summary || item.goal || 'Run';
  return truncate(String(source || 'Run'), 48);
}
function shortModelLabel(value) {
  const text = String(value || 'model').replace(/_/g, '-');
  return truncate(text, 24);
}
function matrixModelLabel(item) {
  return shortModelLabel(item?.model || 'unknown model');
}
function matrixEnvironmentLabel(item) {
  return shortDatasetLabel(item?.task_type || item?.dataset_type || item?.framework || 'unknown environment');
}
function durationLabel(ms) {
  const value = Number(ms || 0);
  if (!value) return '-';
  if (value < 1000) return value + 'ms';
  return (value / 1000).toFixed(value < 10000 ? 1 : 0) + 's';
}
function statusLabel(item) {
  return Number(item.finding_count || 0) ? 'Failed' : 'Passed';
}
function statusClassForItem(item) {
  return Number(item.finding_count || 0) ? 'failed' : 'passed';
}
async function selectTrace(tid, li, reportId) {
  if (ACTIVE_DRAWER) closeWorkspaceDrawer(false);
  document.querySelectorAll('.run').forEach(el => el.classList.remove('active'));
  if (li && li.classList) li.classList.add('active');
  if (CURRENT_TRACE_ID !== tid) CURRENT_DISCUSSION_ID = null;
  CURRENT_TRACE_ID = tid;
  CURRENT_VIEW = 'trace';
  document.getElementById('detail').innerHTML = loadingState('Loading trace analysis...');
  try {
    const reportQuery = reportId ? '?report_id=' + encodeURIComponent(reportId) : '';
    const data = await api('/api/v1/traces/' + encodeURIComponent(tid) + reportQuery);
    CURRENT_TRACE_DATA = data;
    renderTrace(data.trajectory, data.report);
    syncDebugBranches(tid, true);
    return true;
  } catch (e) {
    document.getElementById('detail').innerHTML = '<div class="empty">' + escapeHtml(e) + '</div>';
    return false;
  }
}
function traceViewMode(traceId, capability) {
  if (!capability?.enabled) return 'trace';
  try {
    const stored = sessionStorage.getItem(TRACE_VIEW_MODE_PREFIX + traceId);
    if (stored === 'trace' || stored === 'visual') return stored;
  } catch (_e) {
  }
  return capability.default_view === 'visual' ? 'visual' : 'trace';
}
function setTraceViewMode(traceId, mode) {
  try {
    sessionStorage.setItem(TRACE_VIEW_MODE_PREFIX + traceId, mode);
  } catch (_e) {
  }
}
function visualLayoutMode(traceId) {
  try {
    return sessionStorage.getItem(VISUAL_LAYOUT_PREFIX + traceId) === 'compare'
      ? 'compare'
      : 'single';
  } catch (_e) {
    return 'single';
  }
}
function setVisualLayoutMode(traceId, mode) {
  try {
    sessionStorage.setItem(
      VISUAL_LAYOUT_PREFIX + traceId,
      mode === 'single' ? 'single' : 'compare'
    );
  } catch (_e) {
  }
}
function renderTraceViewToggle(traceId, capability, mode) {
  const enabled = Boolean(capability?.enabled);
  return '<div class="trace-visual-toggle" aria-label="Trace view mode">' +
    '<button type="button" data-trace-view="trace" class="' + (mode === 'trace' ? 'active' : '') + '">Trace</button>' +
    '<button type="button" data-trace-view="visual" class="' + (mode === 'visual' ? 'active' : '') + '" ' + (enabled ? '' : 'disabled title="No safe image artifacts"') + '>Visual</button>' +
    '</div>';
}
function renderTrace(traj, report) {
  document.body.classList.add('trace-editor-mode');
  document.body.classList.remove('hub-mode', 'overview-mode', 'cases-mode');
  setRailMode('trace');
  const crumb = document.querySelector('.crumb');
  if (crumb) crumb.textContent = 'Projects  ›  checkout-agent  ›  Debug Runs  ›  ' + (traj.trace_id || 'trace');
  const events = traj.events || [];
  const findings = report.findings || [];
  const rootId = report.root_cause_event_id;
  const branchEvents = debugBranchEventsForTrace(traj.trace_id || '');
  CURRENT_BRANCH_EVENT_MAP = new Map(branchEvents.map(item => [item.event_id, item]));
  const selectableEvents = events.concat(branchEvents);
  if (CURRENT_EXPANDED_EVENT_ID && !selectableEvents.some(ev => ev.event_id === CURRENT_EXPANDED_EVENT_ID)) {
    CURRENT_EXPANDED_EVENT_ID = null;
  }
  if (!CURRENT_EXPANDED_EVENT_ID) {
    CURRENT_EXPANDED_EVENT_ID = rootId || (events[0] && events[0].event_id) || null;
  }
  const systemPrompt = (traj.metadata || {}).system_prompt || '';
  const alignmentEvents = events.filter(ev => ev.step_index !== null && ev.step_index !== undefined);
  const selectedEvent = selectableEvents.find(ev => ev.event_id === CURRENT_EXPANDED_EVENT_ID) || alignmentEvents[0] || events[0] || null;
  const selectedIsBranch = Boolean(selectedEvent?.metadata?.debug_branch_id);
  const selectedOrdinal = selectedEvent
    ? (selectedIsBranch
      ? (selectedEvent.metadata?.debug_ordinal || selectedEvent.step_index || '?')
      : Math.max(1, alignmentEvents.findIndex(ev => ev.event_id === selectedEvent.event_id) + 1))
    : null;
  const selectedFinding = selectedEvent ? findingForEvent(findings, selectedEvent.event_id) : null;
  const branches = getDebugBranches(traj.trace_id || '');
  const visualCapability = CURRENT_TRACE_DATA?.visual_capability || {enabled: false, events: {}};
  const viewMode = traceViewMode(traj.trace_id || '', visualCapability);
  document.getElementById('trace-count').textContent =
    shortDatasetLabel((traj.metadata || {}).task_type || traj.framework || 'trace') + ' · ' +
    shortModelLabel((traj.metadata || {}).llm_model || traj.framework || 'model') + ' · ' +
    (findings.length ? 'Failed' : 'Passed') + ' · ' + events.length + ' events · ' + findings.length + ' errors';
  let html = '';
  html += '<div class="editor-workbench">';
  html += '<main class="editor-main">';
  html += '<section class="editor-stage" id="event-stage">';
  html += '<div class="editor-stage-head"><div class="editor-stage-title"><div class="panel-title">Selected Event</div><div class="panel-hint">Default view is intentionally concise. Open Details only when you need raw trace, state delta, or nearby context.</div></div>';
  html += '<div class="lane-meta" style="margin-top:0;">';
  html += renderTraceViewToggle(traj.trace_id || '', visualCapability, viewMode);
  if (selectedEvent) {
    html += '<span class="chip">' + escapeHtml(events.length) + ' steps</span>';
    html += '<span class="chip warn">' + escapeHtml(findings.length) + ' errors</span>';
    html += '<button class="timeline-tool debug-resume-btn" id="debug-from-event-btn" type="button" data-debug-from-selected>Prepare Rerun</button>';
    html += '<button class="timeline-tool" id="diagnose-pipeline-btn" type="button" data-open-diagnose>Diagnose Pipeline</button>';
    html += '<button class="timeline-tool" id="discussion-btn" type="button" aria-expanded="false" aria-controls="discussion-drawer">Discuss with Debugger</button>';
    const reportOptions = Array.isArray(CURRENT_TRACE_DATA?.reports) ? CURRENT_TRACE_DATA.reports : [];
    const storedReportOptions = reportOptions.filter(item => item?.source === 'stored');
    if (storedReportOptions.length) {
      html += '<select class="report-select" id="report-select" aria-label="Diagnostic report">' +
        storedReportOptions.map(item => '<option value="' + escapeHtml(item.report_id || '') + '" ' + (item.report_id === report.report_id ? 'selected' : '') + '>' + escapeHtml(reportOptionLabel(item)) + '</option>').join('') +
        '</select>';
    }
    if (reportOptions.length) {
      html += '<span class="chip cyan">' + escapeHtml(CURRENT_TRACE_DATA?.report_source === 'stored' ? 'stored report' : 'heuristic fallback') + '</span>';
    }
  }
  html += '</div></div>';
  html += '<div class="editor-stage-body">';
  html += selectedEvent
    ? (viewMode === 'visual'
      ? renderVisualInspector(selectedEvent, selectedEvent.event_id === rootId, selectedFinding, selectedOrdinal, events, report, visualCapability)
      : renderEventInspector(selectedEvent, selectedEvent.event_id === rootId, selectedFinding, selectedOrdinal, selectableEvents, findings))
    : '<div class="editor-empty"><div><strong>No event selected</strong><span>Choose a clip from the timeline.</span></div></div>';
  html += '</div></section>';
  html += '</main>';
  html += renderDiagnosisPanel(report, findings, selectedEvent, selectableEvents);
  html += '</div>';
  html += '<section class="timeline-dock" id="timeline"><div class="panel-head"><div><div class="panel-title">Timeline</div><div class="panel-hint">Scrub events first; rerun attempts are grouped below as branches.</div></div><div class="timeline-toolbar timeline-toolbar-quiet"><button class="timeline-tool timeline-tool-primary" data-open-sessions type="button">Sessions ' + branches.length + '</button><button class="timeline-tool timeline-tool-primary" data-error-nav="-1" type="button">← Prev Error</button><button class="timeline-tool timeline-tool-primary" data-error-nav="1" type="button">Next Error →</button><span class="timeline-tool-group"><button class="timeline-tool" data-timeline-fit type="button">Fit</button><button class="timeline-tool" data-timeline-zoom="-1" type="button">−</button><button class="timeline-tool" data-timeline-zoom="1" type="button">＋</button></span><span class="chip cyan">' + alignmentEvents.length + ' clips</span></div></div><div class="panel-body">';
  html += renderStepExplorer(traj, alignmentEvents, findings, rootId, CURRENT_EXPANDED_EVENT_ID);
  html += '</div></section>';

  document.getElementById('detail').innerHTML = html;
  positionTimelinePlayhead(CURRENT_EXPANDED_EVENT_ID);
  syncDebugBranches(traj.trace_id || '', false);
  bindInspectorTabs();
  bindStepExplorer(traj, report);
  bindClipBrowser(traj, report);
  bindFindingJumps(traj, report);
  bindRelatedEvents(traj, report);
  bindEventNav(traj, report);
  bindTimelineTools(traj, report);
  bindDebugContinuationButton();
  bindDiagnosePipelineButton();
  bindDebugSessionActions(traj, report);
  bindTimelineScrollSync();
  bindChartTooltips();
  bindReportSelector(traj.trace_id || '');
  bindTraceViewToggle(traj, report);
  bindVisualViewer();
  bindDiscussionButton();
  bindHubButton();
}
function reportOptionLabel(item) {
  const analyzer = item?.analyzer && item.analyzer !== 'unknown' ? item.analyzer : 'diagnostic report';
  const count = Number(item?.finding_count || 0);
  return analyzer + ' · ' + count + ' finding' + (count === 1 ? '' : 's');
}
function activeStoredReportId() {
  if (CURRENT_TRACE_DATA?.report_source !== 'stored') return null;
  return CURRENT_TRACE_DATA?.report?.report_id || null;
}
function bindReportSelector(traceId) {
  const select = document.getElementById('report-select');
  if (!select) return;
  select.onchange = async () => {
    select.disabled = true;
    const active = document.querySelector('.run.active');
    const loaded = await selectTrace(traceId, active, select.value);
    if (loaded) notify('Diagnostic report changed');
  };
}
function renderEventDetail(traj, report, eventId) {
  document.body.classList.remove('trace-editor-mode');
  document.body.classList.remove('hub-mode', 'overview-mode');
  setRailMode('trace');
  const events = traj.events || [];
  const findings = report.findings || [];
  const event = events.find(ev => ev.event_id === eventId);
  if (!event) {
    document.getElementById('detail').innerHTML = '<div class="empty">Unknown event.</div>';
    return;
  }
  const idx = events.findIndex(ev => ev.event_id === eventId);
  const prev = idx > 0 ? events[idx - 1] : null;
  const next = idx >= 0 && idx < events.length - 1 ? events[idx + 1] : null;
  const finding = findingForEvent(findings, eventId);
  let html = '';
  html += '<div class="hero">';
  html += '<div class="panel hero-main">';
  html += '<div class="kicker">Event detail</div>';
  html += '<h1>' + escapeHtml((event.agent_name || 'agent') + ' / ' + (event.event_type || 'event')) + '</h1>';
  html += '<div class="goal">' + escapeHtml(event.module || 'No module recorded.') + '</div>';
  html += '<div class="meta-line"><span class="chip">' + escapeHtml(event.event_id || '-') + '</span><span class="chip cyan">step ' + escapeHtml(event.step_index ?? '-') + '</span></div>';
  html += '</div>';
  html += '<div class="panel stats">';
  html += stat('Step', event.step_index ?? '-', 'warn');
  html += stat('Has finding', finding ? 'yes' : 'no', finding ? 'bad' : 'good');
  html += stat('Event type', event.event_type || '-', 'cyan');
  html += stat('Agent', event.agent_name || '-', 'good');
  html += '</div></div>';
  html += '<div class="event-nav">';
  html += '<a href="/trace/' + encodeURIComponent(traj.trace_id) + '#timeline">Back to trace</a>';
  if (prev) html += '<a href="/trace/' + encodeURIComponent(traj.trace_id) + '/event/' + encodeURIComponent(prev.event_id) + '">Previous event</a>';
  if (next) html += '<a href="/trace/' + encodeURIComponent(traj.trace_id) + '/event/' + encodeURIComponent(next.event_id) + '">Next event</a>';
  html += '</div>';
  html += renderEvent(event, event.event_id === report.root_cause_event_id, finding);
  document.getElementById('detail').innerHTML = html;
}
function renderOverview(overview, target) {
  const inDrawer = Boolean(target);
  if (!inDrawer) {
    document.body.classList.remove('trace-editor-mode');
    document.body.classList.remove('diagnosis-collapsed');
    document.body.classList.add('hub-mode', 'overview-mode');
    setRailMode('overview');
  }
  const detail = target || document.getElementById('detail');
  if (!detail) return;
  const crumb = inDrawer ? null : document.querySelector('.crumb');
  if (crumb) crumb.textContent = 'Projects  ›  checkout-agent  ›  Project Hub';
  const catalog = overview.trace_catalog || [];
  const top = overview.top_error_types || [];
  const failedRuns = Number(overview.error_trace_count || 0);
  const failureRate = Number(overview.error_rate_pct || 0);
  const rootCoverage = rootCauseCoverage(catalog);
  if (!inDrawer) document.getElementById('trace-count').textContent = (overview.trace_count || 0) + ' runs in local store';
  detail.innerHTML =
    '<div class="project-overview intel-overview">' +
    '<div class="sr-only">Project Overview Run triage center Failure Trend Failure Mode Breakdown Model × Environment Heatmap Recent Failed Sequences Issue Summary Failed Runs Runs Table</div>' +
    '<div class="overview-top"><div><div class="kicker">Project Overview</div><h1>Run triage center</h1>' +
    '<div class="goal">Failure intelligence hub for spotting batch health, dominant failure modes, model/environment concentration, and critical runs.</div></div>' +
    '<div class="overview-filterbar"><select class="runs-select" id="overview-window"><option value="all">Window: All</option><option value="24h">Last 24 hours</option><option value="7d">Last 7 days</option></select>' +
    '<input class="runs-input" id="overview-search" placeholder="Search runs, model, dataset, error type..." />' +
    '<select class="runs-select" id="overview-status"><option value="all">Status: All</option><option value="failed">Failed only</option><option value="passed">Passed only</option></select>' +
    '<select class="runs-select" id="overview-env"><option value="all">Environment: All</option></select>' +
    '<select class="runs-select" id="overview-model"><option value="all">Model: All</option></select></div></div>' +
    '<div class="intel-hero">' +
    renderSystemHealthHero(overview, catalog, failureRate, rootCoverage) +
    '<div class="intel-panel trend-panel"><div class="panel-head">' + panelTitle('Failure Findings Timeline', 'Stacked findings by failure family across run order. Hover for run detail; click to drill down.') + '<span class="chip bad">' + escapeHtml(failedRuns) + ' failed runs</span></div><div class="intel-chart-body">' + renderStackedFailureTimeline(catalog) + '</div></div>' +
    '</div>' +
    '<div class="pattern-grid">' +
    '<div class="intel-panel"><div class="panel-head">' + panelTitle('Failure Landscape', 'Treemap of dominant failure modes. Larger tiles indicate more findings.') + '</div><div class="intel-chart-body">' + renderFailureTreemap(top, overview.error_family_distribution || []) + '</div></div>' +
    '<div class="intel-panel"><div class="panel-head">' + panelTitle('Root Cause Intelligence', 'Root cause distribution and frequency × impact severity matrix.') + '</div><div class="intel-chart-body">' + renderRootCauseIntelligence(catalog, top) + '</div></div>' +
    '</div>' +
    '<div class="intel-panel"><div class="panel-head">' + panelTitle('Model × Environment Performance Matrix', 'Failure rate, average findings, and step volume by model/environment pair.') + '</div><div class="intel-chart-body">' + renderPerformanceMatrix(catalog) + '</div></div>' +
    '<div class="intel-panel"><div class="panel-head">' + panelTitle('Critical Runs', 'Highest priority traces sorted by severity, findings, and root-cause signal.') + '<span class="chip warn">top ' + escapeHtml(Math.min(8, failedRuns || catalog.length)) + '</span></div><div class="intel-chart-body">' + renderCriticalRuns(catalog) + '</div></div>' +
    '<div class="intel-panel"><div class="panel-head">' + panelTitle('Runs Table', 'Drill-down workspace. Search, filter, then click a row to open Trace Detail.') + '<span class="chip">' + escapeHtml(catalog.length) + ' runs</span></div>' +
    '<div class="runs-toolbar"><input class="runs-input" id="runs-search" placeholder="Search runs..." />' +
    '<select class="runs-select" id="runs-status"><option value="all">Status: All</option><option value="failed">Failed</option><option value="passed">Passed</option></select>' +
    '<select class="runs-select"><option>Environment: All</option></select><select class="runs-select"><option>Model: All</option></select><select class="runs-select"><option>Error Type: All</option></select><select class="runs-select"><option>Sort: Severity</option></select></div>' +
    '<div class="runs-table-wrap">' + renderRunsTable(catalog) + '</div></div>' +
    '</div>';
  bindRunsTable();
  bindChartTooltips();
  bindOverviewInteractions(catalog);
}
function caseFamily(item) {
  return String(item?.top_family || item?.report?.findings?.[0]?.failure_mode?.family || 'unclassified');
}
function caseMode(item) {
  return String(item?.top_mode || item?.report?.findings?.[0]?.failure_mode?.mode_id || 'unclassified');
}
function caseTitle(item) {
  const mode = caseMode(item);
  const source = item?.title || item?.summary || item?.trace_id || 'Untitled case';
  if (source && !String(source).startsWith('trace_')) return truncate(String(source), 58);
  return truncate(mode.replace(/[._-]+/g, ' ') || source, 58);
}
function shortCaseId(item, fallback) {
  const raw = String(item?.case_id || fallback || item?.trace_id || 'case');
  const tail = raw.includes('::') ? raw.split('::').pop() : raw;
  return 'Case ' + truncate(tail.replace(/^case[_-]?/i, ''), 14);
}
function caseEventCount(item) {
  return Number(item?.event_count || item?.trajectory?.events?.length || 0);
}
function caseSeverity(item) {
  const findings = Number(item?.finding_count || 0);
  if (String(item?.severity || '').trim()) return titleCase(item.severity);
  if (findings >= 20) return 'Critical';
  if (findings >= 10) return 'High';
  if (findings >= 3) return 'Medium';
  return findings ? 'Low' : 'Clean';
}
function caseSeverityClass(item) {
  const sev = caseSeverity(item).toLowerCase();
  if (sev === 'critical' || sev === 'high') return 'bad';
  if (sev === 'medium' || sev === 'low') return 'warn';
  return 'good';
}
function caseReviewStatus(item) {
  return titleCase(item?.review_status || item?.review || (item?.root_cause_event_id ? 'verified' : 'draft'));
}
function caseRegressionStatus(item) {
  return titleCase(item?.regression_status || (item?.regression_suite ? 'protected' : 'not bound'));
}
function caseRegressionSuite(item) {
  return item?.regression_suite || (item?.regression_status ? item.regression_status : 'not bound');
}
function caseSubPattern(item) {
  const text = (caseMode(item) + ' ' + (item?.summary || '')).toLowerCase();
  if (text.includes('loop') || text.includes('repeat')) return 'Repeated loop / no progress';
  if (text.includes('premature') || text.includes('stop')) return 'Premature stop';
  if (text.includes('tool') || text.includes('argument')) return 'Tool-use failure';
  if (text.includes('environment') || text.includes('state')) return 'Environment/state mismatch';
  return caseMode(item).replace(/[._-]+/g, ' ');
}
function caseTags(item) {
  const explicit = Array.isArray(item?.tags) ? item.tags : [];
  const tokens = [caseFamily(item), caseMode(item), caseSubPattern(item), item?.dataset, item?.model]
    .join(' ')
    .toLowerCase();
  const tags = new Set(explicit.map(String));
  if (tokens.includes('loop') || tokens.includes('repeat')) tags.add('loop');
  if (tokens.includes('progress')) tags.add('no-progress');
  if (tokens.includes('tool')) tags.add('tool-error');
  if (tokens.includes('planning') || tokens.includes('plan')) tags.add('planning');
  if (item?.root_cause_event_id || item?.root_cause_step_index) tags.add('root-cause-confirmed');
  if (!tags.size) tags.add(caseFamily(item));
  return Array.from(tags).filter(Boolean).slice(0, 6);
}
function rootCauseSummary(item) {
  return item?.root_cause_summary || item?.summary || item?.report?.summary ||
    'This saved case captures a representative failure signal. Open the source trace to inspect the exact event sequence and detector evidence.';
}
function suggestedCaseFix(item) {
  const suggestion = item?.suggested_fix || item?.report?.suggestions?.[0]?.description || item?.report?.suggestions?.[0];
  if (suggestion) return String(suggestion);
  const mode = caseMode(item).toLowerCase();
  if (mode.includes('plan')) return 'Add progress checks and loop detection before repeating the same plan or action sequence.';
  if (mode.includes('verification')) return 'Require explicit validation evidence before the agent can stop or mark the task complete.';
  if (mode.includes('system')) return 'Capture environment state transitions and fail closed when observations are missing or inconsistent.';
  return 'Document the trigger condition, add a detector assertion, and include this case in regression review.';
}
function primaryCaseRule(item) {
  const finding = item?.report?.findings?.[0] || {};
  return finding?.metadata?.rule_id || finding?.rule_id || finding?.rule || caseMode(item);
}
function caseConfidence(item) {
  const finding = item?.report?.findings?.[0] || {};
  const value = item?.confidence ?? finding?.confidence ?? finding?.metadata?.confidence;
  return value == null ? 'n/a' : String(value);
}
function relativeCaseDate(value) {
  if (!value) return 'unknown';
  const ms = Date.now() - new Date(value).getTime();
  if (!Number.isFinite(ms)) return String(value);
  const days = Math.floor(ms / 86400000);
  if (days <= 0) return 'today';
  if (days === 1) return '1 day ago';
  if (days < 30) return days + ' days ago';
  return String(value).slice(0, 10);
}
function caseLibraryStats(cases) {
  const items = cases || [];
  const familyCounts = new Map();
  const modeCounts = new Map();
  const tags = new Map();
  items.forEach(item => {
    const family = caseFamily(item);
    const mode = caseMode(item);
    familyCounts.set(family, (familyCounts.get(family) || 0) + 1);
    modeCounts.set(family + '||' + mode, (modeCounts.get(family + '||' + mode) || 0) + 1);
    caseTags(item).forEach(tag => tags.set(tag, (tags.get(tag) || 0) + 1));
  });
  return {
    total: items.length,
    patternCount: modeCounts.size,
    regressionCount: items.filter(item => caseRegressionStatus(item).toLowerCase() !== 'not bound').length,
    unreviewedCount: items.filter(item => caseReviewStatus(item).toLowerCase() === 'draft').length,
    recentCount: items.filter(item => isRecentCase(item)).length,
    familyCounts,
    modeCounts,
    tags,
    cases: items,
  };
}
function isRecentCase(item) {
  const ms = Date.now() - new Date(item?.created_at || 0).getTime();
  return Number.isFinite(ms) && ms >= 0 && ms <= 7 * 86400000;
}
function renderCaseSummaryStrip(stats) {
  return '<div class="case-summary-strip">' +
    summaryTile('Total Cases', stats.total, 'saved exemplars', 'all') +
    summaryTile('Failure Patterns', stats.patternCount, 'family / mode groups', 'all') +
    summaryTile('Regression Suites', stats.regressionCount, 'bound cases', 'regression') +
    summaryTile('Unreviewed Cases', stats.unreviewedCount, 'need triage', 'draft') +
    summaryTile('Added This Week', stats.recentCount, 'fresh cases', 'recent') +
    '</div>';
}
function summaryTile(label, value, sub, filter) {
  return '<button type="button" class="case-summary-tile" data-case-quick-filter="' + escapeHtml(filter) + '"><div class="case-summary-label">' + escapeHtml(label) + '</div><div class="case-summary-value">' + escapeHtml(value) + '</div><div class="case-summary-sub">' + escapeHtml(sub) + '</div></button>';
}
function renderPatternNavigator(stats) {
  let html = '<aside class="pattern-nav"><div class="pattern-nav-head"><div class="panel-title">Failure Patterns</div><div class="panel-hint">Filter cases by reusable error pattern.</div></div><div class="pattern-nav-list">';
  html += patternNavItem('All Cases', stats.total, 'all', '', true);
  Array.from(stats.familyCounts.entries()).sort((a, b) => b[1] - a[1]).forEach(([family, count]) => {
    html += patternNavItem(family, count, 'family', family, false);
    Array.from(stats.modeCounts.entries())
      .filter(([key]) => key.startsWith(family + '||'))
      .sort((a, b) => b[1] - a[1])
      .forEach(([key, modeCount]) => {
        html += patternNavItem(key.split('||')[1], modeCount, 'mode', key.split('||')[1], false, 'sub');
      });
  });
  html += '</div><div class="pattern-nav-head"><div class="panel-title">Tags</div></div><div class="tag-cloud">';
  html += Array.from(stats.tags.entries()).sort((a, b) => b[1] - a[1]).slice(0, 12).map(([tag, count]) =>
    '<button type="button" class="tag-pill" data-case-pattern-kind="tag" data-case-pattern-value="' + escapeHtml(tag) + '">' + escapeHtml(tag) + ' · ' + escapeHtml(count) + '</button>'
  ).join('');
  return html + '</div></aside>';
}
function patternNavItem(label, count, kind, value, active, extra) {
  return '<button type="button" class="pattern-nav-item ' + escapeHtml(extra || '') + (active ? ' active' : '') + '" data-case-pattern-kind="' + escapeHtml(kind) + '" data-case-pattern-value="' + escapeHtml(value || '') + '"><span>' + escapeHtml(label) + '</span><span class="pattern-nav-count">' + escapeHtml(count) + '</span></button>';
}
function renderPatternSummary(stats, selected) {
  const firstPattern = Array.from(stats.modeCounts.entries()).sort((a, b) => b[1] - a[1])[0];
  const label = firstPattern ? firstPattern[0].replace('||', ' · ') : 'All Cases';
  const count = firstPattern ? firstPattern[1] : stats.total;
  return '<div class="pattern-summary" id="pattern-summary"><div><div class="case-id-kicker">Pattern Summary</div><div class="pattern-summary-title">' + escapeHtml(label) + '</div>' +
    '<div class="pattern-summary-copy">Representative cases are grouped by failure family and mode so the team can reuse them for debugging, detector validation, and regression review.</div>' +
    '<div class="case-tags"><span class="case-tag">' + escapeHtml(count) + ' representative cases</span><span class="case-tag">' + escapeHtml(stats.familyCounts.size) + ' families</span><span class="case-tag">' + escapeHtml(stats.tags.size) + ' tags</span></div></div>' +
    '<div class="pattern-summary-actions"><button class="button" type="button" data-focus-case-detail>Open Selected Case</button><button class="button" type="button" disabled title="Regression execution is not available in this build.">Run Regression</button><button class="button" type="button" data-export-cases>Export Cases</button></div></div>';
}
function renderCaseMenu(item) {
  const traceId = item?.trace_id || '';
  const caseId = item?.case_id || '';
  return '<div class="case-card-actions"><button type="button" class="case-menu-btn" aria-label="Case actions">⋮</button>' +
    '<div class="case-menu">' +
    '<button type="button" data-open-case-trace="' + escapeHtml(traceId) + '">Open Trace</button>' +
    '<button type="button" disabled title="Case comparison is not available in this build.">Compare</button>' +
    '<button type="button" disabled title="Regression suites are not available in this build.">Add to Regression Suite</button>' +
    '<button type="button" disabled title="Metadata editing is not available in this build.">Edit Metadata</button>' +
    '<button type="button" data-copy-case-id="' + escapeHtml(caseId) + '">Copy Case ID</button>' +
    '<button type="button" data-export-case-id="' + escapeHtml(caseId) + '">Export JSON</button>' +
    '<button type="button" class="danger" data-delete-case-id="' + escapeHtml(caseId) + '">Delete</button>' +
    '</div></div>';
}
function renderCasesPage(payload, target) {
  const inDrawer = Boolean(target);
  if (!inDrawer) {
    document.body.classList.remove('trace-editor-mode', 'overview-mode');
    document.body.classList.add('hub-mode', 'cases-mode');
    setRailMode('cases');
    CURRENT_VIEW = 'cases';
  }
  const crumb = inDrawer ? null : document.querySelector('.crumb');
  if (crumb) crumb.textContent = 'Projects  ›  checkout-agent  ›  Typical Error Database';
  const detail = target || document.getElementById('detail');
  if (!detail) return;
  const cases = payload.cases || [];
  const stats = caseLibraryStats(cases);
  if (!inDrawer) document.getElementById('trace-count').textContent = cases.length + ' saved cases · ' + (payload.path || CASE_DB_FILENAME);
  detail.innerHTML =
    '<div class="project-overview case-library-shell">' +
    '<div class="overview-top"><div><div class="kicker">Failure Pattern Library</div><h1>Curated debugging knowledge base</h1>' +
    '<div class="goal">Curated cases for debugging, regression testing, detector validation, and team review. Stored locally as <span class="mono">' + escapeHtml(payload.path || CASE_DB_FILENAME) + '</span>.</div></div>' +
    '<div class="lane-meta"><span class="chip cyan">' + escapeHtml(cases.length) + ' cases</span><span class="chip warn">' + escapeHtml(stats.patternCount) + ' patterns</span><button class="button" type="button" id="refresh-cases-btn">Refresh</button></div></div>' +
    renderCaseSummaryStrip(stats) +
    '<div class="case-library-grid">' +
    renderPatternNavigator(stats) +
    '<div class="case-workspace">' +
    renderPatternSummary(stats, 'all') +
    '<div class="panel"><div class="panel-head">' + panelTitle('Representative Cases', 'Select a case to inspect classification, root cause, suggested fix, and regression usage.') +
    '<div class="case-view-toggle"><button type="button" class="active" data-case-view="grid">Grid</button><button type="button" data-case-view="table">Table</button></div></div>' +
    '<div class="case-controls"><input class="runs-input" id="case-search" placeholder="Search cases, patterns, tags..." />' +
    '<select class="runs-select" id="case-env"><option value="all">Environment: All</option></select>' +
    '<select class="runs-select" id="case-model"><option value="all">Model: All</option></select>' +
    '<select class="runs-select" id="case-type"><option value="all">Error Type: All</option></select>' +
    '<select class="runs-select" id="case-review"><option value="all">Review: All</option><option value="draft">Draft</option><option value="reviewed">Reviewed</option><option value="verified">Verified</option></select>' +
    '<button class="button primary" type="button" id="new-case-hint">+ Add from Trace</button></div>' +
    '<div class="panel-body case-list-scroll">' + renderCaseCards(cases) + renderCaseTable(cases) + '</div></div></div>' +
    '<div class="case-detail-panel">' + renderCaseDetailPanel((cases || [])[0]) + '</div>' +
    '</div>' +
    '</div>';
  const refresh = document.getElementById('refresh-cases-btn');
  if (refresh) refresh.onclick = () => inDrawer ? loadHubDrawer() : loadCases();
  const hint = document.getElementById('new-case-hint');
  if (hint) hint.onclick = () => notify('Open a trace and use the bookmark button in Run Navigator.');
  bindCaseCards();
  bindCaseControls(cases, stats);
}
function renderCaseCards(cases) {
  if (!cases || !cases.length) {
    return '<div class="empty">No typical cases saved yet. Open a trace and use the bookmark button in Run Navigator.</div>';
  }
  return '<div class="case-grid">' + cases.map(item => {
    const traceId = item.trace_id || '';
    const caseId = item.case_id || '';
    const family = caseFamily(item);
    const mode = caseMode(item);
    const severity = caseSeverity(item);
    const review = caseReviewStatus(item);
    const regression = caseRegressionStatus(item);
    const tags = caseTags(item);
    const recent = isRecentCase(item) ? 'yes' : 'no';
    const haystack = [
      traceId, item.title, item.summary, item.dataset, item.model, family, mode, tags.join(' ')
    ].join(' ').toLowerCase();
    return '<div class="case-card" role="button" tabindex="0" data-trace-id="' + escapeHtml(traceId) + '" data-case-id="' + escapeHtml(caseId) + '" data-env="' + escapeHtml(shortDatasetLabel(item.dataset || 'trace')) + '" data-model="' + escapeHtml(shortModelLabel(item.model || 'unknown model')) + '" data-family="' + escapeHtml(family) + '" data-mode="' + escapeHtml(mode) + '" data-type="' + escapeHtml(mode) + '" data-review="' + escapeHtml(review.toLowerCase()) + '" data-regression="' + escapeHtml(regression.toLowerCase()) + '" data-recent="' + escapeHtml(recent) + '" data-search="' + escapeHtml(haystack) + '" data-tooltip="' + escapeHtml(caseTooltipHtml(item)) + '">' +
      '<div class="case-card-head"><div><div class="case-id-kicker">' + escapeHtml(shortCaseId(item, caseId)) + '</div><div class="case-card-title">' + escapeHtml(caseTitle(item)) + '</div></div>' +
      renderCaseMenu(item) + '</div>' +
      '<div class="lane-meta"><span class="chip bad">' + escapeHtml(family) + '</span><span class="chip warn">' + escapeHtml(mode) + '</span><span class="chip cyan">' + escapeHtml(severity) + '</span></div>' +
      '<div class="case-status-row"><span class="chip ' + (item.root_cause_event_id ? 'good' : 'warn') + '">' + escapeHtml(item.root_cause_event_id ? 'root confirmed' : 'root pending') + '</span><span class="chip">' + escapeHtml(review) + '</span><span class="chip">' + escapeHtml(regression) + '</span></div>' +
      '<div class="case-mini-timeline">' + renderCaseMiniTimeline(item) + '</div>' +
      '<div class="case-card-summary">' + escapeHtml(item.summary || item.note || 'No summary recorded.') + '</div>' +
      '<div class="case-meta-grid">' +
      caseMeta('Dataset', shortDatasetLabel(item.dataset || 'trace')) +
      caseMeta('Model', shortModelLabel(item.model || 'unknown model')) +
      caseMeta('Signals', (item.finding_count || 0) + ' / step ' + (item.root_cause_step_index ?? '-')) +
      '</div>' +
      '<div class="case-tags">' + tags.map(tag => '<span class="case-tag">' + escapeHtml(tag) + '</span>').join('') + '</div>' +
      '<div class="case-card-summary mono">' + escapeHtml(relativeCaseDate(item.created_at)) + '</div>' +
      '</div>';
  }).join('') + '</div>';
}
function renderCaseTable(cases) {
  if (!cases || !cases.length) return '';
  const rows = cases.map(item => {
    const traceId = item.trace_id || '';
    const caseId = item.case_id || '';
    const family = caseFamily(item);
    const mode = caseMode(item);
    const review = caseReviewStatus(item);
    const regression = caseRegressionStatus(item);
    const recent = isRecentCase(item) ? 'yes' : 'no';
    const haystack = [traceId, item.title, item.summary, item.dataset, item.model, family, mode, caseTags(item).join(' ')].join(' ').toLowerCase();
    return '<tr data-trace-id="' + escapeHtml(traceId) + '" data-case-id="' + escapeHtml(caseId) + '" data-env="' + escapeHtml(shortDatasetLabel(item.dataset || 'trace')) + '" data-model="' + escapeHtml(shortModelLabel(item.model || 'unknown model')) + '" data-family="' + escapeHtml(family) + '" data-mode="' + escapeHtml(mode) + '" data-type="' + escapeHtml(mode) + '" data-review="' + escapeHtml(review.toLowerCase()) + '" data-regression="' + escapeHtml(regression.toLowerCase()) + '" data-recent="' + escapeHtml(recent) + '" data-search="' + escapeHtml(haystack) + '">' +
      '<td><strong>' + escapeHtml(caseTitle(item)) + '</strong><div class="mono">' + escapeHtml(shortCaseId(item, caseId)) + '</div></td>' +
      '<td>' + escapeHtml(family) + '<div class="diagnosis-copy">' + escapeHtml(mode) + '</div></td>' +
      '<td>' + escapeHtml(shortDatasetLabel(item.dataset || 'trace')) + '</td>' +
      '<td>' + escapeHtml(shortModelLabel(item.model || 'unknown model')) + '</td>' +
      '<td><span class="chip ' + caseSeverityClass(item) + '">' + escapeHtml(caseSeverity(item)) + '</span></td>' +
      '<td>' + escapeHtml(item.finding_count || 0) + '</td>' +
      '<td>' + escapeHtml(item.root_cause_step_index ?? '-') + '</td>' +
      '<td>' + escapeHtml(caseEventCount(item)) + '</td>' +
      '<td>' + escapeHtml(regression) + '</td>' +
      '<td>' + escapeHtml(review) + '</td>' +
      '<td>' + escapeHtml(relativeCaseDate(item.created_at)) + '</td></tr>';
  }).join('');
  return '<div class="case-table-wrap"><table class="case-table"><thead><tr><th>Case</th><th>Pattern</th><th>Environment</th><th>Model</th><th>Severity</th><th>Findings</th><th>Root Step</th><th>Steps</th><th>Regression</th><th>Review</th><th>Updated</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
}
function renderCaseDetailPanel(item) {
  if (!item) {
    return '<div class="case-empty-side"><strong>No case selected</strong><div class="case-detail-summary">Save a trace from the Trace Editor to build a reusable error database.</div></div>';
  }
  return '<div class="panel"><div class="panel-head">' + panelTitle('Case Detail', 'Focused summary for the selected typical error.') + '<span class="chip cyan">JSONL</span></div><div class="panel-body">' +
    '<div class="case-detail-hero" id="case-detail-content">' + renderCaseDetailContent(item) + '</div></div></div>';
}
function renderCaseDetailContent(item) {
  const traceId = item.trace_id || '';
  const family = caseFamily(item);
  const mode = caseMode(item);
  return '<div class="case-id-kicker">' + escapeHtml(shortCaseId(item, item.case_id || '')) + '</div>' +
    '<div class="case-detail-title">' + escapeHtml(caseTitle(item)) + '</div>' +
    '<div class="case-status-row"><span class="chip ' + caseSeverityClass(item) + '">' + escapeHtml(caseSeverity(item)) + '</span><span class="chip ' + (item.root_cause_event_id ? 'good' : 'warn') + '">' + escapeHtml(item.root_cause_event_id ? 'Root Cause Confirmed' : 'Root Cause Pending') + '</span><span class="chip cyan">' + escapeHtml(caseRegressionStatus(item)) + '</span></div>' +
    '<div class="case-mini-timeline">' + renderCaseMiniTimeline(item) + '</div>' +
    '<div class="case-detail-section"><div class="case-detail-section-title">Case Snapshot</div><div class="case-meta-grid">' +
    caseMeta('Environment', shortDatasetLabel(item.dataset || 'trace')) +
    caseMeta('Model', shortModelLabel(item.model || 'unknown model')) +
    caseMeta('Total Findings', item.finding_count || 0) +
    caseMeta('Root Step', item.root_cause_step_index ?? '-') +
    caseMeta('Total Steps', caseEventCount(item)) +
    caseMeta('Saved', relativeCaseDate(item.created_at)) +
    '</div></div>' +
    '<div class="case-detail-section"><div class="case-detail-section-title">Pattern Classification</div><div class="case-meta-grid">' +
    caseMeta('Family', family) +
    caseMeta('Mode', mode) +
    caseMeta('Sub-pattern', caseSubPattern(item)) +
    '</div><div class="case-tags">' + caseTags(item).map(tag => '<span class="case-tag">' + escapeHtml(tag) + '</span>').join('') + '</div></div>' +
    '<div class="case-detail-section"><div class="case-detail-section-title">Root Cause Summary</div><div class="case-detail-text">' + escapeHtml(rootCauseSummary(item)) + '</div><div class="case-meta-grid">' + caseMeta('Primary Rule', primaryCaseRule(item)) + caseMeta('Confidence', caseConfidence(item)) + '</div></div>' +
    '<div class="case-detail-section"><div class="case-detail-section-title">Suggested Fix</div><div class="case-detail-text">' + escapeHtml(suggestedCaseFix(item)) + '</div><div class="case-detail-actions"><button class="button" type="button" data-copy-case-fix="' + escapeHtml(suggestedCaseFix(item)) + '">Copy Fix</button><button class="button" type="button" disabled title="Detector rule editing is not available in this build.">Open Detector Rule</button></div></div>' +
    '<div class="case-detail-section"><div class="case-detail-section-title">Usage & Review</div><div class="case-meta-grid">' +
    caseMeta('Regression Suite', caseRegressionSuite(item)) +
    caseMeta('Review Status', caseReviewStatus(item)) +
    caseMeta('Saved By', item.saved_by || item.author || 'local user') +
    caseMeta('Last Updated', relativeCaseDate(item.created_at)) +
    '</div></div>' +
    '<div class="case-detail-section"><div class="case-detail-section-title">Notes</div><div class="case-detail-text">' + escapeHtml(item.note || 'No team note yet. Use this case as a stable reference for future annotations.') + '</div></div>' +
    '<div class="case-detail-actions">' +
    '<button class="button primary" type="button" data-open-case-trace="' + escapeHtml(traceId) + '">Open Trace</button>' +
    '<button class="button" type="button" disabled title="Regression suites are not available in this build.">Add to Regression Suite</button>' +
    '<button class="button" type="button" disabled title="Review workflow is not available in this build.">Mark as Reviewed</button>' +
    '<button class="button" type="button" data-copy-case-id="' + escapeHtml(item.case_id || '') + '">Copy Case ID</button>' +
    '</div>';
}
function renderCaseMiniTimeline(item) {
  const mini = Array.isArray(item?.mini_timeline) ? item.mini_timeline : [];
  if (mini.length) {
    return mini.slice(0, 44).map(step => {
      const state = step.state === 'root' ? 'root' : (step.state === 'error' ? 'error' : 'ok');
      return '<span class="case-mini-seg ' + state + '" title="step ' + escapeHtml(step.step_index ?? '-') + '"></span>';
    }).join('');
  }
  const events = item?.trajectory?.events || [];
  const findings = item?.report?.findings || [];
  const findingIds = new Set(findings.map(f => f.event_id));
  const rootId = item?.report?.root_cause_event_id || '';
  if (!events.length) return '<span class="case-mini-seg"></span>';
  return events.slice(0, 44).map((event, idx) => {
    const state = event.event_id === rootId ? 'root' : (findingIds.has(event.event_id) ? 'error' : 'ok');
    return '<span class="case-mini-seg ' + state + '" title="step ' + escapeHtml(event.step_index ?? idx + 1) + '"></span>';
  }).join('');
}
function caseTooltipHtml(item) {
  return '<strong>' + escapeHtml(item.title || item.trace_id || 'Saved case') + '</strong><span>' +
    escapeHtml(shortDatasetLabel(item.dataset || 'trace')) + ' · ' +
    escapeHtml(shortModelLabel(item.model || 'model')) + ' · ' +
    escapeHtml(item.finding_count || 0) + ' findings</span>';
}
function caseMeta(label, value) {
  return '<div class="case-meta"><div class="case-meta-label">' + escapeHtml(label) + '</div><div class="case-meta-value">' + escapeHtml(value) + '</div></div>';
}
function bindCaseCards() {
  document.querySelectorAll('.case-card[data-trace-id]').forEach(card => {
    const select = () => {
      const item = caseByCurrentDomId(card.dataset.caseId || '');
      const detail = document.getElementById('case-detail-content');
      if (item && detail) detail.innerHTML = renderCaseDetailContent(item);
      document.querySelectorAll('.case-card, .case-table tbody tr').forEach(other => {
        other.classList.toggle('selected', other.dataset.caseId === card.dataset.caseId);
      });
      bindCaseCards();
    };
    card.onclick = select;
    card.onkeydown = event => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        select();
      }
    };
  });
  document.querySelectorAll('.case-menu-btn, .case-menu button').forEach(button => {
    button.onclick = event => event.stopPropagation();
  });
  document.querySelectorAll('.case-table tbody tr[data-case-id]').forEach(row => {
    row.onclick = () => {
      const item = caseByCurrentDomId(row.dataset.caseId || '');
      const detail = document.getElementById('case-detail-content');
      if (item && detail) detail.innerHTML = renderCaseDetailContent(item);
      document.querySelectorAll('.case-card, .case-table tbody tr').forEach(other => {
        other.classList.toggle('selected', other.dataset.caseId === row.dataset.caseId);
      });
      bindCaseCards();
    };
  });
  document.querySelectorAll('[data-delete-case-id]').forEach(button => {
    button.onclick = async event => {
      event.stopPropagation();
      const caseId = button.dataset.deleteCaseId || '';
      if (!caseId) return;
      if (!window.confirm('Delete this typical error case?')) return;
      button.disabled = true;
      const previous = button.textContent;
      button.textContent = 'Deleting...';
      try {
        const response = await fetch('/api/v1/cases/' + encodeURIComponent(caseId), {method: 'DELETE'});
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          throw new Error(payload.detail || 'delete failed');
        }
        notify('Case deleted');
        await loadCases();
      } catch (e) {
        notify('Delete failed: ' + (e.message || e));
        button.disabled = false;
        button.textContent = previous || 'Delete';
      }
    };
  });
  document.querySelectorAll('[data-open-case-trace]').forEach(button => {
    button.onclick = async () => {
      const traceId = button.dataset.openCaseTrace || '';
      if (!traceId) return;
      const loaded = await selectTrace(traceId, button);
      if (loaded) history.pushState({view: 'trace', traceId: traceId}, '', '/trace/' + encodeURIComponent(traceId));
    };
  });
  document.querySelectorAll('[data-copy-case-id]').forEach(button => {
    button.onclick = () => copyText(button.dataset.copyCaseId || '', 'Case ID copied');
  });
  document.querySelectorAll('[data-copy-case-fix]').forEach(button => {
    button.onclick = () => copyText(button.dataset.copyCaseFix || '', 'Suggested fix copied');
  });
  document.querySelectorAll('[data-export-case-id]').forEach(button => {
    button.onclick = event => {
      event.stopPropagation();
      const item = caseByCurrentDomId(button.dataset.exportCaseId || '');
      if (!item) return notify('Case record is unavailable');
      const name = String(item.case_id || 'case').replace(/[^a-z0-9._-]+/gi, '_');
      downloadJson(name + '.json', item);
      notify('Case exported');
    };
  });
  document.querySelectorAll('[data-export-cases]').forEach(button => {
    button.onclick = () => {
      const records = currentCaseRecords();
      downloadJson('agentdebugx.cases.json', records);
      notify('Cases exported');
    };
  });
  document.querySelectorAll('[data-focus-case-detail]').forEach(button => {
    button.onclick = () => document.getElementById('case-detail-content')?.scrollIntoView({behavior: 'smooth', block: 'start'});
  });
  document.querySelectorAll('[data-info-popover]').forEach(button => {
    button.onclick = event => {
      event.stopPropagation();
      notify(button.dataset.infoPopover || 'No extra information.');
    };
  });
}
function caseByCurrentDomId(caseId) {
  return currentCaseRecords().find(item => String(item.case_id || '') === String(caseId || '')) || null;
}
function currentCaseRecords() {
  const raw = document.getElementById('case-payload-cache')?.textContent || '[]';
  try {
    const records = JSON.parse(raw);
    return Array.isArray(records) ? records : [];
  } catch {
    return [];
  }
}
function bindCaseControls(cases, stats) {
  let cache = document.getElementById('case-payload-cache');
  if (!cache) {
    cache = document.createElement('script');
    cache.type = 'application/json';
    cache.id = 'case-payload-cache';
    document.body.appendChild(cache);
  }
  cache.textContent = JSON.stringify(cases || []);
  const search = document.getElementById('case-search');
  const env = document.getElementById('case-env');
  const model = document.getElementById('case-model');
  const type = document.getElementById('case-type');
  const review = document.getElementById('case-review');
  const cards = Array.from(document.querySelectorAll('.case-card'));
  const rows = Array.from(document.querySelectorAll('.case-table tbody tr'));
  let patternKind = 'all';
  let patternValue = '';
  fillSelect(env, cards.map(card => card.dataset.env || ''), 'Environment: All');
  fillSelect(model, cards.map(card => card.dataset.model || ''), 'Model: All');
  fillSelect(type, cards.map(card => card.dataset.type || ''), 'Failure Mode: All');
  const apply = () => {
    const q = (search?.value || '').toLowerCase().trim();
    const envValue = env?.value || 'all';
    const modelValue = model?.value || 'all';
    const typeValue = type?.value || 'all';
    const reviewValue = review?.value || 'all';
    const visibleCaseIds = new Set();
    const filterNode = node => {
      const tags = (node.dataset.search || '').toLowerCase();
      const patternOk = patternKind === 'all' ||
        (patternKind === 'family' && node.dataset.family === patternValue) ||
        (patternKind === 'mode' && node.dataset.mode === patternValue) ||
        (patternKind === 'tag' && tags.includes(patternValue.toLowerCase())) ||
        (patternKind === 'regression' && node.dataset.regression !== 'not bound') ||
        (patternKind === 'draft' && node.dataset.review === 'draft') ||
        (patternKind === 'recent' && node.dataset.recent === 'yes');
      return (!q || tags.includes(q)) &&
        (envValue === 'all' || node.dataset.env === envValue) &&
        (modelValue === 'all' || node.dataset.model === modelValue) &&
        (typeValue === 'all' || node.dataset.type === typeValue) &&
        (reviewValue === 'all' || node.dataset.review === reviewValue) &&
        patternOk;
    };
    cards.forEach(card => {
      const ok = filterNode(card);
      card.style.display = ok ? '' : 'none';
      if (ok) visibleCaseIds.add(card.dataset.caseId || '');
    });
    rows.forEach(row => {
      const ok = filterNode(row);
      row.style.display = ok ? '' : 'none';
    });
    updatePatternSummaryFromVisible(cases, visibleCaseIds, patternKind, patternValue);
  };
  [search, env, model, type, review].forEach(control => {
    if (!control) return;
    control.oninput = apply;
    control.onchange = apply;
  });
  document.querySelectorAll('[data-case-pattern-kind]').forEach(button => {
    button.onclick = () => {
      patternKind = button.dataset.casePatternKind || 'all';
      patternValue = button.dataset.casePatternValue || '';
      document.querySelectorAll('[data-case-pattern-kind]').forEach(other => other.classList.toggle('active', other === button));
      apply();
    };
  });
  document.querySelectorAll('[data-case-quick-filter]').forEach(button => {
    button.onclick = () => {
      patternKind = button.dataset.caseQuickFilter || 'all';
      patternValue = '';
      if (patternKind === 'all') {
        if (search) search.value = '';
        if (env) env.value = 'all';
        if (model) model.value = 'all';
        if (type) type.value = 'all';
        if (review) review.value = 'all';
      }
      if (patternKind === 'draft' && review) review.value = 'draft';
      apply();
    };
  });
  document.querySelectorAll('[data-case-view]').forEach(button => {
    button.onclick = () => {
      const mode = button.dataset.caseView || 'grid';
      document.querySelectorAll('[data-case-view]').forEach(other => other.classList.toggle('active', other === button));
      document.querySelector('.case-grid')?.classList.toggle('hidden', mode !== 'grid');
      document.querySelector('.case-table-wrap')?.classList.toggle('active', mode === 'table');
    };
  });
  cards.forEach((card, idx) => {
    if (idx === 0) card.classList.add('selected');
  });
  apply();
}
function updatePatternSummaryFromVisible(cases, visibleCaseIds, kind, value) {
  const target = document.getElementById('pattern-summary');
  if (!target) return;
  const visible = (cases || []).filter(item => visibleCaseIds.has(String(item.case_id || '')));
  const title = kind === 'family' ? value : (kind === 'mode' ? value : (kind === 'tag' ? '#' + value : 'All Cases'));
  const models = new Set(visible.map(item => shortModelLabel(item.model || 'unknown model')));
  const envs = new Set(visible.map(item => shortDatasetLabel(item.dataset || 'trace')));
  target.querySelector('.pattern-summary-title').textContent = title || 'All Cases';
  target.querySelector('.pattern-summary-copy').textContent =
    (visible.length || 0) + ' representative cases detected in ' + models.size + ' models and ' + envs.size + ' environments.';
}
function fillSelect(select, values, allLabel) {
  if (!select) return;
  const unique = Array.from(new Set((values || []).filter(Boolean))).sort();
  const previous = select.value || 'all';
  select.innerHTML = '<option value="all">' + escapeHtml(allLabel || 'All') + '</option>' +
    unique.map(value => '<option value="' + escapeHtml(value) + '">' + escapeHtml(value) + '</option>').join('');
  select.value = unique.includes(previous) ? previous : 'all';
}
function setRailMode(mode) {
  document.getElementById('overview-btn')?.classList.toggle('drawer-active', mode === 'overview');
}
function stat(label, value, klass) {
  return '<div class="stat"><div class="stat-label">' + escapeHtml(label) + '</div><div class="stat-value ' + klass + '">' + escapeHtml(value) + '</div></div>';
}
function triageKpi(label, value, sub, klass, filter) {
  return '<div class="triage-kpi ' + escapeHtml(klass || '') + '" data-kpi="' + escapeHtml(label.toLowerCase()) + '" data-kpi-filter="' + escapeHtml(filter || 'all') + '">' +
    '<div class="triage-kpi-label">' + escapeHtml(label) + '</div>' +
    '<div class="triage-kpi-value ' + escapeHtml(klass || '') + '">' + escapeHtml(value) + '</div>' +
    '<div class="triage-kpi-sub">' + escapeHtml(sub || '') + '</div></div>';
}
function unresolvedRootCauses(catalog) {
  return (catalog || []).filter(item => Number(item.finding_count || 0) && !item.root_cause_found).length;
}
function rootCauseCoverage(catalog) {
  const failed = (catalog || []).filter(item => Number(item.finding_count || 0));
  if (!failed.length) return 100;
  const found = failed.filter(item => item.root_cause_found || item.root_cause_step_index).length;
  return Math.round((found / failed.length) * 100);
}
function severityLabel(item) {
  const findings = Number(item.finding_count || item.error_count || 0);
  if (findings >= 20) return 'Critical';
  if (findings >= 10) return 'High';
  if (findings >= 3) return 'Medium';
  return findings ? 'Low' : 'Clean';
}
function familyColor(family, idx) {
  const key = String(family || '').toLowerCase();
  if (key.includes('planning')) return '#F07167';
  if (key.includes('verification')) return '#F1B958';
  if (key.includes('system')) return '#9D8CFF';
  if (key.includes('tool') || key.includes('action')) return '#48D7E6';
  if (key.includes('environment') || key.includes('multiagent')) return '#58C88A';
  return ['#F07167', '#F1B958', '#9D8CFF', '#48D7E6', '#58C88A'][idx % 5];
}
function renderSystemHealthHero(overview, catalog, failureRate, rootCoverage) {
  const status = failureRate >= 80 ? 'Critical' : (failureRate >= 40 ? 'Degraded' : 'Stable');
  const primary = (overview.top_error_types || [])[0]?.mode_id || (catalog || [])[0]?.top_error_type || 'No dominant failure';
  const criticalRuns = (catalog || []).filter(item => severityLabel(item) === 'Critical').length;
  return '<div class="health-hero">' +
    '<div><div class="health-topline"><div><div class="kicker">System Health</div><div class="health-status">' + escapeHtml(status) + '</div></div><span class="chip bad">' + escapeHtml(failureRate) + '% failed</span></div>' +
    '<div class="health-ring" style="--health-rate:' + escapeHtml(Math.max(0, Math.min(100, failureRate))) + '%"><div class="health-ring-center"><strong>' + escapeHtml(failureRate) + '%</strong><span>Failed runs</span></div></div></div>' +
    '<div class="health-metrics">' +
    healthMini('Runs Failed', (overview.error_trace_count || 0) + ' / ' + (overview.trace_count || 0)) +
    healthMini('RCA Coverage', rootCoverage + '%') +
    healthMini('Average Steps', overview.event_avg || 0) +
    healthMini('Critical Runs', criticalRuns) +
    '</div><div class="case-detail-summary"><strong>Primary Failure Mode</strong><br>' + escapeHtml(primary) + '</div></div>';
}
function healthMini(label, value) {
  return '<div class="health-mini"><div class="health-mini-label">' + escapeHtml(label) + '</div><div class="health-mini-value">' + escapeHtml(value) + '</div></div>';
}
function renderStackedFailureTimeline(catalog) {
  const points = (catalog || []).slice(0, 18);
  if (!points.length) return '<div class="empty">No timeline data.</div>';
  const families = Array.from(new Set(points.map(item => item.top_family || 'clean').filter(Boolean))).slice(0, 5);
  const width = 760, height = 280, pad = 28;
  const totals = points.map(item => Math.max(0, Number(item.finding_count || item.error_count || 0)));
  const max = Math.max(...totals, 1);
  let baselines = new Array(points.length).fill(0);
  const areas = families.map((family, familyIdx) => {
    const top = [];
    const bottom = [];
    points.forEach((item, idx) => {
      const value = (item.top_family || 'clean') === family ? Number(item.finding_count || item.error_count || 0) : 0;
      const x = pad + (idx / Math.max(1, points.length - 1)) * (width - pad * 2);
      const y0 = height - pad - (baselines[idx] / max) * (height - pad * 2);
      baselines[idx] += value;
      const y1 = height - pad - (baselines[idx] / max) * (height - pad * 2);
      top.push([x, y1]);
      bottom.unshift([x, y0]);
    });
    const path = top.concat(bottom).map((p, idx) => (idx ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ') + ' Z';
    return '<path class="stack-area" d="' + path + '" fill="' + familyColor(family, familyIdx) + '" data-breakdown-filter="' + escapeHtml(family) + '" data-tooltip="' + escapeHtml('<strong>' + escapeHtml(family) + '</strong><span>click to filter this failure family</span>') + '"></path>';
  }).join('');
  const markers = points.map((item, idx) => {
    const value = totals[idx];
    if (!value && idx !== 0) return '';
    const x = pad + (idx / Math.max(1, points.length - 1)) * (width - pad * 2);
    const y = height - pad - (value / max) * (height - pad * 2);
    const isRoot = item.root_cause_found || item.root_cause_step_index;
    const tip = '<strong>' + escapeHtml(readableTaskName(item)) + '</strong><span>' + escapeHtml(shortModelLabel(item.model || item.framework || 'model')) + ' · ' + escapeHtml(shortDatasetLabel(item.task_type || item.dataset_type || item.framework || 'env')) + ' · ' + escapeHtml(value) + ' findings</span>';
    return '<circle class="stack-marker" cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1) + '" r="' + (isRoot ? 5 : 3.5) + '" fill="' + (isRoot ? '#F59F5B' : '#EDF5F7') + '" data-trace-id="' + escapeHtml(item.trace_id || '') + '" data-tooltip="' + escapeHtml(tip) + '"></circle>';
  }).join('');
  const grid = [0, .25, .5, .75, 1].map(ratio => {
    const y = height - pad - ratio * (height - pad * 2);
    return '<line x1="' + pad + '" y1="' + y.toFixed(1) + '" x2="' + (width - pad) + '" y2="' + y.toFixed(1) + '" stroke="rgba(255,255,255,.07)"></line>';
  }).join('');
  const legend = '<div class="chart-legend">' + families.map((family, idx) => '<button type="button" class="legend-pill" style="--legend-color:' + familyColor(family, idx) + '" data-breakdown-filter="' + escapeHtml(family) + '">' + escapeHtml(family) + '</button>').join('') + '</div>';
  return '<svg class="failure-stack-chart" viewBox="0 0 ' + width + ' ' + height + '" role="img">' + grid + areas + '<path class="stack-line" d="M' + pad + ' ' + (height - pad) + ' L' + (width - pad) + ' ' + (height - pad) + '"></path>' + markers + '</svg>' + legend;
}
function renderFailureTreemap(modes, families) {
  const items = (modes && modes.length ? modes : families || []).slice(0, 8);
  if (!items.length) return '<div class="empty">No failure modes yet.</div>';
  const total = items.reduce((sum, item) => sum + Number(item.count || 0), 0) || 1;
  return '<div class="treemap">' + items.map((item, idx) => {
    const name = item.mode_id || item.family || '-';
    const value = Number(item.count || 0);
    const pct = Math.round((value / total) * 100);
    const klass = idx === 0 ? 'major' : (idx < 3 ? 'medium' : '');
    const family = item.family || String(name).split('.')[0];
    return '<div class="treemap-node ' + klass + '" style="--node-color:' + familyColor(family, idx) + '" data-breakdown-filter="' + escapeHtml(name) + '" data-tooltip="' + escapeHtml(chartTooltipHtml(name, value, total)) + '">' +
      '<div class="treemap-mode">' + escapeHtml(name) + '</div><div class="treemap-meta">' + escapeHtml(value) + ' findings · ' + escapeHtml(pct) + '%</div></div>';
  }).join('') + '</div>';
}
function renderRootCauseIntelligence(catalog, modes) {
  const bars = renderRootCauseBars(modes || []);
  const matrix = renderSeverityMatrix(catalog || []);
  return '<div class="root-intel"><div><div class="diagnosis-label">Root Cause Distribution</div>' + bars + '</div><div><div class="diagnosis-label">Severity Matrix</div>' + matrix + '</div></div>';
}
function renderRootCauseBars(items) {
  if (!items.length) return '<div class="empty" style="padding:12px;">No root cause data.</div>';
  const total = items.reduce((sum, item) => sum + Number(item.count || 0), 0) || 1;
  const max = Math.max(...items.map(item => Number(item.count || 0)), 1);
  return '<div class="root-dist">' + items.slice(0, 5).map(item => {
    const value = Number(item.count || 0);
    const name = item.mode_id || item.family || '-';
    const pct = Math.round((value / total) * 100);
    return '<div class="root-dist-row" data-breakdown-filter="' + escapeHtml(name) + '" data-tooltip="' + escapeHtml(chartTooltipHtml(name, value, total)) + '"><div class="root-dist-name">' + escapeHtml(name) + '</div><div class="mono">' + escapeHtml(value) + '</div><div class="mono">' + escapeHtml(pct) + '%</div><div class="root-dist-track"><div class="root-dist-fill" style="width:' + Math.max(5, value / max * 100) + '%"></div></div></div>';
  }).join('') + '</div>';
}
function renderSeverityMatrix(catalog) {
  const families = Array.from(new Set(catalog.filter(item => Number(item.finding_count || 0)).map(item => item.top_family || 'unknown'))).slice(0, 4);
  const levels = ['Critical', 'High', 'Medium', 'Low'];
  let html = '<div class="severity-matrix"><div class="severity-cell head">Family</div>' + levels.map(level => '<div class="severity-cell head">' + level + '</div>').join('');
  families.forEach(family => {
    html += '<div class="severity-cell head">' + escapeHtml(family) + '</div>';
    levels.forEach(level => {
      const count = catalog.filter(item => (item.top_family || 'unknown') === family && severityLabel(item) === level).length;
      html += '<div class="severity-cell ' + (count >= 2 ? 'hot' : (count ? 'warm' : '')) + '" data-breakdown-filter="' + escapeHtml(family) + '" data-tooltip="' + escapeHtml('<strong>' + escapeHtml(family + ' · ' + level) + '</strong><span>' + escapeHtml(count) + ' affected runs</span>') + '">' + (count ? escapeHtml(count) : '') + '</div>';
    });
  });
  html += '</div>';
  return html;
}
function renderPerformanceMatrix(catalog) {
  const models = Array.from(new Set((catalog || []).map(item => matrixModelLabel(item)))).slice(0, 6);
  const envs = Array.from(new Set((catalog || []).map(item => matrixEnvironmentLabel(item)))).slice(0, 5);
  if (!models.length || !envs.length) return '<div class="empty">No performance matrix data.</div>';
  let html = '<div class="performance-matrix"><div class="perf-table"><div class="perf-row" style="--env-count:' + escapeHtml(envs.length) + '"><div></div>' + envs.map(env => '<div class="perf-axis">' + escapeHtml(env) + '</div>').join('') + '</div>';
  models.forEach(model => {
    html += '<div class="perf-row" style="--env-count:' + escapeHtml(envs.length) + '"><div class="perf-axis">' + escapeHtml(model) + '</div>';
    envs.forEach(env => {
      const group = (catalog || []).filter(item => matrixModelLabel(item) === model && matrixEnvironmentLabel(item) === env);
      if (!group.length) {
        html += '<div class="perf-cell empty"><div class="perf-main">-</div><div class="perf-sub">No runs</div></div>';
        return;
      }
      const failed = group.filter(item => Number(item.finding_count || 0)).length;
      const rate = Math.round((failed / group.length) * 100);
      const avgFindings = (group.reduce((sum, item) => sum + Number(item.finding_count || 0), 0) / group.length).toFixed(1);
      const avgSteps = (group.reduce((sum, item) => sum + Number(item.event_count || 0), 0) / group.length).toFixed(1);
      const color = rate >= 80 ? '#F07167' : (rate >= 40 ? '#F1B958' : '#48D7E6');
      const traceId = (group.find(item => Number(item.finding_count || 0)) || group[0]).trace_id || '';
      const tip = '<strong>' + escapeHtml(model + ' × ' + env) + '</strong><span>' + escapeHtml(rate) + '% fail · ' + escapeHtml(avgFindings) + ' findings/run · ' + escapeHtml(avgSteps) + ' steps/run</span>';
      html += '<div class="perf-cell" style="--risk-color:' + color + ';--risk-alpha:' + (0.08 + rate / 180) + '" data-trace-id="' + escapeHtml(traceId) + '" data-tooltip="' + escapeHtml(tip) + '"><span class="perf-dot"></span><div class="perf-main">' + escapeHtml(rate) + '% fail</div><div class="perf-sub">' + escapeHtml(avgFindings) + ' findings/run<br>' + escapeHtml(avgSteps) + ' avg steps</div></div>';
    });
    html += '</div>';
  });
  return html + '</div></div>';
}
function renderCriticalRuns(catalog) {
  const runs = (catalog || [])
    .filter(item => Number(item.finding_count || 0))
    .sort((a, b) => criticalScore(b) - criticalScore(a))
    .slice(0, 8);
  if (!runs.length) return '<div class="empty">No critical runs detected.</div>';
  return '<div class="critical-runs">' + runs.map(item => {
    const traceId = item.trace_id || '';
    const tip = '<strong>' + escapeHtml(readableTaskName(item)) + '</strong><span>' + escapeHtml(severityLabel(item)) + ' · ' + escapeHtml(item.finding_count || 0) + ' findings · root step ' + escapeHtml(item.root_cause_step_index ?? '-') + '</span>';
    return '<a class="critical-run" href="/trace/' + encodeURIComponent(traceId) + '" data-trace-id="' + escapeHtml(traceId) + '" data-tooltip="' + escapeHtml(tip) + '">' +
      '<div class="critical-title">' + escapeHtml(readableTaskName(item)) + '</div>' +
      '<div class="critical-meta">' + escapeHtml(shortDatasetLabel(item.task_type || item.dataset_type || item.framework || 'env')) + ' · ' + escapeHtml(shortModelLabel(item.model || item.framework || 'model')) + '</div>' +
      '<div class="critical-meta">' + escapeHtml(item.finding_count || 0) + ' findings · ' + escapeHtml(item.event_count || 0) + ' steps · ' + escapeHtml(severityLabel(item)) + '</div>' +
      '<div class="critical-mini">' + renderMiniTimeline(item.mini_timeline || []) + '</div></a>';
  }).join('') + '</div>';
}
function criticalScore(item) {
  const severity = {'Critical': 4, 'High': 3, 'Medium': 2, 'Low': 1, 'Clean': 0}[severityLabel(item)] || 0;
  return severity * 1000 + Number(item.finding_count || 0) * 10 + (item.root_cause_found ? 5 : 0);
}
function renderRunsTable(catalog) {
  if (!catalog || !catalog.length) return '<div class="empty">No runs found.</div>';
  const rows = catalog
    .slice()
    .sort((a, b) => criticalScore(b) - criticalScore(a))
    .map(item => {
      const failed = Number(item.finding_count || 0) > 0;
      const env = shortDatasetLabel(item.task_type || item.dataset_type || item.framework || '-');
      const primaryMode = item.top_error_type || item.top_family || '-';
      return '<tr data-trace-id="' + escapeHtml(item.trace_id || '') + '" data-status="' + (failed ? 'failed' : 'passed') + '" data-search="' + escapeHtml(((item.trace_id || '') + ' ' + readableTaskName(item) + ' ' + env + ' ' + (item.model || '') + ' ' + primaryMode).toLowerCase()) + '" data-tooltip="' + escapeHtml(caseTooltipHtml(item)) + '">' +
        '<td><span class="status-dot ' + (failed ? 'failed' : 'passed') + '">' + escapeHtml(statusLabel(item)) + '</span></td>' +
        '<td><strong>' + escapeHtml(readableTaskName(item)) + '</strong><div class="event-type mono">' + escapeHtml(truncate(item.trace_id || '', 36)) + '</div></td>' +
        '<td>' + escapeHtml(env) + '</td>' +
        '<td>' + escapeHtml(shortModelLabel(item.model || item.framework || '-')) + '</td>' +
        '<td class="mono">' + escapeHtml(item.event_count || 0) + '</td>' +
        '<td class="mono">' + escapeHtml(item.finding_count || item.error_count || 0) + '</td>' +
        '<td>' + (item.root_cause_found ? '<span class="chip warn">Found</span>' : '<span class="chip">-</span>') + '</td>' +
        '<td><button class="button table-mode-filter" type="button" data-breakdown-filter="' + escapeHtml(primaryMode) + '">' + escapeHtml(truncate(primaryMode, 28)) + '</button></td>' +
        '<td><span class="chip ' + (severityLabel(item) === 'Critical' ? 'bad' : (severityLabel(item) === 'High' ? 'warn' : 'cyan')) + '">' + escapeHtml(severityLabel(item)) + '</span></td>' +
        '<td class="mono">local</td>' +
      '</tr>';
    }).join('');
  return '<table class="runs-table"><thead><tr><th>Status</th><th>Run</th><th>Environment</th><th>Model</th><th>Steps</th><th>Findings</th><th>Root Cause</th><th>Primary Mode</th><th>Severity</th><th>Updated</th></tr></thead><tbody>' + rows + '</tbody></table>';
}
function renderIssueSummary(items) {
  if (!items || !items.length) return '<div class="empty">No detector issues yet.</div>';
  const max = Math.max(...items.map(item => Number(item.count || 0)), 1);
  return '<div class="issue-list">' + items.map(item => {
    const value = Number(item.count || 0);
    return '<div class="issue-row"><div class="issue-name">' + escapeHtml(item.mode_id || '-') + '</div><div class="mono">' + escapeHtml(value) + '</div><div class="issue-bar"><div class="issue-fill" style="width:' + Math.max(6, value / max * 100) + '%"></div></div></div>';
  }).join('') + '</div>';
}
function renderFailureModeBreakdown(families, modes) {
  const familyHtml = renderBreakdownRows(families || [], 'family');
  const modeHtml = renderBreakdownRows(modes || [], 'mode_id');
  return '<div class="breakdown-list">' +
    '<div class="diagnosis-label">Families</div>' + familyHtml +
    '<div class="diagnosis-label" style="margin-top:6px;">Top Modes</div>' + modeHtml +
    '</div>';
}
function renderBreakdownRows(items, key) {
  if (!items || !items.length) return '<div class="empty" style="padding:12px;">No failures.</div>';
  const max = Math.max(...items.map(item => Number(item.count || 0)), 1);
  return items.slice(0, 6).map(item => {
    const value = Number(item.count || 0);
    const name = item[key] || '-';
    return '<div class="breakdown-row" data-breakdown-filter="' + escapeHtml(name) + '" data-tooltip="' + escapeHtml(chartTooltipHtml(name, value, max)) + '"><div class="issue-name">' + escapeHtml(name) + '</div><div class="mono">' + escapeHtml(value) + '</div><div class="breakdown-bar"><div class="breakdown-fill" style="width:' + Math.max(5, value / max * 100) + '%"></div></div></div>';
  }).join('');
}
function renderModelEnvironmentHeatmap(catalog) {
  const models = Array.from(new Set((catalog || []).map(item => shortModelLabel(item.model || item.framework || 'model')))).slice(0, 6);
  const envs = Array.from(new Set((catalog || []).map(item => shortDatasetLabel(item.task_type || item.dataset_type || item.framework || 'env')))).slice(0, 5);
  if (!models.length || !envs.length) return '<div class="empty">No heatmap data.</div>';
  const counts = new Map();
  (catalog || []).forEach(item => {
    const model = shortModelLabel(item.model || item.framework || 'model');
    const env = shortDatasetLabel(item.task_type || item.dataset_type || item.framework || 'env');
    const key = model + '::' + env;
    const prev = counts.get(key) || {runs: 0, failed: 0, firstTraceId: ''};
    prev.runs += 1;
    if (Number(item.finding_count || 0)) {
      prev.failed += 1;
      if (!prev.firstTraceId) prev.firstTraceId = item.trace_id || '';
    }
    counts.set(key, prev);
  });
  let html = '<div class="heatmap-grid"><div class="heatmap-table" style="--env-count:' + escapeHtml(envs.length) + '">';
  html += '<div class="heatmap-header"><div></div>' + envs.map(env => '<div class="heatmap-axis">' + escapeHtml(env) + '</div>').join('') + '</div>';
  models.forEach(model => {
    html += '<div class="heatmap-row-v2"><div class="heatmap-axis">' + escapeHtml(model) + '</div>';
    envs.forEach(env => {
      const cell = counts.get(model + '::' + env) || {runs: 0, failed: 0};
      const rate = cell.runs ? cell.failed / cell.runs : 0;
      const klass = rate >= .5 ? 'hot' : (rate > 0 ? 'warm' : '');
      const tip = '<strong>' + escapeHtml(model + ' × ' + env) + '</strong><span>' + escapeHtml(cell.failed + '/' + cell.runs) + ' failed runs</span>';
      html += '<div class="heat-cell ' + klass + '" style="--heat-width:' + Math.round(rate * 100) + '%" data-trace-id="' + escapeHtml(cell.firstTraceId || '') + '" data-tooltip="' + escapeHtml(tip) + '"><span>' + escapeHtml(cell.failed || '') + '</span></div>';
    });
    html += '</div>';
  });
  html += '</div></div>';
  return html;
}
function renderRecentFailedSequences(catalog) {
  const failed = (catalog || [])
    .filter(item => Number(item.finding_count || 0))
    .sort((a, b) => Number(b.finding_count || 0) - Number(a.finding_count || 0))
    .slice(0, 5);
  if (!failed.length) return '<div class="empty">No failed sequences.</div>';
  return '<div class="recent-sequences">' + failed.map(item =>
    '<a class="sequence-card" href="/trace/' + encodeURIComponent(item.trace_id || '') + '" data-trace-id="' + escapeHtml(item.trace_id || '') + '" data-tooltip="' + escapeHtml(caseTooltipHtml(item)) + '">' +
    '<div><div class="sequence-title">' + escapeHtml(readableTaskName(item)) + '</div>' +
    '<div class="sequence-meta">' + escapeHtml(shortDatasetLabel(item.task_type || item.dataset_type || item.framework || 'env')) + ' · ' + escapeHtml(shortModelLabel(item.model || item.framework || 'model')) + ' · ' + escapeHtml(item.finding_count || 0) + ' findings</div></div>' +
    '<div class="sequence-mini">' + renderMiniTimeline(item.mini_timeline || []) + '</div>' +
    '</a>'
  ).join('') + '</div>';
}
function renderMiniTimeline(items) {
  if (!items.length) return '<span class="sequence-segment"></span>';
  return items.slice(0, 36).map(item =>
    '<span class="sequence-segment ' + escapeHtml(item.state || '') + '" title="step ' + escapeHtml(item.step_index ?? '-') + '"></span>'
  ).join('');
}
function renderFailureTrend(catalog) {
  const points = (catalog || []).slice(0, 18);
  if (!points.length) return '<div class="empty">No trend data.</div>';
  const width = 640, height = 148, pad = 20;
  const values = points.map(item => Number(item.error_count || item.finding_count || 0));
  const max = Math.max(...values, 1);
  const coords = values.map((value, idx) => {
    const x = pad + (idx / Math.max(1, values.length - 1)) * (width - pad * 2);
    const y = height - pad - (value / max) * (height - pad * 2);
    return {x, y, value, item: points[idx]};
  });
  const path = coords.map((point, idx) => (idx ? 'L' : 'M') + point.x.toFixed(1) + ' ' + point.y.toFixed(1)).join(' ');
  const area = path + ' L ' + coords[coords.length - 1].x.toFixed(1) + ' ' + (height - pad) + ' L ' + coords[0].x.toFixed(1) + ' ' + (height - pad) + ' Z';
  const dots = coords.map((point, idx) => {
    const failed = Number(point.item.finding_count || 0) > 0;
    const tip = '<strong>' + escapeHtml(readableTaskName(point.item)) + '</strong><span>run #' + escapeHtml(idx + 1) + ' · ' + escapeHtml(point.value) + ' errors · ' + escapeHtml(point.item.event_count || 0) + ' steps</span>';
    return '<circle class="trend-point ' + (failed ? 'bad' : 'good') + '" cx="' + point.x.toFixed(1) + '" cy="' + point.y.toFixed(1) + '" r="4" fill="' + (failed ? 'var(--rose)' : 'var(--green)') + '" data-trace-id="' + escapeHtml(point.item.trace_id || '') + '" data-tooltip="' + escapeHtml(tip) + '"></circle>';
  }).join('');
  return '<svg class="trend-line" viewBox="0 0 ' + width + ' ' + height + '" role="img">' +
    '<defs><linearGradient id="failureTrendFill" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="var(--rose)" stop-opacity=".34"></stop><stop offset="100%" stop-color="var(--rose)" stop-opacity="0"></stop></linearGradient></defs>' +
    '<line x1="' + pad + '" y1="' + (height - pad) + '" x2="' + (width - pad) + '" y2="' + (height - pad) + '" stroke="var(--line2)"></line>' +
    '<path class="trend-area" d="' + area + '"></path><path class="trend-path" d="' + path + '"></path>' + dots + '</svg>';
}
function bindOverviewInteractions(catalog) {
  const rows = Array.from(document.querySelectorAll('.runs-table tbody tr'));
  const search = document.getElementById('runs-search');
  const status = document.getElementById('runs-status');
  const overviewSearch = document.getElementById('overview-search');
  const overviewStatus = document.getElementById('overview-status');
  const overviewEnv = document.getElementById('overview-env');
  const overviewModel = document.getElementById('overview-model');
  const envValues = (catalog || []).map(item => shortDatasetLabel(item.task_type || item.dataset_type || item.framework || 'env'));
  const modelValues = (catalog || []).map(item => shortModelLabel(item.model || item.framework || 'model'));
  fillSelect(overviewEnv, envValues, 'Environment: All');
  fillSelect(overviewModel, modelValues, 'Model: All');
  const apply = () => {
    const q = (overviewSearch?.value || search?.value || '').toLowerCase().trim();
    const s = overviewStatus?.value || status?.value || 'all';
    const env = overviewEnv?.value || 'all';
    const model = overviewModel?.value || 'all';
    rows.forEach(row => {
      const cells = row.querySelectorAll('td');
      const rowEnv = cells[2]?.textContent || '';
      const rowModel = cells[3]?.textContent || '';
      const okSearch = !q || (row.dataset.search || '').includes(q);
      const okStatus = s === 'all' || row.dataset.status === s;
      const okEnv = env === 'all' || rowEnv === env;
      const okModel = model === 'all' || rowModel === model;
      row.style.display = okSearch && okStatus && okEnv && okModel ? '' : 'none';
    });
  };
  if (overviewSearch) overviewSearch.oninput = () => { if (search) search.value = overviewSearch.value; apply(); };
  if (overviewStatus) overviewStatus.onchange = () => { if (status) status.value = overviewStatus.value; apply(); };
  if (search) search.oninput = () => { if (overviewSearch) overviewSearch.value = search.value; apply(); };
  if (status) status.onchange = () => { if (overviewStatus) overviewStatus.value = status.value; apply(); };
  [overviewEnv, overviewModel].forEach(control => { if (control) control.onchange = apply; });
  document.querySelectorAll('[data-kpi-filter]').forEach(card => {
    card.onclick = () => {
      const filter = card.dataset.kpiFilter || 'all';
      if (overviewStatus) overviewStatus.value = filter === 'failed' ? 'failed' : 'all';
      if (status) status.value = filter === 'failed' ? 'failed' : 'all';
      if (filter === 'root' && overviewSearch) overviewSearch.value = 'root';
      apply();
      document.querySelector('.runs-table-wrap')?.scrollIntoView({behavior:'smooth', block:'nearest'});
    };
  });
  document.querySelectorAll('[data-breakdown-filter]').forEach(row => {
    row.onclick = event => {
      event.preventDefault();
      event.stopPropagation();
      const value = row.dataset.breakdownFilter || '';
      if (overviewSearch) overviewSearch.value = value;
      if (search) search.value = value;
      apply();
    };
  });
  document.querySelectorAll('[data-trace-id]').forEach(el => {
    el.onclick = async event => {
      const traceId = el.dataset.traceId || '';
      if (!traceId) return;
      event.preventDefault();
      const loaded = await selectTrace(traceId, el);
      if (loaded) history.pushState({view: 'trace', traceId: traceId}, '', '/trace/' + encodeURIComponent(traceId));
    };
  });
  apply();
}
function bindRunsTable() {
  const search = document.getElementById('runs-search');
  const status = document.getElementById('runs-status');
  const rows = Array.from(document.querySelectorAll('.runs-table tbody tr'));
  const apply = () => {
    const q = (search?.value || '').toLowerCase().trim();
    const s = status?.value || 'all';
    rows.forEach(row => {
      const okSearch = !q || (row.dataset.search || '').includes(q);
      const okStatus = s === 'all' || row.dataset.status === s;
      row.style.display = okSearch && okStatus ? '' : 'none';
    });
  };
  if (search) search.oninput = apply;
  if (status) status.onchange = apply;
  rows.forEach(row => {
    row.onclick = async () => {
      const traceId = row.dataset.traceId || '';
      if (!traceId) return;
      let run = document.querySelector('.run[data-tid="' + cssEscape(traceId) + '"]');
      if (!run) {
        renderTraceList((BOOTSTRAP && BOOTSTRAP.traces) || TRACE_CATALOG.map(item => item.trace_id), traceId);
        run = document.querySelector('.run[data-tid="' + cssEscape(traceId) + '"]');
      }
      const loaded = await selectTrace(traceId, run || row);
      if (loaded) {
        history.pushState({view: 'trace', traceId: traceId}, '', '/trace/' + encodeURIComponent(traceId));
      }
    };
  });
}
function panelTitle(title, subtitle) {
  return '<div><div class="panel-title">' + escapeHtml(title) + '</div><div class="chart-subtitle">' + escapeHtml(subtitle) + '</div></div>';
}
function loadingState(label) {
  return '<div class="loading-card" role="status" aria-live="polite">' +
    '<div class="kicker">' + escapeHtml(label || 'Loading') + '</div>' +
    '<div class="skeleton-line"></div><div class="skeleton-line mid"></div><div class="skeleton-line short"></div>' +
    '</div>';
}
function kpi(label, value, klass) {
  return '<div class="kpi"><div class="kpi-label">' + escapeHtml(label) + '</div><div class="kpi-value ' + klass + '">' + escapeHtml(value) + '</div></div>';
}
function renderBatchMetrics(overview) {
  return '<div class="overview-card"><div class="panel-head"><div class="panel-title">Batch Metrics</div><span class="chip cyan">auto aggregated</span></div><div class="panel-body">' +
    '<div class="kpi-strip">' +
    kpi('Batch traces', overview.trace_count ?? 0, 'cyan') +
    kpi('First error avg', overview.first_error_step_avg ?? '-', 'warn') +
    kpi('Events', overview.event_total ?? 0, 'good') +
    kpi('Event avg', overview.event_avg ?? 0, 'good') +
    kpi('Finding avg', overview.finding_avg ?? 0, 'cyan') +
    kpi('Error event avg', overview.error_event_avg ?? 0, 'warn') +
    '</div>' +
    '</div></div>';
}
function renderAuditPanel(findings, rootId) {
  const primary = (findings || []).find(f => f.event_id === rootId) || (findings || [])[0] || null;
  if (!primary) {
    return '<div class="panel audit-card"><div class="panel-head"><div class="panel-title">Why Flagged</div><span class="chip good">clean</span></div><div class="panel-body"><div class="audit-note">No local failure signal was detected for this trace.</div></div></div>';
  }
  const meta = primary.metadata || {};
  const mode = primary.failure_mode || {};
  const why = meta.why_reported || meta.trigger_reason || primary.suggestion || 'This finding matched the heuristic failure signal for the selected root-cause path.';
  const evidence = meta.confidence_basis || (primary.evidence || []).join('; ') || 'Evidence is derived from the event payload and analyzer rule match.';
  let html = '<div class="panel audit-card"><div class="panel-head"><div class="panel-title">Why Flagged</div><span class="chip bad">audit</span></div><div class="panel-body">';
  html += '<div class="audit-note">' + escapeHtml(why) + '</div>';
  html += '<div class="lane-meta"><span class="chip bad">' + escapeHtml(mode.family || 'failure') + '</span><span class="chip">' + escapeHtml(mode.mode_id || 'unclassified') + '</span></div>';
  html += '<div class="event-note" style="margin-top:10px;"><div class="event-note-title">Evidence</div><div class="event-note-copy">' + escapeHtml(evidence) + '</div></div>';
  html += '</div></div>';
  return html;
}
function healthCard(label, value, klass) {
  return '<div class="health-card"><div class="health-label">' + escapeHtml(label) + '</div><div class="health-value ' + klass + '">' + escapeHtml(value) + '</div></div>';
}
function mini(label, value) {
  return '<div class="mini"><div class="mini-label">' + escapeHtml(label) + '</div><div class="mini-value">' + escapeHtml(value) + '</div></div>';
}
function miniCompact(label, value) {
  return '<div class="mini compact"><div class="mini-label">' + escapeHtml(label) + '</div><div class="mini-value">' + escapeHtml(value) + '</div></div>';
}
function flow(n, text) {
  return '<div class="flow-item"><div class="flow-dot">' + n + '</div><div>' + escapeHtml(text) + '</div></div>';
}
function findingForEvent(findings, eventId) {
  return (findings || []).find(f => f.event_id === eventId) || null;
}
function eventStateClass(ev, finding) {
  if (finding || eventProblem(ev)) return 'error';
  return 'clean';
}
function eventAccentLabel(ev, finding, isRoot) {
  if (isRoot) return 'root';
  if (finding?.failure_mode?.mode_id) return finding.failure_mode.mode_id;
  if (eventProblem(ev)) return 'signal';
  return 'clean';
}
function timelineStatus(ev, finding) {
  return (finding || eventProblem(ev)) ? 'error' : 'ok';
}
function timelineTooltip(ev, finding, isRoot, ordinal) {
  const status = timelineStatus(ev, finding);
  const mode = finding?.failure_mode?.mode_id || finding?.failure_mode?.family || (eventProblem(ev) ? 'signal' : 'clean step');
  const rootText = isRoot ? ' · root cause' : '';
  return 'event ' + (ordinal ?? '-') + ' · recorded step ' + (ev.step_index ?? '-') + ' · ' + status + rootText + ' · ' + mode;
}
function nativeTrace(ev) {
  const meta = ev.metadata || {};
  const native = meta.native_trace || {};
  const fallback = fmt(ev.output || ev.input || ev.error || 'Recorded framework event.');
  const tags = Array.isArray(native.tags) ? native.tags : [ev.module || 'module', ev.event_type || 'event'];
  return {
    span: native.span_id || ev.event_id || '-',
    title: native.title || ((ev.agent_name || 'agent') + ' / ' + (ev.event_type || 'event')),
    body: native.message || fallback,
    tags: tags,
    state: native.state || native.tool || ''
  };
}
function errorTrace(ev, finding) {
  const meta = ev.metadata || {};
  const findingMeta = finding?.metadata || {};
  const overlay = meta.error_trace || {};
  const mode = overlay.failure_mode || finding?.failure_mode?.mode_id || (eventProblem(ev) ? 'unclassified.signal' : 'context');
  const title = overlay.title || finding?.failure_mode?.name || (eventProblem(ev) ? 'Failure signal detected' : 'Context event');
  const body = overlay.human_readout || finding?.suggestion || (eventProblem(ev)
    ? 'AgentDebugX keeps this event in the failure trace because it contains an error, lost-context signal, or invalid state transition.'
    : 'No local failure signal; shown to preserve the causal path for the reviewer.');
  const severity = overlay.severity || (finding ? 'high' : (eventProblem(ev) ? 'medium' : 'context'));
  const repair = overlay.repair || finding?.suggestion || '';
  return {
    mode,
    title,
    body,
    severity,
    repair,
    rulePack: findingMeta.rule_pack || '',
    ruleId: findingMeta.rule_id || '',
    confidenceBasis: findingMeta.confidence_basis || '',
    findingSourceLabel: findingMeta.finding_source_label || '',
    triggerReason: findingMeta.trigger_reason || '',
    whyReported: findingMeta.why_reported || ''
  };
}
function severityClass(severity) {
  if (severity === 'critical' || severity === 'high') return 'bad';
  if (severity === 'medium') return 'warn';
  if (severity === 'context') return '';
  return 'cyan';
}
function renderEvent(ev, isRoot, finding) {
  const debug = errorTrace(ev, finding);
  const inputValue = fmt(ev.input);
  const outputValue = fmt(ev.output);
  const errorValue = fmt(ev.error);
  const primaryLabel = ev.error ? 'Error' : (outputValue ? 'Output' : (inputValue ? 'Input' : 'Event payload'));
  const primaryValue = ev.error || outputValue || inputValue || 'No payload recorded.';
  let html = '<div class="event focused ' + (isRoot ? 'root' : '') + '">';
  html += '<div class="step-index">' + escapeHtml(ev.step_index ?? '-') + '</div>';
  html += '<div><div class="event-title"><div class="event-identity"><span class="event-agent">' + escapeHtml(ev.agent_name || 'agent') + '</span>';
  html += '<span class="event-type">' + escapeHtml(ev.event_type || '') + ' / ' + escapeHtml(ev.module || 'module') + '</span><span class="event-id-small">' + escapeHtml(ev.event_id || '-') + '</span></div>';
  html += isRoot ? '<span class="chip warn">root candidate</span>' : (eventProblem(ev) ? '<span class="chip bad">signal</span>' : '<span class="chip good">ok</span>');
  html += '</div>';
  html += renderEventReadout(ev, finding, primaryLabel, primaryValue, inputValue, outputValue, errorValue, debug);
  html += '</div></div>';
  return html;
}
function renderInlineEventDetail(ev, finding) {
  const debug = errorTrace(ev, finding);
  const inputValue = fmt(ev.input);
  const outputValue = fmt(ev.output);
  const errorValue = fmt(ev.error);
  const primaryLabel = ev.error ? 'Error' : (outputValue ? 'Output' : (inputValue ? 'Input' : 'Event payload'));
  const primaryValue = ev.error || outputValue || inputValue || 'No payload recorded.';
  let html = '<div class="event-inline-detail">';
  html += renderEventReadout(ev, finding, primaryLabel, primaryValue, inputValue, outputValue, errorValue, debug);
  html += '</div>';
  return html;
}
function renderEditorStageEvent(ev, isRoot, finding, ordinal) {
  const debug = errorTrace(ev, finding);
  const inputValue = fmt(ev.input);
  const outputValue = fmt(ev.output);
  const errorValue = fmt(ev.error);
  const primaryLabel = ev.error ? 'Error' : (outputValue ? 'Output' : (inputValue ? 'Input' : 'Event payload'));
  const primaryValue = ev.error || outputValue || inputValue || 'No payload recorded.';
  let html = '<div class="editor-event-hero ' + (isRoot ? 'root' : '') + '">';
  html += '<div class="editor-event-index">' + escapeHtml(ordinal ?? ev.step_index ?? '-') + '</div>';
  html += '<div><div class="editor-event-name">' + escapeHtml(ev.agent_name || 'agent') + '</div>';
  html += '<div class="editor-event-sub">' + escapeHtml(ev.event_type || 'event') + ' / ' + escapeHtml(ev.module || 'module') + ' / ' + escapeHtml(ev.event_id || '-') + '</div></div>';
  html += '<div class="lane-meta" style="margin-top:0; justify-content:flex-end;">';
  html += isRoot ? '<span class="chip warn">root</span>' : '<span class="chip ' + (timelineStatus(ev, finding) === 'error' ? 'bad' : 'good') + '">' + escapeHtml(timelineStatus(ev, finding)) + '</span>';
  if (debug.rulePack) html += '<span class="chip cyan">' + escapeHtml(debug.rulePack) + '</span>';
  html += '</div></div>';
  html += renderEventReadout(ev, finding, primaryLabel, primaryValue, inputValue, outputValue, errorValue, debug);
  return html;
}
function parseClickCoordinates(code) {
  if (typeof code !== 'string') return null;
  let match = code.match(/(?:pyautogui\.)?click\(\(?\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\)?\)/i);
  if (!match && /click/i.test(code)) {
    match = code.match(/=\s*\(\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\)/);
  }
  if (match) return {x: Number(match[1]), y: Number(match[2])};
  const xMatch = code.match(/\bx\s*=\s*(\d+(?:\.\d+)?)/i);
  const yMatch = code.match(/\by\s*=\s*(\d+(?:\.\d+)?)/i);
  return xMatch && yMatch ? {x: Number(xMatch[1]), y: Number(yMatch[1])} : null;
}
function visualStepSummary(report, ev, ordinal) {
  const summaries = report?.metadata?.per_step_summaries || report?.per_step_summaries || [];
  if (!Array.isArray(summaries)) return null;
  return summaries.find(item =>
    item?.event_id === ev?.event_id ||
    String(item?.step_num ?? item?.step_index ?? '') === String(ev?.step_index ?? '')
  ) || summaries[Math.max(0, Number(ordinal || 1) - 1)] || null;
}
function visualText(value) {
  if (value === null || value === undefined || value === '') return 'Not recorded.';
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
}
function renderVisualPane(label, paneKey, pane, click, showMarker) {
  const media = Array.isArray(pane?.media) ? pane.media : [];
  const source = pane?.source === 'previous_event'
    ? 'previous event result'
    : (pane?.source === 'explicit' ? 'explicit artifact' : 'selected event result');
  let html = '<section class="visual-pane" data-visual-pane="' + escapeHtml(paneKey) + '">';
  html += '<div class="visual-pane-head"><div class="visual-pane-title">' + escapeHtml(label) + '</div><div class="visual-pane-source">' + escapeHtml(source) + (pane?.event_id ? ' · ' + escapeHtml(pane.event_id) : '') + '</div></div>';
  if (media.length) {
    html += '<div class="visual-stage">';
    media.forEach((image, mediaIndex) => {
      html += '<div class="visual-image-wrap visual-gallery-image ' + (mediaIndex === 0 ? 'active' : '') + '" data-gallery-index="' + mediaIndex + '"><img class="visual-screenshot" loading="lazy" src="' + escapeHtml(image.url) + '" alt="' + escapeHtml(image.description || (label + ' screenshot ' + (mediaIndex + 1))) + '"';
      if (showMarker && click) html += ' data-click-x="' + escapeHtml(click.x) + '" data-click-y="' + escapeHtml(click.y) + '"';
      html += ' />';
      if (showMarker && click) html += '<span class="visual-click-marker" aria-label="Recorded click position"></span>';
      html += '</div>';
    });
    html += '</div><div class="visual-thumbnails" role="tablist" aria-label="' + escapeHtml(label + ' screenshots') + '">';
    media.forEach((image, mediaIndex) => {
      html += '<button type="button" class="visual-thumb ' + (mediaIndex === 0 ? 'active' : '') + '" data-gallery-select="' + mediaIndex + '" title="' + escapeHtml(image.description || image.media_type || 'image') + '">' + (mediaIndex + 1) + '</button>';
    });
    html += '</div><div class="visual-media-caption">' + escapeHtml(media.length + ' image' + (media.length === 1 ? '' : 's') + ' · ' + (media[0]?.media_type || 'image')) + '</div>';
  } else {
    html += '<div class="visual-stage"><div class="editor-empty"><div><strong>' + escapeHtml(label) + ' screenshot unavailable</strong><span>No safe image is recorded for this state. The selected event has not changed.</span></div></div></div>';
  }
  html += '</section>';
  return html;
}
function renderVisualInspector(ev, isRoot, finding, ordinal, events, report, capability) {
  const comparison = capability?.comparisons?.[ev.event_id] || {
    before: {event_id: null, source: 'previous_event', media: []},
    after: {event_id: ev.event_id, source: 'selected_event', media: capability?.events?.[ev.event_id] || []},
    auxiliary: []
  };
  const beforeMedia = comparison.before?.media || [];
  const afterMedia = comparison.after?.media || [];
  const index = Math.max(0, events.findIndex(item => item.event_id === ev.event_id));
  const click = parseClickCoordinates(visualText(ev.output));
  const metadata = ev.metadata || {};
  const layoutMode = visualLayoutMode(CURRENT_TRACE_ID || ev.trace_id || '');
  const summary = visualStepSummary(report, ev, ordinal);
  const evidence = (finding?.evidence || []).join('\\n') || summary?.evidence || 'No event-specific RCA evidence.';
  const correction = finding?.suggestion || summary?.correction || summary?.suggested_correction || 'No correction recorded.';
  let html = '<div class="visual-inspector" data-visual-capability="enabled">';
  html += '<div class="visual-nav"><div class="lane-meta" style="margin-top:0;"><span class="chip cyan">CUA Visual</span><span class="chip">step ' + escapeHtml(ordinal ?? ev.step_index ?? '-') + ' / ' + escapeHtml(events.length) + '</span>';
  html += '<span class="chip ' + (isRoot ? 'warn' : (finding || ev.error ? 'bad' : 'good')) + '">' + escapeHtml(isRoot ? 'root cause' : (finding || ev.error ? 'error' : 'clean')) + '</span></div>';
  html += '<div class="lane-meta" style="margin-top:0;"><div class="trace-visual-toggle visual-layout-toggle" aria-label="Screenshot layout"><button type="button" data-visual-layout="single" class="' + (layoutMode === 'single' ? 'active' : '') + '">Single</button><button type="button" data-visual-layout="compare" class="' + (layoutMode === 'compare' ? 'active' : '') + '">Compare</button></div><button class="timeline-tool" type="button" data-nav-event="-1" ' + (index <= 0 ? 'disabled' : '') + '>← Previous</button><button class="timeline-tool" type="button" data-nav-event="1" ' + (index >= events.length - 1 ? 'disabled' : '') + '>Next →</button></div></div>';
  if (layoutMode === 'single') {
    html += renderVisualPane('Selected screenshot', 'single', comparison.after, null, false);
  } else {
    html += '<div class="visual-comparison">';
    html += renderVisualPane('Before action', 'before', comparison.before, click, true);
    html += renderVisualPane('After action', 'after', comparison.after, null, false);
    html += '</div>';
  }
  if (comparison.auxiliary?.length) {
    html += renderVisualPane('Auxiliary images', 'auxiliary', {event_id: ev.event_id, source: 'selected_event', media: comparison.auxiliary}, null, false);
  }
  html += '<div class="visual-facts">';
  html += inspectorCard('Action type', metadata.action_type || ev.event_type || '-');
  html += inspectorCard('Reward', metadata.reward ?? '-');
  html += inspectorCard('Done', metadata.done === undefined ? '-' : String(metadata.done));
  html += inspectorCard('Media', beforeMedia.length + ' before / ' + afterMedia.length + ' after');
  html += '</div><div class="visual-copy-grid">';
  html += '<div class="visual-copy-card"><div class="inspector-label">Reasoning / Input</div><p>' + escapeHtml(visualText(ev.input)) + '</p></div>';
  html += '<div class="visual-copy-card"><div class="inspector-label">Action / Output</div><p>' + escapeHtml(visualText(ev.output)) + '</p></div>';
  html += '<div class="visual-copy-card"><div class="inspector-label">Step summary</div><p>' + escapeHtml(visualText(summary?.outcome_summary || summary?.reasoning_summary || summary?.summary)) + '</p></div>';
  html += '<div class="visual-copy-card"><div class="inspector-label">Error</div><p>' + escapeHtml(visualText(ev.error)) + '</p></div>';
  html += '<div class="visual-copy-card wide"><div class="inspector-label">RCA evidence</div><p>' + escapeHtml(visualText(evidence)) + '</p></div>';
  html += '<div class="visual-copy-card wide"><div class="inspector-label">Correction</div><p>' + escapeHtml(visualText(correction)) + '</p></div>';
  html += '</div></div>';
  return html;
}
function bindTraceViewToggle(traj, report) {
  document.querySelectorAll('[data-trace-view]').forEach(button => {
    button.onclick = () => {
      const mode = button.dataset.traceView || 'trace';
      if (mode === 'visual' && !CURRENT_TRACE_DATA?.visual_capability?.enabled) return;
      setTraceViewMode(traj.trace_id || '', mode);
      renderTrace(traj, report);
    };
  });
}
function bindVisualViewer() {
  document.querySelectorAll('[data-visual-layout]').forEach(button => {
    button.onclick = () => {
      setVisualLayoutMode(
        CURRENT_TRACE_ID || '',
        button.dataset.visualLayout || 'compare'
      );
      if (CURRENT_TRACE_DATA) {
        renderTrace(CURRENT_TRACE_DATA.trajectory, CURRENT_TRACE_DATA.report);
      }
    };
  });
  document.querySelectorAll('.visual-pane').forEach(pane => {
    pane.querySelectorAll('[data-gallery-select]').forEach(button => {
      button.onclick = () => {
        const selected = String(button.dataset.gallerySelect || '0');
        pane.querySelectorAll('[data-gallery-select]').forEach(item => item.classList.toggle('active', item === button));
        pane.querySelectorAll('[data-gallery-index]').forEach(item => item.classList.toggle('active', item.dataset.galleryIndex === selected));
        const image = pane.querySelector('[data-gallery-index="' + selected + '"] img');
        if (image) positionVisualMarker(image);
      };
    });
  });
  document.querySelectorAll('.visual-screenshot').forEach(image => {
    image.addEventListener('load', () => positionVisualMarker(image));
    if (image.complete) positionVisualMarker(image);
  });
}
function positionVisualMarker(image) {
  const x = Number(image.dataset.clickX);
  const y = Number(image.dataset.clickY);
  const marker = image.parentElement?.querySelector('.visual-click-marker');
  if (!marker || !Number.isFinite(x) || !Number.isFinite(y) || !image.naturalWidth || !image.naturalHeight) return;
  const left = x <= 1 ? x * 100 : (x / image.naturalWidth) * 100;
  const top = y <= 1 ? y * 100 : (y / image.naturalHeight) * 100;
  if (left < 0 || left > 100 || top < 0 || top > 100) return;
  marker.style.left = left + '%';
  marker.style.top = top + '%';
  marker.style.display = 'block';
}
function renderEventInspector(ev, isRoot, finding, ordinal, events, findings) {
  const debug = errorTrace(ev, finding);
  const inputValue = fmt(ev.input);
  const outputValue = fmt(ev.output);
  const errorValue = fmt(ev.error);
  const primaryValue = ev.error || outputValue || inputValue || 'No payload recorded.';
  const context = localContext(events, ev.event_id);
  const status = isRoot ? 'root' : timelineStatus(ev, finding);
  const hasDetectorSignal = Boolean(finding || isRoot || eventProblem(ev));
  let html = '<div class="event-inspector">';
  html += '<div class="event-inspector-summary ' + (isRoot ? 'root' : '') + ' status-' + escapeHtml(status) + '">';
  html += '<div class="event-head-left"><div class="event-alert-dot">' + (status === 'ok' ? '✓' : '!') + '</div>';
  html += '<div><div class="event-inspector-title"><button class="event-number" type="button" data-copy-event-number style="border:0; background:transparent; padding:0; cursor:pointer;">#' + escapeHtml(ordinal ?? ev.step_index ?? '-') + '</button><span>' + escapeHtml(titleCase(agentRoleLabel(ev))) + ' Step</span><span class="event-chevron">›</span><span>' + escapeHtml(titleCase(ev.module || 'module')) + '</span></div>';
  html += '<div class="event-inspector-sub">' + escapeHtml((ev.event_type || 'event') + '  ›  ' + (ev.module || 'module') + '  ›  ' + truncate(primaryValue, 54)) + '</div></div></div>';
  html += '<div class="event-head-right">';
  html += '<button class="chip ' + (status === 'error' ? 'bad' : (status === 'root' ? 'warn' : 'good')) + '" type="button" data-info-popover="Status: ' + escapeHtml(status === 'root' ? 'root cause candidate' : status) + ' event.">' + escapeHtml(status === 'root' ? 'Root' : titleCase(status)) + '</button>';
  html += '</div></div>';
  html += '<div class="inspector-tabs" role="tablist">';
  html += inspectorTab('summary', 'Summary', true);
  html += inspectorTab('details', 'Details', false);
  if (hasDetectorSignal) html += inspectorTab('detector', 'Diagnosis', false);
  html += '</div>';
  html += '<div class="inspector-pane active" data-pane="summary"><div class="inspector-grid">';
  html += inspectorCard('Event Type', ev.event_type || 'event');
  html += inspectorCard('Role', agentRoleLabel(ev));
  html += inspectorCard('Stage', ev.module || 'module');
  html += inspectorCard('Status', isRoot ? 'Root Cause' : timelineStatus(ev, finding));
  html += '<details class="summary-block summary-primary wide" open><summary>What happened</summary><p>' + escapeHtml(truncate(primaryValue, 680)) + '</p></details>';
  if (finding) {
    html += '<details class="summary-block summary-plan" open><summary>Why it matters</summary><p>' + escapeHtml(debug.triggerReason || debug.whyReported || debug.confidenceBasis || (finding?.evidence || []).join('; ') || debug.body) + '</p></details>';
  } else if (!hasDetectorSignal) {
    html += '<details class="summary-block summary-plan" open><summary>Diagnostic note</summary><p>No detector signal on this event. Use Details only if you need to compare raw input/output or adjacent context.</p></details>';
  }
  html += '</div></div>';
  html += '<div class="inspector-pane" data-pane="details"><div class="inspector-actions"><button class="timeline-tool" type="button" data-copy-current-tab>Copy Details</button><button class="timeline-tool" type="button" data-export-current-tab>Export Event JSON</button><span class="chip cyan">progressive details</span></div>';
  html += '<details class="summary-block summary-detail-block"><summary>Input / Output / Error</summary>';
  if (inputValue) html += '<div class="detail-field"><div class="inspector-label">Input</div><p>' + escapeHtml(inputValue) + '</p></div>';
  if (outputValue) html += '<div class="detail-field"><div class="inspector-label">Output</div><p>' + escapeHtml(outputValue) + '</p></div>';
  if (errorValue) html += '<div class="detail-field danger"><div class="inspector-label">Error</div><p>' + escapeHtml(errorValue) + '</p></div>';
  if (!inputValue && !outputValue && !errorValue) html += '<p>No input, output, or error payload recorded.</p>';
  html += '</details>';
  html += '<details class="summary-block summary-detail-block"><summary>Raw JSON</summary><pre class="raw-pre">' + escapeHtml(JSON.stringify({input: ev.input, output: ev.output, error: ev.error, metadata: ev.metadata}, null, 2)) + '</pre></details>';
  html += '<details class="summary-block summary-detail-block"><summary>State Delta</summary><div class="delta-grid">';
  html += '<div class="summary-block"><h3>Before</h3><p>' + escapeHtml(context.prev ? eventShort(context.prev) : 'No previous event') + '</p></div>';
  html += '<div class="summary-block"><h3>After</h3><p>' + escapeHtml(context.next ? eventShort(context.next) : 'No next event') + '</p></div>';
  html += '<div class="summary-block wide"><h3>Delta Summary</h3><p>' + escapeHtml(finding ? 'Detector signal changed or persisted at this step. Compare adjacent state before applying a fix.' : 'No meaningful failure delta detected around this event.') + '</p></div>';
  html += '</div></details>';
  html += '<details class="summary-block summary-detail-block"><summary>Nearby Context</summary><div class="context-stack">';
  context.window.forEach(item => {
    const itemFinding = findingForEvent(findings || [], item.ev.event_id);
    const selected = item.ev.event_id === ev.event_id;
    html += '<button type="button" class="context-row ' + (selected ? 'selected' : '') + '" data-event-id="' + escapeHtml(item.ev.event_id || '') + '"><span class="mono">#' + escapeHtml(item.ordinal) + '</span><span>' + escapeHtml(truncate(eventShort(item.ev), 90)) + '</span><span>' + escapeHtml(selected ? 'Selected' : timelineStatus(item.ev, itemFinding)) + '</span></button>';
  });
  html += '</div></details></div>';
  if (hasDetectorSignal) {
    html += '<div class="inspector-pane" data-pane="detector"><div class="inspector-grid">';
    html += inspectorCard('Detector', debug.mode || (eventProblem(ev) ? 'event.error' : 'No detector signal'));
    html += inspectorCard('Rule Pack', debug.rulePack || 'n/a');
    html += inspectorCard('Rule ID', debug.ruleId || 'n/a');
    html += inspectorCard('Severity', debug.severity || (eventProblem(ev) ? 'medium' : 'context'));
    html += inspectorCard('Evidence', debug.triggerReason || debug.whyReported || debug.confidenceBasis || (finding?.evidence || []).join('; ') || eventProblem(ev) || 'No detector evidence.', 'wide');
    html += '</div></div>';
  }
  html += '</div>';
  return html;
}
function titleCase(value) {
  const text = String(value || '');
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : '';
}
function inspectorTab(key, label, active) {
  return '<button type="button" class="inspector-tab ' + (active ? 'active' : '') + '" data-tab="' + key + '">' + escapeHtml(label) + '</button>';
}
function inspectorCard(label, value, klass) {
  return '<div class="inspector-card ' + escapeHtml(klass || '') + '"><div class="inspector-label">' + escapeHtml(label) + '</div><div class="inspector-value">' + escapeHtml(value || '-') + '</div></div>';
}
function localContext(events, eventId) {
  const idx = Math.max(0, events.findIndex(ev => ev.event_id === eventId));
  const start = Math.max(0, idx - 2);
  const end = Math.min(events.length, idx + 3);
  return {
    prev: idx > 0 ? events[idx - 1] : null,
    next: idx < events.length - 1 ? events[idx + 1] : null,
    window: events.slice(start, end).map((ev, offset) => ({ev, ordinal: start + offset + 1}))
  };
}
function eventShort(ev) {
  return (ev.agent_name || 'agent') + ' / ' + (ev.event_type || 'event') + '\\n' + truncate(ev.error || ev.output || ev.input || 'No payload recorded.', 180);
}
function renderDiagnosisPanel(report, findings, selectedEvent, events) {
  const selectedFinding = selectedEvent ? findingForEvent(findings, selectedEvent.event_id) : null;
  const primary = selectedFinding || (selectedEvent?.event_id === report.root_cause_event_id ? (findings || [])[0] : null);
  const debug = selectedEvent ? errorTrace(selectedEvent, primary) : null;
  const hasEventSignal = Boolean(selectedEvent && eventProblem(selectedEvent));
  const issue = primary?.failure_mode?.name
    || (selectedEvent?.event_id === report.root_cause_event_id ? 'Root cause candidate' : '')
    || (hasEventSignal ? debug?.title || 'Event error signal' : 'No issue detected');
  const related = relatedEvents(report, findings, selectedEvent, events);
  const ordinal = selectedEvent ? Math.max(1, (events || []).findIndex(ev => ev.event_id === selectedEvent.event_id) + 1) : '-';
  const hasIssue = Boolean(primary || hasEventSignal || selectedEvent?.event_id === report.root_cause_event_id);
  let html = '<aside class="diagnosis-panel ' + (hasIssue ? 'has-issue' : 'compact-clean') + '">';
  html += '<div class="diagnosis-section diagnosis-hero"><div class="diagnosis-hero-head"><div class="diagnosis-hero-copy"><div class="diagnosis-label">Diagnosis</div><div class="diagnosis-title">' + escapeHtml(issue) + '</div></div>';
  html += '<button class="workspace-launcher" id="hub-btn" type="button" aria-label="Open Error Hub panel" title="Open Error Hub panel" aria-expanded="false" aria-controls="hub-drawer"><span>Error Hub</span><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"></rect><path d="M15 4v16"></path></svg></button></div>';
  html += '<div class="lane-meta"><span class="chip ' + (primary || hasEventSignal ? 'bad' : 'good') + '">' + escapeHtml(primary ? (debug?.severity || 'high') : (hasEventSignal ? 'event signal' : 'clean')) + '</span>';
  if (selectedEvent?.event_id === report.root_cause_event_id) html += '<span class="chip warn">root cause</span>';
  html += '<span class="chip cyan">event #' + escapeHtml(ordinal) + '</span></div></div>';
  html += '<div class="diagnosis-section diagnosis-facts">';
  html += miniCompact('Agent', selectedEvent?.agent_name || 'agent');
  html += miniCompact('Stage', selectedEvent?.module || 'module');
  html += miniCompact('Event', selectedEvent?.event_type || 'event');
  html += '</div>';
  if (!hasIssue) {
    html += '<div class="diagnosis-section diagnosis-next-step"><div class="diagnosis-label">Next useful action</div><div class="diagnosis-copy">No local detector signal here. Continue along the timeline, or jump to the next error/root event before opening raw details.</div></div>';
    html += '</aside>';
    return html;
  }
  html += '<div class="diagnosis-section"><div class="diagnosis-label">Why It Matters</div><div class="diagnosis-copy">' + escapeHtml(primary ? (debug?.body || primary.suggestion || report.summary || 'Detector flagged this event.') : (hasEventSignal ? (debug?.body || eventProblem(selectedEvent)) : 'This event did not trigger any detector. Use Context to inspect nearby steps.')) + '</div></div>';
  html += '<div class="diagnosis-section"><div class="diagnosis-label">Evidence</div><ul class="evidence-list">';
  const evidence = primary ? diagnosisEvidence(primary, debug) : (hasEventSignal ? [eventProblem(selectedEvent), 'The event payload itself carries the signal; no analyzer finding is attached.'] : ['No local detector signal for this event.']);
  evidence.forEach(item => { html += '<li>' + escapeHtml(item) + '</li>'; });
  html += '</ul></div>';
  html += '<div class="diagnosis-section"><div class="diagnosis-label">Related Events</div><div class="related-chain">';
  related.forEach(item => { html += '<button type="button" class="related-link" data-event-id="' + escapeHtml(item.event_id || '') + '">#' + escapeHtml(item.ordinal) + '</button>'; });
  if (!related.length) html += '<span class="diagnosis-copy">No nearby issue.</span>';
  html += '</div></div>';
  html += '<div class="diagnosis-section"><div class="diagnosis-label">Suggested Fix</div><button class="related-link" type="button" data-info-popover="Suggested Fix: detector-generated remediation hint for the selected event.">Explain</button><div class="diagnosis-copy">' + escapeHtml(primary?.suggestion || debug?.repair || (hasEventSignal ? 'Inspect the raw event payload and compare adjacent state before applying a fix.' : 'Continue inspecting adjacent events and state delta before applying a fix.')) + '</div></div>';
  html += '<div class="diagnosis-section"><div class="diagnosis-label">Rule</div><button class="related-link" type="button" data-info-popover="Rule: deterministic detector rule that produced this finding.">Explain</button><div class="diagnosis-copy mono">' + escapeHtml(primary?.metadata?.rule_id || debug?.ruleId || 'n/a') + '</div></div>';
  html += '</aside>';
  return html;
}
function diagnosisEvidence(finding, debug) {
  const items = [];
  if (debug?.triggerReason) items.push(debug.triggerReason);
  if (debug?.whyReported) items.push(debug.whyReported);
  if (debug?.confidenceBasis) items.push(debug.confidenceBasis);
  (finding?.evidence || []).forEach(item => items.push(item));
  if (!items.length && finding?.metadata?.rule_pack) items.push('Matched rule pack: ' + finding.metadata.rule_pack);
  return items.length ? items.slice(0, 5) : ['Detector matched this event payload.'];
}
function relatedEvents(report, findings, selectedEvent, events) {
  const ids = new Set();
  if (report.root_cause_event_id) ids.add(report.root_cause_event_id);
  if (selectedEvent?.event_id) ids.add(selectedEvent.event_id);
  (findings || []).slice(0, 5).forEach(f => { if (f.event_id) ids.add(f.event_id); });
  return (events || [])
    .map((ev, idx) => ({event_id: ev.event_id, ordinal: idx + 1}))
    .filter(item => ids.has(item.event_id))
    .slice(0, 8);
}
function renderEventReadout(ev, finding, primaryLabel, primaryValue, inputValue, outputValue, errorValue, debug) {
  const evidence = debug.triggerReason || debug.whyReported || debug.confidenceBasis || (finding?.evidence || []).join('; ') || 'No explicit evidence beyond the recorded event payload.';
  let html = '<div class="event-readout">';
  html += '<div class="readout-card primary"><div class="readout-label">What happened</div><div class="readout-value">' + escapeHtml(primaryValue || 'No payload recorded.') + '</div><div class="lane-meta"><span class="chip cyan">' + escapeHtml(primaryLabel) + '</span></div></div>';
  html += '<div class="readout-card warn"><div class="readout-label">Why suspicious</div><div class="readout-value">' + escapeHtml(debug.title + ': ' + debug.body) + '</div><div class="lane-meta"><span class="chip ' + severityClass(debug.severity) + '">' + escapeHtml(debug.severity) + '</span><span class="chip ' + (finding ? 'bad' : 'good') + '">' + escapeHtml(timelineStatus(ev, finding)) + '</span></div></div>';
  html += '<div class="readout-card"><div class="readout-label">Evidence</div><div class="readout-value">' + escapeHtml(evidence) + '</div>';
  if (debug.rulePack || debug.ruleId) html += '<div class="lane-meta">' + ruleMeta(debug.rulePack, debug.ruleId) + '</div>';
  html += '</div></div>';
  html += '<details class="raw-details"><summary>Raw input / output / error</summary><div class="event-meta-strip">';
  html += field('Input', inputValue, false);
  html += field('Output', outputValue, false);
  html += field('Error', errorValue, Boolean(ev.error));
  html += '</div></details>';
  return html;
}
function renderTimelineRow(traj, ev, isRoot, finding) {
  const summary = truncate(ev.error || ev.output || ev.input || 'No payload recorded.', 180);
  const status = timelineStatus(ev, finding);
  let html = '<div class="timeline-row ' + (isRoot ? 'root' : '') + '">';
  html += '<div class="timeline-step">' + escapeHtml(ev.step_index ?? '-') + '</div>';
  html += '<div class="timeline-main"><div class="timeline-head"><div class="event-agent">' + escapeHtml(ev.agent_name || 'agent') + '</div><div class="event-type">' + escapeHtml(ev.event_type || '') + ' / ' + escapeHtml(ev.module || 'module') + '</div>';
  html += '<span class="chip ' + (status === 'error' ? 'bad' : 'good') + '">' + escapeHtml(status) + '</span>';
  if (isRoot) html += '<span class="chip warn">root</span>';
  html += '</div>';
  html += '<div class="timeline-summary">' + escapeHtml(summary) + '</div></div>';
  html += '<a class="timeline-open" href="/trace/' + encodeURIComponent(traj.trace_id) + '/event/' + encodeURIComponent(ev.event_id) + '">Open</a>';
  html += '</div>';
  return html;
}
function renderStepExplorer(traj, events, findings, rootId, expandedId) {
  const activeIndex = Math.max(0, events.findIndex(ev => ev.event_id === expandedId));
  const clipWidth = Math.max(70, Math.min(132, 86 * TIMELINE_ZOOM));
  const playheadLeft = 22 + activeIndex * (clipWidth + 10) + clipWidth / 2;
  const branches = getDebugBranches(traj.trace_id || '');
  let html = '<div class="timeline-editor unified' + (branches.length ? ' has-branches' : '') + '" id="full-trajectory">';
  html += '<div class="timeline-fixed-labels">';
  html += '<div class="track-label" data-track-label="event">Event</div>';
  if (branches.length) html += '<div class="track-label debug-branch-label">Rerun Attempts</div>';
  html += '</div>';
  html += '<div class="timeline-unified-scroll timeline-sync-scroll" data-track="timeline-canvas" style="--zoom:' + escapeHtml(TIMELINE_ZOOM) + '">';
  html += '<div class="timeline-unified-grid">';
  html += renderTimelineTrackBody((_ev, idx) => '#' + (idx + 1), 'event-lane', events, findings, rootId, expandedId, activeIndex, playheadLeft, 'event');
  if (branches.length) html += renderDebugBranchTree(branches, events, expandedId);
  html += '</div></div></div>';
  return html;
}
function renderTimelineOverview(events, findings, rootId, expandedId) {
  let html = '<div class="overview-strip" style="--zoom:' + escapeHtml(TIMELINE_ZOOM) + '">';
  events.forEach((ev, idx) => {
    const finding = findingForEvent(findings, ev.event_id);
    const isRoot = ev.event_id === rootId;
    const isActive = ev.event_id === expandedId;
    const status = isRoot ? 'root' : timelineStatus(ev, finding);
    html += '<button type="button" class="overview-dot ' + escapeHtml(status) + (isActive ? ' active' : '') + '" data-event-id="' + escapeHtml(ev.event_id || '') + '" aria-label="' + escapeHtml(timelineTooltip(ev, finding, isRoot, idx + 1)) + '"></button>';
  });
  html += '</div>';
  return html;
}
function renderTimelineTrack(label, getLabel, trackClass, events, findings, rootId, expandedId, activeIndex, playheadLeft, advanced, trackKey) {
  const hidden = trackKey && HIDDEN_TRACKS.has(trackKey);
  let html = '<div class="track-label ' + (hidden ? 'hidden-track' : '') + '" data-track-label="' + escapeHtml(trackKey || '') + '">' + escapeHtml(label) + '</div><div class="timeline-track timeline-sync-scroll ' + escapeHtml(trackClass || '') + (hidden ? ' hidden-track' : '') + '" data-track="' + escapeHtml(trackKey || '') + '" style="--zoom:' + escapeHtml(TIMELINE_ZOOM) + '"><div class="track-strip" style="--zoom:' + escapeHtml(TIMELINE_ZOOM) + '">';
  html += renderTimelineTrackInner(getLabel, events, findings, rootId, expandedId, activeIndex, playheadLeft, advanced);
  html += '</div></div>';
  return html;
}
function renderTimelineTrackBody(getLabel, trackClass, events, findings, rootId, expandedId, activeIndex, playheadLeft, trackKey) {
  const hidden = trackKey && HIDDEN_TRACKS.has(trackKey);
  let html = '<div class="timeline-track ' + escapeHtml(trackClass || '') + (hidden ? ' hidden-track' : '') + '" data-track="' + escapeHtml(trackKey || '') + '" style="--zoom:' + escapeHtml(TIMELINE_ZOOM) + '"><div class="track-strip" style="--zoom:' + escapeHtml(TIMELINE_ZOOM) + '">';
  html += renderTimelineTrackInner(getLabel, events, findings, rootId, expandedId, activeIndex, playheadLeft, false);
  html += '</div></div>';
  return html;
}
function renderTimelineTrackInner(getLabel, events, findings, rootId, expandedId, activeIndex, playheadLeft, advanced) {
  let html = '';
  html += '<div class="playhead" data-step="Step ' + escapeHtml(activeIndex + 1) + '" style="left:' + escapeHtml(playheadLeft) + 'px;"></div>';
  events.forEach((ev, idx) => {
    const finding = findingForEvent(findings, ev.event_id);
    const isRoot = ev.event_id === rootId;
    const isActive = ev.event_id === expandedId;
    const status = isRoot ? 'root' : timelineStatus(ev, finding);
    const rawLabel = getLabel(ev, idx) || '';
    const labelText = rawLabel || (advanced ? '' : '#' + (idx + 1));
    const tooltip = (isRoot || finding || eventProblem(ev))
      ? timelineMistakeTooltipHtml(ev, finding, isRoot, idx + 1)
      : timelineTooltipHtml(ev, finding, isRoot, idx + 1);
    html += '<button type="button" class="track-clip ' + escapeHtml(status === 'ok' ? 'ok' : status) + (isActive ? ' active' : '') + '" data-event-id="' + escapeHtml(ev.event_id || '') + '" data-tooltip="' + escapeHtml(tooltip) + '" title="' + escapeHtml(timelineTooltip(ev, finding, isRoot, idx + 1)) + '">' + escapeHtml(labelText) + '</button>';
  });
  return html;
}
function renderDebugBranchTree(branches, events, expandedId) {
  const sorted = (branches || []).slice().sort((a, b) => {
    const aStep = Number(a.checkpoint_ordinal || 0);
    const bStep = Number(b.checkpoint_ordinal || 0);
    if (aStep !== bStep) return aStep - bStep;
    return String(a.created_at || '').localeCompare(String(b.created_at || ''));
  });
  const modeCounts = {plan: 0, simulate: 0, live: 0};
  let html = '<div class="timeline-track debug-branch-lane" data-track="debug-tree" style="--zoom:' + escapeHtml(TIMELINE_ZOOM) + '"><div class="branch-track-stack">';
  sorted.forEach((branch) => {
    const startIndex = Math.max(0, Number(branch.checkpoint_ordinal || 1) - 1);
    const generated = Array.isArray(branch.generated_events) ? branch.generated_events : [];
    const mode = debugBranchMode(branch);
    modeCounts[mode.key] += 1;
    const sourceLabel = mode.label + ' ' + modeCounts[mode.key] + ' · from #' + (startIndex + 1);
    const sourceTip = debugBranchTooltipHtml(branch, startIndex + 1);
    html += '<div class="branch-sequence-row mode-' + escapeHtml(mode.key) + '" data-rerun-mode="' + escapeHtml(mode.key) + '" data-debug-branch-id="' + escapeHtml(branch.branch_id || '') + '" style="--branch-start:' + escapeHtml(startIndex) + '; --branch-color:' + escapeHtml(mode.color) + '">';
    for (let i = 0; i < startIndex; i += 1) {
      html += '<span class="branch-gap" aria-hidden="true"></span>';
    }
    html += '<button type="button" class="branch-origin-chip" data-debug-branch-id="' + escapeHtml(branch.branch_id || '') + '" data-event-id="' + escapeHtml(branch.parent_event_id || branch.event_id || '') + '" data-tooltip="' + escapeHtml(sourceTip) + '">' + escapeHtml(sourceLabel) + '</button>';
    if (!generated.length) {
      html += '<button type="button" class="track-clip debug-branch-track-clip mode-' + escapeHtml(mode.key) + '" data-debug-branch-id="' + escapeHtml(branch.branch_id || '') + '" data-event-id="' + escapeHtml(branch.parent_event_id || branch.event_id || '') + '" data-tooltip="' + escapeHtml(sourceTip) + '">' + escapeHtml(mode.emptyLabel) + '</button>';
      html += '</div>';
      return;
    }
    generated.forEach((ev, idx) => {
      const ordinal = startIndex + idx + 1;
      const eventId = ev?.event_id || ((branch.branch_id || 'branch') + '_evt_' + (idx + 1));
      const state = ev?.error ? 'error' : 'ok';
      const isActive = eventId === expandedId;
      const tip = ev?.error
        ? timelineMistakeTooltipHtml(ev, null, false, ordinal)
        : timelineTooltipHtml(ev, null, false, ordinal);
      html += '<button type="button" class="track-clip debug-branch-track-clip mode-' + escapeHtml(mode.key) + ' ' + escapeHtml(state) + (isActive ? ' active' : '') + '" data-debug-branch-id="' + escapeHtml(branch.branch_id || '') + '" data-event-id="' + escapeHtml(eventId) + '" data-branch-parent-event-id="' + escapeHtml(branch.parent_event_id || branch.event_id || '') + '" data-tooltip="' + escapeHtml(tip) + '" title="' + escapeHtml(mode.label + ' · ' + (branch.label || 'rerun branch') + ' · #' + ordinal) + '">' + escapeHtml(mode.clipPrefix + ' #' + ordinal) + '</button>';
    });
    html += '</div>';
  });
  html += '</div></div>';
  return html;
}
function debugBranchMode(branch) {
  const runType = String(branch?.run_type || '').toLowerCase();
  const executionMode = String(branch?.execution_mode || '').toLowerCase();
  if (runType === 'rerun_plan' || branch?.status === 'planned') {
    return {key:'plan', label:'Plan', clipPrefix:'PLAN', emptyLabel:'PLAN', color:'#72E8F4'};
  }
  if (runType === 'simulated_rerun' || executionMode.includes('simulat')) {
    return {key:'simulate', label:'Simulation', clipPrefix:'SIM', emptyLabel:'SIM', color:'#A884FF'};
  }
  const isMcp = runType === 'mcp_rerun' || executionMode === 'live_mcp';
  return {key:'live', label:isMcp ? 'Live · MCP' : 'Live · Runner', clipPrefix:'LIVE', emptyLabel:'LIVE', color:'#6EDB98'};
}
function renderStepCard(ev, finding, stateClass, isRoot, isActive, ordinal, showTick) {
  const aria = timelineTooltip(ev, finding, isRoot, ordinal);
  return '<button type="button" class="stepbar-card ' + stateClass + (isRoot ? ' root' : '') + (isActive ? ' active' : '') + '" data-event-id="' + escapeHtml(ev.event_id || '') + '" data-tooltip="' + escapeHtml(timelineTooltipHtml(ev, finding, isRoot, ordinal)) + '" aria-label="' + escapeHtml(aria) + '">' +
    (showTick ? '<span class="stepbar-card-label">' + escapeHtml(ordinal) + '</span>' : '') +
    '</button>';
}
function renderClipButton(ev, isRoot, finding, isActive, ordinal, stateClass, showConnector) {
  const status = isRoot ? 'root' : timelineStatus(ev, finding);
  const klass = isRoot ? 'root' : (stateClass || eventStateClass(ev, finding));
  let html = '<div class="timeline-clip-node">';
  if (showConnector) html += '<span class="timeline-link"></span>';
  html += '<button type="button" class="timeline-clip ' + escapeHtml(klass) + (isActive ? ' active' : '') + '" data-event-id="' + escapeHtml(ev.event_id || '') + '">' +
    '<div class="timeline-clip-top"><span class="timeline-clip-num">#' + escapeHtml(ordinal ?? '-') + '</span><span class="chip ' + (status === 'root' ? 'warn' : (status === 'error' ? 'bad' : 'good')) + '" style="height:18px; padding:0 6px; font-size:9px;">' + escapeHtml(status) + '</span></div>' +
    '<div class="timeline-clip-role">' + escapeHtml(agentRoleLabel(ev)) + '</div>' +
    '<div class="timeline-clip-foot">' + escapeHtml(ev.event_type || ev.module || 'event') + '</div>';
  html += '</button></div>';
  return html;
}
function eventMarkerLabel(ev, findings, rootId) {
  if (ev.event_id === rootId) return 'ROOT';
  if (findingForEvent(findings, ev.event_id) || eventProblem(ev)) return 'ERR';
  return '';
}
function mistakeClipLabel(ev, findings, rootId) {
  if (ev.event_id === rootId) return 'ROOT';
  if (findingForEvent(findings, ev.event_id) || eventProblem(ev)) return 'ERR';
  return '';
}
function mainSequenceLabel(ev) {
  const role = agentRoleLabel(ev);
  if (role === 'planner') return 'plan';
  if (role === 'runner') return actionClipLabel(ev) || 'act';
  if (role === 'reader') return observationClipLabel(ev) || 'obs';
  return truncate(ev.module || ev.event_type || 'event', 10);
}
function reasoningClipLabel(ev) {
  const text = ((ev.module || '') + ' ' + (ev.event_type || '') + ' ' + (ev.agent_name || '')).toLowerCase();
  if (text.includes('plan')) return 'plan';
  if (text.includes('memory')) return 'memory';
  if (text.includes('think') || text.includes('reason')) return 'reason';
  return agentRoleLabel(ev) === 'planner' ? 'plan' : '';
}
function actionClipLabel(ev) {
  const text = ((ev.module || '') + ' ' + (ev.event_type || '') + ' ' + fmt(ev.output || ev.input)).toLowerCase();
  if (text.includes('click')) return 'click';
  if (text.includes('search')) return 'search';
  if (text.includes('tool') || text.includes('action')) return truncate(ev.module || ev.event_type || 'action', 10);
  return agentRoleLabel(ev) === 'runner' ? truncate(ev.module || 'action', 10) : '';
}
function observationClipLabel(ev) {
  const text = ((ev.module || '') + ' ' + (ev.event_type || '') + ' ' + (ev.agent_name || '')).toLowerCase();
  if (text.includes('observ')) return 'obs';
  if (text.includes('result')) return 'result';
  return agentRoleLabel(ev) === 'reader' ? 'obs' : '';
}
function stateDeltaLabel(ev, findings) {
  if (findingForEvent(findings, ev.event_id) || eventProblem(ev)) return 'weak';
  return 'ok';
}
function detectorClipLabel(ev, findings) {
  const finding = findingForEvent(findings, ev.event_id);
  return finding ? truncate(finding.failure_mode?.family || 'detector', 10) : '';
}
function signalClipLabel(ev, findings, rootId) {
  if (ev.event_id === rootId) return 'root';
  if (findingForEvent(findings, ev.event_id) || eventProblem(ev)) return 'hit';
  return 'clean';
}
function timelineRulerTicks(count) {
  const total = Math.max(1, Number(count || 0));
  const interval = timelineTickInterval(total);
  const ticks = [];
  for (let i = 1; i <= total; i += interval) ticks.push(String(i));
  if (ticks[ticks.length - 1] !== String(total)) ticks.push(String(total));
  return ticks;
}
function timelineTooltipHtml(ev, finding, isRoot, ordinal) {
  const status = isRoot ? 'root' : timelineStatus(ev, finding);
  return '<div class="timeline-tooltip">' +
    '<div class="timeline-tooltip-main"><div class="timeline-tooltip-index">#' + escapeHtml(ordinal ?? '-') + '</div>' +
    '<div class="timeline-tooltip-status ' + escapeHtml(status) + '">' + escapeHtml(status) + '</div></div>' +
    '<div class="timeline-tooltip-type">' + escapeHtml(agentRoleLabel(ev)) + '</div>' +
    '</div>';
}
function timelineMistakeTooltipHtml(ev, finding, isRoot, ordinal) {
  const status = isRoot ? 'root' : timelineStatus(ev, finding);
  const mode = finding?.failure_mode || {};
  const title = isRoot ? 'Root cause candidate' : (mode.mode_id || mode.name || 'Detected mistake');
  const evidence = finding?.evidence || finding?.message || eventProblem(ev) || eventShort(ev) || 'No detector evidence recorded.';
  const severity = finding?.severity || finding?.confidence_label || (status === 'root' ? 'root' : 'error');
  return '<div class="timeline-tooltip timeline-error-tooltip">' +
    '<div class="timeline-tooltip-main"><div class="timeline-tooltip-index">#' + escapeHtml(ordinal ?? '-') + '</div>' +
    '<div class="timeline-tooltip-status ' + escapeHtml(status) + '">' + escapeHtml(status) + '</div></div>' +
    '<div class="timeline-tooltip-type">' + escapeHtml(title) + '</div>' +
    '<div class="timeline-tooltip-evidence">' + escapeHtml(truncate(evidence, 150)) + '</div>' +
    '<div class="timeline-tooltip-meta"><span>' + escapeHtml(agentRoleLabel(ev)) + '</span><span>' + escapeHtml(severity) + '</span></div>' +
    '</div>';
}
function debugBranchTooltipHtml(branch, parentOrdinal) {
  const generatedCount = Array.isArray(branch.generated_events) ? branch.generated_events.length : 0;
  const evaluation = branch.evaluation || localBranchEvaluation(branch);
  const mode = debugBranchMode(branch);
  const executionNote = mode.key === 'plan'
    ? 'Plan only · nothing executed'
    : mode.key === 'simulate'
      ? 'Hypothetical trajectory · unverified'
      : 'Observed execution · tools may run';
  return '<div class="timeline-tooltip timeline-branch-tooltip">' +
    '<div class="timeline-tooltip-main"><div class="timeline-tooltip-index">#' + escapeHtml(parentOrdinal ?? '-') + '</div>' +
    '<div class="timeline-tooltip-status ' + escapeHtml(sessionResultClass(evaluation.result)) + '">' + escapeHtml(mode.label) + '</div></div>' +
    '<div class="timeline-tooltip-type">' + escapeHtml(branch.label || branch.generated_trace_id || 'Rerun branch') + '</div>' +
    '<div class="timeline-tooltip-evidence">' + escapeHtml(truncate(evaluation.reason || branch.prompt_preview || branch.note || 'Rerun session saved locally.', 150)) + '</div>' +
    '<div class="timeline-tooltip-meta"><span>' + escapeHtml(executionNote) + '</span><span>' + escapeHtml(branch.created_at || '') + '</span></div>' +
    '<div class="timeline-tooltip-meta"><span>' + escapeHtml(generatedCount ? (generatedCount + ' generated events') : (mode.key === 'plan' ? 'plan saved' : 'no generated events')) + '</span><span>delete in menu</span></div>' +
    '</div>';
}
function sessionResultClass(result) {
  const key = String(result || 'unknown').toLowerCase();
  if (key === 'resolved' || key === 'improved') return key;
  if (key === 'worse') return 'worse';
  if (key === 'unchanged') return 'unchanged';
  return 'unknown';
}
function sessionResultLabel(result) {
  const key = String(result || 'unknown').toLowerCase();
  if (key === 'resolved') return 'Resolved';
  if (key === 'improved') return 'Improved';
  if (key === 'worse') return 'Worse';
  if (key === 'unchanged') return 'Unchanged';
  return 'Unknown';
}
function branchSessionId(branch) {
  return branch?.session_id || branch?.branch_id || '';
}
function localBranchEvaluation(branch) {
  if (branch?.evaluation && typeof branch.evaluation === 'object') return branch.evaluation;
  const generated = Array.isArray(branch?.generated_events) ? branch.generated_events : [];
  const generatedErrors = generated.filter(ev => eventProblem(ev)).length;
  const result = generated.length ? (generatedErrors ? 'unknown' : 'improved') : 'unknown';
  return {
    result,
    score_before: 0,
    score_after: generated.length && !generatedErrors ? 1 : 0,
    error_count_before: null,
    error_count_after: generatedErrors,
    generated_event_count: generated.length,
    root_cause_fixed: false,
    new_error_introduced: generatedErrors > 0,
    reason: generated.length
      ? (generatedErrors ? 'Generated branch has local error signals; needs manual review.' : 'Generated branch has no local error signals.')
      : 'No generated rerun events are attached to this session yet.',
    compare_summary: {
      rerun_from_ordinal: branch?.checkpoint_ordinal || null,
      rerun_event_count: generated.length
    }
  };
}
function branchOriginalSuffix(branch) {
  const events = CURRENT_TRACE_DATA?.trajectory?.events || [];
  const start = Math.max(0, Number(branch?.checkpoint_ordinal || 1) - 1);
  return events.slice(start, start + Math.max(6, Math.min(12, (branch?.generated_events || []).length || 6)));
}
function renderSessionMetric(label, value) {
  return '<div class="session-mini"><span>' + escapeHtml(label) + '</span><strong>' + escapeHtml(value ?? '-') + '</strong></div>';
}
function renderSessionCard(branch, idx, active) {
  const evaluation = localBranchEvaluation(branch);
  const generatedCount = Array.isArray(branch.generated_events) ? branch.generated_events.length : 0;
  return '<button type="button" class="session-card ' + (active ? 'active' : '') + '" data-session-id="' + escapeHtml(branchSessionId(branch)) + '">' +
    '<div class="session-card-title"><span>' + escapeHtml(branch.label || ('Rerun attempt ' + (idx + 1))) + '</span><span class="chip session-result ' + escapeHtml(sessionResultClass(evaluation.result)) + '">' + escapeHtml(sessionResultLabel(evaluation.result)) + '</span></div>' +
    '<div class="session-card-meta">from event #' + escapeHtml(branch.checkpoint_ordinal || '?') + ' · ' + escapeHtml(branch.debug_model || 'rerun model') + ' · ' + escapeHtml(generatedCount) + ' generated events</div>' +
    '<div class="session-eval-grid">' +
      renderSessionMetric('Before err', evaluation.error_count_before ?? '-') +
      renderSessionMetric('After err', evaluation.error_count_after ?? '-') +
      renderSessionMetric('Score', (evaluation.score_before ?? '-') + '→' + (evaluation.score_after ?? '-')) +
    '</div>' +
    '</button>';
}
function renderSessionDetail(branch) {
  if (!branch) {
    return '<div class="session-detail"><div class="session-detail-body"><div class="empty">No rerun sessions yet. Select an event and run Rerun From Here.</div></div></div>';
  }
  const evaluation = localBranchEvaluation(branch);
  const summary = evaluation.compare_summary || {};
  return '<div class="session-detail" data-session-detail="' + escapeHtml(branchSessionId(branch)) + '">' +
    '<div class="session-detail-head"><div><div class="session-kicker">Session Detail</div><div class="session-title">' + escapeHtml(branch.label || branchSessionId(branch) || 'Rerun session') + '</div><div class="session-sub">' + escapeHtml(branch.generated_trace_id || branchSessionId(branch) || '-') + '</div></div>' +
    '<span class="chip session-result ' + escapeHtml(sessionResultClass(evaluation.result)) + '">' + escapeHtml(sessionResultLabel(evaluation.result)) + '</span></div>' +
    '<div class="session-detail-body">' +
      '<div class="compare-summary">' + escapeHtml(evaluation.reason || 'No evaluation reason recorded.') + '</div>' +
      '<div class="session-eval-grid">' +
        renderSessionMetric('Rerun from', '#' + (summary.rerun_from_ordinal || branch.checkpoint_ordinal || '?')) +
        renderSessionMetric('Generated', evaluation.generated_event_count ?? (branch.generated_events || []).length) +
        renderSessionMetric('Root fixed', evaluation.root_cause_fixed ? 'yes' : 'not sure') +
      '</div>' +
      '<div class="session-actions">' +
        '<button class="button primary" type="button" data-compare-session="' + escapeHtml(branchSessionId(branch)) + '">Compare Original vs Rerun</button>' +
        '<button class="button" type="button" data-session-eval="resolved" data-session-id="' + escapeHtml(branchSessionId(branch)) + '">Solved</button>' +
        '<button class="button" type="button" data-session-eval="improved" data-session-id="' + escapeHtml(branchSessionId(branch)) + '">Improved</button>' +
        '<button class="button" type="button" data-session-eval="unchanged" data-session-id="' + escapeHtml(branchSessionId(branch)) + '">Still Failed</button>' +
        '<button class="button" type="button" data-session-eval="worse" data-session-id="' + escapeHtml(branchSessionId(branch)) + '">Worse</button>' +
        '<button class="button" type="button" data-delete-session="' + escapeHtml(branchSessionId(branch)) + '">Delete</button>' +
      '</div>' +
      '<div class="compare-summary"><strong>Path changed:</strong> ' + escapeHtml(summary.path_changed_from || 'original suffix') + ' → ' + escapeHtml(summary.path_changed_to || 'rerun branch') + '</div>' +
    '</div></div>';
}
function showDebugSessionsModal(traceId) {
  const branches = getDebugBranches(traceId || CURRENT_TRACE_ID || '');
  let modal = document.getElementById('session-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'session-modal';
    modal.className = 'session-modal';
    document.body.appendChild(modal);
  }
  const activeId = branchSessionId(branches[0] || {});
  modal.innerHTML =
    '<div class="session-shell" role="dialog" aria-modal="true" aria-label="Debug sessions">' +
      '<div class="session-head"><div><div class="session-kicker">Debug Sessions</div><div class="session-title">Rerun attempts for this task</div><div class="session-sub">Each session records model, prompt, generated events, local evaluation, and compare view.</div></div><button class="session-close" type="button" data-close-sessions aria-label="Close">×</button></div>' +
      '<div class="session-body"><div class="session-list">' +
        (branches.length ? branches.map((branch, idx) => renderSessionCard(branch, idx, branchSessionId(branch) === activeId)).join('') : '<div class="empty">No sessions yet. Run from any event to create the first one.</div>') +
      '</div><div id="session-detail-host">' + renderSessionDetail(branches[0] || null) + '</div></div>' +
    '</div>';
  modal.classList.add('visible');
  bindSessionModal();
}
function bindSessionModal() {
  const modal = document.getElementById('session-modal');
  if (!modal) return;
  modal.querySelectorAll('[data-close-sessions]').forEach(button => {
    button.onclick = () => modal.classList.remove('visible');
  });
  modal.onclick = event => {
    if (event.target === modal) modal.classList.remove('visible');
  };
  modal.querySelectorAll('.session-card').forEach(card => {
    card.onclick = () => {
      const branch = getDebugBranches(CURRENT_TRACE_ID).find(item => branchSessionId(item) === card.dataset.sessionId);
      modal.querySelectorAll('.session-card').forEach(item => item.classList.toggle('active', item === card));
      const host = document.getElementById('session-detail-host');
      if (host) host.innerHTML = renderSessionDetail(branch || null);
      bindSessionModal();
    };
  });
  modal.querySelectorAll('[data-compare-session]').forEach(button => {
    button.onclick = () => {
      const branch = getDebugBranches(CURRENT_TRACE_ID).find(item => branchSessionId(item) === button.dataset.compareSession);
      if (branch) showSessionCompare(branch);
    };
  });
  modal.querySelectorAll('[data-session-eval]').forEach(button => {
    button.onclick = () => updateSessionEvaluation(button.dataset.sessionId || '', button.dataset.sessionEval || 'unknown');
  });
  modal.querySelectorAll('[data-delete-session]').forEach(button => {
    button.onclick = async () => {
      const sessionId = button.dataset.deleteSession || '';
      if (!sessionId || !window.confirm('Delete this rerun session?')) return;
      try {
        await removeDebugBranch(CURRENT_TRACE_ID || '', sessionId);
        notify('Rerun session deleted');
        showDebugSessionsModal(CURRENT_TRACE_ID || '');
        if (CURRENT_TRACE_DATA) renderTrace(CURRENT_TRACE_DATA.trajectory, CURRENT_TRACE_DATA.report);
      } catch (error) {
        notify('Delete failed: ' + (error.message || error));
      }
    };
  });
}
function showSessionCompare(branch) {
  let modal = document.getElementById('compare-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'compare-modal';
    modal.className = 'session-modal';
    document.body.appendChild(modal);
  }
  const evaluation = localBranchEvaluation(branch);
  const original = branchOriginalSuffix(branch);
  const generated = Array.isArray(branch.generated_events) ? branch.generated_events : [];
  modal.innerHTML =
    '<div class="session-shell" role="dialog" aria-modal="true" aria-label="Original versus rerun comparison">' +
      '<div class="session-head"><div><div class="session-kicker">Original vs Rerun</div><div class="session-title">' + escapeHtml(branch.label || 'Rerun comparison') + '</div><div class="session-sub">' + escapeHtml(evaluation.reason || 'Local proxy evaluation') + '</div></div><button class="session-close" type="button" data-close-compare aria-label="Close">×</button></div>' +
      '<div class="session-body" style="grid-template-columns:1fr;"><div class="compare-summary">Result: <strong>' + escapeHtml(sessionResultLabel(evaluation.result)) + '</strong> · errors ' + escapeHtml(evaluation.error_count_before ?? '-') + ' → ' + escapeHtml(evaluation.error_count_after ?? '-') + ' · generated events ' + escapeHtml(generated.length) + '</div>' +
      '<div class="compare-grid">' + renderCompareColumn('Original suffix', original, Number(branch.checkpoint_ordinal || 1)) + renderCompareColumn('Rerun branch', generated, Number(branch.checkpoint_ordinal || 1)) + '</div></div>' +
    '</div>';
  modal.classList.add('visible');
  modal.querySelectorAll('[data-close-compare]').forEach(button => {
    button.onclick = () => modal.classList.remove('visible');
  });
  modal.onclick = event => {
    if (event.target === modal) modal.classList.remove('visible');
  };
}
function renderCompareColumn(title, events, startOrdinal) {
  let html = '<div class="compare-column"><div class="compare-title">' + escapeHtml(title) + '</div>';
  if (!events.length) {
    html += '<div class="empty">No events to compare.</div></div>';
    return html;
  }
  events.slice(0, 12).forEach((ev, idx) => {
    const status = eventProblem(ev) ? 'error' : 'ok';
    html += '<div class="compare-event ' + escapeHtml(status) + '"><div class="compare-event-title"><span>#' + escapeHtml(startOrdinal + idx) + ' ' + escapeHtml(agentRoleLabel(ev)) + '</span><span class="chip ' + (status === 'error' ? 'bad' : 'good') + '">' + escapeHtml(status) + '</span></div><div class="compare-event-copy">' + escapeHtml(truncate(ev.error || ev.output || ev.input || 'No payload recorded.', 180)) + '</div></div>';
  });
  html += '</div>';
  return html;
}
async function updateSessionEvaluation(sessionId, result) {
  if (!sessionId || !CURRENT_TRACE_ID) return;
  const branches = getDebugBranches(CURRENT_TRACE_ID);
  const branch = branches.find(item => branchSessionId(item) === sessionId);
  if (!branch) return;
  branch.evaluation = {
    ...localBranchEvaluation(branch),
    result,
    manual: true,
    reason: 'Manually marked as ' + sessionResultLabel(result) + ' in the UI.',
    updated_at: new Date().toISOString()
  };
  branch.status = 'evaluated';
  persistDebugBranches();
  try {
    await fetch('/api/v1/traces/' + encodeURIComponent(CURRENT_TRACE_ID) + '/debug-sessions/' + encodeURIComponent(sessionId), {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({status: 'evaluated', evaluation: branch.evaluation})
    });
  } catch (_e) {
  }
  notify('Session marked as ' + sessionResultLabel(result));
  showDebugSessionsModal(CURRENT_TRACE_ID);
  if (CURRENT_TRACE_DATA) renderTrace(CURRENT_TRACE_DATA.trajectory, CURRENT_TRACE_DATA.report);
}
function timelineTickInterval(count) {
  if (count <= 20) return 5;
  if (count <= 80) return 10;
  return 20;
}
function agentRoleLabel(ev) {
  const text = ((ev.agent_name || '') + ' ' + (ev.module || '') + ' ' + (ev.event_type || '')).toLowerCase();
  if (text.includes('plan') || text.includes('think') || text.includes('reason')) return 'planner';
  if (text.includes('read') || text.includes('observe') || text.includes('response')) return 'reader';
  if (text.includes('tool') || text.includes('action') || text.includes('run') || text.includes('search')) return 'runner';
  if (text.includes('critic') || text.includes('verify') || text.includes('check')) return 'checker';
  return ev.agent_name || 'runner';
}
function bindStepExplorer(traj, report) {
  document.querySelectorAll('.stepbar-card').forEach(card => {
    card.onclick = () => {
      const stepbar = card.closest('.stepbar');
      const scrollLeft = stepbar ? stepbar.scrollLeft : 0;
      CURRENT_EXPANDED_EVENT_ID = card.dataset.eventId || null;
      renderTrace(traj, report);
      const nextStepbar = document.querySelector('.stepbar');
      if (nextStepbar) nextStepbar.scrollLeft = scrollLeft;
      pulseEditorStage();
    };
  });
}
function renderTrajectoryEvent(ev, isRoot, finding, isOpen, ordinal) {
  const summary = truncate(ev.error || ev.output || ev.input || 'No payload recorded.', 180);
  const status = timelineStatus(ev, finding);
  let html = '<div class="trajectory-event ' + (isOpen ? 'open' : '') + '" id="trajectory-event-' + escapeHtml(ev.event_id || '') + '" data-event-id="' + escapeHtml(ev.event_id || '') + '">';
  html += '<button type="button" class="trajectory-summary" data-event-id="' + escapeHtml(ev.event_id || '') + '">';
  html += '<div class="timeline-step" title="recorded step ' + escapeHtml(ev.step_index ?? '-') + '">' + escapeHtml(ordinal ?? '-') + '</div>';
  html += '<div class="trajectory-summary-main">';
  html += '<div class="trajectory-title"><span>' + escapeHtml(ev.agent_name || 'agent') + '</span><span class="event-type">' + escapeHtml(ev.event_type || '') + ' / ' + escapeHtml(ev.module || 'module') + '</span></div>';
  html += '<div class="trajectory-copy">' + escapeHtml(summary) + '</div>';
  html += '</div>';
  html += '<div class="lane-meta" style="margin-top:0; justify-content:flex-end;"><span class="chip ' + (status === 'error' ? 'bad' : 'good') + '">' + escapeHtml(status) + '</span>';
  if (isRoot) html += '<span class="chip warn">root</span>';
  html += '<span class="chip ' + (isOpen ? 'cyan' : '') + '">' + (isOpen ? 'expanded' : 'open') + '</span></div>';
  html += '</button>';
  if (isOpen) {
    html += '<div class="trajectory-detail">' + renderInlineEventDetail(ev, finding) + '</div>';
  }
  html += '</div>';
  return html;
}
function bindTrajectoryEvents(traj, report) {
  document.querySelectorAll('.trajectory-summary').forEach(button => {
    button.onclick = () => {
      const eventId = button.dataset.eventId || null;
      const shouldCollapse = CURRENT_EXPANDED_EVENT_ID === eventId;
      CURRENT_EXPANDED_EVENT_ID = shouldCollapse ? null : eventId;
      renderTrace(traj, report);
      if (!shouldCollapse) scrollToTrajectoryEvent(CURRENT_EXPANDED_EVENT_ID);
    };
  });
}
function bindClipBrowser(traj, report) {
  document.querySelectorAll('.track-clip, .mini-segment, .overview-dot').forEach(button => {
    button.onclick = () => {
      const scroller = button.closest('.timeline-sync-scroll');
      const scrollLeft = scroller ? scroller.scrollLeft : 0;
      CURRENT_EXPANDED_EVENT_ID = button.dataset.eventId || null;
      renderTrace(traj, report);
      document.querySelectorAll('.timeline-sync-scroll').forEach(track => { track.scrollLeft = scrollLeft; });
      pulseEditorStage();
    };
  });
  document.querySelectorAll('[data-debug-branch-id]').forEach(button => {
    button.onclick = event => {
      event.stopPropagation();
      const scroller = button.closest('.timeline-sync-scroll');
      const scrollLeft = scroller ? scroller.scrollLeft : 0;
      const eventId = button.dataset.eventId || button.dataset.branchParentEventId || null;
      if (!eventId) return;
      CURRENT_EXPANDED_EVENT_ID = eventId;
      renderTrace(traj, report);
      document.querySelectorAll('.timeline-sync-scroll').forEach(track => { track.scrollLeft = scrollLeft; });
      pulseEditorStage();
    };
    button.oncontextmenu = async event => {
      event.preventDefault();
      const branchId = button.dataset.debugBranchId || '';
      if (!branchId) return;
      if (window.confirm('Delete this local rerun branch?')) {
        try {
          await removeDebugBranch(traj.trace_id || '', branchId);
          renderTrace(traj, report);
          notify('Rerun branch deleted');
        } catch (error) {
          notify('Delete failed: ' + (error.message || error));
        }
      }
    };
  });
}
function bindTimelineScrollSync() {
  const scrollers = Array.from(document.querySelectorAll('.timeline-sync-scroll'));
  let syncing = false;
  scrollers.forEach(scroller => {
    scroller.onscroll = () => {
      if (syncing) return;
      syncing = true;
      const left = scroller.scrollLeft;
      scrollers.forEach(other => {
        if (other !== scroller) other.scrollLeft = left;
      });
      window.requestAnimationFrame(() => { syncing = false; });
    };
  });
}
function bindDebugSessionActions(traj, report) {
  document.querySelectorAll('[data-open-sessions]').forEach(button => {
    button.onclick = () => showDebugSessionsModal(traj.trace_id || CURRENT_TRACE_ID || '');
  });
  document.querySelectorAll('[data-compare-session]').forEach(button => {
    button.onclick = () => {
      const branch = getDebugBranches(traj.trace_id || CURRENT_TRACE_ID || '').find(item => branchSessionId(item) === button.dataset.compareSession);
      if (branch) showSessionCompare(branch);
    };
  });
}
function bindInspectorTabs() {
  document.querySelectorAll('.inspector-tab').forEach(tab => {
    tab.onclick = () => {
      const key = tab.dataset.tab || 'summary';
      document.querySelectorAll('.inspector-tab').forEach(item => item.classList.toggle('active', item === tab));
      document.querySelectorAll('.inspector-pane').forEach(pane => pane.classList.toggle('active', pane.dataset.pane === key));
    };
  });
  document.querySelectorAll('.context-row[data-event-id]').forEach(row => {
    row.onclick = () => {
      if (!CURRENT_TRACE_DATA) return;
      CURRENT_EXPANDED_EVENT_ID = row.dataset.eventId || null;
      renderTrace(CURRENT_TRACE_DATA.trajectory, CURRENT_TRACE_DATA.report);
      pulseEditorStage();
    };
  });
  document.querySelectorAll('[data-copy-current-tab]').forEach(button => {
    button.onclick = () => copyText(activeInspectorText(), 'Current tab copied');
  });
  document.querySelectorAll('[data-export-current-tab]').forEach(button => {
    button.onclick = () => {
      const active = document.querySelector('.inspector-pane.active');
      const key = active?.dataset?.pane || 'tab';
      downloadBlob((CURRENT_TRACE_ID || 'trace') + '.' + key + '.txt', activeInspectorText(), 'text/plain');
      notify('Current tab exported');
    };
  });
  document.querySelectorAll('[data-copy-event-number]').forEach(button => {
    button.onclick = () => {
      copyText(button.textContent.trim(), 'Event number copied');
      if (CURRENT_EXPANDED_EVENT_ID) focusTimelineClip(CURRENT_EXPANDED_EVENT_ID);
    };
  });
  document.querySelectorAll('[data-info-popover]').forEach(button => {
    button.onclick = () => notify(button.dataset.infoPopover || 'No extra information.');
  });
}
function bindRelatedEvents(traj, report) {
  document.querySelectorAll('.related-link[data-event-id]').forEach(button => {
    button.onclick = () => {
      CURRENT_EXPANDED_EVENT_ID = button.dataset.eventId || null;
      renderTrace(traj, report);
      pulseEditorStage();
    };
  });
  document.querySelectorAll('[data-info-popover]').forEach(button => {
    button.onclick = () => notify(button.dataset.infoPopover || 'No extra information.');
  });
}
function bindEventNav(traj, report) {
  document.querySelectorAll('[data-nav-event]').forEach(button => {
    button.onclick = () => moveSelectedEvent(Number(button.dataset.navEvent || 0), false);
  });
}
function bindTimelineTools(traj, report) {
  document.querySelectorAll('[data-timeline-zoom]').forEach(button => {
    button.onclick = () => {
      TIMELINE_ZOOM = Math.max(0.65, Math.min(1.8, TIMELINE_ZOOM + Number(button.dataset.timelineZoom || 0) * 0.15));
      renderTrace(traj, report);
    };
  });
  document.querySelectorAll('[data-timeline-fit]').forEach(button => {
    button.onclick = () => {
      TIMELINE_ZOOM = 1;
      renderTrace(traj, report);
      notify('Timeline fit reset');
    };
  });
  document.querySelectorAll('[data-error-nav]').forEach(button => {
    button.onclick = () => moveSelectedEvent(Number(button.dataset.errorNav || 0), true);
  });
  document.querySelectorAll('[data-axis-toggle]').forEach(button => {
    button.onclick = () => {
      const modes = ['Step Count', 'Wall-clock Time'];
      const nextIdx = (modes.indexOf(TIMELINE_AXIS_MODE) + 1) % modes.length;
      TIMELINE_AXIS_MODE = modes[nextIdx];
      renderTrace(traj, report);
      notify('Timeline axis: ' + TIMELINE_AXIS_MODE);
    };
  });
  document.querySelectorAll('[data-track-toggle]').forEach(button => {
    const key = button.dataset.trackToggle || '';
    button.classList.toggle('active', !HIDDEN_TRACKS.has(key));
    button.onclick = () => {
      if (HIDDEN_TRACKS.has(key)) HIDDEN_TRACKS.delete(key);
      else HIDDEN_TRACKS.add(key);
      renderTrace(traj, report);
      notify((HIDDEN_TRACKS.has(key) ? 'Hidden ' : 'Shown ') + key + ' track');
    };
  });
}
function bindDebugContinuationButton() {
  document.querySelectorAll('[data-debug-from-selected]').forEach(button => {
    button.onclick = () => startDebugContinuation();
  });
}
function bindFindingJumps(traj, report) {
  const events = traj.events || [];
  document.querySelectorAll('.finding[data-event-id], .finding[data-step-index]').forEach(card => {
    card.onclick = () => {
      const directId = card.dataset.eventId || '';
      const stepIndex = card.dataset.stepIndex;
      const fallback = stepIndex !== undefined
        ? events.find(ev => String(ev.step_index) === String(stepIndex))
        : null;
      const eventId = directId || (fallback && fallback.event_id) || null;
      if (!eventId) return;
      CURRENT_EXPANDED_EVENT_ID = eventId;
      renderTrace(traj, report);
      pulseEditorStage();
    };
  });
}
function moveSelectedEvent(delta, errorsOnly) {
  if (!CURRENT_TRACE_DATA) return;
  const traj = CURRENT_TRACE_DATA.trajectory;
  const report = CURRENT_TRACE_DATA.report;
  const events = traj.events || [];
  if (!events.length) return;
  let idx = events.findIndex(ev => ev.event_id === CURRENT_EXPANDED_EVENT_ID);
  if (idx < 0) idx = 0;
  const isError = ev => findingForEvent(report.findings || [], ev.event_id) || eventProblem(ev) || ev.event_id === report.root_cause_event_id;
  let next = idx;
  do {
    next += delta;
    if (next < 0 || next >= events.length) return;
  } while (errorsOnly && !isError(events[next]));
  CURRENT_EXPANDED_EVENT_ID = events[next].event_id;
  renderTrace(traj, report);
  pulseEditorStage();
}
function pulseEditorStage() {
  window.requestAnimationFrame(() => {
    const stage = document.getElementById('event-stage');
    if (!stage) return;
    stage.classList.add('focus-pulse');
    window.setTimeout(() => stage.classList.remove('focus-pulse'), 950);
  });
}
function scrollToTrajectoryEvent(eventId) {
  if (!eventId) return;
  window.requestAnimationFrame(() => {
    const target = document.getElementById('trajectory-event-' + eventId);
    if (!target) return;
    target.scrollIntoView({behavior: 'smooth', block: 'center'});
    target.classList.add('focus-pulse');
    window.setTimeout(() => target.classList.remove('focus-pulse'), 950);
  });
}
function positionTimelinePlayhead(eventId) {
  if (!eventId) return;
  document.querySelectorAll('.track-strip').forEach(strip => {
    const playhead = Array.from(strip.children).find(child => child.classList.contains('playhead'));
    const clip = Array.from(strip.children).find(child =>
      child.classList.contains('track-clip') && child.dataset.eventId === eventId
    );
    if (!playhead || !clip) return;
    const stripRect = strip.getBoundingClientRect();
    const clipRect = clip.getBoundingClientRect();
    const playheadWidth = playhead.getBoundingClientRect().width;
    playhead.style.left = (
      clipRect.left - stripRect.left + clipRect.width / 2 - playheadWidth / 2
    ) + 'px';
  });
}
function focusTimelineClip(eventId) {
  if (!eventId) return;
  window.requestAnimationFrame(() => {
    const clip = document.querySelector('[data-event-id="' + cssEscape(eventId) + '"]');
    if (!clip) return;
    const scroller = clip.closest('.timeline-sync-scroll');
    if (scroller) {
      const left = Math.max(0, clip.offsetLeft - scroller.clientWidth / 2 + clip.clientWidth / 2);
      scroller.scrollTo({left, behavior: 'smooth'});
    }
    clip.classList.add('focus-pulse');
    window.setTimeout(() => clip.classList.remove('focus-pulse'), 950);
  });
}
function downloadJson(filename, value) {
  downloadBlob(filename, JSON.stringify(value, null, 2), 'application/json');
}
function downloadBlob(filename, content, type) {
  const blob = new Blob([content], {type: type || 'application/octet-stream'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
function traceCsv(data) {
  const events = data?.trajectory?.events || [];
  const rows = [['event_id','step_index','agent_name','event_type','module','status','error','input','output']];
  const findings = data?.report?.findings || [];
  events.forEach(ev => {
    const status = ev.event_id === data?.report?.root_cause_event_id ? 'root' : timelineStatus(ev, findingForEvent(findings, ev.event_id));
    rows.push([ev.event_id || '', ev.step_index ?? '', ev.agent_name || '', ev.event_type || '', ev.module || '', status, fmt(ev.error), fmt(ev.input), fmt(ev.output)]);
  });
  return rows.map(row => row.map(cell => '"' + String(cell).replace(/"/g, '""') + '"').join(',')).join('\\n');
}
function exportTraceBundle(format) {
  if (!CURRENT_TRACE_DATA) {
    notify('Select a trace before exporting');
    return;
  }
  const base = CURRENT_TRACE_ID || 'trace';
  const selected = (format || window.prompt('Export format: json, csv, pdf', 'json') || 'json').toLowerCase();
  if (selected === 'csv') {
    downloadBlob(base + '.events.csv', traceCsv(CURRENT_TRACE_DATA), 'text/csv');
  } else if (selected === 'pdf') {
    window.print();
    notify('Opened browser print dialog for PDF export');
    return;
  } else if (selected === 'json') {
    downloadJson(base + '.agentdebugx.report.json', CURRENT_TRACE_DATA);
  } else {
    notify('Unsupported export format: ' + selected);
    return;
  }
  notify('Exported ' + selected.toUpperCase() + ' bundle');
}
async function saveTraceCase(traceId, btn) {
  if (!traceId) {
    notify('No trace selected for this case');
    return;
  }
  const previousLabel = btn ? btn.getAttribute('aria-label') : '';
  if (btn) {
    btn.disabled = true;
    btn.setAttribute('aria-label', 'Saving ' + traceId + ' as case');
    btn.title = 'Saving...';
  }
  try {
    const traceData = traceId === CURRENT_TRACE_ID && CURRENT_TRACE_DATA
      ? CURRENT_TRACE_DATA
      : await api('/api/v1/traces/' + encodeURIComponent(traceId));
    const report = traceData.report || {};
    const primary = (report.findings || [])[0] || {};
    const mode = primary.failure_mode || {};
    const title = window.prompt('Case title', (mode.mode_id || mode.name || traceId));
    if (title === null) return;
    const response = await fetch('/api/v1/cases', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        trace_id: traceId,
        report_id: traceId === CURRENT_TRACE_ID ? activeStoredReportId() : (report.report_id || null),
        title
      })
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || 'save failed');
    }
    const payload = await response.json();
    notify('Saved to ' + (payload.path || CASE_DB_FILENAME));
  } catch (e) {
    notify('Save case failed: ' + (e.message || e));
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.setAttribute('aria-label', previousLabel || ('Save ' + traceId + ' as case'));
      btn.title = 'Save this trace as case';
    }
  }
}
function loadDebugBranches() {
  try {
    const raw = localStorage.getItem(DEBUG_BRANCH_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (_e) {
    return {};
  }
}
function loadDebugBackendConfig() {
  try {
    const raw = localStorage.getItem(DEBUG_BACKEND_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    if (parsed && typeof parsed === 'object') {
      if (Object.prototype.hasOwnProperty.call(parsed, 'api_key')) {
        delete parsed.api_key;
        localStorage.setItem(DEBUG_BACKEND_STORAGE_KEY, JSON.stringify(parsed));
      }
      return parsed;
    }
    return {};
  } catch (_e) {
    return {};
  }
}
function persistDebugBackendConfig() {
  const payload = {
    debug_model: document.getElementById('continuation-debug-model')?.value || defaultDebugModel()
  };
  try {
    localStorage.setItem(DEBUG_BACKEND_STORAGE_KEY, JSON.stringify(payload));
  } catch (_e) {
    notify('Debug backend settings could not be saved locally');
  }
}
function persistDebugBranches() {
  try {
    localStorage.setItem(DEBUG_BRANCH_STORAGE_KEY, JSON.stringify(DEBUG_BRANCHES || {}));
  } catch (_e) {
    notify('Rerun branch storage is full or blocked');
  }
}
function getDebugBranches(traceId) {
  if (!traceId) return [];
  const items = DEBUG_BRANCHES[traceId];
  return Array.isArray(items) ? items : [];
}
function debugBranchEventsForTrace(traceId) {
  return getDebugBranches(traceId).flatMap(branch => {
    const startIndex = Math.max(0, Number(branch.checkpoint_ordinal || 1) - 1);
    const generated = Array.isArray(branch.generated_events) ? branch.generated_events : [];
    return generated.map((ev, idx) => {
      const ordinal = startIndex + idx + 1;
      return {
        ...ev,
        event_id: ev.event_id || ((branch.branch_id || 'branch') + '_evt_' + (idx + 1)),
        trace_id: ev.trace_id || traceId,
        parent_event_id: ev.parent_event_id || branch.parent_event_id || branch.event_id || null,
        metadata: {
          ...(ev.metadata || {}),
          debug_branch_id: branch.branch_id || '',
          debug_branch_label: branch.label || 'rerun branch',
          debug_generated_trace_id: branch.generated_trace_id || '',
          debug_parent_event_id: branch.parent_event_id || branch.event_id || '',
          debug_ordinal: ordinal,
          debug_branch_created_at: branch.created_at || ''
        }
      };
    });
  });
}
function selectedEventPayloadForRerun(eventId) {
  const events = CURRENT_TRACE_DATA?.trajectory?.events || [];
  const ev = events.find(item => item.event_id === eventId) || CURRENT_BRANCH_EVENT_MAP.get(eventId) || null;
  if (!ev) return null;
  return {
    event_id: ev.event_id || eventId,
    trace_id: ev.trace_id || CURRENT_TRACE_ID || '',
    parent_event_id: ev.parent_event_id || ev.metadata?.debug_parent_event_id || null,
    agent_name: ev.agent_name || 'agent',
    event_type: ev.event_type || 'agent.step',
    module: ev.module || 'module',
    step_index: ev.step_index ?? ev.metadata?.debug_ordinal ?? null,
    input: ev.input ?? null,
    output: ev.output ?? null,
    error: ev.error ?? null,
    metadata: ev.metadata || {}
  };
}
function addDebugBranch(traceId, branch) {
  if (!traceId || !branch) return null;
  const items = getDebugBranches(traceId).slice();
  const generated = Array.isArray(branch.generated_events) ? branch.generated_events : [];
  const branchId = branch.branch_id || ('branch_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 8));
  const saved = {
    ...branch,
    branch_id: branchId,
    session_id: branch.session_id || branchId,
    status: branch.status || (generated.length ? 'completed' : 'created'),
    created_at: branch.created_at || new Date().toISOString()
  };
  saved.evaluation = localBranchEvaluation(saved);
  items.unshift(saved);
  DEBUG_BRANCHES[traceId] = items.slice(0, 80);
  persistDebugBranches();
  return saved;
}
async function removeDebugBranch(traceId, branchId) {
  if (!traceId || !branchId) return;
  const response = await fetch('/api/v1/traces/' + encodeURIComponent(traceId) + '/debug-sessions/' + encodeURIComponent(branchId), {method: 'DELETE'});
  if (!response.ok && response.status !== 404) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || 'delete failed');
  }
  DEBUG_BRANCHES[traceId] = getDebugBranches(traceId).filter(item => item.branch_id !== branchId);
  persistDebugBranches();
}
function mergeDebugBranches(traceId, branches) {
  if (!traceId) return false;
  const merged = new Map();
  getDebugBranches(traceId).forEach(item => {
    if (item?.branch_id) merged.set(item.branch_id, item);
  });
  (branches || []).forEach(item => {
    if (item?.branch_id) merged.set(item.branch_id, {...merged.get(item.branch_id), ...item});
  });
  const next = Array.from(merged.values()).sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));
  const previous = JSON.stringify(getDebugBranches(traceId));
  const current = JSON.stringify(next);
  DEBUG_BRANCHES[traceId] = next.slice(0, 80);
  persistDebugBranches();
  return previous !== current;
}
async function syncDebugBranches(traceId, force) {
  if (!traceId) return;
  if (!force && DEBUG_BRANCH_SYNCED.has(traceId)) return;
  DEBUG_BRANCH_SYNCED.add(traceId);
  try {
    const payload = await api('/api/v1/traces/' + encodeURIComponent(traceId) + '/debug-branches');
    const changed = mergeDebugBranches(traceId, payload.branches || []);
    if (changed && CURRENT_TRACE_DATA && CURRENT_TRACE_ID === traceId) {
      renderTrace(CURRENT_TRACE_DATA.trajectory, CURRENT_TRACE_DATA.report);
    }
  } catch (_e) {
  }
}
function saveCurrentContinuationAsBranch() {
  const modal = document.getElementById('continuation-modal');
  if (!modal) return;
  const pkg = currentContinuationPackage();
  const traceId = pkg.trace_id || CURRENT_TRACE_ID;
  const parentEventId = pkg.event_id || pkg.selected_event?.event_id || CURRENT_EXPANDED_EVENT_ID;
  const label = window.prompt('Rerun branch name', 'rerun from #' + (pkg.checkpoint_ordinal || '?'));
  if (label === null) return;
  const branch = addDebugBranch(traceId, {
    label: label || ('rerun from #' + (pkg.checkpoint_ordinal || '?')),
    parent_event_id: parentEventId,
    checkpoint_ordinal: pkg.checkpoint_ordinal,
    checkpoint_step_index: pkg.checkpoint_step_index,
    debug_model: pkg.prompt_config?.debug_model || defaultDebugModel(),
    generated_trace_id: pkg.generated_trace_id || '',
    prompt_preview: truncate(pkg.composed_prompt || '', 240),
    package: pkg
  });
  modal.classList.remove('visible');
  if (CURRENT_TRACE_DATA) renderTrace(CURRENT_TRACE_DATA.trajectory, CURRENT_TRACE_DATA.report);
  notify(branch ? 'Saved rerun branch on timeline tree' : 'Rerun branch was not saved');
}
async function runContinuationRerun() {
  const modal = document.getElementById('continuation-modal');
  if (!modal) return;
  const pkg = currentContinuationPackage();
  const rerunMode = ['plan_only', 'simulate', 'live'].includes(modal.__rerunMode) ? modal.__rerunMode : 'plan_only';
  const liveTransport = modal.__liveTransport === 'mcp' ? 'mcp' : 'server';
  const statusNode = document.getElementById('continuation-run-status');
  if (!pkg.trace_id || !pkg.event_id) {
    if (statusNode) statusNode.textContent = 'Missing trace/event checkpoint for backend rerun.';
    return;
  }
  const button = modal.querySelector('[data-run-continuation-debug]');
  const previous = button ? button.textContent : 'Run Rerun';
  if (button) {
    button.disabled = true;
    button.textContent = 'Running...';
  }
  if (rerunMode === 'live' && liveTransport === 'mcp' && !document.getElementById('rerun-mcp-endpoint')?.value.trim()) {
    if (statusNode) statusNode.textContent = 'Enter an MCP endpoint before running.';
    if (button) { button.disabled = false; button.textContent = previous || 'Run with MCP'; }
    return;
  }
  if (statusNode) statusNode.textContent = rerunMode === 'plan_only'
    ? 'Building an auditable rerun request...'
    : rerunMode === 'simulate'
      ? 'Generating a labeled hypothetical trajectory without tool execution...'
      : liveTransport === 'mcp'
        ? 'Connecting to MCP, discovering tools, and starting observed execution...'
        : 'Starting the configured live framework runner...';
  try {
    const privateEndpoint = Boolean(document.getElementById('rerun-mcp-private')?.checked);
    const mcpToken = document.getElementById('rerun-mcp-token')?.value || '';
    const requestPayload = {
      event_id: pkg.event_id,
      report_id: activeStoredReportId(),
      selected_event: pkg.selected_event || selectedEventPayloadForRerun(pkg.event_id),
      note: pkg.note || '',
      rerun_mode: rerunMode,
      checkpoint_policy: 'from_event',
      model: rerunMode === 'simulate'
        ? (document.getElementById('rerun-sim-model')?.value || '')
        : (rerunMode === 'live' && liveTransport === 'mcp' ? (document.getElementById('rerun-mcp-model')?.value || '') : ''),
      base_url: rerunMode === 'simulate'
        ? (document.getElementById('rerun-sim-base-url')?.value || '')
        : (rerunMode === 'live' && liveTransport === 'mcp' ? (document.getElementById('rerun-mcp-base-url')?.value || '') : ''),
      api_key: rerunMode === 'simulate'
        ? (document.getElementById('rerun-sim-api-key')?.value || '')
        : (rerunMode === 'live' && liveTransport === 'mcp' ? (document.getElementById('rerun-mcp-api-key')?.value || '') : ''),
      prompt_text: pkg.composed_prompt,
      label: (rerunMode === 'plan_only' ? 'Rerun plan from #' : rerunMode === 'simulate' ? 'Simulation from #' : liveTransport === 'mcp' ? 'MCP live rerun from #' : 'Live rerun from #') + (pkg.checkpoint_ordinal || '?'),
      options: pkg.prompt_config || {}
    };
    if (rerunMode === 'live' && liveTransport === 'mcp') {
      requestPayload.mcp = {
        endpoint: document.getElementById('rerun-mcp-endpoint')?.value.trim(),
        auth: mcpToken ? {type: 'bearer', token: mcpToken} : {type: 'none'},
        max_tool_calls: Number(document.getElementById('rerun-mcp-max-calls')?.value || 12),
        allow_private: privateEndpoint,
        allow_insecure: privateEndpoint
      };
    }
    const response = await fetch('/api/v1/traces/' + encodeURIComponent(pkg.trace_id) + '/rerun-from-event', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(requestPayload)
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(result.detail || 'rerun failed');
    }
    const branch = addDebugBranch(pkg.trace_id, result.branch || {});
    if (CURRENT_TRACE_DATA) renderTrace(CURRENT_TRACE_DATA.trajectory, CURRENT_TRACE_DATA.report);
    if (statusNode) {
      statusNode.textContent = 'Backend rerun completed and saved to ' + (result.path || 'local branch store') + '.';
    }
    const effectivePolicy = result?.branch?.requested_checkpoint_policy || 'from_start';
    notify(rerunMode === 'plan_only'
      ? 'Rerun plan created without execution'
      : rerunMode === 'simulate'
        ? 'Simulated rollout created; outcome is not verified'
        : liveTransport === 'mcp'
          ? 'Live MCP rerun completed with real tool results'
          : (effectivePolicy === 'from_event' ? 'Live rerun completed from selected event' : 'Live full-task rerun completed'));
    if (branch) {
      modal.classList.remove('visible');
    }
  } catch (e) {
    if (statusNode) statusNode.textContent = 'Rerun failed: ' + (e.message || e);
    notify('Rerun failed');
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = previous || 'Run Rerun';
    }
  }
}
async function startDebugContinuation() {
  if (!CURRENT_TRACE_ID || !CURRENT_TRACE_DATA) {
    notify('Open a trace before starting event rerun');
    return;
  }
  const eventId = CURRENT_EXPANDED_EVENT_ID || CURRENT_TRACE_DATA?.report?.root_cause_event_id;
  if (!eventId) {
    notify('Select a timeline event first');
    return;
  }
  const btn = document.getElementById('debug-from-event-btn');
  const previous = btn ? btn.textContent : '';
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Preparing...';
  }
  try {
    const selectedEvent = selectedEventPayloadForRerun(eventId);
    const response = await fetch('/api/v1/traces/' + encodeURIComponent(CURRENT_TRACE_ID) + '/debug-continuation', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        event_id: eventId,
        report_id: activeStoredReportId(),
        selected_event: selectedEvent,
        mode: 'rerun',
        prompt_config: {placeholder: true}
      })
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || 'continuation failed');
    }
    const payload = await response.json();
    payload.full_trajectory = CURRENT_TRACE_DATA.trajectory || {};
    payload.agentdebug_report = CURRENT_TRACE_DATA.report || {};
    showDebugContinuation(payload);
    notify('Rerun checkpoint ready');
  } catch (e) {
    notify('Rerun preparation failed: ' + (e.message || e));
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = previous || 'Prepare Rerun';
    }
  }
}
function showDebugContinuation(payload) {
  const backendConfig = loadDebugBackendConfig();
  let modal = document.getElementById('continuation-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'continuation-modal';
    modal.className = 'continuation-modal';
    document.body.appendChild(modal);
  }
  const instruction = defaultContinuationInstruction();
  const basePrompt = buildContinuationPrompt(payload, {
    debugModel: defaultDebugModel(),
    instruction,
    includeReport: false,
    selectedCaseIds: [],
    selectedExampleIds: [],
    customExtra: '',
    cases: [],
    examples: []
  });
  modal.innerHTML =
    '<div class="continuation-shell" role="dialog" aria-modal="true" aria-label="Rerun from event workspace">' +
      '<div class="continuation-head"><div><div class="continuation-kicker">Rerun Composer</div>' +
      '<div class="continuation-title">Rerun from event #' + escapeHtml(payload.checkpoint_ordinal || '-') + '</div>' +
      '<div class="continuation-sub">' + escapeHtml(payload.trace_id || '-') + ' · step ' + escapeHtml(payload.checkpoint_step_index ?? '-') + '</div><div class="composer-pill-row"><span class="composer-pill locked">Checkpoint: event #' + escapeHtml(payload.checkpoint_ordinal || '-') + '</span><span class="composer-pill locked">Context: compact</span></div></div>' +
      '<button class="continuation-close" type="button" data-close-continuation aria-label="Close">×</button></div>' +
      '<div class="continuation-body">' +
        '<div class="continuation-builder">' +
          '<div class="mode-switch" role="tablist" aria-label="Rerun mode"><button class="active" type="button" data-rerun-mode="plan_only">Plan only</button><button type="button" data-rerun-mode="simulate">Simulation</button><button type="button" data-rerun-mode="live">Live execution</button></div>' +
          '<div class="composer-section"><div class="continuation-label">Recovery directive</div><textarea class="composer-textarea prompt" id="continuation-instruction">' + escapeHtml(instruction) + '</textarea></div>' +
          '<div class="composer-compact-options">' +
            '<div class="composer-section"><label class="composer-check"><input type="checkbox" id="continuation-include-report"> Error report</label></div>' +
            '<div class="composer-section"><label class="composer-check"><input type="checkbox" id="continuation-include-custom"> Custom context</label></div>' +
            '<div class="composer-section"><label class="composer-check"><input type="checkbox" id="continuation-include-cases"> Typical errors</label></div>' +
            '<div class="composer-section"><label class="composer-check"><input type="checkbox" id="continuation-include-examples"> Fix examples</label></div>' +
          '</div>' +
          '<select class="composer-select" id="continuation-case-select" multiple disabled><option>Loading typical errors...</option></select>' +
          '<select class="composer-select" id="continuation-example-select" multiple disabled><option value="">No fix examples yet</option></select>' +
          '<textarea class="composer-textarea small" id="continuation-custom-extra" placeholder="Custom extra context for the continuation LLM..." disabled></textarea>' +
          '<div class="rerun-mode-panel" id="rerun-plan-panel">' +
            '<div class="composer-section locked"><div class="continuation-label">Request only</div><p class="workflow-copy">Build and store an auditable RerunPlan. No model, tools, runner, or environment will execute.</p></div>' +
          '</div>' +
          '<div class="rerun-mode-panel" id="rerun-simulate-panel" hidden>' +
            '<div class="composer-section locked"><div class="continuation-label">Hypothetical rollout</div><p class="workflow-copy">Generate a labeled simulated trajectory. No tools execute and the result is never treated as verified recovery.</p></div>' +
            '<input class="composer-input" id="rerun-sim-base-url" placeholder="Base URL (otherwise AGENTDEBUG_LLM_BASE_URL)">' + passwordField('rerun-sim-api-key', 'API key from LLM Settings', '') + '<input class="composer-input" id="rerun-sim-model" placeholder="Model (otherwise AGENTDEBUG_LLM_MODEL)">' +
          '</div>' +
          '<div class="rerun-mode-panel" id="rerun-live-panel" hidden>' +
            '<div class="mode-switch" role="tablist" aria-label="Live transport"><button class="active" type="button" data-live-transport="server">Server runner</button><button type="button" data-live-transport="mcp">MCP</button></div>' +
            '<div id="rerun-live-server-panel"><div class="composer-section locked"><div class="continuation-label">Observed execution</div><p class="workflow-copy">Run the configured HTTP or process framework runner with its real model, tools, credentials, and environment.</p></div></div>' +
            '<div id="rerun-live-mcp-panel" hidden>' +
              '<div class="composer-section"><div class="continuation-label">MCP Endpoint</div><input class="composer-input" id="rerun-mcp-endpoint" placeholder="https://mcp.example.com/mcp"></div>' +
              '<div class="workflow-grid"><input class="composer-input" id="rerun-mcp-token" type="password" placeholder="Bearer token (never stored)"><input class="composer-input" id="rerun-mcp-max-calls" type="number" min="1" max="40" value="12" placeholder="Max tool calls"></div>' +
              '<label class="composer-check"><input type="checkbox" id="rerun-mcp-private"> Allow local/private endpoint</label>' +
              '<div class="composer-section"><div class="continuation-label">LLM for MCP orchestration</div><input class="composer-input" id="rerun-mcp-base-url" placeholder="Base URL (otherwise AGENTDEBUG_LLM_BASE_URL)">' + passwordField('rerun-mcp-api-key', 'API key from LLM Settings', '') + '<input class="composer-input" id="rerun-mcp-model" placeholder="Model (otherwise AGENTDEBUG_LLM_MODEL)"></div>' +
            '</div>' +
          '</div>' +
        '</div>' +
        '<div class="continuation-card"><div class="continuation-label">Final Prompt Preview</div><textarea class="continuation-prompt" id="continuation-final-prompt">' + escapeHtml(basePrompt) + '</textarea></div>' +
      '</div>' +
      '<div class="continuation-inline-status" id="continuation-run-status">' + escapeHtml(rerunStatusMessage()) + '</div>' +
      '<div class="continuation-actions"><div class="continuation-action-group">' +
        '<button class="button primary" type="button" data-run-continuation-debug>Build Plan</button>' +
        '<button class="button" type="button" data-copy-continuation-prompt>Copy Prompt</button>' +
        '<button class="button" type="button" data-copy-continuation-config>Copy Config</button>' +
        '<button class="button" type="button" data-download-continuation>Download JSON</button>' +
        '<button class="button" type="button" data-copy-continuation-event>Copy Event ID</button>' +
      '</div><button class="button" type="button" data-close-continuation>Close</button></div>' +
    '</div>';
  modal.__continuationPayload = payload;
  modal.__rerunMode = 'plan_only';
  modal.__liveTransport = 'server';
  modal.__caseLibrary = [];
  modal.__fixExamples = [];
  modal.classList.add('visible');
  hydrateLLMInputs(false);
  bindPasswordToggles(modal);
  bindDebugContinuationModal();
  hydrateContinuationCasePickers();
  updateContinuationPrompt();
}
function bindDebugContinuationModal() {
  const modal = document.getElementById('continuation-modal');
  if (!modal) return;
  const payload = modal.__continuationPayload || {};
  modal.querySelectorAll('[data-close-continuation]').forEach(button => {
    button.onclick = () => modal.classList.remove('visible');
  });
  modal.onclick = event => {
    if (event.target === modal) modal.classList.remove('visible');
  };
  modal.querySelectorAll('[data-rerun-mode]').forEach(button => {
    button.onclick = () => setRerunMode(button.dataset.rerunMode || 'plan_only');
  });
  modal.querySelectorAll('[data-live-transport]').forEach(button => {
    button.onclick = () => setLiveTransport(button.dataset.liveTransport || 'server');
  });
  ['continuation-debug-model', 'continuation-instruction', 'continuation-include-report', 'continuation-include-cases', 'continuation-case-select', 'continuation-include-examples', 'continuation-example-select', 'continuation-include-custom', 'continuation-custom-extra'].forEach(id => {
    const control = document.getElementById(id);
    if (!control) return;
    control.oninput = () => {
      persistDebugBackendConfig();
      updateContinuationPrompt();
    };
    control.onchange = () => {
      const custom = document.getElementById('continuation-custom-extra');
      const caseSelect = document.getElementById('continuation-case-select');
      const exampleSelect = document.getElementById('continuation-example-select');
      if (custom) custom.disabled = !document.getElementById('continuation-include-custom')?.checked;
      if (caseSelect) caseSelect.disabled = !document.getElementById('continuation-include-cases')?.checked;
      if (exampleSelect) exampleSelect.disabled = !document.getElementById('continuation-include-examples')?.checked || !(modal.__fixExamples || []).length;
      persistDebugBackendConfig();
      updateContinuationPrompt();
    };
  });
  const runDebug = modal.querySelector('[data-run-continuation-debug]');
  if (runDebug) {
    runDebug.onclick = () => runContinuationRerun();
  }
  setLiveTransport('server');
  setRerunMode('plan_only');
  const copyPrompt = modal.querySelector('[data-copy-continuation-prompt]');
  if (copyPrompt) copyPrompt.onclick = () => copyText(currentContinuationPrompt(), 'Continuation prompt copied');
  const copyConfig = modal.querySelector('[data-copy-continuation-config]');
  if (copyConfig) copyConfig.onclick = () => copyText(JSON.stringify(currentContinuationPackage(), null, 2), 'Continuation config copied');
  const copyEvent = modal.querySelector('[data-copy-continuation-event]');
  if (copyEvent) copyEvent.onclick = () => copyText(payload.event_id || '', 'Checkpoint event ID copied');
  const download = modal.querySelector('[data-download-continuation]');
  if (download) {
    download.onclick = () => downloadJson((payload.trace_id || 'trace') + '.event-' + (payload.checkpoint_ordinal || 'checkpoint') + '.continuation.json', currentContinuationPackage());
  }
}
function setRerunMode(mode) {
  const modal = document.getElementById('continuation-modal');
  if (!modal) return;
  const resolved = ['plan_only', 'simulate', 'live'].includes(mode) ? mode : 'plan_only';
  modal.__rerunMode = resolved;
  modal.querySelectorAll('[data-rerun-mode]').forEach(button => button.classList.toggle('active', button.dataset.rerunMode === resolved));
  const plan = document.getElementById('rerun-plan-panel');
  const simulate = document.getElementById('rerun-simulate-panel');
  const live = document.getElementById('rerun-live-panel');
  if (plan) plan.hidden = resolved !== 'plan_only';
  if (simulate) simulate.hidden = resolved !== 'simulate';
  if (live) live.hidden = resolved !== 'live';
  const run = modal.querySelector('[data-run-continuation-debug]');
  if (run) {
    const serverLive = resolved === 'live' && modal.__liveTransport === 'server';
    run.disabled = serverLive && !Boolean(UI_STATUS?.rerun?.configured);
    run.textContent = resolved === 'plan_only' ? 'Build Plan' : resolved === 'simulate' ? 'Run Simulation' : (modal.__liveTransport === 'mcp' ? 'Run Live with MCP' : 'Run Live');
    run.title = serverLive && !UI_STATUS?.rerun?.configured
      ? 'Configure AGENTDEBUG_RUNNER_URL or AGENTDEBUG_RERUN_COMMAND'
      : '';
  }
  const status = document.getElementById('continuation-run-status');
  if (status) status.textContent = resolved === 'plan_only'
    ? 'Plan only builds an auditable request and executes nothing.'
    : resolved === 'simulate'
      ? 'Simulation generates a hypothetical trajectory; tools are not executed and recovery is not verified.'
      : liveRerunStatusMessage();
}
function setLiveTransport(transport) {
  const modal = document.getElementById('continuation-modal');
  if (!modal) return;
  const resolved = transport === 'mcp' ? 'mcp' : 'server';
  modal.__liveTransport = resolved;
  modal.querySelectorAll('[data-live-transport]').forEach(button => button.classList.toggle('active', button.dataset.liveTransport === resolved));
  const server = document.getElementById('rerun-live-server-panel');
  const mcp = document.getElementById('rerun-live-mcp-panel');
  if (server) server.hidden = resolved !== 'server';
  if (mcp) mcp.hidden = resolved !== 'mcp';
  if (modal.__rerunMode === 'live') setRerunMode('live');
}
function liveRerunStatusMessage() {
  const modal = document.getElementById('continuation-modal');
  if (modal?.__liveTransport === 'mcp') {
    return 'MCP is a live transport: real tool results are recorded. Credentials are used only for this request.';
  }
  return rerunStatusMessage();
}
function defaultDebugModel() {
  return loadLLMSettings().model || loadDebugBackendConfig().debug_model || 'gpt-4o';
}
function rerunStatusMessage() {
  const rerun = UI_STATUS?.rerun || {};
  if (rerun.configuration_error) return 'Rerun configuration error: ' + rerun.configuration_error;
  if (!rerun.configured) return 'Live rerun is unavailable. You can still copy or download this rerun request.';
  return 'Live rerun ready via ' + (rerun.transport || 'configured runner') + ' · policy ' + (rerun.checkpoint_policy || 'from_start') + '.';
}
function defaultContinuationInstruction() {
  return 'Retry the original task using the selected diagnosis and recovery guidance. Preserve every verified task constraint, avoid repeating the failed decision, and produce an inspectable replacement trajectory.';
}
function traceProblemText(traj) {
  const meta = traj?.metadata || {};
  return meta.task || meta.question || meta.query || meta.goal || meta.instruction || meta.system_prompt || traj?.task || traj?.goal || 'Original task was not explicitly recorded in metadata.';
}
function buildContinuationPrompt(payload, config) {
  const traj = payload.full_trajectory || CURRENT_TRACE_DATA?.trajectory || {};
  const report = payload.agentdebug_report || CURRENT_TRACE_DATA?.report || {};
  const prefixEvents = Array.isArray(payload.prefix_events) ? payload.prefix_events : [];
  const contextWindow = Array.isArray(payload.context_window) ? payload.context_window : [];
  const nextPreview = Array.isArray(payload.next_events_preview) ? payload.next_events_preview : [];
  const instruction = config.instruction || defaultContinuationInstruction();
  const debugModel = config.debugModel || defaultDebugModel();
  const rerunMode = document.getElementById('continuation-modal')?.__rerunMode || 'plan_only';
  const sections = [
    'Rerun mode: ' + rerunMode,
    ...(rerunMode === 'plan_only' ? [] : ['Rerun model: ' + debugModel]),
    instruction,
    '## Original Task / Full Problem\\n' + traceProblemText(traj),
    '## Trace Metadata\\n' + JSON.stringify({
      trace_id: traj.trace_id,
      task_id: traj.task_id,
      framework: traj.framework,
      goal: traj.goal,
      metadata: traj.metadata || {}
    }, null, 2),
    '## Prefix Events Before Checkpoint\\n' + JSON.stringify(prefixEvents.slice(-8), null, 2),
    '## Local Context Window Around Checkpoint\\n' + JSON.stringify(contextWindow, null, 2),
    '## Known Upcoming Events To Replace\\n' + JSON.stringify(nextPreview, null, 2)
  ];
  if (config.includeReport) {
    sections.push('## AgentDebugX Error Report\\n' + JSON.stringify(report, null, 2));
  }
  const selectedCases = (config.cases || []).filter(item => (config.selectedCaseIds || []).includes(item.case_id));
  if (selectedCases.length) {
    sections.push('## Typical Error Cases\\n' + selectedCases.map(caseContinuationSnippet).join('\\n\\n---\\n\\n'));
  }
  const selectedExamples = (config.examples || []).filter(item => (config.selectedExampleIds || []).includes(item.example_id));
  if (selectedExamples.length) {
    sections.push('## Typical Fix Examples\\n' + selectedExamples.map(item => JSON.stringify(item, null, 2)).join('\\n\\n---\\n\\n'));
  }
  if ((config.customExtra || '').trim()) {
    sections.push('## Custom Extra Context\\n' + config.customExtra.trim());
  }
  sections.push('## Rerun Checkpoint\\n' + JSON.stringify({
    trace_id: payload.trace_id,
    event_id: payload.event_id,
    checkpoint_ordinal: payload.checkpoint_ordinal,
    checkpoint_step_index: payload.checkpoint_step_index,
    status: payload.status,
    selected_event: payload.selected_event,
    selected_findings: payload.selected_findings
  }, null, 2));
  sections.push('## Required Output Format\\n' + JSON.stringify({
    continuation_events: [
      {
        agent_name: 'planner',
        event_type: 'agent.step',
        module: 'planning',
        step_index: payload.checkpoint_step_index || payload.checkpoint_ordinal || 1,
        input: 'short observation summary',
        output: 'short next action or reasoning summary',
        error: null,
        metadata: {
          branch_from_event_id: payload.event_id,
          note: 'return 3 to 6 compact events; keep strings short'
        }
      }
    ]
  }, null, 2));
  return sections.join('\\n\\n');
}
function caseContinuationSnippet(item) {
  return JSON.stringify({
    case_id: item.case_id,
    title: item.title,
    dataset: item.dataset,
    model: item.model,
    top_family: item.top_family,
    top_mode: item.top_mode,
    summary: item.summary,
    root_cause_step_index: item.root_cause_step_index,
    finding_count: item.finding_count
  }, null, 2);
}
function selectedValues(select) {
  return Array.from(select?.selectedOptions || []).map(option => option.value).filter(Boolean);
}
function currentContinuationConfig() {
  const modal = document.getElementById('continuation-modal');
  const includeReport = Boolean(document.getElementById('continuation-include-report')?.checked);
  const includeCases = Boolean(document.getElementById('continuation-include-cases')?.checked);
  const includeExamples = Boolean(document.getElementById('continuation-include-examples')?.checked);
  const includeCustom = Boolean(document.getElementById('continuation-include-custom')?.checked);
  return {
    debugModel: document.getElementById('rerun-sim-model')?.value || document.getElementById('rerun-mcp-model')?.value || defaultDebugModel(),
    instruction: document.getElementById('continuation-instruction')?.value || defaultContinuationInstruction(),
    includeReport,
    selectedCaseIds: includeCases ? selectedValues(document.getElementById('continuation-case-select')) : [],
    selectedExampleIds: includeExamples ? selectedValues(document.getElementById('continuation-example-select')) : [],
    customExtra: includeCustom ? (document.getElementById('continuation-custom-extra')?.value || '') : '',
    cases: modal?.__caseLibrary || [],
    examples: modal?.__fixExamples || []
  };
}
function currentContinuationPrompt() {
  const modal = document.getElementById('continuation-modal');
  const payload = modal?.__continuationPayload || {};
  return document.getElementById('continuation-final-prompt')?.value || buildContinuationPrompt(payload, currentContinuationConfig());
}
function currentContinuationPackage() {
  const modal = document.getElementById('continuation-modal');
  const payload = modal?.__continuationPayload || {};
  const config = currentContinuationConfig();
  return {
    ...payload,
    rerun_mode: modal?.__rerunMode || 'plan_only',
    live_transport: modal?.__liveTransport || 'server',
    checkpoint_policy: 'from_event',
    prompt_config: {
      debug_model: config.debugModel,
      include_agentdebug_report: config.includeReport,
      selected_typical_error_case_ids: config.selectedCaseIds,
      selected_typical_fix_example_ids: config.selectedExampleIds,
      has_custom_extra: Boolean((config.customExtra || '').trim()),
      execution_contract: 'plan_only | simulated_rollout | live_execution'
    },
    composed_prompt: currentContinuationPrompt()
  };
}
function updateContinuationPrompt() {
  const modal = document.getElementById('continuation-modal');
  if (!modal) return;
  const target = document.getElementById('continuation-final-prompt');
  if (!target) return;
  target.value = buildContinuationPrompt(modal.__continuationPayload || {}, currentContinuationConfig());
}
async function hydrateContinuationCasePickers() {
  const modal = document.getElementById('continuation-modal');
  const select = document.getElementById('continuation-case-select');
  if (!modal || !select) return;
  try {
    const payload = await api('/api/v1/cases');
    const cases = payload.cases || [];
    modal.__caseLibrary = cases;
    if (!cases.length) {
      select.innerHTML = '<option value="">No typical errors saved yet</option>';
      return;
    }
    select.innerHTML = cases.map(item => '<option value="' + escapeHtml(item.case_id || '') + '">' + escapeHtml(truncate((item.title || item.trace_id || 'case') + ' · ' + (item.top_mode || item.top_family || 'failure'), 92)) + '</option>').join('');
  } catch (e) {
    select.innerHTML = '<option value="">Failed to load typical errors</option>';
  } finally {
    select.disabled = !document.getElementById('continuation-include-cases')?.checked;
    updateContinuationPrompt();
  }
}
function activeInspectorText() {
  const active = document.querySelector('.inspector-pane.active') || document.querySelector('.inspector-pane');
  return active ? active.innerText.trim() : '';
}
async function copyText(text, message) {
  try {
    await navigator.clipboard.writeText(text || '');
    notify(message || 'Copied');
  } catch (e) {
    notify('Copy failed: browser blocked clipboard access');
  }
}
function notify(message) {
  const toast = document.getElementById('toast');
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add('visible');
  window.clearTimeout(notify._timer);
  notify._timer = window.setTimeout(() => toast.classList.remove('visible'), 1900);
}
function closeWorkflowModal(id) {
  document.getElementById(id)?.classList.remove('visible');
}
function loadLLMSettings() {
  let persisted = {};
  try {
    const raw = localStorage.getItem(LLM_SETTINGS_STORAGE_KEY);
    persisted = raw ? JSON.parse(raw) : {};
  } catch (_e) {
    persisted = {};
  }
  let apiKey = '';
  try {
    apiKey = sessionStorage.getItem(LLM_API_KEY_SESSION_KEY) || '';
  } catch (_e) {
    apiKey = '';
  }
  return {
    base_url: String(persisted?.base_url || ''),
    api_key: apiKey,
    model: String(persisted?.model || '')
  };
}
function persistLLMSettings(settings) {
  try {
    localStorage.setItem(LLM_SETTINGS_STORAGE_KEY, JSON.stringify({
      base_url: String(settings.base_url || '').trim(),
      model: String(settings.model || '').trim()
    }));
    if (settings.api_key) sessionStorage.setItem(LLM_API_KEY_SESSION_KEY, settings.api_key);
    else sessionStorage.removeItem(LLM_API_KEY_SESSION_KEY);
    return true;
  } catch (_e) {
    notify('Browser storage is unavailable; LLM settings were not saved');
    return false;
  }
}
function passwordField(id, placeholder, extraClass) {
  return '<div class="password-field ' + escapeHtml(extraClass || '') + '">' +
    '<input class="composer-input" id="' + escapeHtml(id) + '" type="password" autocomplete="off" placeholder="' + escapeHtml(placeholder) + '">' +
    '<button class="password-toggle" type="button" data-password-toggle="' + escapeHtml(id) + '" aria-label="Show API key" title="Show API key">' + passwordToggleIcon(false) + '</button>' +
  '</div>';
}
function passwordToggleIcon(visible) {
  if (visible) {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m3 3 18 18"></path><path d="M10.6 10.6a2 2 0 0 0 2.8 2.8"></path><path d="M9.9 4.2A10.8 10.8 0 0 1 12 4c5 0 9 4.2 10 8a12.4 12.4 0 0 1-2.1 4.2"></path><path d="M6.6 6.6A12 12 0 0 0 2 12c1 3.8 5 8 10 8 1.5 0 2.9-.4 4.1-1"></path></svg>';
  }
  return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"></path><circle cx="12" cy="12" r="3"></circle></svg>';
}
function bindPasswordToggles(scope) {
  (scope || document).querySelectorAll('[data-password-toggle]').forEach(button => {
    button.onclick = () => {
      const input = document.getElementById(button.dataset.passwordToggle || '');
      if (!input) return;
      const visible = input.type === 'password';
      input.type = visible ? 'text' : 'password';
      button.innerHTML = passwordToggleIcon(visible);
      button.setAttribute('aria-label', visible ? 'Hide API key' : 'Show API key');
      button.title = visible ? 'Hide API key' : 'Show API key';
    };
  });
}
function hydrateLLMInputs(overwrite) {
  const settings = loadLLMSettings();
  const groups = {
    base_url: ['upload-base-url', 'diagnose-base-url', 'rerun-sim-base-url', 'rerun-mcp-base-url'],
    api_key: ['upload-api-key', 'diagnose-api-key', 'rerun-sim-api-key', 'rerun-mcp-api-key'],
    model: ['upload-model', 'diagnose-model', 'rerun-sim-model', 'rerun-mcp-model']
  };
  Object.entries(groups).forEach(([key, ids]) => {
    ids.forEach(id => {
      const input = document.getElementById(id);
      if (input && (overwrite || !input.value)) input.value = settings[key] || '';
    });
  });
}
function showLLMSettingsModal() {
  const settings = loadLLMSettings();
  let modal = document.getElementById('llm-settings-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'llm-settings-modal';
    modal.className = 'continuation-modal';
    document.body.appendChild(modal);
  }
  modal.innerHTML =
    '<form class="continuation-shell workflow-modal-shell settings-modal-shell" id="llm-settings-form" role="dialog" aria-modal="true" aria-label="LLM Settings">' +
      '<div class="continuation-head"><div><div class="continuation-kicker">Shared Connection</div><div class="continuation-title">LLM Settings</div><div class="continuation-sub">Used automatically by Diagnose, trace conversion, Simulation, and MCP reruns.</div></div><button class="continuation-close" type="button" data-close-workflow aria-label="Close">×</button></div>' +
      '<div class="workflow-modal-body settings-fields">' +
        '<label class="settings-field"><span class="continuation-label">Base URL</span><input class="composer-input" id="llm-settings-base-url" inputmode="url" placeholder="https://api.example.com/v1"></label>' +
        '<label class="settings-field"><span class="continuation-label">API Key</span>' + passwordField('llm-settings-api-key', 'API key', '') + '</label>' +
        '<label class="settings-field"><span class="continuation-label">Model</span><input class="composer-input" id="llm-settings-model" placeholder="Model name"></label>' +
        '<p class="workflow-copy">URL and model are saved in this browser. The API key is kept only for this browser tab and is cleared when the tab closes.</p>' +
      '</div>' +
      '<div class="continuation-actions"><button class="button" type="button" data-clear-llm-settings>Clear</button><div class="continuation-action-group"><button class="button primary" type="submit">Save Settings</button><button class="button" type="button" data-close-workflow>Close</button></div></div>' +
    '</form>';
  document.getElementById('llm-settings-base-url').value = settings.base_url;
  document.getElementById('llm-settings-api-key').value = settings.api_key;
  document.getElementById('llm-settings-model').value = settings.model;
  bindPasswordToggles(modal);
  modal.querySelectorAll('[data-close-workflow]').forEach(btn => btn.onclick = () => closeWorkflowModal('llm-settings-modal'));
  modal.querySelector('[data-clear-llm-settings]').onclick = () => {
    persistLLMSettings({base_url: '', api_key: '', model: ''});
    document.getElementById('llm-settings-base-url').value = '';
    document.getElementById('llm-settings-api-key').value = '';
    document.getElementById('llm-settings-model').value = '';
    hydrateLLMInputs(true);
    notify('LLM settings cleared');
  };
  modal.querySelector('#llm-settings-form').onsubmit = event => {
    event.preventDefault();
    const saved = persistLLMSettings({
      base_url: document.getElementById('llm-settings-base-url').value,
      api_key: document.getElementById('llm-settings-api-key').value,
      model: document.getElementById('llm-settings-model').value
    });
    if (!saved) return;
    hydrateLLMInputs(true);
    closeWorkflowModal('llm-settings-modal');
    notify('LLM settings saved');
  };
  modal.onclick = event => { if (event.target === modal) closeWorkflowModal('llm-settings-modal'); };
  modal.classList.add('visible');
  window.requestAnimationFrame(() => document.getElementById('llm-settings-base-url')?.focus());
}
function showUploadModal() {
  let modal = document.getElementById('upload-trace-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'upload-trace-modal';
    modal.className = 'continuation-modal';
    document.body.appendChild(modal);
  }
  modal.innerHTML =
    '<div class="continuation-shell workflow-modal-shell" role="dialog" aria-modal="true" aria-label="Upload trajectories">' +
      '<div class="continuation-head"><div><div class="continuation-kicker">Local Ingest</div><div class="continuation-title">Upload trajectories</div><div class="continuation-sub">JSON, JSONL, message logs, framework exports, and AgentErrorBench rows</div></div><button class="continuation-close" type="button" data-close-workflow aria-label="Close">×</button></div>' +
      '<div class="workflow-modal-body">' +
        '<label class="upload-drop" id="upload-drop"><span><strong>Choose or drop a file</strong><br><span class="workflow-copy">Maximum 25 MB. Imported traces stay in the active local store.</span></span><input id="upload-file" type="file" accept=".json,.jsonl,application/json" hidden></label>' +
        '<label class="composer-check"><input type="checkbox" id="upload-allow-llm" checked> Use LLM fallback when deterministic adapters cannot recognize the format</label>' +
        '<details><summary class="workflow-copy">Optional LLM override</summary><div class="workflow-grid">' +
          '<input class="composer-input wide" id="upload-base-url" placeholder="Base URL (otherwise AGENTDEBUG_LLM_BASE_URL)">' +
          passwordField('upload-api-key', 'API key from LLM Settings', '') +
          '<input class="composer-input" id="upload-model" placeholder="Model">' +
        '</div></details>' +
        '<div class="continuation-inline-status" id="upload-status">Choose a file to begin.</div>' +
      '</div>' +
      '<div class="continuation-actions"><span class="workflow-copy"><a href="/api/v1/schema" target="_blank" rel="noopener">Schema reference</a></span><button class="button" type="button" data-close-workflow>Close</button></div>' +
    '</div>';
  modal.classList.add('visible');
  hydrateLLMInputs(false);
  bindPasswordToggles(modal);
  modal.querySelectorAll('[data-close-workflow]').forEach(btn => btn.onclick = () => closeWorkflowModal('upload-trace-modal'));
  modal.onclick = event => { if (event.target === modal) closeWorkflowModal('upload-trace-modal'); };
  const input = document.getElementById('upload-file');
  const drop = document.getElementById('upload-drop');
  drop.onclick = () => input?.click();
  input.onchange = () => uploadTrajectoryFile(input.files?.[0]);
  drop.ondragover = event => { event.preventDefault(); drop.classList.add('dragging'); };
  drop.ondragleave = () => drop.classList.remove('dragging');
  drop.ondrop = event => {
    event.preventDefault();
    drop.classList.remove('dragging');
    uploadTrajectoryFile(event.dataTransfer?.files?.[0]);
  };
}
async function uploadTrajectoryFile(file) {
  if (!file) return;
  const status = document.getElementById('upload-status');
  if (status) status.textContent = 'Reading ' + file.name + '...';
  try {
    const content = await file.text();
    if (status) status.textContent = 'Converting and saving trajectories...';
    const response = await fetch('/api/v1/traces/upload', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        content,
        filename: file.name,
        allow_llm: Boolean(document.getElementById('upload-allow-llm')?.checked),
        base_url: document.getElementById('upload-base-url')?.value || '',
        api_key: document.getElementById('upload-api-key')?.value || '',
        model: document.getElementById('upload-model')?.value || ''
      })
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : JSON.stringify(payload.detail || 'upload failed'));
    if (status) status.textContent = 'Imported ' + payload.count + ' trace(s): ' + (payload.imported || []).join(', ');
    const overview = await api('/api/v1/overview');
    BOOTSTRAP.overview = overview;
    BOOTSTRAP.traces = overview.traces || (payload.imported || []);
    TRACE_CATALOG = (overview.trace_catalog || []).map(item => ({...item, error_count: Number(item.error_count || item.finding_count || 0)}));
    renderTraceList(BOOTSTRAP.traces, CURRENT_TRACE_ID);
    notify('Imported ' + payload.count + ' trace' + (payload.count === 1 ? '' : 's'));
    const first = (payload.imported || [])[0];
    if (first) {
      window.setTimeout(async () => {
        closeWorkflowModal('upload-trace-modal');
        const loaded = await selectTrace(first, document.querySelector('.run[data-tid="' + cssEscape(first) + '"]'));
        if (loaded) history.pushState({view: 'trace', traceId: first}, '', '/trace/' + encodeURIComponent(first));
      }, 450);
    }
  } catch (error) {
    if (status) status.textContent = 'Upload failed: ' + (error.message || error);
    notify('Upload failed');
  }
}
function bindDiagnosePipelineButton() {
  document.querySelectorAll('[data-open-diagnose]').forEach(button => {
    button.onclick = () => showDiagnosePipelineModal();
  });
}
async function showDiagnosePipelineModal() {
  if (!CURRENT_TRACE_ID) return;
  const fallbackOptions = {
    modes: ['heuristic', 'judge', 'deep', 'gui-rca'],
    attributors: ['none', 'heuristic', 'all_at_once', 'step_by_step', 'binary_search', 'counterfactual'],
    recoveries: ['none', 'deepdebug', 'reflexion', 'critic', 'self_refine', 'auto_manual', 'saga_rollback'],
    rule_packs: ['auto', 'core', 'agenterrorbench', 'gui', 'all'],
    llm_configured: false,
    llm_model: ''
  };
  let options = fallbackOptions;
  try {
    options = await api('/api/v1/diagnose/options');
  } catch (error) {
    notify('Using built-in Diagnose options: ' + (error.message || error));
  }
  let modal = document.getElementById('diagnose-pipeline-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'diagnose-pipeline-modal';
    modal.className = 'continuation-modal';
    document.body.appendChild(modal);
  }
  modal.innerHTML =
    '<div class="continuation-shell workflow-modal-shell" role="dialog" aria-modal="true" aria-label="Diagnose Pipeline">' +
      '<div class="continuation-head"><div><div class="continuation-kicker">Detect → Attribute → Recover</div><div class="continuation-title">Diagnose Pipeline</div><div class="continuation-sub">' + escapeHtml(CURRENT_TRACE_ID) + '</div></div><button class="continuation-close" type="button" data-close-workflow aria-label="Close">×</button></div>' +
      '<div class="workflow-modal-body"><div class="workflow-grid">' +
        pipelineSelect('diagnose-mode', 'Detect', diagnoseOptionPairs(options.modes, 'heuristic')) +
        pipelineSelect('diagnose-attributor', 'Attribute', diagnoseOptionPairs(options.attributors, 'heuristic')) +
        pipelineSelect('diagnose-recovery', 'Recover', diagnoseOptionPairs(options.recoveries, 'none')) +
        pipelineSelect('diagnose-rule-pack', 'Rule pack', diagnoseOptionPairs(options.rule_packs, 'auto')) +
        '<input class="composer-input wide" id="diagnose-base-url" placeholder="LLM Base URL (optional for heuristic)">' +
        passwordField('diagnose-api-key', 'API key from LLM Settings', '') +
        '<input class="composer-input" id="diagnose-model" placeholder="Model">' +
        '<input class="composer-input" id="diagnose-embedding-model" placeholder="Embedding model (optional for DeepDebug)">' +
      '</div><p class="workflow-copy">Heuristic runs entirely locally. Judge, DeepDebug, GUI RCA, LLM attribution, and Self refine require an OpenAI-compatible endpoint.</p><div class="continuation-inline-status" id="diagnose-status">' + escapeHtml(options.llm_configured ? ('Ready · configured model: ' + (options.llm_model || 'default')) : 'Ready · no configured LLM endpoint') + '</div></div>' +
      '<div class="continuation-actions"><span></span><div class="continuation-action-group"><button class="button primary" type="button" data-run-diagnose>Run Pipeline</button><button class="button" type="button" data-close-workflow>Close</button></div></div>' +
    '</div>';
  modal.classList.add('visible');
  hydrateLLMInputs(false);
  bindPasswordToggles(modal);
  modal.querySelectorAll('[data-close-workflow]').forEach(btn => btn.onclick = () => closeWorkflowModal('diagnose-pipeline-modal'));
  modal.onclick = event => { if (event.target === modal) closeWorkflowModal('diagnose-pipeline-modal'); };
  modal.querySelector('[data-run-diagnose]').onclick = event => runDiagnosePipeline(event.currentTarget);
  bindDiagnoseChoiceConstraints();
}
function diagnoseOptionPairs(values, preferred) {
  const labels = {
    heuristic:'Heuristic', judge:'LLM Judge', deep:'DeepDebug', 'gui-rca':'GUI RCA',
    none:'None', all_at_once:'All at once', step_by_step:'Step by step',
    binary_search:'Binary search', counterfactual:'Counterfactual', deepdebug:'DeepDebug',
    reflexion:'Reflexion', critic:'Critic', self_refine:'Self refine',
    auto_manual:'Auto manual', saga_rollback:'Saga rollback', auto:'Auto', core:'Core',
    agenterrorbench:'AgentErrorBench', gui:'GUI', all:'All'
  };
  const ordered = Array.from(values || []);
  if (ordered.includes(preferred)) ordered.splice(ordered.indexOf(preferred), 1), ordered.unshift(preferred);
  return ordered.map(value => [value, labels[value] || value]);
}
function bindDiagnoseChoiceConstraints() {
  const mode = document.getElementById('diagnose-mode');
  const attributor = document.getElementById('diagnose-attributor');
  const recovery = document.getElementById('diagnose-recovery');
  if (!mode || !attributor || !recovery) return;
  const apply = () => {
    const deep = mode.value === 'deep';
    Array.from(attributor.options).forEach(option => { option.disabled = deep && option.value !== 'none'; });
    Array.from(recovery.options).forEach(option => { option.disabled = deep && !['none', 'deepdebug'].includes(option.value); });
    if (deep) {
      attributor.value = 'none';
      if (recovery.value !== 'none') recovery.value = 'deepdebug';
    }
  };
  mode.onchange = () => {
    if (mode.value === 'deep') recovery.value = 'deepdebug';
    apply();
  };
  apply();
}
function pipelineSelect(id, label, values) {
  return '<label class="composer-section"><span class="continuation-label">' + label + '</span><select class="composer-input" id="' + id + '">' + values.map(item => '<option value="' + item[0] + '">' + item[1] + '</option>').join('') + '</select></label>';
}
function updateTraceCatalogFromAnalysis(data) {
  const trajectory = data?.trajectory || {};
  const report = data?.report || {};
  const traceId = trajectory.trace_id || '';
  if (!traceId) return;
  const findings = Array.isArray(report.findings) ? report.findings : [];
  const errorEventIds = new Set(findings.map(item => item?.event_id).filter(Boolean));
  const existingIndex = TRACE_CATALOG.findIndex(item => item.trace_id === traceId);
  const existing = existingIndex >= 0 ? TRACE_CATALOG[existingIndex] : {trace_id: traceId};
  const firstFinding = findings[0] || {};
  const next = {
    ...existing,
    event_count: Array.isArray(trajectory.events) ? trajectory.events.length : Number(existing.event_count || 0),
    finding_count: findings.length,
    error_count: errorEventIds.size,
    status: findings.length ? 'failed' : 'passed',
    first_error_step: findings.reduce((first, item) => {
      const step = Number(item?.step_index);
      return Number.isFinite(step) ? (first === null ? step : Math.min(first, step)) : first;
    }, null),
    root_cause_step_index: report.root_cause_step_index,
    root_cause_found: Boolean(report.root_cause_event_id),
    summary: report.summary || '',
    top_family: firstFinding?.failure_mode?.family || '',
    top_error_type: firstFinding?.failure_mode?.mode_id || ''
  };
  if (existingIndex >= 0) TRACE_CATALOG.splice(existingIndex, 1, next);
  else TRACE_CATALOG.unshift(next);
}
async function applyDiagnoseResult(traceId, reportId) {
  if (!reportId) throw new Error('pipeline completed without a report id');
  const data = await api(
    '/api/v1/traces/' + encodeURIComponent(traceId) +
    '?report_id=' + encodeURIComponent(reportId)
  );
  if (data?.report?.report_id !== reportId || data?.report_source !== 'stored') {
    throw new Error('the saved report could not be selected in the workspace');
  }
  CURRENT_TRACE_ID = traceId;
  CURRENT_TRACE_DATA = data;
  CURRENT_VIEW = 'trace';
  updateTraceCatalogFromAnalysis(data);
  const traceIds = (BOOTSTRAP && BOOTSTRAP.traces) || TRACE_CATALOG.map(item => item.trace_id);
  renderTraceList(traceIds, traceId);
  renderTrace(data.trajectory, data.report);
  syncDebugBranches(traceId, true);
}
async function runDiagnosePipeline(button) {
  const status = document.getElementById('diagnose-status');
  const traceId = CURRENT_TRACE_ID;
  button.disabled = true;
  button.textContent = 'Running...';
  if (status) status.textContent = 'Running detect, attribution, and recovery stages...';
  try {
    const response = await fetch('/api/v1/traces/' + encodeURIComponent(traceId) + '/diagnose', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        mode: document.getElementById('diagnose-mode')?.value,
        attributor: document.getElementById('diagnose-attributor')?.value,
        recovery: document.getElementById('diagnose-recovery')?.value,
        rule_pack: document.getElementById('diagnose-rule-pack')?.value,
        base_url: document.getElementById('diagnose-base-url')?.value || '',
        api_key: document.getElementById('diagnose-api-key')?.value || '',
        model: document.getElementById('diagnose-model')?.value || '',
        embedding_model: document.getElementById('diagnose-embedding-model')?.value || ''
      })
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || 'diagnose failed');
    if (status) status.textContent = 'Report saved. Applying it to the workspace...';
    await applyDiagnoseResult(traceId, payload.report?.report_id);
    if (status) status.textContent = 'Applied in ' + payload.duration_ms + ' ms.';
    closeWorkflowModal('diagnose-pipeline-modal');
    notify('Diagnose report applied to workspace');
  } catch (error) {
    if (status) status.textContent = 'Diagnose did not update the workspace: ' + (error.message || error);
    notify('Diagnose workspace update failed');
  } finally {
    button.disabled = false;
    button.textContent = 'Run Pipeline';
  }
}
function applyTheme(theme) {
  const resolved = theme === 'light' ? 'light' : 'dark';
  document.body.classList.toggle('theme-light', resolved === 'light');
  const btn = document.getElementById('theme-btn');
  if (btn) btn.textContent = resolved === 'light' ? 'Theme: Light' : 'Theme: Dark';
}
function initTheme() {
  const saved = localStorage.getItem('agentdebugx-theme') || 'dark';
  applyTheme(saved);
}
function renderRuntimeStatus() {
  const rerun = UI_STATUS?.rerun || {};
  const title = document.getElementById('runtime-status-title');
  const copy = document.getElementById('runtime-status-copy');
  const meta = document.getElementById('runtime-status-meta');
  if (title) title.textContent = rerun.configured ? 'Local UI · Runner Ready' : 'Local UI';
  if (copy) copy.textContent = rerun.configuration_error || (rerun.configured
    ? 'Traces stay in the local store. Live reruns use the server-configured agent environment.'
    : 'Traces stay in the local store. No live agent runner is configured.');
  if (meta) meta.innerHTML = '<span class="chip good">local store</span>' +
    '<span class="chip ' + (rerun.configured ? 'cyan' : 'warn') + '">' + escapeHtml(rerun.configured ? (rerun.transport || 'runner') : 'rerun unavailable') + '</span>' +
    '<span class="chip">' + escapeHtml(rerun.checkpoint_policy || 'from_start') + '</span>';
}
function bindTopActions() {
  renderRuntimeStatus();
  const offlineBtn = document.getElementById('offline-status-btn');
  const offlinePopover = document.getElementById('offline-popover');
  if (offlineBtn && offlinePopover) {
    offlineBtn.onclick = event => {
      event.stopPropagation();
      const next = !offlinePopover.classList.contains('visible');
      offlinePopover.classList.toggle('visible', next);
      offlineBtn.setAttribute('aria-expanded', next ? 'true' : 'false');
      offlinePopover.setAttribute('aria-hidden', next ? 'false' : 'true');
    };
    offlinePopover.onclick = event => event.stopPropagation();
    document.addEventListener('click', () => {
      offlinePopover.classList.remove('visible');
      offlineBtn.setAttribute('aria-expanded', 'false');
      offlinePopover.setAttribute('aria-hidden', 'true');
    });
  }
  const overviewBtn = document.getElementById('overview-btn');
  if (overviewBtn) {
    overviewBtn.onclick = () => {
      openWorkspaceDrawer('overview', overviewBtn);
    };
  }
  const runSearch = document.getElementById('run-search');
  if (runSearch) {
    runSearch.oninput = () => renderTraceList((BOOTSTRAP && BOOTSTRAP.traces) || TRACE_CATALOG.map(item => item.trace_id), CURRENT_TRACE_ID);
    runSearch.onkeydown = event => {
      if (event.key !== 'Enter') return;
      const first = document.querySelector('.run[data-tid]');
      if (first) {
        event.preventDefault();
        first.click();
      }
    };
  }
  const filterBtn = document.querySelector('.run-filter-btn');
  if (filterBtn) {
    filterBtn.onclick = () => {
      document.body.classList.toggle('run-compact');
      notify(document.body.classList.contains('run-compact') ? 'Run list compact mode' : 'Run list detail mode');
    };
  }
  const crumb = document.querySelector('.crumb');
  if (crumb) {
    crumb.title = 'Open Project Overview';
    crumb.style.cursor = 'pointer';
    crumb.onclick = () => {
      openWorkspaceDrawer('overview', overviewBtn || crumb);
    };
  }
  document.getElementById('theme-btn').onclick = () => {
    const next = document.body.classList.contains('theme-light') ? 'dark' : 'light';
    localStorage.setItem('agentdebugx-theme', next);
    applyTheme(next);
    notify(next === 'light' ? 'Light theme enabled' : 'Dark theme enabled');
  };
  document.getElementById('llm-settings-btn').onclick = () => showLLMSettingsModal();
  document.getElementById('upload-btn').onclick = () => showUploadModal();
  document.getElementById('analyze-btn').onclick = async () => {
    const active = document.querySelector('.run.active');
    const btn = document.getElementById('analyze-btn');
    if (!CURRENT_TRACE_ID || !active) {
      notify('Select a run before analysis');
      return;
    }
    btn.disabled = true;
    const previous = btn.textContent;
    btn.textContent = 'Analyzing...';
    try {
      await selectTrace(CURRENT_TRACE_ID, active);
      notify('Analysis refreshed');
    } finally {
      btn.disabled = false;
      btn.textContent = previous;
    }
  };
  bindHubButton();
  document.getElementById('workspace-drawer-scrim')?.addEventListener('click', () => closeWorkspaceDrawer());
  document.querySelectorAll('[data-close-drawer]').forEach(button => {
    button.onclick = () => closeWorkspaceDrawer();
  });
  document.addEventListener('keydown', event => {
    const tag = (event.target && event.target.tagName || '').toLowerCase();
    if (event.key === 'Escape' && ACTIVE_DRAWER) {
      event.preventDefault();
      closeWorkspaceDrawer();
      return;
    }
    if (tag === 'input' || tag === 'textarea' || event.target?.isContentEditable) return;
    if (CURRENT_VIEW !== 'trace') return;
    if (event.key === 'ArrowLeft') {
      event.preventDefault();
      moveSelectedEvent(-1, event.shiftKey);
    } else if (event.key === 'ArrowRight') {
      event.preventDefault();
      moveSelectedEvent(1, event.shiftKey);
    } else if (event.key.toLowerCase() === 'r' && CURRENT_TRACE_DATA?.report?.root_cause_event_id) {
      event.preventDefault();
      CURRENT_EXPANDED_EVENT_ID = CURRENT_TRACE_DATA.report.root_cause_event_id;
      renderTrace(CURRENT_TRACE_DATA.trajectory, CURRENT_TRACE_DATA.report);
      pulseEditorStage();
    } else if (event.key.toLowerCase() === 'f') {
      event.preventDefault();
      pulseEditorStage();
    } else if (event.key === '+' || event.key === '=') {
      event.preventDefault();
      TIMELINE_ZOOM = Math.min(1.8, TIMELINE_ZOOM + 0.15);
      renderTrace(CURRENT_TRACE_DATA.trajectory, CURRENT_TRACE_DATA.report);
    } else if (event.key === '-' || event.key === '_') {
      event.preventDefault();
      TIMELINE_ZOOM = Math.max(0.65, TIMELINE_ZOOM - 0.15);
      renderTrace(CURRENT_TRACE_DATA.trajectory, CURRENT_TRACE_DATA.report);
    } else if (event.key === ' ') {
      event.preventDefault();
      moveSelectedEvent(1, false);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      document.body.classList.remove('diagnosis-collapsed');
      const offlinePopover = document.getElementById('offline-popover');
      const offlineBtn = document.getElementById('offline-status-btn');
      const continuationModal = document.getElementById('continuation-modal');
      if (offlinePopover) offlinePopover.classList.remove('visible');
      if (offlineBtn) offlineBtn.setAttribute('aria-expanded', 'false');
      if (offlinePopover) offlinePopover.setAttribute('aria-hidden', 'true');
      if (continuationModal) continuationModal.classList.remove('visible');
      notify('Focus overlays cleared');
    }
  });
}
function bindHubButton() {
  const hubButton = document.getElementById('hub-btn');
  if (hubButton) hubButton.onclick = () => openWorkspaceDrawer('hub', hubButton);
}
function bindDiscussionButton() {
  const button = document.getElementById('discussion-btn');
  if (button) button.onclick = () => openWorkspaceDrawer('discussion', button);
}
function field(label, value, isError) {
  return '<div class="field ' + (isError ? 'error' : '') + '"><div class="field-label">' + escapeHtml(label) + '</div><div class="field-value">' + escapeHtml(value || '-') + '</div></div>';
}
function renderFinding(f) {
  const mode = f.failure_mode || {};
  const family = mode.family || '';
  const meta = f.metadata || {};
  const targetAttrs = (f.event_id ? ' data-event-id="' + escapeHtml(f.event_id) + '"' : '') +
    (f.step_index !== null && f.step_index !== undefined ? ' data-step-index="' + escapeHtml(f.step_index) + '"' : '');
  let html = '<div class="finding clickable"' + targetAttrs + ' title="Click to open the related event">';
  html += '<div class="finding-title"><div><div class="mode">' + escapeHtml(mode.mode_id || '') + '</div>';
  html += '<div class="event-type">' + escapeHtml(family) + ' / step ' + escapeHtml(f.step_index ?? '-') + ' / ' + escapeHtml(f.agent_name || '-') + '</div></div>';
  html += '<div class="confidence">' + (typeof f.confidence === 'number' ? Math.round(f.confidence * 100) + '%' : 'n/a') + '</div></div>';
  if (meta.rule_pack || meta.rule_id) html += '<div class="lane-meta">' + ruleMeta(meta.rule_pack || '', meta.rule_id || '') + '</div>';
  if (meta.finding_source_label) html += '<div class="evidence">Source: ' + escapeHtml(meta.finding_source_label) + '</div>';
  if (meta.trigger_reason) html += '<div class="evidence">Triggered by: ' + escapeHtml(meta.trigger_reason) + '</div>';
  if (meta.why_reported) html += '<div class="evidence">Why flagged: ' + escapeHtml(meta.why_reported) + '</div>';
  if (f.suggestion) html += '<div class="suggestion">' + escapeHtml(f.suggestion) + '</div>';
  if ((f.evidence || []).length) html += '<div class="evidence">Evidence: ' + escapeHtml((f.evidence || []).join('; ')) + '</div>';
  if (meta.confidence_basis) html += '<div class="evidence">Confidence basis: ' + escapeHtml(meta.confidence_basis) + '</div>';
  html += '</div>';
  return html;
}
function ruleMeta(rulePack, ruleId) {
  let html = '';
  if (rulePack) html += '<span class="chip cyan">pack: ' + escapeHtml(rulePack) + '</span>';
  if (ruleId) html += '<span class="chip">rule: ' + escapeHtml(ruleId) + '</span>';
  return html;
}
function chartPalette(idx) {
  const colors = ['#ff6b7a', '#f0b75a', '#6bd6d8', '#6fcf97', '#b7a4ff', '#e9e3d4', '#7a8480'];
  return colors[idx % colors.length];
}
function renderDonutCard(title, centerLabel, items, nameKey, valueKey, variant) {
  const total = (items || []).reduce((sum, item) => sum + Number(item[valueKey] || 0), 0);
  const cardVariant = variant || '';
  let html = '<div class="donut-card ' + escapeHtml(cardVariant) + '">';
  html += '<div class="donut-label">' + escapeHtml(title) + '</div>';
  html += '<div class="chart-subtitle">' + escapeHtml(donutSubtitle(title)) + '</div>';
  html += '<div class="donut-shell">';
  html += '<div class="donut-figure">' + donutSvg(items || [], nameKey, valueKey) +
    '<div class="donut-center"><div><strong>' + escapeHtml(total) + '</strong><span>' + escapeHtml(centerLabel) + '</span></div></div></div>';
  html += '<div class="legend-stack">';
  if (!items || !items.length) {
    html += '<div class="empty" style="padding:12px 0;">No data.</div>';
  } else {
    html += items.map((item, idx) => {
      const label = item[nameKey] || '-';
      const value = Number(item[valueKey] || 0);
      return '<div class="legend-row" data-tooltip="' + escapeHtml(chartTooltipHtml(label, value, total)) + '">' +
        '<span class="legend-swatch" style="background:' + chartPalette(idx) + ';"></span>' +
        '<span class="legend-name">' + escapeHtml(label) + '</span>' +
        '<span class="legend-value">' + escapeHtml(value) + '</span>' +
        '</div>';
    }).join('');
  }
  html += '</div></div></div>';
  return html;
}
function donutSubtitle(title) {
  if (title.includes('Error Family')) return 'What failed most across the analyzed traces.';
  if (title.includes('Framework')) return 'Framework mix represented in this batch.';
  return 'Distribution across the current local batch.';
}
function donutSvg(items, nameKey, valueKey) {
  const total = (items || []).reduce((sum, item) => sum + Number(item[valueKey] || 0), 0);
  const radius = 50;
  const stroke = 18;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;
  let svg = '<svg viewBox="0 0 132 132" aria-hidden="true">' +
    '<circle cx="66" cy="66" r="' + radius + '" fill="none" stroke="#212525" stroke-width="' + stroke + '"></circle>';
  if (total > 0) {
    svg += items.map((item, idx) => {
      const value = Number(item[valueKey] || 0);
      const segment = (value / total) * circumference;
      const label = item[nameKey] || '-';
      const part = '<circle class="donut-segment" data-tooltip="' + escapeHtml(chartTooltipHtml(label, value, total)) + '" cx="66" cy="66" r="' + radius + '" fill="none" stroke="' + chartPalette(idx) + '" stroke-width="' + stroke + '" stroke-linecap="butt" stroke-dasharray="' + segment + ' ' + (circumference - segment) + '" stroke-dashoffset="' + (-offset) + '"></circle>';
      offset += segment;
      return part;
    }).join('');
  }
  svg += '</svg>';
  return svg;
}
function chartTooltipHtml(label, value, total) {
  const pct = total ? ((Number(value || 0) / total) * 100).toFixed(1) : '0.0';
  return '<strong>' + escapeHtml(label || '-') + '</strong><span>' + escapeHtml(value) + ' items · ' + pct + '% of total</span>';
}
function bindChartTooltips() {
  const tooltip = document.getElementById('chart-tooltip');
  if (!tooltip) return;
  document.querySelectorAll('[data-tooltip]').forEach(el => {
    el.onmouseenter = event => {
      tooltip.textContent = tooltipText(el.getAttribute('data-tooltip') || '');
      tooltip.classList.add('visible');
      positionChartTooltip(event, tooltip);
    };
    el.onmousemove = event => positionChartTooltip(event, tooltip);
    el.onmouseleave = () => tooltip.classList.remove('visible');
  });
}
function tooltipText(value) {
  return String(value || '')
    .replace(/<[^>]*>/g, ' ')
    .replace(/&nbsp;/g, ' ')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ')
    .trim();
}
function positionChartTooltip(event, tooltip) {
  const pad = 14;
  const width = tooltip.offsetWidth || 220;
  const height = tooltip.offsetHeight || 56;
  const left = Math.min(event.clientX + pad, window.innerWidth - width - pad);
  const top = Math.min(event.clientY + pad, window.innerHeight - height - pad);
  tooltip.style.left = Math.max(pad, left) + 'px';
  tooltip.style.top = Math.max(pad, top) + 'px';
}
function renderDistributionList(items, nameKey, valueKey) {
  if (!items || !items.length) return '<div class="empty" style="padding:12px 0;">No findings yet.</div>';
  const max = Math.max(...items.map(item => Number(item[valueKey] || 0)), 1);
  return '<div class="dist-list">' + items.map(item => {
    const value = Number(item[valueKey] || 0);
    const width = Math.max(8, (value / max) * 100);
    return '<div class="dist-row">' +
      '<div class="dist-head"><span class="dist-name">' + escapeHtml(item[nameKey] || '-') + '</span><span class="legend-value">' + escapeHtml(value) + '</span></div>' +
      '<div class="dist-track"><div class="dist-fill" style="width:' + width + '%;"></div></div>' +
      '</div>';
  }).join('') + '</div>';
}
function renderStageDistribution(items) {
  const total = (items || []).reduce((sum, item) => sum + Number(item.count || 0), 0);
  if (!total) return '<div class="empty" style="padding:12px 0;">No failures yet.</div>';
  const order = ['early', 'middle', 'late', 'none'];
  const labels = {early:'Early', middle:'Middle', late:'Late', none:'No finding'};
  let html = '<div class="stage-bar">';
  html += order.map(stage => {
    const item = (items || []).find(entry => entry.stage === stage) || {count: 0};
    const width = (Number(item.count || 0) / total) * 100;
    return '<div class="stage-segment ' + stage + '" style="width:' + width + '%;"></div>';
  }).join('');
  html += '</div><div class="legend-stack">';
  html += order.map(stage => {
    const item = (items || []).find(entry => entry.stage === stage) || {count: 0};
    return '<div class="legend-row"><span class="legend-swatch stage-segment ' + stage + '"></span><span class="legend-name">' + labels[stage] + '</span><span class="legend-value">' + escapeHtml(item.count || 0) + '</span></div>';
  }).join('');
  html += '</div>';
  return html;
}
function renderRootCauseDistribution(items, avgStep) {
  const active = (items || []).filter(item => Number(item.count || 0) > 0);
  if (!active.length) return '<div class="empty" style="padding:12px 0;">No root cause positions yet.</div>';
  const width = 620;
  const height = 220;
  const left = 34;
  const right = 18;
  const top = 20;
  const bottom = 38;
  const plotW = width - left - right;
  const plotH = height - top - bottom;
  const maxStep = Math.max(...items.map(item => Number(item.step || 0)), 1);
  const maxCount = Math.max(...items.map(item => Number(item.count || 0)), 1);
  const barW = Math.max(3, Math.min(18, plotW / Math.max(1, items.length) - 2));
  const xFor = step => left + ((Number(step) - 1) / Math.max(1, maxStep - 1)) * plotW;
  const yFor = count => top + plotH - (Number(count || 0) / maxCount) * plotH;
  const smooth = items.map((item, idx) => {
    const prev = items[Math.max(0, idx - 1)] || item;
    const next = items[Math.min(items.length - 1, idx + 1)] || item;
    return {
      step: item.step,
      count: (Number(prev.count || 0) + Number(item.count || 0) * 2 + Number(next.count || 0)) / 4
    };
  });
  let svg = '<svg class="mini-chart" viewBox="0 0 ' + width + ' ' + height + '" role="img" style="height:220px;">';
  svg += '<line x1="' + left + '" y1="' + (top + plotH) + '" x2="' + (width - right) + '" y2="' + (top + plotH) + '" stroke="#2d3130"></line>';
  svg += '<line x1="' + left + '" y1="' + top + '" x2="' + left + '" y2="' + (top + plotH) + '" stroke="#2d3130"></line>';
  items.forEach(item => {
    const count = Number(item.count || 0);
    const x = xFor(item.step) - barW / 2;
    const y = yFor(count);
    const h = top + plotH - y;
    const fill = count ? 'var(--cyan)' : '#202626';
    svg += '<rect x="' + x + '" y="' + y + '" width="' + barW + '" height="' + h + '" rx="3" fill="' + fill + '" opacity="' + (count ? '.82' : '.35') + '"><title>step ' + escapeHtml(item.step) + ': ' + escapeHtml(count) + '</title></rect>';
  });
  const path = smooth.map((item, idx) => (idx === 0 ? 'M' : 'L') + xFor(item.step) + ' ' + yFor(item.count)).join(' ');
  svg += '<path d="' + path + '" fill="none" stroke="var(--amber)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"></path>';
  if (typeof avgStep === 'number') {
    const avgX = xFor(avgStep);
    svg += '<line x1="' + avgX + '" y1="' + top + '" x2="' + avgX + '" y2="' + (top + plotH) + '" stroke="var(--rose)" stroke-dasharray="4 4"></line>';
    svg += '<text class="axis-label" x="' + (avgX + 5) + '" y="' + (top + 12) + '">avg ' + escapeHtml(avgStep) + '</text>';
  }
  const labelEvery = Math.max(1, Math.ceil(maxStep / 8));
  items.forEach(item => {
    if (Number(item.step) === 1 || Number(item.step) === maxStep || Number(item.step) % labelEvery === 0) {
      svg += '<text class="axis-label" x="' + xFor(item.step) + '" y="' + (height - 14) + '" text-anchor="middle">' + escapeHtml(item.step) + '</text>';
    }
  });
  svg += '<text class="axis-label" x="' + (width - right) + '" y="' + (height - 2) + '" text-anchor="end">root cause step</text>';
  svg += '<text class="axis-label" x="' + left + '" y="12">count</text>';
  svg += '</svg>';
  svg += '<div class="lane-meta"><span class="chip cyan">bars: exact count per step</span><span class="chip warn">line: smoothed trend</span><span class="chip bad">dashed: average</span></div>';
  return svg;
}
function renderPriorityTraces(items) {
  if (!items || !items.length) return '<div class="empty" style="padding:12px 0;">No failing traces.</div>';
  return '<div class="priority-list">' + items.map(item =>
    '<a class="priority-item" href="/trace/' + encodeURIComponent(item.trace_id || '') + '">' +
    '<div><div class="priority-title">' + escapeHtml(item.trace_id || '') + '</div>' +
    '<div class="priority-copy">' + escapeHtml(truncate(item.summary || '', 120)) + '</div>' +
    '<div class="lane-meta"><span class="chip">' + escapeHtml(item.framework || '-') + '</span><span class="chip warn">step ' + escapeHtml(item.first_error_step ?? '-') + '</span></div></div>' +
    '<span class="chip bad">' + escapeHtml(item.finding_count || 0) + ' findings</span>' +
    '</a>'
  ).join('') + '</div>';
}
function renderHistogram(items, labelKey, valueKey) {
  if (!items || !items.length) return '<div class="empty" style="padding:12px 0;">No histogram data.</div>';
  const max = Math.max(...items.map(item => Number(item[valueKey] || 0)), 1);
  const width = 520;
  const height = 180;
  const barGap = 14;
  const barWidth = (width - 50 - barGap * (items.length - 1)) / Math.max(1, items.length);
  let svg = '<svg class="mini-chart" viewBox="0 0 ' + width + ' ' + height + '" role="img">';
  svg += '<line x1="28" y1="150" x2="' + (width - 8) + '" y2="150" stroke="#2d3130"></line>';
  items.forEach((item, idx) => {
    const value = Number(item[valueKey] || 0);
    const h = (value / max) * 112;
    const x = 34 + idx * (barWidth + barGap);
    const y = 150 - h;
    svg += '<rect x="' + x + '" y="' + y + '" width="' + barWidth + '" height="' + h + '" rx="6" fill="' + chartPalette(idx) + '"></rect>';
    svg += '<text class="axis-label" x="' + (x + barWidth / 2) + '" y="168" text-anchor="middle">' + escapeHtml(item[labelKey]) + '</text>';
    svg += '<text class="axis-label" x="' + (x + barWidth / 2) + '" y="' + (y - 6) + '" text-anchor="middle">' + escapeHtml(value) + '</text>';
  });
  svg += '</svg>';
  return svg;
}
function renderScatter(items) {
  if (!items || !items.length) return '<div class="empty" style="padding:12px 0;">No scatter data.</div>';
  const width = 520;
  const height = 180;
  const maxEvents = Math.max(...items.map(item => Number(item.event_count || 0)), 1);
  const maxFindings = Math.max(...items.map(item => Number(item.finding_count || 0)), 1);
  let svg = '<svg class="mini-chart" viewBox="0 0 ' + width + ' ' + height + '" role="img">';
  svg += '<line x1="36" y1="150" x2="' + (width - 18) + '" y2="150" stroke="#2d3130"></line>';
  svg += '<line x1="36" y1="18" x2="36" y2="150" stroke="#2d3130"></line>';
  items.forEach(item => {
    const x = 36 + (Number(item.event_count || 0) / maxEvents) * (width - 70);
    const y = 150 - (Number(item.finding_count || 0) / maxFindings) * 118;
    const klass = Number(item.finding_count || 0) > 3 ? 'bad' : (Number(item.finding_count || 0) > 0 ? 'warn' : '');
    svg += '<circle class="chart-dot ' + klass + '" cx="' + x + '" cy="' + y + '" r="5"><title>' + escapeHtml((item.trace_id || '') + ': ' + item.event_count + ' events, ' + item.finding_count + ' findings') + '</title></circle>';
  });
  svg += '<text class="axis-label" x="' + (width - 18) + '" y="172" text-anchor="end">events</text>';
  svg += '<text class="axis-label" x="38" y="14">findings</text>';
  svg += '</svg>';
  return svg;
}
initTheme();
bindTopActions();
(async function initializeTraceWorkspace() {
  const requestedView = CURRENT_VIEW;
  const traces = (BOOTSTRAP && BOOTSTRAP.traces) || [];
  const selected = BOOTSTRAP?.selected?.trajectory?.trace_id || null;
  CURRENT_VIEW = 'trace';
  renderTraceList(traces, selected);
  if (BOOTSTRAP?.selected) {
    CURRENT_TRACE_ID = selected;
    CURRENT_TRACE_DATA = BOOTSTRAP.selected;
    CURRENT_EXPANDED_EVENT_ID = BOOTSTRAP.selected_event_id || null;
    renderTrace(BOOTSTRAP.selected.trajectory, BOOTSTRAP.selected.report);
  } else if (traces.length) {
    const first = traces[0];
    const run = document.querySelector('.run[data-tid="' + cssEscape(first) + '"]');
    await selectTrace(first, run);
  } else {
    document.getElementById('detail').innerHTML = '<div class="empty">No traces in store.</div>';
    setRailMode('trace');
  }
  if (requestedView === 'overview') {
    const initialDrawer = window.location.hash === '#cases' ? 'hub' : 'overview';
    const trigger = initialDrawer === 'hub'
      ? document.getElementById('hub-btn')
      : document.getElementById('overview-btn');
    openWorkspaceDrawer(initialDrawer, trigger);
  }
})();
</script>
</body>
</html>
"""
