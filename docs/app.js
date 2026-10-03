"use strict";

const ASSET = {
  BTC: "#f7931a", ETH: "#7b87ff", SOL: "#19fb9b", XRP: "#3fb6e8",
  BNB: "#f3ba2f", DOGE: "#c2a633", HYPE: "#22d3a6", ZEC: "#f4b728",
};
const ac = (a) => ASSET[a] || "#7c8cff";

const COLS_MONTHLY = [
  { k: "_mkt",          t: "Market",   align: "l", sort: "kalshi_ticker" },
  { k: "best_side",     t: "Side",     align: "l" },
  { k: "_px",           t: "K / P px", align: "l", sort: "combined_cost" },
  { k: "combined_cost", t: "Comb $",   f: "px" },
  { k: "worst_pnl",     t: "Min $/ct", f: "s4" },
  { k: "net_return",    t: "Net Ret",  f: "pctBig" },
  { k: "net_return_cash", t: "Cash Ret", f: "pctBig" },
  { k: "annualized",    t: "Annual",   f: "pct" },
  { k: "max_contracts", t: "Max Ct",   f: "sz" },
  { k: "total_gain",    t: "Tot $",    f: "s2" },
  { k: "poly_volume",   t: "P Vol",    f: "money" },
  { k: "days_to_expiry",t: "Days",     f: "int" },
  { k: "status",        t: "Status",   align: "l", f: "status" },
];
const COLS_HOURLY = [
  { k: "_mkt",          t: "Market",     align: "l", sort: "asset" },
  { k: "_window",       t: "Window",     align: "l", sort: "minutes_to_resolve" },
  { k: "kalshi_strike", t: "K Strike",   f: "strike" },
  { k: "implied_strike",t: "Binance open", f: "strike" },
  { k: "best_side",     t: "Side",       align: "l" },
  { k: "_px",           t: "K / P px",   align: "l", sort: "combined_cost" },
  { k: "combined_cost", t: "Comb $",     f: "px" },
  { k: "worst_pnl",     t: "Min $/ct",   f: "s4" },
  { k: "net_return",    t: "Net Ret",    f: "pctBig" },
  { k: "divergence",    t: "Feed Δ",     f: "div" },
  { k: "poly_volume",   t: "P Vol",      f: "money" },
  { k: "status",        t: "Status",     align: "l", f: "status" },
];
const COLS_DAILY = [
  { k: "_mkt",          t: "Market",     align: "l", sort: "ibkr_label" },
  { k: "ibkr_strike",   t: "IBKR Strike",f: "strike" },
  { k: "kalshi_strike", t: "K Strike",   f: "strike" },
  { k: "basis_pct",     t: "Basis",      f: "pct" },
  { k: "best_side",     t: "Side",       align: "l" },
  { k: "_px",           t: "K / I px",   align: "l", sort: "combined_cost" },
  { k: "combined_cost", t: "Comb $",     f: "px" },
  { k: "worst_pnl",     t: "Min $/ct",   f: "s4" },
  { k: "net_return",    t: "Net Ret",    f: "pctBig" },
  { k: "_settle",       t: "Settle",     align: "l" },
  { k: "status",        t: "Status",     align: "l", f: "status" },
];

let MODE = "monthly", POS_VIEW = "venue";
const COLS = () => MODE === "daily" ? COLS_DAILY
  : MODE === "hourly" ? COLS_HOURLY : COLS_MONTHLY;
// Static snapshot URLs — no backend; cache-bust on each load so returning
// users see fresh data after a GHA cron commit.
const endpoint = () => {
  const f = MODE === "hourly" ? "hourly" : "scan";
  return `./data/${f}.json?t=${Date.now()}`;
};
const STATIC_MODE = true;
// Backend-only elements are stripped from the static page, so $() hands back an
// inert stub rather than null. `hidden: true` is the right default — anything
// that asks whether a modal is open gets "closed".
const _NULLEL = {
  hidden: true, innerHTML: "", value: "", textContent: "", dataset: {},
  classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
  querySelector: () => null, querySelectorAll: () => [],
  addEventListener() {}, removeEventListener() {}, focus() {}, click() {},
};

let RAW = [], sortKey = "net_return", sortAsc = false, openKey = null;
const $ = (id) => document.getElementById(id) || _NULLEL;
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const rid = (r) => (r.asset || "") + "|" + (r.kalshi_ticker || "") +
  "|" + (r.poly_slug || "");

