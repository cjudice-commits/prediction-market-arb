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

let MODE = "monthly", POS_VIEW = "venue";
const COLS = () => (MODE === "hourly" ? COLS_HOURLY : COLS_MONTHLY);
const endpoint = () => MODE === "positions" ? "/api/positions"
  : MODE === "hourly" ? "/api/scan/hourly" : "/api/scan";

let RAW = [], sortKey = "net_return", sortAsc = false, openKey = null;
const $ = (id) => document.getElementById(id);
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
  const title = r.kalshi_title || r.poly_question ||
    `${r.asset} ${r.direction} ${r.kalshi_strike ?? ""}`;
  const strikes = `K $${r.kalshi_strike ?? "—"} · P $${r.poly_strike ?? "—"}`;
  return `<div class="mkt">${thumb(r)}
    <div class="info">
      <div class="qa">
        <span class="achip" style="background:${ac(r.asset)}22;color:${ac(r.asset)}">${esc(r.asset || "?")}</span>
        <span class="q" title="${esc(title)}">${esc(title)}</span>
      </div>
      <div class="sub"><span class="dir">${esc(r.direction || "")}</span> &nbsp;${esc(strikes)}</div>
    </div></div>`;
}

function cellPx(r) {
  const k = r.kalshi_price, p = r.poly_price, c = r.combined_cost;
  if (k == null || p == null) return '<span class="dimv">—</span>';
  const pct = Math.min(100, (c / 1) * 100);
  const col = c < 1 ? "var(--good)" : "var(--bad)";
  return `<div class="pricebar">
    <div class="lbl"><span>K ${k.toFixed(3)}</span><span>P ${p.toFixed(3)}</span></div>
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
  return b ? b.dataset.s : "";
}
function activeAssets() {
  return [...document.querySelectorAll("#assetChips .ac.on")].map((e) => e.dataset.a);
}

function filtered() {
  const q = $("search").value.trim().toLowerCase();
  const s = activeStatus(), as = activeAssets(), fav = $("favOnly").checked;
  return RAW.filter((r) => {
    if (s && r.status !== s) return false;
    if (as.length && !as.includes(r.asset)) return false;
    if (fav && !r.basis_favorable) return false;
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
      ${kv("Total guaranteed $", money(r.total_gain, 2))}
      ${kv("Days to expiry", r.days_to_expiry ?? "—")}`;
  const bd = `<div class="vcard">
    <h4>${hourly ? "Speculative breakdown" : "Arb breakdown"}</h4>
    ${scen}
    ${hourly ? '<div class="warn-line">Different settlement feeds — legs are not a locked hedge.</div>' : ""}
    <div class="kv">
      ${kv("Best side", esc(r.best_side || "—"))}
      ${kv("Combined cost", r.combined_cost != null ? "$" + r.combined_cost.toFixed(4) : "—")}
      ${kv("Total fee / ct", r.total_fee != null ? "$" + r.total_fee.toFixed(4) : "—")}
      ${kv("Net return", fmt(r.net_return, "pctBig"))}
      ${kv("Basis %", fmt(r.basis_pct, "pct") + (r.basis_favorable ? ' <span class="pos">✓ favorable</span>' : ' <span class="neg">✗ risk</span>'))}
      ${kv("Max contracts", r.max_contracts != null ? Math.round(r.max_contracts).toLocaleString() : "—")}
      ${hRows}
    </div></div>`;
  return `<tr class="detail"><td colspan="${COLS().length}">
    <div class="drawer">${kCard}${pCard}${bd}</div></td></tr>`;
}

