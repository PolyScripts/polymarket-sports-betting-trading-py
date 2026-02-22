"""
Sports Manual Trading Bot - Web UI + API.
Monitors Polymarket sports markets (football, basketball, hockey, tennis).
User clicks to place bets - fast execution, no automation.
"""
import json
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template_string, jsonify, request
from datetime import datetime

from sports_config import (
    GAMMA_API,
    ALL_SPORTS_TAG_IDS,
    BET_AMOUNT_USD,
    SPORTS_POLL_INTERVAL_SEC,
    ESPORTS_SPORT_CODES,
    LIVE_ONLY,
)
from sports_executor import execute_bet
from sports_websocket import start_live_prices, get_live_price

app = Flask(__name__)
executor = ThreadPoolExecutor(max_workers=8)

# Cache (short TTL so prices update continuously)
_markets_cache = []
_markets_cache_time = 0
CACHE_TTL = 5

# Esports tag ID (slug "esports") - exclude events with this tag
ESPORTS_TAG_IDS = {"64", 64}

_sports_tag_ids_cache = None


def get_sports_only_tag_ids() -> list:
    """Fetch /sports metadata and return tag IDs for non-esports only."""
    global _sports_tag_ids_cache
    if _sports_tag_ids_cache is not None:
        return _sports_tag_ids_cache
    try:
        r = requests.get(f"{GAMMA_API}/sports", timeout=10)
        if r.status_code != 200:
            return ALL_SPORTS_TAG_IDS
        sports = r.json() or []
        tag_ids = set()
        for s in sports:
            sport_code = (s.get("sport") or "").lower()
            if sport_code in ESPORTS_SPORT_CODES:
                continue
            tags_str = s.get("tags") or ""
            for tid in tags_str.replace(" ", "").split(","):
                if tid.isdigit():
                    tag_ids.add(int(tid))
        _sports_tag_ids_cache = list(tag_ids) if tag_ids else ALL_SPORTS_TAG_IDS
        return _sports_tag_ids_cache
    except Exception:
        return ALL_SPORTS_TAG_IDS


def _is_esports_event(ev: dict) -> bool:
    """Check if event is esports (by tags or teams.league)."""
    for t in ev.get("tags") or []:
        tid = t.get("id")
        if tid is not None and str(tid) in ESPORTS_TAG_IDS:
            return True
        if (t.get("slug") or "").lower() == "esports":
            return True
    for team in ev.get("teams") or []:
        league = (team.get("league") or "").lower()
        if league in ESPORTS_SPORT_CODES:
            return True
    return False


def fetch_event_by_slug(slug: str) -> list:
    """Fetch event(s) by slug, including 'More Markets' child. Returns list of events."""
    events = []
    for s in [slug, f"{slug}-more-markets"]:
        try:
            r = requests.get(
                f"{GAMMA_API}/events",
                params={"slug": s, "closed": "false", "limit": 5},
                timeout=10,
            )
            if r.status_code == 200:
                batch = r.json() or []
                events.extend(batch)
        except Exception:
            pass
    return events


def fetch_events_for_tag(tag_id: int) -> list:
    """Fetch active events for a tag."""
    try:
        r = requests.get(
            f"{GAMMA_API}/events",
            params={
                "tag_id": tag_id,
                "closed": "false",
                "limit": 50,
                "order": "id",
                "ascending": False,
            },
            timeout=10,
        )
        if r.status_code == 200:
            return r.json() or []
    except Exception:
        pass
    return []


def fetch_sports_markets() -> list:
    """Fetch live sports markets from Polymarket (sports only, no esports)."""
    global _markets_cache, _markets_cache_time
    now = datetime.now().timestamp()
    if _markets_cache and (now - _markets_cache_time) < CACHE_TTL:
        return _markets_cache

    tag_ids = get_sports_only_tag_ids()
    seen_slugs = set()
    events = []
    # Fetch in parallel for speed
    with ThreadPoolExecutor(max_workers=min(12, len(tag_ids))) as ex:
        futures = {ex.submit(fetch_events_for_tag, tid): tid for tid in tag_ids}
        for f in as_completed(futures):
            try:
                batch = f.result()
                for ev in batch:
                    slug = ev.get("slug") or ev.get("id")
                    if slug and slug not in seen_slugs:
                        if _is_esports_event(ev):
                            continue
                        if LIVE_ONLY and not ev.get("live"):
                            continue
                        seen_slugs.add(slug)
                        events.append(ev)
            except Exception:
                pass

    # Sort by liquidity/volume
    def score(e):
        liq = float(e.get("liquidity") or e.get("liquidityClob") or 0)
        vol = float(e.get("volume") or e.get("volume24hr") or 0)
        return liq + vol * 2

    events.sort(key=score, reverse=True)
    _markets_cache = events
    _markets_cache_time = now

    # Start/refresh WebSocket for live prices (top 500 assets by liquidity)
    try:
        token_ids = []
        for ev in events:
            for m in ev.get("markets", []):
                for tid in _parse_token_ids(m.get("clobTokenIds", "[]")):
                    if tid:
                        token_ids.append(tid)
        if token_ids:
            start_live_prices(token_ids)
    except Exception:
        pass
    return events