function fmt(v, kind) {
  if (v === null || v === undefined || v === "")
    return '<span class="dimv">—</span>';
  const sign = (n, body, big) => {
    const c = n > 0 ? "pos" : n < 0 ? "neg" : "dimv";
    return `<span class="num ${c}${big ? " big" : ""}">${body}</span>`;
  };
  switch (kind) {
    case "int":   return String(Math.round(v));
    case "sz":    return (+v).toLocaleString(undefined, { maximumFractionDigits: 0 });
    case "px":    return `<span class="mono">${(+v).toFixed(3)}</span>`;
    case "money": return "$" + (+v).toLocaleString(undefined, { notation: v >= 1e6 ? "compact" : "standard", maximumFractionDigits: 1 });
    case "pct":   return sign(v, (v * 100).toFixed(1) + "%");
    case "pctBig":return sign(v, (v > 0 ? "+" : "") + (v * 100).toFixed(2) + "%", true);
    case "s4":    return sign(v, (v > 0 ? "+" : "") + (+v).toFixed(4));
    case "s2":    return sign(v, (v > 0 ? "+" : "") + "$" + (+v).toFixed(2));
    case "strike":return `<span class="mono">${(+v).toLocaleString(undefined, { maximumFractionDigits: v < 10 ? 4 : 0 })}</span>`;
    case "div": { const p = (v * 100), hot = Math.abs(p) >= 0.3;
      return `<span class="mono ${hot ? "neg" : "dimv"}">${p >= 0 ? "+" : ""}${p.toFixed(3)}%</span>`; }
    case "status":{ const s = String(v).replace(/\s+/g, "");
      return `<span class="pill ${s}">${v}</span>`; }
    default:      return esc(v);
  }
}

function thumb(r, sz) {
  const s = sz || 34;
  if (r.image)
    return `<img class="thumb" style="width:${s}px;height:${s}px"
      src="${esc(r.image)}" loading="lazy"
      onerror="this.replaceWith(Object.assign(document.createElement('div'),
      {className:'badge',style:'width:${s}px;height:${s}px;background:${ac(r.asset)}',
      textContent:'${esc(r.asset || "?").slice(0,4)}'}))">`;
  return `<div class="badge" style="width:${s}px;height:${s}px;background:${ac(r.asset)}">${esc((r.asset || "?").slice(0, 4))}</div>`;
}

function cellMarket(r) {
  const title = r.ibkr_label || r.kalshi_title || r.poly_question ||
    `${r.asset} ${r.direction} ${r.kalshi_strike ?? ""}`;
  const secondLabel = (MODE === "daily")
    ? `K $${r.kalshi_strike ?? "—"} · I $${r.ibkr_strike ?? "—"}`
    : `K $${r.kalshi_strike ?? "—"} · P $${r.poly_strike ?? "—"}`;
  return `<div class="mkt">${thumb(r)}
    <div class="info">
      <div class="qa">
        <span class="achip" style="background:${ac(r.asset)}22;color:${ac(r.asset)}">${esc(r.asset || "?")}</span>
        <span class="q" title="${esc(title)}">${esc(title)}</span>
      </div>
      <div class="sub"><span class="dir">${esc(r.direction || "")}</span> &nbsp;${esc(secondLabel)}</div>
    </div></div>`;
}

function cellSettle(r) {
  const iso = r.kalshi_close_iso || r.ibkr_close_iso;
  if (!iso) return '<span class="dimv">—</span>';
  try {
    const d = new Date(iso);
    const opts = { month: "short", day: "numeric", hour: "numeric" };
    return `<span class="mono">${d.toLocaleString(undefined, opts)}</span>`;
  } catch (e) { return esc(iso); }
}

function cellPx(r) {
  const k = r.kalshi_price, p = r.poly_price, c = r.combined_cost;
  if (k == null || p == null) return '<span class="dimv">—</span>';
  const pct = Math.min(100, (c / 1) * 100);
  const col = c < 1 ? "var(--good)" : "var(--bad)";
  const otherL = MODE === "daily" ? "I" : "P";
  return `<div class="pricebar">
    <div class="lbl"><span>K ${k.toFixed(3)}</span><span>${otherL} ${p.toFixed(3)}</span></div>
    <div class="track"><div class="fill" style="width:${pct}%;background:${col}"></div></div>
    <div class="lbl"><span class="dimv">cost vs $1</span><span class="${c < 1 ? "pos" : "neg"}">${c.toFixed(3)}</span></div>
  </div>`;
}