function renderBody() {
  const rows = sortRows(filtered());
  $("rowCount").textContent = `${rows.length} of ${RAW.length} markets`;
  $("empty").hidden = rows.length > 0;
  const html = [];
  for (const r of rows) {
    const id = rid(r), isOpen = id === openKey;
    html.push(`<tr class="r${isOpen ? " open" : ""}" data-id="${esc(id)}">` +
      COLS().map((c) => {
        const cls = c.align === "l" ? "l" : "";
        if (c.k === "_mkt") return `<td class="${cls}">${cellMarket(r)}</td>`;
        if (c.k === "_px") return `<td class="${cls}">${cellPx(r)}</td>`;
        if (c.k === "_window") return `<td class="${cls}">${cellWindow(r)}</td>`;
        if (c.k === "best_side") {
          let bs = r.best_side || "—";
          if (MODE === "hourly" && bs !== "—")
            bs = bs === "YES+NO" ? "K-Yes · P-Down" : "K-No · P-Up";
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
  ] : [
    ["ARB", "Live arbs", "s-arb"], ["NO ARB", "No edge", ""],
    ["BAD BASIS", "Bad basis", "s-bad"], ["LOW SIZE", "Low size", "s-warn"],
    ["NO DATA", "No data", ""], ["total", "Scanned", ""],
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
  $("heroCards").innerHTML = top.map((r) => `
    <div class="hcard${hourly ? " spec" : ""}" data-id="${esc(rid(r))}">
      <div class="glow"></div>
      <div class="top">${thumb(r, 38)}
        <div class="q">${esc(r.kalshi_title || r.poly_question || r.asset)}</div></div>
      <div class="ret">+${(r.net_return * 100).toFixed(2)}%</div>
      <div class="meta">
        ${hourly
          ? `<span><b>${r.minutes_to_resolve ?? "—"}m</b> left</span>
             <span><b>${r.divergence != null ? (r.divergence * 100).toFixed(3) + "%" : "—"}</b> feed Δ</span>`
          : `<span><b>${r.annualized ? (r.annualized * 100).toFixed(0) + "%" : "—"}</b> annual</span>
             <span><b>$${(r.total_gain || 0).toFixed(2)}</b> locked</span>`}
        <span><b>${Math.round(r.max_contracts || 0).toLocaleString()}</b> ct</span>
        <span class="mono">${esc(r.best_side)}</span>
      </div>
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
  return `<span class="num ${c}">${n > 0 ? "+" : ""}${usd(n)}${p}</span>`;
}

function posThumb(p) {
  if (p.icon) return `<img class="thumb" style="width:30px;height:30px"
    src="${esc(p.icon)}" loading="lazy" onerror="this.remove()">`;
  const a = p.asset ? p.asset.slice(0, 4) : "?";
  return `<div class="badge" style="width:30px;height:30px;background:${ac(p.asset)}">${esc(a)}</div>`;
}

function venuePanel(name, tag, v) {
  const setup = (body) => `<div class="vcard setup">
    <h4><span class="tag ${tag}">${name}</span> not connected</h4>${body}</div>`;
  if (!v || v.configured === false) {
    if (name === "Polymarket")
      return setup(`<p>Add your wallet to <code>data/secrets.json</code>:</p>
        <pre>{ "polymarket_wallet": "0xYourProxyWallet" }</pre>
        <p class="dimv">Public address only — no private key. Copy
        <code>data/secrets.example.json</code> to start.</p>`);
    return setup(`<p>Create a read-only API key in Kalshi → Settings → API Keys,
      save the private-key file locally, then add to
      <code>data/secrets.json</code>:</p>
      <pre>{ "kalshi_key_id": "…",
  "kalshi_private_key_path": "/abs/path/key.pem" }</pre>
      <p class="dimv">Signed locally via openssl — the key never leaves your
      machine. ${v && v.reason === "key_file_not_found"
        ? '<span class="neg">Key file not found at that path.</span>' : ""}</p>`);
  }
  if (v.error)
    return `<div class="vcard"><h4><span class="tag ${tag}">${name}</span></h4>
      <p class="neg">${esc(v.error)}</p></div>`;
  const ps = v.positions || [];
  const head = `<div class="vc-head">
    <h4><span class="tag ${tag}">${name}</span> ${ps.length} open</h4>
    <span class="vc-tot">${usd(v.total_value)}</span></div>`;
  if (!ps.length)
    return `<div class="vcard">${head}<p class="dimv">No open positions.</p></div>`;
  const rows = ps.sort((a, b) => (b.value || 0) - (a.value || 0)).map((p) => `
    <tr>
      <td class="l"><div class="mkt">${posThumb(p)}<div class="info">
        <div class="q" title="${esc(p.market)}">${esc(p.market)}</div>
        <div class="sub mono">${esc(p.ref || "")}</div></div></div></td>
      <td><span class="sidetag ${String(p.side).toLowerCase()}">${esc(p.side)}</span></td>
      <td class="num">${(p.size || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
      <td class="num mono">${p.avg_price != null ? (+p.avg_price).toFixed(3) : "—"} → ${p.cur_price != null ? (+p.cur_price).toFixed(3) : "—"}</td>
      <td class="num">${usd(p.cost)}</td>
      <td class="num">${usd(p.value)}</td>
      <td class="num">${pnl(p.pnl, p.pnl_pct)}</td>
    </tr>`).join("");
  return `<div class="vcard">${head}
    <table class="ptbl"><thead><tr>
      <th class="l">Market</th><th>Side</th><th>Size</th>
      <th>Avg → Cur</th><th>Cost</th><th>Value</th><th>P&amp;L</th>
    </tr></thead><tbody>${rows}</tbody></table></div>`;
}

function pairedView(rows) {
  if (!rows || !rows.length)
    return `<div class="vcard"><h4>Paired exposure</h4>
      <p class="dimv">No held positions match a known Kalshi↔Polymarket arb
      pair (pairs come from the Monthly map). Positions still show under
      <b>By venue</b>.</p></div>`;
  const body = rows.map((r) => {
    const legs = r.legs.map((l) => `<div class="leg2">
      <span class="tag ${l.venue === "Kalshi" ? "k" : "p"}">${l.venue[0]}</span>
      <span class="sidetag ${String(l.side).toLowerCase()}">${esc(l.side)}</span>
      <span class="dimv mono">${esc(l.ref || "")}</span>
      <span class="num">${(l.size || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
      <span>${pnl(l.pnl)}</span></div>`).join("");
    return `<tr>
      <td class="l"><span class="achip" style="background:${ac(r.asset)}22;color:${ac(r.asset)}">${esc(r.asset || "?")}</span>
        ${r.complete ? '<span class="okpill">paired</span>' : '<span class="onepill">one leg</span>'}</td>
      <td class="l">${legs}</td>
      <td class="num">${usd(r.cost)}</td>
      <td class="num">${usd(r.value)}</td>
      <td class="num">${pnl(r.pnl, r.pnl_pct)}</td>
    </tr>`;
  }).join("");
  return `<div class="vcard"><h4>Paired / netted exposure</h4>
    <table class="ptbl"><thead><tr>
      <th class="l">Pair</th><th class="l">Legs</th>
      <th>Cost</th><th>Value</th><th>Net P&amp;L</th>
    </tr></thead><tbody>${body}</tbody></table></div>`;
}

function historyCard(name, tag, rows, note) {
  const head = `<div class="vc-head">
    <h4><span class="tag ${tag}">${name}</span> ${rows.length} settled${note ? ` <span class="dimv">· ${note}</span>` : ""}</h4>
    <span class="vc-tot">${pnl(rows.reduce((s, r) => s + (r.realized || 0), 0))}</span></div>`;
  if (!rows.length)
    return `<div class="vcard">${head}<p class="dimv">No settled markets found.</p></div>`;
  const body = rows.slice().sort((a, b) =>
    (b.settled_date || "").localeCompare(a.settled_date || "")).map((r) => `
    <tr>
      <td class="l"><div class="mkt">
        ${r.icon ? `<img class="thumb" style="width:26px;height:26px" src="${esc(r.icon)}" loading="lazy" onerror="this.remove()">` : ""}
        <div class="info"><div class="q" title="${esc(r.market)}">${esc(r.market)}</div>
        <div class="sub mono">${esc(r.settled_date || "")}</div></div></div></td>
      <td><span class="sidetag ${String(r.result).toLowerCase()}">${esc(r.result)}</span></td>
      <td><span class="sidetag ${String(r.side).toLowerCase()}">${esc(r.side || "—")}</span></td>
      <td class="num">${(r.size || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
      <td class="num">${usd(r.cost)}</td>
      <td class="num">${usd(r.payout)}</td>
      <td class="num">${pnl(r.realized)}</td>
    </tr>`).join("");
  return `<div class="vcard">${head}
    <table class="ptbl"><thead><tr>
      <th class="l">Market</th><th>Result</th><th>Side</th><th>Size</th>
      <th>Cost</th><th>Payout</th><th>Realized</th>
    </tr></thead><tbody>${body}</tbody></table></div>`;
}

function historyView(h) {
  h = h || {};
  return `<div class="vstack">
    ${historyCard("Kalshi", "k", h.kalshi || [], "exact (settlements)")}
    ${historyCard("Polymarket", "p", h.polymarket || [], "reconstructed · payout derived")}
  </div>`;
}

function renderPositions(d) {
  if (d) POS_DATA = d;
  d = POS_DATA;
  const t = d.totals || {};
  const stat = (n, l, cls) =>
    `<div class="stat ${cls || ""}"><div class="n">${n}</div><div class="l">${l}</div></div>`;
  const pstat = (n, l, big) => {
    if (n == null)
      return `<div class="stat"><div class="n">—</div><div class="l">${l}</div></div>`;
    const cls = n > 0 ? "s-arb" : n < 0 ? "s-bad" : "";
    const v = (n > 0 ? "+" : n < 0 ? "-" : "") +
      "$" + Math.abs(n).toLocaleString(undefined,
        { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return `<div class="stat ${cls}${big ? " stat-lg" : ""}">
      <div class="n">${v}</div><div class="l">${l}</div></div>`;
  };
  const strip = `<div class="summary">
    ${stat(usd(t.poly_value), "Polymarket value")}
    ${stat(t.kalshi_value == null ? "—" : usd(t.kalshi_value), "Kalshi value")}
    ${pstat(t.kalshi_pnl, "Kalshi P&L")}
    ${pstat(t.poly_pnl, "Polymarket P&L")}
    ${(() => {
      const n = t.total_pnl, r = t.total_return;
      if (n == null)
        return `<div class="stat stat-lg"><div class="n">—</div><div class="l">Total P&L</div></div>`;
      const cls = n > 0 ? "s-arb" : n < 0 ? "s-bad" : "";
      const v = (n > 0 ? "+" : n < 0 ? "-" : "") + "$" +
        Math.abs(n).toLocaleString(undefined,
          { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      const sub = r == null ? "" :
        `<div class="stat-sub ${r > 0 ? "pos" : r < 0 ? "neg" : ""}">${r > 0 ? "+" : ""}${(r * 100).toFixed(2)}% return</div>`;
      return `<div class="stat stat-lg ${cls}"><div class="n">${v}</div>${sub}<div class="l">Total P&L</div></div>`;
    })()}
    ${pstat(t.total_realized, "Realized P&L")}
    ${stat(t.open_positions ?? 0, "Open positions")}
    ${stat(t.settled_count ?? 0, "Settled")}
    ${stat(t.paired_count ?? 0, "Complete pairs", "s-arb")}</div>`;
  const tab = (v, l) =>
    `<button data-v="${v}" class="${POS_VIEW === v ? "on" : ""}">${l}</button>`;
  const seg = `<div class="seg posseg">
    ${tab("venue", "By venue")}${tab("paired", "Paired")}${tab("history", "History")}
  </div>`;
  const content = POS_VIEW === "paired" ? pairedView(d.paired)
    : POS_VIEW === "history" ? historyView(d.history)
    : `<div class="vstack">
        ${venuePanel("Polymarket", "p", d.polymarket)}
        ${venuePanel("Kalshi", "k", d.kalshi)}</div>`;
  $("positions").innerHTML = strip +
    `<div class="postoolbar">${seg}
      <span class="dimv pos-note">Read-only · credentials stay in local
      data/secrets.json</span></div>` + content;
  $("positions").querySelectorAll(".posseg button").forEach((b) =>
    b.onclick = () => {
      POS_VIEW = b.dataset.v; renderPositions();
    });
}

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
    if (MODE === "positions") {
      renderPositions(d);
      $("meta").textContent =
        `${d.generated_at} · ${d.totals.open_positions} open`;
    } else {
      RAW = d.rows;
      renderSummary(d.summary);
      buildAssetChips();
      renderHero(RAW);
      renderBody();
      updateBanner(d.summary);
      const unit = MODE === "hourly" ? "assets" : "pairs";
      $("meta").textContent = `${d.generated_at} · ${d.summary.total} ${unit}`;
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

function buildAssetChips() {
  const el = $("assetChips");
  if (el.dataset.built) return;
  const assets = [...new Set(RAW.map((r) => r.asset))].filter(Boolean).sort();
  el.innerHTML = assets.map((a) =>
    `<span class="ac" data-a="${a}"><span class="blob" style="background:${ac(a)}"></span>${a}</span>`).join("");
  el.dataset.built = "1";
  el.querySelectorAll(".ac").forEach((c) => c.onclick = () => {
    c.classList.toggle("on"); renderBody();
  });
}

async function loadSettings() {
  const s = await (await fetch("/api/settings")).json();
  s_kfee.value = s.kalshi_fee_rate; s_pfee.value = s.poly_fee_rate;
  s_minret.value = s.min_net_return; s_minvol.value = s.min_poly_volume;
  s_minct.value = s.min_contracts;
}

function wire() {
  document.querySelectorAll("#tabs button").forEach((b) => b.onclick = () => {
    if (b.classList.contains("on")) return;
    document.querySelector("#tabs button.on")?.classList.remove("on");
    b.classList.add("on");
    MODE = b.dataset.m;
    openKey = null;
    sortKey = "net_return"; sortAsc = false;
    const ac = $("assetChips"); ac.dataset.built = ""; ac.innerHTML = "";
    document.querySelector("#statusSeg button.on")?.classList.remove("on");
    document.querySelector('#statusSeg button[data-s=""]').classList.add("on");
    $("search").value = "";
    renderHead();
    load(true);
  });
  $("refresh").onclick = () => load(true);
  $("search").addEventListener("input", renderBody);
  $("favOnly").addEventListener("change", renderBody);
  document.querySelectorAll("#statusSeg button").forEach((b) => b.onclick = () => {
    document.querySelector("#statusSeg button.on")?.classList.remove("on");
    b.classList.add("on"); renderBody();
  });
  let timer = null;
  $("auto").onchange = (e) => {
    clearInterval(timer);
    if (e.target.checked) { load(true); timer = setInterval(() => load(true), 8000); }
  };
  $("settingsBtn").onclick = async () => {
    await loadSettings(); $("settingsModal").hidden = false;
  };
  $("closeSettings").onclick = () => ($("settingsModal").hidden = true);
  $("saveSettings").onclick = async () => {
    const body = {
      kalshi_fee_rate: +s_kfee.value, poly_fee_rate: +s_pfee.value,
      min_net_return: +s_minret.value, min_poly_volume: +s_minvol.value,
      min_contracts: +s_minct.value,
    };
    const r = await fetch("/api/settings", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (r.ok) { $("settingsModal").hidden = true; toast("Saved — rescanning", "ok"); load(true); }
    else toast("Save failed", "err");
  };
}

renderHead();
wire();
load(false);