def _market_to_buttons(m: dict, title: str) -> list:
    """Convert market to list of {outcome, price, token_id} buttons."""
    outcomes = _parse_outcomes(m.get("outcomes", "[]"))
    prices = _parse_prices(m.get("outcomePrices", m.get("bestBid", "0.5")))
    token_ids = _parse_token_ids(m.get("clobTokenIds", "[]"))
    buttons = []
    for i, out in enumerate(outcomes):
        if i < len(token_ids) and token_ids[i]:
            buttons.append({
                "outcome": out,
                "price": prices[i] if i < len(prices) else 0.5,
                "token_id": token_ids[i],
            })
    return buttons


def _classify_market_type(m: dict) -> str:
    """Map sportsMarketType to UI section. Returns: moneyline, totals, spread, btts, other."""
    mt = (m.get("sportsMarketType") or "").lower()
    slug = (m.get("slug") or "").lower()
    if mt == "moneyline":
        return "moneyline"
    if mt in ("totals", "total_goals"):
        return "totals"
    if mt in ("spreads", "match_handicap"):
        return "spread"
    if mt == "both_teams_to_score" or "btts" in slug:
        return "btts"
    if mt in ("total_corners", "correct_score", "double_chance"):
        return "other"
    if mt:
        return "other"
    # Fallback: infer from question/groupItemTitle
    q = (m.get("question") or "").lower()
    gt = (m.get("groupItemTitle") or "").lower()
    if "o/u" in gt or "over" in q or "under" in q:
        return "totals"
    if "draw" in q or "win" in q:
        return "moneyline"
    if "both teams" in q or "btts" in q:
        return "btts"
    return "other"


def build_events_for_ui(events: list) -> list:
    """
    Build event-centric structure for Polymarket-style UI.
    Groups: Moneyline (H/D/A), Totals (O/U), Spread, BTTS, Other.
    Merges child events (More Markets) into main event.
    """
    # Index main events by id; collect child events
    main_by_id = {}
    children = []
    for ev in events:
        pid = ev.get("parentEventId")
        if pid:
            children.append(ev)
        else:
            main_by_id[str(ev.get("id", ""))] = ev

    # Merge children into parents
    for c in children:
        pid = str(c.get("parentEventId", ""))
        if pid in main_by_id:
            parent = main_by_id[pid]
            pm = parent.get("markets") or []
            cm = c.get("markets") or []
            parent["markets"] = pm + cm

    result = []
    for ev in main_by_id.values():
        title = ev.get("title", "")
        slug = ev.get("slug", "")
        teams = ev.get("teams", [])
        score = ev.get("score")
        live = ev.get("live", False)

        sections = {"moneyline": [], "totals": [], "spread": [], "btts": [], "other": []}
        for m in ev.get("markets") or []:
            if not m.get("active") or not m.get("acceptingOrders"):
                continue
            buttons = _market_to_buttons(m, title)
            if not buttons:
                continue
            section = _classify_market_type(m)
            if section not in sections:
                sections[section] = []
            item = {
                "groupItemTitle": m.get("groupItemTitle", ""),
                "line": m.get("line"),
                "question": m.get("question", ""),
                "buttons": buttons,
            }
            sections[section].append(item)

        # Sort totals by line (1.5, 2.5, 3.5...)
        sections["totals"].sort(key=lambda x: (x.get("line") or 0))

        # Only include events that have at least one market
        has_any = any(sections[k] for k in sections)
        if not has_any:
            continue

        result.append({
            "event_title": title,
            "event_slug": slug,
            "teams": teams,
            "score": score,
            "live": live,
            "sections": sections,
        })
    return result


def merge_live_into_events(events_data: list) -> None:
    """Merge live WebSocket prices into event buttons. In-place."""
    for ev in events_data:
        for section_name, items in (ev.get("sections") or {}).items():
            for item in items:
                for btn in item.get("buttons", []):
                    tid = btn.get("token_id")
                    if tid:
                        lp = get_live_price(tid)
                        if lp:
                            btn["price"] = lp.get("ask") or lp.get("mid") or btn["price"]
                            btn["live"] = True


def _parse_outcomes(s: str) -> list:
    try:
        if isinstance(s, list):
            return s
        return json.loads(s) if s else []
    except Exception:
        return []