function cellWindow(r) {
  if (!r.window_start) return '<span class="dimv">—</span>';
  const m = r.minutes_to_resolve;
  const mc = m != null && m <= 10 ? "neg" : m != null && m <= 25 ? "pos" : "dimv";
  return `<div class="info">
    <div class="q mono">${esc(r.window_start)} → ${esc(r.window_close)}</div>
    <div class="sub ${mc}">${m != null ? "resolves in " + m + "m" : ""}</div>
  </div>`;
}

function renderHead() {
  $("head").innerHTML = COLS().map((c) => {
    const sk = c.sort || c.k;
    let cls = c.align === "l" ? "l" : "";
    if (sk === sortKey) cls += sortAsc ? " asc" : " sorted";
    return `<th class="${cls}" data-sk="${sk}">${c.t}</th>`;
  }).join("");
  document.querySelectorAll("#head th").forEach((th) => {
    th.onclick = () => {
      const k = th.dataset.sk;
      if (k === sortKey) sortAsc = !sortAsc; else { sortKey = k; sortAsc = false; }
      renderHead(); renderBody();
    };
  });
}

function activeStatus() {
  const b = document.querySelector("#statusSeg button.on");
  return b ? (b.dataset.s || "") : "";
}
// Which segmented button is active: a status filter ("table") or the paired view.
function activeView() {
  const b = document.querySelector("#statusSeg button.on");
  return b && b.dataset.v ? b.dataset.v : "table";
}

function filtered() {
  const q = $("search").value.trim().toLowerCase();
  const s = activeStatus();
  return RAW.filter((r) => {
    if (s && r.status !== s) return false;
    if (q) {
      const h = `${r.asset} ${r.kalshi_ticker} ${r.poly_slug || ""} ${r.kalshi_title || ""} ${r.poly_question || ""}`.toLowerCase();
      if (!h.includes(q)) return false;
    }
    return true;
  });
}

function sortRows(rows) {
  return rows.sort((x, y) => {
    let a = x[sortKey], b = y[sortKey];
    a = a == null ? -Infinity : a; b = b == null ? -Infinity : b;
    if (typeof a === "string" || typeof b === "string")
      return sortAsc ? String(a).localeCompare(b) : String(b).localeCompare(a);
    return sortAsc ? a - b : b - a;
  });
}