def _parse_prices(s) -> list:
    try:
        if isinstance(s, list):
            return [float(x) for x in s]
        arr = json.loads(s) if isinstance(s, str) else [s]
        return [float(x) for x in arr]
    except Exception:
        return [0.5, 0.5]


def _parse_token_ids(s: str) -> list:
    try:
        if isinstance(s, list):
            return [str(x) for x in s]
        arr = json.loads(s) if s else []
        return [str(x) for x in arr]
    except Exception:
        return []


HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Polymarket Sports Betting Bot</title>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Space+Grotesk:wght@500;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #0a0e14;
      --surface: #131920;
      --border: #1e2733;
      --accent: #39bae6;
      --accent-dim: #1a6b85;
      --green: #7fd962;
      --red: #f07178;
      --text: #e6e6e6;
      --muted: #8b949e;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Space Grotesk', sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      padding: 1rem;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 1rem;
      margin-bottom: 1.5rem;
      padding-bottom: 1rem;
      border-bottom: 1px solid var(--border);
    }
    h1 {
      font-size: 1.75rem;
      font-weight: 700;
      color: var(--accent);
      letter-spacing: 0.05em;
    }
    .meta {
      display: flex;
      gap: 1rem;
      align-items: center;
      font-size: 0.9rem;
      color: var(--muted);
    }
    .meta span { display: flex; align-items: center; gap: 0.3rem; }
    .bet-input-wrap {
      display: flex;
      align-items: center;
      gap: 0.4rem;
    }
    .bet-input-wrap label { color: var(--muted); font-size: 0.9rem; }
    .bet-input {
      width: 5rem;
      background: var(--surface);
      border: 1px solid var(--border);
      color: var(--green);
      padding: 0.4rem 0.6rem;
      border-radius: 6px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.95rem;
    }
    .bet-input:focus { outline: none; border-color: var(--accent); }
    .refresh-btn {
      background: var(--accent);
      color: var(--bg);
      border: none;
      padding: 0.5rem 1rem;
      border-radius: 6px;
      font-weight: 600;
      cursor: pointer;
      font-family: inherit;
    }
    .refresh-btn:hover { background: var(--accent-dim); }
    .refresh-btn:disabled { opacity: 0.6; cursor: not-allowed; }
    .price-live { color: var(--accent); font-size: 0.85rem; }
    .live-dot { color: var(--green); font-size: 0.6em; margin-left: 2px; animation: livePulse 1.5s ease infinite; }
    @keyframes livePulse { 50% { opacity: 0.6; } }
    .filters {
      display: flex;
      gap: 0.5rem;
      margin-bottom: 1rem;
      flex-wrap: wrap;
    }
    .filter-btn {
      background: var(--surface);
      color: var(--muted);
      border: 1px solid var(--border);
      padding: 0.4rem 0.8rem;
      border-radius: 6px;
      cursor: pointer;
      font-size: 0.85rem;
    }
    .filter-btn:hover, .filter-btn.active { color: var(--accent); border-color: var(--accent); }
    .markets {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 1.5rem;
    }
    @media (max-width: 900px) {
      .markets { grid-template-columns: repeat(2, 1fr); }
    }
    @media (max-width: 600px) {
      .markets { grid-template-columns: 1fr; }
    }
    .event-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      overflow: hidden;
      transition: border-color 0.2s;
    }
    .event-card:hover { border-color: var(--accent-dim); }
    .event-header-link {
      text-decoration: none;
      color: inherit;
      cursor: pointer;
      transition: background 0.15s;
    }
    .event-header-link:hover { background: rgba(57, 186, 230, 0.05); }
    .event-arrow { margin-left: auto; color: var(--muted); font-size: 0.9rem; }
    .event-header-link:hover .event-arrow { color: var(--accent); }
    .event-header {
      padding: 1rem 1.25rem;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 0.5rem;
    }
    .event-title {
      font-size: 1.1rem;
      font-weight: 600;
      color: var(--text);
    }
    .event-score {
      font-family: 'JetBrains Mono', monospace;
      font-size: 1rem;
      color: var(--accent);
      font-weight: 600;
    }
    .event-score.live-badge { font-size: 0.75rem; color: var(--green); }
    .section-block {
      padding: 1rem 1.25rem;
      border-bottom: 1px solid var(--border);
    }
    .section-block:last-child { border-bottom: none; }
    .section-label {
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      margin-bottom: 0.5rem;
    }
    .section-row {
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
      align-items: center;
    }
    .totals-grid {
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
    }
    .totals-row {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      flex-wrap: wrap;
    }
    .totals-line {
      min-width: 4rem;
      color: var(--muted);
      font-size: 0.85rem;
      font-family: 'JetBrains Mono', monospace;
    }
    .spread-label {
      font-size: 0.85rem;
      color: var(--muted);
      margin-right: 0.25rem;
    }
    .moneyline-grid {
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
    }
    .moneyline-row {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      flex-wrap: wrap;
    }
    .moneyline-label {
      min-width: 4rem;
      font-size: 0.9rem;
      color: var(--text);
    }
    .outcomes {
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
    }
    .outcome-btn {
      background: var(--bg);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 0.5rem 0.9rem;
      border-radius: 8px;
      cursor: pointer;
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.9rem;
      display: flex;
      align-items: center;
      gap: 0.4rem;
      transition: all 0.15s;
      min-width: 4.5rem;
      justify-content: center;
    }
    .outcome-btn.moneyline { min-width: 5rem; }
    .outcome-btn.outcome-no { color: var(--red); border-color: rgba(240, 113, 120, 0.4); }
    .outcome-btn.outcome-no:hover { border-color: var(--red); background: rgba(240, 113, 120, 0.1); }
    .outcome-btn.outcome-no .price-tag { color: var(--red); }
    .outcome-btn:hover {
      border-color: var(--accent);
      background: rgba(57, 186, 230, 0.1);
    }
    .outcome-btn:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }
    .outcome-btn.betting { animation: pulse 0.6s ease infinite; }
    @keyframes pulse { 50% { opacity: 0.7; } }
    .price-tag {
      color: var(--green);
      font-weight: 600;
    }
    .toast {
      position: fixed;
      bottom: 1.5rem;
      right: 1.5rem;
      padding: 0.8rem 1.2rem;
      border-radius: 8px;
      font-size: 0.9rem;
      z-index: 1000;
      animation: slideIn 0.3s ease;
    }
    @keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
    .toast.success { background: var(--green); color: var(--bg); }
    .toast.error { background: var(--red); color: var(--bg); }
    .loading { text-align: center; padding: 2rem; color: var(--muted); }
  </style>