function drawer(r) {
  const kv = (k, v) => `<div class="k">${k}</div><div class="v">${v}</div>`;
  const money = (n, d = 4) => n == null ? "—"
    : `<span class="${n > 0 ? "pos" : n < 0 ? "neg" : ""}">${n > 0 ? "+" : ""}${(+n).toFixed(d)}</span>`;
  const kCard = `<div class="vcard">
    <h4><span class="tag k">Kalshi</span> ${esc(r.kalshi_ticker)}</h4>
    <div class="title">${esc(r.kalshi_title || "—")}</div>
    <div class="legs">
      <div class="leg"><div class="k">Yes ask</div><div class="v">${r.kalshi_price != null && r.best_side === "YES+NO" ? r.kalshi_price.toFixed(3) : "—"}</div></div>
      <div class="leg"><div class="k">No ask</div><div class="v">${r.kalshi_price != null && r.best_side === "NO+YES" ? r.kalshi_price.toFixed(3) : "—"}</div></div>
      <div class="leg"><div class="k">Open int</div><div class="v">${r.kalshi_size != null ? Math.round(r.kalshi_size).toLocaleString() : "—"}</div></div>
    </div>
    <div class="desc">${esc(r.kalshi_rules || "Resolution rules unavailable.")}</div>
  </div>`;
  const pImg = r.image ? `<img class="hero-img" src="${esc(r.image)}" onerror="this.remove()">` : "";
  const pSideRaw = (r.best_side || "—").split("+")[1] || "—";
  const pSide = MODE === "hourly"
    ? (pSideRaw === "NO" ? "Down" : pSideRaw === "YES" ? "Up" : pSideRaw)
    : pSideRaw;
  const pCard = `<div class="vcard">
    <h4><span class="tag p">Polymarket</span> ${esc(r.poly_slug || "no paired market")}</h4>
    ${pImg}
    <div class="title">${esc(r.poly_question || "—")}</div>
    <div class="legs">
      <div class="leg"><div class="k">Side held</div><div class="v">${esc(pSide)}</div></div>
      <div class="leg"><div class="k">Poly px</div><div class="v">${r.poly_price != null ? r.poly_price.toFixed(3) : "—"}</div></div>
      <div class="leg"><div class="k">Volume</div><div class="v">$${(r.poly_volume || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}</div></div>
    </div>
    <div class="desc">${esc(r.poly_description || "Market description unavailable.")}</div>
  </div>`;
  const hourly = MODE === "hourly";
  const scen = `<div class="scen">
    <div class="b"><div class="t">${hourly ? "Worst case" : "Min / guaranteed"}</div><div class="x">${money(r.worst_pnl)}</div></div>
    <div class="b"><div class="t">Strike gap</div><div class="x">${r.mid_pnl == null ? '<span class="dimv">none</span>' : money(r.mid_pnl)}</div></div>
    <div class="b"><div class="t">Best case</div><div class="x">${money(r.best_pnl)}</div></div>
  </div>`;
  const hRows = hourly ? `
      ${kv("Window", esc((r.window_start || "—") + " → " + (r.window_close || "—")))}
      ${kv("Resolves in", r.minutes_to_resolve != null ? r.minutes_to_resolve + " min" : "—")}
      ${kv("Implied strike (Binance open)", r.implied_strike != null ? r.implied_strike.toLocaleString() : "—")}
      ${kv("Binance spot / CF spot", `${r.binance_spot != null ? r.binance_spot.toLocaleString() : "—"} / ${r.cf_spot != null ? r.cf_spot.toLocaleString() : "n/a"}`)}
      ${kv("Feed divergence", fmt(r.divergence, "div"))}` : `
      ${kv("Annualized", fmt(r.annualized, "pct"))}
      ${kv("Annualized (cash)", fmt(r.annualized_cash, "pct"))}
      ${kv("Total guaranteed $", money(r.total_gain, 2))}
      ${kv("Days to expiry", r.days_to_expiry ?? "—")}`;
  // Book walk: only worth showing when there's real size behind the top level.
  const lv = (!hourly && Array.isArray(r.depth_levels)) ? r.depth_levels : [];
  const depth = lv.length > 1 ? `
    <div class="depth">
      <div class="depth-h">Book walk · <b>${Math.round(r.max_contracts).toLocaleString()}</b> ct for <b class="pos">$${(+r.total_gain).toFixed(2)}</b> guaranteed</div>
      <table class="depth-t">
        <tr><th>Kalshi</th><th>Poly</th><th>Cost</th><th>Ct</th><th>$/ct</th><th>Subtotal $</th></tr>
        ${lv.map(l => `<tr>
          <td>${(l.kalshi_price * 100).toFixed(1)}¢</td>
          <td>${(l.poly_price * 100).toFixed(1)}¢</td>
          <td>${((l.kalshi_price + l.poly_price) * 100).toFixed(1)}¢</td>
          <td>${Math.round(l.contracts).toLocaleString()}</td>
          <td class="pos">+${(l.pnl_per_contract * 100).toFixed(2)}¢</td>
          <td>${money(l.pnl_per_contract * l.contracts, 2)}</td></tr>`).join("")}
      </table>
      <div class="depth-f">Blended cost ${r.depth_avg_cost != null ? "$" + r.depth_avg_cost.toFixed(4) : "—"} · every level is worst-case positive on its own, but levels past the first need the order worked, not one click.</div>
    </div>` : "";
  const bd = `<div class="vcard">
    <h4>${hourly ? "Speculative breakdown" : "Arb breakdown"}</h4>
    ${scen}
    ${hourly ? '<div class="warn-line">Different settlement feeds — legs are not a locked hedge.</div>' : ""}
    <div class="kv">
      ${kv("Best side", esc(r.best_side || "—"))}
      ${kv("Combined cost", r.combined_cost != null ? "$" + r.combined_cost.toFixed(4) : "—")}
      ${kv("Total fee / ct", r.total_fee != null ? "$" + r.total_fee.toFixed(4) : "—")}
      ${kv("Cash cost / ct", r.cash_cost != null ? "$" + r.cash_cost.toFixed(4) + ' <span class="muted">(price + fee)</span>' : "—")}
      ${kv("Net return", fmt(r.net_return, "pctBig") + ' <span class="muted">sheet</span>')}
      ${kv("Net return (cash)", fmt(r.net_return_cash, "pctBig") + ' <span class="muted">true cash-on-cash</span>')}
      ${kv("Basis %", fmt(r.basis_pct, "pct") + (r.basis_favorable ? ' <span class="pos">✓ favorable</span>' : ' <span class="neg">✗ risk</span>'))}
      ${kv("Max contracts", r.max_contracts != null ? Math.round(r.max_contracts).toLocaleString() + (r.tob_contracts != null && r.max_contracts > r.tob_contracts + 1e-9 ? ` <span class="muted">(${Math.round(r.tob_contracts).toLocaleString()} at top of book)</span>` : "") : "—")}
      ${hRows}
    </div>${depth}""</div>`;
  return `<tr class="detail"><td colspan="${COLS().length}">
    <div class="drawer">${bd}${kCard}${pCard}</div></td></tr>`;
}