</head>
<body>
  <header>
    <h1>POLYMARKET SPORTS BETTING BOT</h1>
    <div class="meta">
      <div class="bet-input-wrap">
        <label for="betAmount">Bet $</label>
        <input type="number" id="betAmount" class="bet-input" value="{{ bet_amount }}" min="1" max="1000" step="1">
      </div>
      <span class="price-live">Live prices · </span>
      <span>Live Sports · No Esports</span>
      <button class="refresh-btn" id="refreshBtn">Refresh</button>
    </div>
  </header>
  <div class="filters">
    <button class="filter-btn active" data-filter="all">All</button>
    <button class="filter-btn" data-filter="football">Football</button>
    <button class="filter-btn" data-filter="basketball">Basketball</button>
    <button class="filter-btn" data-filter="hockey">Hockey</button>
    <button class="filter-btn" data-filter="tennis">Tennis</button>
  </div>
  <div id="markets" class="markets">
    <div class="loading">Loading markets...</div>
  </div>
  <div id="toast"></div>
  <script>
    let betAmount = {{ bet_amount }};
    let events = [];
    let filter = 'all';
    const sportKeywords = {
      football: ['football','soccer','nfl','epl','laliga','uefa','mls','goal','match'],
      basketball: ['basketball','nba','wnba','ncaa','hoops'],
      hockey: ['hockey','nhl','ice'],
      tennis: ['tennis','atp','wta','grand slam']
    };
    function classifySport(title) {
      const t = (title || '').toLowerCase();
      for (const [sport, kw] of Object.entries(sportKeywords)) {
        if (kw.some(k => t.includes(k))) return sport;
      }
      return 'other';
    }
    function renderBtn(b, extraClass) {
      const noClass = (b.outcome || '').toLowerCase() === 'no' ? ' outcome-no' : '';
      return `<button class="outcome-btn ${extraClass || ''}${noClass}" data-token="${b.token_id}" data-price="${b.price}" data-outcome="${escapeHtml(b.outcome)}">
        <span>${escapeHtml(b.outcome)}</span>
        <span class="price-tag" data-token-id="${b.token_id}">${(b.price * 100).toFixed(0)}¢${b.live ? '<span class="live-dot" title="Live">●</span>' : ''}</span>
      </button>`;
    }
    function renderSection(sectionKey, label, ev) {
      const items = (ev.sections || {})[sectionKey] || [];
      if (items.length === 0) return '';
      let html = `<div class="section-block"><div class="section-label">${label}</div>`;
      if (sectionKey === 'moneyline') {
        html += '<div class="moneyline-grid">';
        const mlLabels = ['Home', 'Draw', 'Away'];
        items.forEach((it, i) => {
          const lbl = (it.groupItemTitle || '').toLowerCase().includes('draw') ? 'Draw' : (mlLabels[i] || it.groupItemTitle || '');
          html += `<div class="moneyline-row"><span class="moneyline-label">${escapeHtml(lbl)}</span><div class="outcomes">`;
          (it.buttons || []).forEach(b => html += renderBtn(b, 'moneyline'));
          html += '</div></div>';
        });
        html += '</div>';
      } else if (sectionKey === 'totals') {
        html += '<div class="totals-grid">';
        items.forEach(it => {
          const line = it.line != null ? it.line : (it.groupItemTitle || '').replace(/[^0-9.]/g, '') || '?';
          html += `<div class="totals-row"><span class="totals-line">O/U ${line}</span><div class="outcomes">`;
          (it.buttons || []).forEach(b => html += renderBtn(b));
          html += '</div></div>';
        });
        html += '</div>';
      } else if (sectionKey === 'spread' || sectionKey === 'btts' || sectionKey === 'other') {
        html += '<div class="section-row">';
        items.forEach(it => {
          if (it.groupItemTitle) html += `<span class="spread-label">${escapeHtml(it.groupItemTitle)}</span>`;
          (it.buttons || []).forEach(b => html += renderBtn(b));
        });
        html += '</div>';
      }
      html += '</div>';
      return html;
    }
    function render() {
      const container = document.getElementById('markets');
      let filtered = events;
      if (filter !== 'all') {
        filtered = events.filter(e => classifySport(e.event_title) === filter);
      }
      if (filtered.length === 0) {
        container.innerHTML = '<div class="loading">No live games right now. Try refreshing.</div>';
        return;
      }
      container.innerHTML = filtered.map(ev => {
        const scoreHtml = ev.score ? `<span class="event-score">${escapeHtml(ev.score)}</span>` : '';
        const liveBadge = ev.live ? '<span class="event-score live-badge">LIVE</span>' : '';
        let sectionsHtml = renderSection('moneyline', 'Moneyline', ev);
        sectionsHtml += renderSection('totals', 'Totals (Over/Under)', ev);
        sectionsHtml += renderSection('spread', 'Spread', ev);
        sectionsHtml += renderSection('btts', 'Both Teams to Score', ev);
        sectionsHtml += renderSection('other', 'Other', ev);
        return `<div class="event-card" data-sport="${classifySport(ev.event_title)}">
          <a href="/event/${escapeHtml(ev.event_slug)}" class="event-header event-header-link">
            <span class="event-title">${escapeHtml(ev.event_title)}</span>
            ${liveBadge}
            ${scoreHtml}
            <span class="event-arrow">→</span>
          </a>
          ${sectionsHtml}
        </div>`;
      }).join('');
      container.querySelectorAll('.outcome-btn').forEach(btn => {
        btn.addEventListener('click', () => placeBet(btn));
      });
    }
    function escapeHtml(s) {
      const d = document.createElement('div');
      d.textContent = s;
      return d.innerHTML;
    }
    function showToast(msg, isError) {
      const el = document.getElementById('toast');
      el.textContent = msg;
      el.className = 'toast ' + (isError ? 'error' : 'success');
      el.style.display = 'block';
      setTimeout(() => { el.style.display = 'none'; }, 4000);
    }
    async function placeBet(btn) {
      if (btn.disabled) return;
      const tokenId = btn.dataset.token;
      const price = parseFloat(btn.dataset.price);
      const outcome = btn.dataset.outcome;
      const amt = parseFloat(document.getElementById('betAmount').value) || betAmount;
      btn.disabled = true;
      btn.classList.add('betting');
      try {
        const r = await fetch('/api/place-bet', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token_id: tokenId, amount: amt, price })
        });
        const data = await r.json();
        if (data.ok) {
          showToast('✅ ' + data.message, false);
        } else {
          showToast('❌ ' + (data.error || 'Failed'), true);
          btn.disabled = false;
        }
      } catch (e) {
        showToast('❌ ' + e.message, true);
        btn.disabled = false;
      }
      btn.classList.remove('betting');
    }
    async function load() {
      const btn = document.getElementById('refreshBtn');
      btn.disabled = true;
      try {
        const r = await fetch('/api/markets');
        const data = await r.json();
        events = data.events || [];
        render();
      } catch (e) {
        document.getElementById('markets').innerHTML = '<div class="loading">Error loading. Retry?</div>';
      }
      btn.disabled = false;
    }
    function updatePricesOnly() {
      fetch('/api/prices').then(r => r.json()).then(prices => {
        document.querySelectorAll('.price-tag[data-token-id]').forEach(el => {
          const p = prices[el.getAttribute('data-token-id')];
          if (p != null) {
            const liveDot = el.querySelector('.live-dot');
            el.textContent = (p * 100).toFixed(0) + '¢';
            if (liveDot) el.appendChild(liveDot);
            const btn = el.closest('.outcome-btn');
            if (btn) btn.dataset.price = p;
          }
        });
      }).catch(() => {});
    }
    document.getElementById('refreshBtn').addEventListener('click', load);
    document.querySelectorAll('.filter-btn').forEach(b => {
      b.addEventListener('click', () => {
        document.querySelectorAll('.filter-btn').forEach(x => x.classList.remove('active'));
        b.classList.add('active');
        filter = b.dataset.filter;
        render();
      });
    });
    document.getElementById('betAmount').addEventListener('change', function() {
      const v = parseFloat(this.value);
      if (!isNaN(v) && v > 0) betAmount = v;
    });
    load();
    setInterval(updatePricesOnly, {{ poll_interval_ms }});
  </script>
</body>
</html>
"""

# Shared CSS for detail page (reuse vars + key styles)
DETAIL_STYLES = """
    :root { --bg:#0a0e14; --surface:#131920; --border:#1e2733; --accent:#39bae6; --accent-dim:#1a6b85; --green:#7fd962; --red:#f07178; --text:#e6e6e6; --muted:#8b949e; }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Space Grotesk', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; padding: 1rem; }
    .back-link { color: var(--accent); text-decoration: none; font-size: 0.9rem; margin-bottom: 1rem; display: inline-block; }
    .back-link:hover { color: var(--accent-dim); }
    .detail-header { padding: 1.5rem; background: var(--surface); border-radius: 12px; margin-bottom: 1rem; border: 1px solid var(--border); }
    .detail-title { font-size: 1.4rem; font-weight: 600; margin-bottom: 0.5rem; }
    .detail-meta { display: flex; gap: 1rem; align-items: center; font-size: 0.9rem; color: var(--muted); }
    .live-badge { color: var(--green); font-weight: 600; }
    .detail-content { padding: 1rem; background: var(--surface); border-radius: 12px; border: 1px solid var(--border); }
    .section-block { padding: 1rem 0; border-bottom: 1px solid var(--border); }
    .section-block:last-child { border-bottom: none; }
    .section-label { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); margin-bottom: 0.5rem; }
    .moneyline-grid, .totals-grid { display: flex; flex-direction: column; gap: 0.5rem; }
    .moneyline-row, .totals-row { display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; }
    .moneyline-label, .totals-line { min-width: 4rem; font-size: 0.9rem; color: var(--text); }
    .totals-line { color: var(--muted); font-family: 'JetBrains Mono', monospace; }
    .outcomes { display: flex; flex-wrap: wrap; gap: 0.5rem; }
    .outcome-btn { background: var(--bg); border: 1px solid var(--border); color: var(--text); padding: 0.5rem 0.9rem; border-radius: 8px; cursor: pointer; font-family: 'JetBrains Mono', monospace; font-size: 0.9rem; display: flex; align-items: center; gap: 0.4rem; transition: all 0.15s; min-width: 4.5rem; justify-content: center; }
    .outcome-btn:hover { border-color: var(--accent); background: rgba(57,186,230,0.1); }
    .outcome-btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .outcome-btn.betting { animation: pulse 0.6s ease infinite; }
    @keyframes pulse { 50% { opacity: 0.7; } }
    .outcome-btn.outcome-no { color: var(--red); border-color: rgba(240,113,120,0.4); }
    .outcome-btn.outcome-no:hover { border-color: var(--red); background: rgba(240,113,120,0.1); }
    .outcome-btn.outcome-no .price-tag { color: var(--red); }
    .price-tag { color: var(--green); font-weight: 600; }
    .bet-input-wrap { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 1rem; }
    .bet-input { width: 5rem; background: var(--surface); border: 1px solid var(--border); color: var(--green); padding: 0.4rem 0.6rem; border-radius: 6px; font-family: 'JetBrains Mono', monospace; }
    .toast { position: fixed; bottom: 1.5rem; right: 1.5rem; padding: 0.8rem 1.2rem; border-radius: 8px; font-size: 0.9rem; z-index: 1000; }
    .toast.success { background: var(--green); color: var(--bg); }
    .toast.error { background: var(--red); color: var(--bg); }
    .loading { text-align: center; padding: 2rem; color: var(--muted); }