function renderBody() {
  $("pairedWrap").hidden = true;
  $("grid").hidden = false;
  const rows = sortRows(filtered());
  $("rowCount").textContent = `${rows.length} of ${RAW.length} markets`;
  $("empty").hidden = rows.length > 0;
  const html = [];
  for (const r of rows) {
    const id = rid(r), isOpen = id === openKey;
    // Favorable-basis arb: a guaranteed arb PLUS a double-win strike gap (the
    // "In Between" region pays on BOTH legs -> bonus). mid_pnl > 0 flags it;
    // plain same-strike arbs have mid_pnl == null.
    const favBasis = r.status === "ARB" && r.mid_pnl != null && r.mid_pnl > 0;
    html.push(`<tr class="r${isOpen ? " open" : ""}${favBasis ? " arb-fav" : ""}" data-id="${esc(id)}">` +
      COLS().map((c) => {
        const cls = c.align === "l" ? "l" : "";
        if (c.k === "_mkt") return `<td class="${cls}">${cellMarket(r)}</td>`;
        if (c.k === "_px") return `<td class="${cls}">${cellPx(r)}</td>`;
        if (c.k === "_window") return `<td class="${cls}">${cellWindow(r)}</td>`;
        if (c.k === "_settle") return `<td class="${cls}">${cellSettle(r)}</td>`;
        if (c.k === "best_side") {
          let bs = r.best_side || "—";
          if (MODE === "hourly" && bs !== "—")
            bs = bs === "YES+NO" ? "K-Yes · P-Down" : "K-No · P-Up";
          else if (MODE === "daily" && bs !== "—")
            bs = bs === "YES+NO" ? "K-Yes · I-No" : "K-No · I-Yes";
          return `<td class="${cls}"><span class="mono">${esc(bs)}</span></td>`;
        }
        return `<td class="${cls}">${fmt(r[c.k], c.f)}</td>`;
      }).join("") + "</tr>");
    if (isOpen) html.push(drawer(r));
  }
  $("body").innerHTML = html.join("");
  document.querySelectorAll("#body tr.r").forEach((tr) => {
    tr.onclick = () => {
      openKey = openKey === tr.dataset.id ? null : tr.dataset.id;
      renderBody();
    };
  });
}

function renderSummary(s) {
  const cards = MODE === "hourly" ? [
    ["ARB", "Edges", "s-arb"], ["NO ARB", "No edge", ""],
    ["BAD BASIS", "Bad basis", "s-bad"], ["NO DATA", "No data", ""],
    ["total", "Assets", ""],
  ] : MODE === "daily" ? [
    ["ARB", "Live arbs", "s-arb"], ["NO ARB", "No edge", ""],
    ["BAD BASIS", "Bad basis", "s-bad"], ["NO KALSHI", "No K match", ""],
    ["total", "Contracts", ""],
  ] : [
    ["ARB", "Live arbs", "s-arb"], ["NO ARB", "No edge", ""],
    ["BAD BASIS", "Bad basis", "s-bad"], ["LOW SIZE", "Low size", "s-warn"],
    ["NO POLY", "No Poly", ""], ["total", "Scanned", ""],
  ];
  $("summary").innerHTML = cards.map(([k, l, cls]) =>
    `<div class="stat ${cls}"><div class="n">${s[k] ?? 0}</div>
     <div class="l">${l}</div></div>`).join("") +
    `<div class="stat"><div class="n">${s.fetch_ms ?? "—"}<span style="font-size:13px;color:var(--faint)">ms</span></div>
     <div class="l">Fetch time</div></div>`;
}

function renderHero(rows) {
  const hourly = MODE === "hourly";
  const top = rows.filter((r) => r.status === "ARB" && r.net_return > 0)
    .sort((a, b) => b.net_return - a.net_return).slice(0, 4);
  const h = $("hero");
  h.querySelector("h2").textContent =
    hourly ? "Top dislocations" : "Top opportunities";
  h.querySelector(".muted").textContent = hourly
    ? "combined cost < $1 — speculative, basis risk applies"
    : "positive guaranteed return, ranked";
  if (!top.length) { h.hidden = true; return; }
  h.hidden = false;
  const hpct = (v) => v != null ? "+" + (v * 100).toFixed(2) + "%" : "—";
  const hann = (v) => v != null ? (v * 100).toFixed(0) + "% ann" : "— ann";
  const hct = (v) => Math.round(v || 0).toLocaleString();
  // Monthly cards split into two rows: the top-of-book rate (best price, least
  // size) and the full positive-P&L sweep (most dollars, blended-down rate).
  // They answer different questions, so showing one without the other misleads.
  const hbody = (r) => {
    if (hourly) return `
      <div class="ret">${hpct(r.net_return)}</div>
      <div class="meta">
        <span><b>${r.minutes_to_resolve ?? "—"}m</b> left</span>
        <span><b>${r.divergence != null ? (r.divergence * 100).toFixed(3) + "%" : "—"}</b> feed Δ</span>
        <span><b>${hct(r.max_contracts)}</b> ct</span>
      </div>`;
    const tobCt = r.tob_contracts != null ? r.tob_contracts : r.max_contracts;
    const tobGain = (r.worst_pnl || 0) * (tobCt || 0);
    const deeper = (r.depth_levels || []).length > 1;
    return `
      <div class="hsplit">
        <div class="hrow">
          <div class="hlbl">Top of book</div>
          <div class="hnum"><span class="ret">${hpct(r.net_return)}</span><span class="hsub">${hann(r.annualized)}</span></div>
          <div class="meta"><span><b>${hct(tobCt)}</b> ct</span>
            <span><b>$${tobGain.toFixed(2)}</b> locked</span></div>
        </div>
        <div class="hrow sweep">
          <div class="hlbl">Full sweep</div>
          ${deeper ? `
          <div class="hnum"><span class="ret">${hpct(r.depth_net_return)}</span><span class="hsub">${hann(r.depth_annualized)}</span></div>
          <div class="meta"><span><b>${hct(r.max_contracts)}</b> ct</span>
            <span><b>$${(r.total_gain || 0).toFixed(2)}</b> locked</span></div>`
          : `<div class="hnone">no depth past the top level</div>`}
        </div>
      </div>`;
  };
  $("heroCards").innerHTML = top.map((r) => `
    <div class="hcard${hourly ? " spec" : ""}" data-id="${esc(rid(r))}">
      <div class="glow"></div>
      <div class="top">${thumb(r, 38)}
        <div class="q">${esc(r.kalshi_title || r.poly_question || r.asset)}</div></div>
      ${hbody(r)}
      <div class="hfoot"><span class="mono">${esc(r.best_side)}</span></div>
    </div>`).join("");
  document.querySelectorAll(".hcard").forEach((c) => c.onclick = () => {
    openKey = c.dataset.id;
    document.querySelector("#statusSeg button.on")?.classList.remove("on");
    document.querySelector('#statusSeg button[data-s=""]').classList.add("on");
    renderBody();
    document.querySelector(`#body tr.r[data-id="${CSS.escape(c.dataset.id)}"]`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  });
}

let POS_DATA = null;

const usd = (n, d = 2) => n == null ? "—"
  : "$" + (+n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
function pnl(n, pct) {
  if (n == null) return '<span class="dimv">—</span>';
  const c = n > 0 ? "pos" : n < 0 ? "neg" : "dimv";
  const p = pct != null ? ` <span class="pp">(${(pct * 100).toFixed(1)}%)</span>` : "";
  return `<span class="num ${c}">${n > 0 ? "+" : n < 0 ? "-" : ""}${usd(Math.abs(n), 0)}${p}</span>`;
}

function pctCell(v) {
  if (v == null) return '<span class="dimv">—</span>';
  const c = v > 0 ? "pos" : v < 0 ? "neg" : "dimv";
  return `<span class="num ${c}">${v > 0 ? "+" : ""}${(v * 100).toFixed(1)}%</span>`;
}

function kindBadge(r) {
  const m = {
    matched: ["matched", "#16c784", "#16c78422"],
    "basis+": ["basis +", "#e0a93b", "#e0a93b22"],
    "basis-": ["basis −", "#ef5350", "#ef535022"],
    single: ["one leg", "#8a93a6", "#8a93a622"],
  };
  const fb = r.complete ? ["paired", "#16c784", "#16c78422"] : m.single;
  const [label, fg, bg] = m[r.kind] || fb;
  return `<span class="kindpill" style="background:${bg};color:${fg}">${label}</span>`;
}

function strikeBand(r) {
  if (r.low_strike == null && r.high_strike == null) return "";
  const f = (v) => (v == null ? "?" : v >= 1000 ? `${(v / 1000).toLocaleString()}k` : `${v}`);
  if (r.kind === "matched") return `<span class="dimv mono">@ ${f(r.low_strike)}</span>`;
  const lo = Math.min(r.low_strike, r.high_strike);
  const hi = Math.max(r.low_strike, r.high_strike);
  return `<span class="dimv mono">${f(lo)}–${f(hi)}</span>`;
}

// ---- Manual per-pair lot assignment (for ambiguous split legs) -------------
let ASG_REF = null;

const contraLabel = (c) => {
  const m = (c.contra_ref || "").match(/-(\d+)$/);
  return m ? "$" + (+m[1] / 100).toLocaleString() : esc(c.contra_market || c.contra_ref || "?");
};
const fmtTs = (ts) => {
  const d = new Date((ts || 0) * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getMonth() + 1}/${d.getDate()} ${p(d.getHours())}:${p(d.getMinutes())}`;
};

function toast(msg, kind) {
  const t = $("toast");
  t.textContent = msg; t.className = "toast" + (kind ? " " + kind : "");
  t.hidden = false; clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), kind === "err" ? 7000 : 2800);
}

function applyChrome() {
  const scanner = MODE !== "positions";
  $("summary").hidden = !scanner;
  document.querySelector(".toolbar").hidden = !scanner;
  document.querySelector("main").hidden = !scanner;
  $("positions").hidden = scanner;
  if (!scanner) { $("hero").hidden = true; $("hbanner").hidden = true; }
  if (MODE !== "daily") $("ibkrSetup").hidden = true;
}

function showIbkrSetup(payload) {
  const reason = payload.reason || "";
  const gw = payload.gateway || {};
  const titleEl = $("ibkrSetupTitle");
  const msgEl = $("ibkrSetupMsg");
  if (reason === "no_contracts_file") {
    titleEl.textContent = "No IBKR contracts configured.";
    msgEl.innerHTML = ` Copy <code>data/ibkr_contracts.example.json</code> → <code>data/ibkr_contracts.json</code> and fill in your daily-BTC conids. `;
  } else if (reason === "gateway_not_running") {
    titleEl.textContent = "IBKR Gateway not running.";
    msgEl.innerHTML = ` Start the Client Portal Gateway on this Mac (default port 5000). Detail: <code>${esc(gw.message || "no connection")}</code>. `;
  } else if (payload.error) {
    titleEl.textContent = "IBKR session needs re-auth.";
    msgEl.innerHTML = ` ${esc(payload.error)} `;
  } else if (reason && reason.startsWith("bad_json")) {
    titleEl.textContent = "data/ibkr_contracts.json is invalid JSON.";
    msgEl.innerHTML = ` <code>${esc(reason)}</code> `;
  } else {
    titleEl.textContent = "IBKR not connected.";
    msgEl.textContent = "";
  }
  $("ibkrSetup").hidden = false;
}

async function load(force) {
  const b = $("refresh");
  b.disabled = true; b.classList.add("loading");
  b.querySelector(".blabel").textContent =
    MODE === "positions" ? "Loading…" : "Scanning…";
  applyChrome();
  try {
    const res = await fetch(endpoint() + (force ? "?force=1" : ""));
    const d = await res.json();
    if (!res.ok) throw new Error(d.error || res.status);
    if (MODE === "daily" && d.configured === false) {
      RAW = [];
      renderSummary({ total: 0 });
      renderHero(RAW);
      renderBody();
      showIbkrSetup(d);
      $("meta").textContent = "IBKR not connected";
    } else if (MODE === "daily" && d.stale_settled) {
      RAW = [];
      renderSummary({ total: 0 });
      renderHero(RAW);
      renderBody();
      $("ibkrSetup").hidden = false;
      $("ibkrSetupTitle").textContent = "Today's ladder settled at 5pm ET.";
      $("ibkrSetupMsg").innerHTML =
        ` ${esc(d.message || "Waiting for the next day's contracts to list.")} `;
      $("meta").textContent = "awaiting next-day ladder";
    } else {
      RAW = d.rows || [];
      renderSummary(d.summary || { total: RAW.length });
      renderHero(RAW);
      renderBody();
      updateBanner(d.summary);
      if (MODE === "daily") {
        $("ibkrSetup").hidden = false;
        $("ibkrSetupTitle").textContent = "IBKR connected.";
        $("ibkrSetupMsg").innerHTML =
          ' Local-only feature; updates as long as the Client Portal Gateway is running and authenticated. ';
      }
      const unit = MODE === "hourly" ? "assets"
        : MODE === "daily" ? "pairs" : "pairs";
      const ga = d.generated_at || "—";
      $("meta").textContent = `${ga} · ${RAW.length} ${unit}`;
    }
  } catch (e) {
    toast((MODE === "positions" ? "Load" : "Scan") + " failed: " + e.message, "err");
    $("meta").textContent = "load failed";
  } finally {
    b.disabled = false; b.classList.remove("loading");
    b.querySelector(".blabel").textContent = "Refresh";
  }
}

function updateBanner(summary) {
  const b = $("hbanner");
  if (MODE !== "hourly") { b.hidden = true; return; }
  b.hidden = false;
  const d = summary && summary.max_divergence;
  const el = $("hbDiv");
  if (d == null) { el.textContent = "n/a"; el.classList.remove("hot"); return; }
  const pct = (d * 100);
  el.textContent = "max " + pct.toFixed(3) + "%";
  el.classList.toggle("hot", pct >= 0.3);
}

// ---- Prepare-trade ticket (Step 1: read-only preview, places no orders) ----
let TICKET = null;

const hostShort = (h) => (h || "").replace(/^https?:\/\//, "");
const timeoutMsg = (e, what) => (e && e.name === "AbortError")
  ? `${what} timed out — Kalshi/Polymarket were slow to respond. Try again.`
  : `${what} failed: ${(e && e.message) || e}`;

// ---- Polymarket hedge leg (signer sidecar) ----

function polyResultHTML(d) {
  const sp = d.order_spec || {};
  const banner = d.posted
    ? `<div class="tk-banner placed"><b>✅ POLY HEDGE PLACED.</b></div>`
    : `<div class="tk-banner failed"><b>✗ POLY NOT PLACED.</b> ${esc(d.error || "Polymarket rejected the order.")}</div>`;
  const line = `<div class="tk-order">
      <span class="tag p">Polymarket</span><span class="tk-buy">BUY</span>
      <span class="tk-sz">${(sp.size || 0).toLocaleString()}<span class="dimv"> sh</span></span>
      <span class="tk-px">@ $${sp.price != null ? (+sp.price).toFixed(3) : "—"}</span>
    </div>`;
  return `${banner}<div class="tk-orders">${line}</div>
    <div class="dimv" style="margin-top:6px">${esc(d.hedge_note || "")}</div>`;
}

function wire() {
  document.querySelectorAll("#tabs button").forEach((b) => b.onclick = () => {
    if (b.classList.contains("on")) return;
    document.querySelector("#tabs button.on")?.classList.remove("on");
    b.classList.add("on");
    MODE = b.dataset.m;
    openKey = null;
    sortKey = "net_return"; sortAsc = false;
    SCAN_POS = null;
    $("pairedWrap").hidden = true;
    $("pairedBtn").hidden = (MODE !== "monthly");
    document.querySelector("#statusSeg button.on")?.classList.remove("on");
    document.querySelector('#statusSeg button[data-s=""]').classList.add("on");
    $("search").value = "";
    renderHead();
    load(true);
  });
  $("pairedBtn").hidden = (MODE !== "monthly");
  $("refresh").onclick = () => load(true);
  $("search").addEventListener("input", renderBody);
  document.querySelectorAll("#statusSeg button").forEach((b) => b.onclick = () => {
    document.querySelector("#statusSeg button.on")?.classList.remove("on");
    b.classList.add("on"); renderBody();
  });
  let timer = null;
  $("auto").onchange = (e) => {
    clearInterval(timer);
    // Pause auto-refresh while the trade ticket is open — the heavy force=1 scan
    // competes with the modal's quote re-checks for the browser's connection pool
    // (~6/host), which is what made "Re-checking the live edge…" appear to hang.
    if (e.target.checked) {
      load(true);
      timer = setInterval(() => { if ($("ticketModal").hidden) load(true); }, 8000);
    }
  };

  // prepare-ticket modal controls

  // pair-assignment modal controls
  const closeAsg = () => ($("assignModal").hidden = true);
  $("asgClose").onclick = closeAsg;
  $("asgCancel").onclick = closeAsg;
  $("assignModal").addEventListener("click", (e) => {
    if (e.target === $("assignModal")) closeAsg();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!$("assignModal").hidden) return closeAsg();
  });
}

renderHead();
wire();
load(false);