"""

EVENT_DETAIL_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Event - Polymarket Sports</title>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Space+Grotesk:wght@500;700&display=swap" rel="stylesheet">
  <style>{{ detail_styles }}</style>
</head>
<body>
  <a href="/" class="back-link">← Back to matches</a>
  <div class="bet-input-wrap">
    <label>Bet $</label>
    <input type="number" id="betAmount" class="bet-input" value="{{ bet_amount }}" min="1" max="1000" step="1">
  </div>
  <div id="detail-container">
    <div class="loading">Loading event...</div>
  </div>
  <div id="toast"></div>
  <script>
    const slug = {{ slug|tojson }};
    const API_BASE = {{ request.url_root.rstrip('/')|tojson }};
    let betAmount = {{ bet_amount }};
    let eventData = null;
    const SECTION_ORDER = ['moneyline','totals','btts','spread','other'];
    const SECTION_LABELS = { moneyline:'Moneyline', totals:'Totals', btts:'Both Teams to Score', spread:'Spread', other:'Other' };
    function escapeHtml(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
    function renderBtn(b) {
      const noClass = (b.outcome || '').toLowerCase() === 'no' ? ' outcome-no' : '';
      return `<button class="outcome-btn${noClass}" data-token="${b.token_id}" data-price="${b.price}" data-outcome="${escapeHtml(b.outcome)}">
        <span>${escapeHtml(b.outcome)}</span>
        <span class="price-tag" data-token-id="${b.token_id}">${(b.price*100).toFixed(0)}¢</span>
      </button>`;
    }
    function renderSection(sectionKey, ev) {
      const items = (ev.sections || {})[sectionKey] || [];
      if (items.length === 0) return '';
      let html = '<div class="section-block"><div class="section-label">' + SECTION_LABELS[sectionKey] + '</div>';
      if (sectionKey === 'moneyline') {
        const mlLabels = ['Home','Draw','Away'];
        items.forEach((it,i) => {
          const lbl = (it.groupItemTitle||'').toLowerCase().includes('draw') ? 'Draw' : (mlLabels[i]||it.groupItemTitle||'');
          html += `<div class="moneyline-row"><span class="moneyline-label">${escapeHtml(lbl)}</span><div class="outcomes">`;
          (it.buttons||[]).forEach(b => html += renderBtn(b));
          html += '</div></div>';
        });
      } else if (sectionKey === 'totals') {
        items.forEach(it => {
          const line = it.line != null ? it.line : (it.groupItemTitle||'').replace(/[^0-9.]/g,'') || '?';
          html += `<div class="totals-row"><span class="totals-line">O/U ${line}</span><div class="outcomes">`;
          (it.buttons||[]).forEach(b => html += renderBtn(b));
          html += '</div></div>';
        });
      } else {
        items.forEach(it => {
          if (it.groupItemTitle) html += `<span style="min-width:6rem;color:var(--muted)">${escapeHtml(it.groupItemTitle)}</span>`;
          (it.buttons||[]).forEach(b => html += renderBtn(b));
        });
      }
      html += '</div>';
      return html;
    }
    function render() {
      if (!eventData) return;
      const ev = eventData;
      const scoreHtml = ev.score ? `<span>${escapeHtml(ev.score)}</span>` : '';
      const liveBadge = ev.live ? '<span class="live-badge">LIVE</span>' : '';
      let sectionsHtml = '';
      SECTION_ORDER.forEach(k => {
        const html = renderSection(k, ev);
        if (html) sectionsHtml += html;
      });
      document.getElementById('detail-container').innerHTML = `
        <div class="detail-header">
          <div class="detail-title">${escapeHtml(ev.event_title)}</div>
          <div class="detail-meta">${liveBadge} ${scoreHtml}</div>
        </div>
        <div class="detail-content">${sectionsHtml}</div>
      `;
      document.querySelectorAll('.outcome-btn').forEach(btn => btn.addEventListener('click', () => placeBet(btn)));
    }
    function showToast(msg, isError) {
      const el = document.getElementById('toast');
      el.textContent = msg;
      el.className = 'toast ' + (isError ? 'error' : 'success');
      el.style.display = 'block';
      setTimeout(() => { el.style.display = 'none'; }, 4000);
    }
    async function placeBet(btn) {
      if (btn.disabled) return;
      const amt = parseFloat(document.getElementById('betAmount').value) || betAmount;
      btn.disabled = true;
      btn.classList.add('betting');
      try {
        const url = (API_BASE || '') + '/api/place-bet';
        const r = await fetch(url, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ token_id: btn.dataset.token, amount: amt, price: btn.dataset.price }) });
        const ct = r.headers.get('content-type') || '';
        if (!ct.includes('application/json')) {
          const text = await r.text();
          showToast('❌ Server error (not JSON). Check console.', true);
          console.error('Place-bet response:', r.status, text.slice(0, 200));
          btn.disabled = false;
          btn.classList.remove('betting');
          return;
        }
        const data = await r.json();
        if (data.ok) showToast('✅ ' + data.message, false);
        else { showToast('❌ ' + (data.error || 'Failed'), true); btn.disabled = false; }
      } catch (e) {
        showToast('❌ ' + (e.message || 'Request failed'), true);
        btn.disabled = false;
      }
      btn.classList.remove('betting');
    }
    async function load() {
      try {
        const r = await fetch((API_BASE || '') + '/api/event/' + encodeURIComponent(slug));
        if (!r.ok) throw new Error('Not found');
        const ct = r.headers.get('content-type') || '';
        if (!ct.includes('application/json')) throw new Error('Invalid response');
        const data = await r.json();
        eventData = data.event;
        render();
      } catch (e) {
        document.getElementById('detail-container').innerHTML = '<div class="loading">Event not found.</div>';
      }
    }
    document.getElementById('betAmount').addEventListener('change', function() { const v = parseFloat(this.value); if (!isNaN(v) && v > 0) betAmount = v; });
    load();
    setInterval(() => {
      fetch((API_BASE || '') + '/api/prices').then(r => r.ok && (r.headers.get('content-type')||'').includes('json') ? r.json() : {}).then(prices => {
        if (prices && typeof prices === 'object') document.querySelectorAll('.price-tag[data-token-id]').forEach(el => { const p = prices[el.getAttribute('data-token-id')]; if (p != null) { el.textContent = (p*100).toFixed(0) + '¢'; const ob = el.closest('.outcome-btn'); if (ob) ob.dataset.price = p; } });
      }).catch(()=>{});
    }, 500);
  </script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(
        HTML_TEMPLATE,
        bet_amount=BET_AMOUNT_USD,
        poll_interval_ms=int(SPORTS_POLL_INTERVAL_SEC * 1000),
    )


@app.route("/event/<slug>")
def event_detail(slug: str):
    return render_template_string(
        EVENT_DETAIL_TEMPLATE,
        detail_styles=DETAIL_STYLES,
        slug=slug,
        bet_amount=BET_AMOUNT_USD,
    )


@app.route("/api/event/<slug>")
def api_event(slug: str):
    """Fetch single event by slug for detail page."""
    raw = fetch_event_by_slug(slug)
    if not raw:
        return jsonify({"error": "Event not found"}), 404
    events_data = build_events_for_ui(raw)
    if not events_data:
        return jsonify({"error": "Event not found"}), 404
    merge_live_into_events(events_data)
    ev = events_data[0]
    # Start WebSocket for this event's tokens so live prices work
    token_ids = []
    for items in (ev.get("sections") or {}).values():
        for item in items:
            for btn in item.get("buttons", []):
                if btn.get("token_id"):
                    token_ids.append(btn["token_id"])
    if token_ids:
        start_live_prices(token_ids)
    return jsonify({"event": ev})


@app.route("/api/markets")
def api_markets():
    events = fetch_sports_markets()
    events_data = build_events_for_ui(events)
    merge_live_into_events(events_data)  # Overlay live WebSocket prices
    return jsonify({"events": events_data, "count": len(events_data)})


@app.route("/api/prices")
def api_prices():
    """Lightweight: token_id -> price (for live updates without full refresh)."""
    from sports_websocket import get_all_live_prices
    prices = {}
    for tid, data in get_all_live_prices().items():
        p = data.get("ask") or data.get("mid") or data.get("bid")
        if p is not None:
            prices[tid] = round(p, 4)
    return jsonify(prices)


@app.route("/api/place-bet", methods=["POST"])
def api_place_bet():
    data = request.get_json() or {}
    token_id = data.get("token_id")
    if not token_id:
        return jsonify({"ok": False, "error": "Missing token_id"}), 400
    amount = data.get("amount")
    price = data.get("price")
    result = execute_bet(token_id, amount, price)
    if result.get("ok"):
        return jsonify(result)
    return jsonify(result), 400


def main():
    from sports_config import SPORTS_SERVER_PORT, FUNDER_ADDRESS, PRIVATE_KEY
    if not FUNDER_ADDRESS or not PRIVATE_KEY:
        print("\n❌ Missing FUNDER_ADDRESS or PRIVATE_KEY in .env")
        print("   Copy .env.example to .env and set your wallet credentials.")
        return
    print("\n" + "=" * 50)
    print("  ⚡ Sports Fast Bet - Manual Click-to-Trade")
    print("  Live Sports · No Esports")
    print(f"  Bet amount: ${BET_AMOUNT_USD}")
    print(f"  Open: http://localhost:{SPORTS_SERVER_PORT}")
    print("=" * 50 + "\n")
    app.run(host="0.0.0.0", port=SPORTS_SERVER_PORT, threaded=True)


if __name__ == "__main__":
    main()
