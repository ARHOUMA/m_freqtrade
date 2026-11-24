/* Backtest dashboard front end - talks to the local FastAPI backend in app.py. */
"use strict";

const $ = (sel) => document.querySelector(sel);
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const state = {
  runs: [],
  runId: null,
  run: null,         // all strategies of the selected run
  strategy: null,
  data: null,        // detail of the selected strategy
  tab: "overview",
  pair: null,
  charts: {},
  candleCache: new Map(),
  tradeLimit: 200,
  sort: {
    compare: { key: "return_dd", asc: false },
    trades: { key: "close", asc: false },
  },
};

// ---------------------------------------------------------------- formatting
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const isNum = (x) => typeof x === "number" && Number.isFinite(x);
const pct = (x, d = 1, sign = true) => (isNum(x) ? `${sign && x > 0 ? "+" : ""}${(x * 100).toFixed(d)}%` : "—");
const num = (x, d = 2) => (isNum(x) ? x.toFixed(d) : "—");
const cur = () => (state.data && state.data.meta.stake_currency) || "";
const money = (x, d = 0) =>
  isNum(x)
    ? `${x < 0 ? "−" : ""}${Math.abs(x).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })} ${cur()}`
    : "—";
const cls = (x) => (!isNum(x) ? "" : x > 0 ? "pos" : x < 0 ? "neg" : "");
const day = (ms) => new Date(ms).toISOString().slice(0, 10);
const stamp = (ms) => new Date(ms).toISOString().slice(0, 16).replace("T", " ");
const shortName = (s) => s.replace(/^SMC_/, "");
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

function duration(min) {
  if (!isNum(min)) return "—";
  const d = Math.floor(min / 1440), h = Math.floor((min % 1440) / 60), m = Math.round(min % 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}
function holding(str) {
  if (!str) return "—";
  const m = /^(?:(\d+) days?, )?(\d+):(\d+)/.exec(str);
  return m ? duration((+m[1] || 0) * 1440 + +m[2] * 60 + +m[3]) : str;
}
function rgbaFor(x, cap = 0.15) {
  if (!isNum(x) || x === 0) return "transparent";
  const a = 0.1 + 0.7 * Math.min(Math.abs(x) / cap, 1);
  return `rgba(${css(x > 0 ? "--pos-rgb" : "--neg-rgb")}, ${a.toFixed(2)})`;
}

// ---------------------------------------------------------------- data
async function api(path, params = {}) {
  const res = await fetch(`/api/${path}?${new URLSearchParams(params)}`);
  if (!res.ok) throw new Error(`${path}: ${(await res.json().catch(() => ({}))).detail || res.statusText}`);
  return res.json();
}

function showError(err) {
  const box = $("#error");
  box.hidden = !err;
  box.textContent = err ? String(err.message || err) : "";
}

function readHash() {
  return Object.fromEntries(new URLSearchParams(location.hash.slice(1)));
}
function writeHash() {
  const h = new URLSearchParams({ run: state.runId || "", s: state.strategy || "", tab: state.tab });
  if (state.pair) h.set("pair", state.pair);
  history.replaceState(null, "", `#${h}`);
}

// ---------------------------------------------------------------- charts
function chart(key, canvasId, config) {
  if (state.charts[key]) state.charts[key].destroy();
  state.charts[key] = new Chart(document.getElementById(canvasId), config);
  return state.charts[key];
}

function baseOptions(extra = {}) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: "nearest", intersect: false },
    plugins: { legend: { display: false }, tooltip: { displayColors: false } },
    ...extra,
  };
}

function timeAxis() {
  return { type: "time", time: { tooltipFormat: "yyyy-MM-dd HH:mm" }, grid: { color: css("--grid") }, ticks: { maxRotation: 0, autoSkipPadding: 18 } };
}
function valueAxis(fmt, extra = {}) {
  return { grid: { color: css("--grid") }, ticks: { callback: fmt }, ...extra };
}

function applyChartTheme() {
  Chart.defaults.color = css("--muted");
  Chart.defaults.borderColor = css("--grid");
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
}

// Labels next to scatter points (compare tab)
const pointLabels = {
  id: "pointLabels",
  afterDatasetsDraw(c) {
    const ds = c.data.datasets[0];
    if (!ds || ds.data.length > 30) return;
    const { ctx } = c;
    ctx.save();
    ctx.font = "11px " + Chart.defaults.font.family;
    c.getDatasetMeta(0).data.forEach((pt, i) => {
      const raw = ds.data[i];
      ctx.fillStyle = raw.name === state.strategy ? css("--text") : css("--muted");
      ctx.fillText(shortName(raw.name), pt.x + 7, pt.y - 6);
    });
    ctx.restore();
  },
};

// ---------------------------------------------------------------- pickers
function populateRuns() {
  const sel = $("#run-select");
  sel.innerHTML = "";
  const groups = new Map();
  for (const r of state.runs) {
    const parts = r.folder.split("/");
    const group = parts.length > 2 ? parts.slice(0, 2).join("/") : parts[0];
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(r);
  }
  for (const [group, runs] of groups) {
    const og = document.createElement("optgroup");
    og.label = group;
    for (const r of runs) {
      const rest = r.folder.split("/").slice(2).join("/");
      const what = r.strategies.length > 1 ? `${r.strategies.length} strategies` : r.strategies[0];
      const when = new Date(r.started * 1000).toISOString().slice(0, 16).replace("T", " ");
      const opt = new Option(`${rest ? rest + " · " : ""}${what} · ${r.timeframe} · ${when}`, r.id);
      og.appendChild(opt);
    }
    sel.appendChild(og);
  }
}

async function selectRun(id, wanted) {
  state.runId = id;
  $("#run-select").value = id;
  state.run = await api("run", { id });
  const sel = $("#strategy-select");
  sel.innerHTML = "";
  for (const s of state.run.strategies) sel.appendChild(new Option(s.strategy, s.strategy));
  const names = state.run.strategies.map((s) => s.strategy);
  await selectStrategy(names.includes(wanted) ? wanted : names[0]);
}

async function selectStrategy(name) {
  state.strategy = name;
  $("#strategy-select").value = name;
  state.data = await api("strategy", { id: state.runId, name });
  state.tradeLimit = 200;
  const pairs = state.data.meta.pairs || [];
  if (!pairs.includes(state.pair)) state.pair = null;
  renderMeta();
  await renderTab();
  writeHash();
}

function renderMeta() {
  const m = state.data.meta;
  const range = `${(m.start || "").slice(0, 10)} → ${(m.end || "").slice(0, 10)}`;
  $("#meta").innerHTML = [
    `<span><b>${esc(m.strategy)}</b></span>`,
    `<span>${esc(m.timeframe)} · ${esc(m.trading_mode || "spot")}</span>`,
    `<span>${esc(range)}</span>`,
    `<span>${(m.pairs || []).length} pairs · max ${esc(m.max_open_trades)} open</span>`,
    `<span>protections ${m.protections ? "on" : "off"}</span>`,
  ].join("");
}

// ---------------------------------------------------------------- tabs
async function renderTab() {
  for (const t of ["overview", "compare", "trades"]) {
    $(`#tab-${t}`).hidden = t !== state.tab;
    document.querySelector(`.tabs button[data-tab="${t}"]`).classList.toggle("active", t === state.tab);
  }
  applyChartTheme();
  if (!state.data) return;
  if (state.tab === "overview") renderOverview();
  else if (state.tab === "compare") renderCompare();
  else await renderTrades();
}

// ---------------------------------------------------------------- overview
function renderOverview() {
  const k = state.data.kpis;
  const cards = [
    ["Total profit", pct(k.profit_total), money(k.profit_total_abs), cls(k.profit_total)],
    ["Final balance", money(k.final_balance), `from ${money(k.starting_balance)}`, ""],
    ["CAGR", pct(k.cagr), "per year, compounded", cls(k.cagr)],
    ["Max drawdown", pct(isNum(k.max_drawdown) ? -k.max_drawdown : null), `${(k.drawdown_start || "").slice(0, 10)} → ${(k.drawdown_end || "").slice(0, 10)}`, "neg"],
    ["Return / drawdown", num(k.return_dd), "CAGR ÷ max drawdown", cls(k.return_dd)],
    ["Profit factor", num(k.profit_factor), "gross win ÷ gross loss", isNum(k.profit_factor) ? (k.profit_factor >= 1 ? "pos" : "neg") : ""],
    ["Sharpe", num(k.sharpe), `Sortino ${num(k.sortino)}`, cls(k.sharpe)],
    ["Win rate", pct(k.winrate, 1, false), `max losing streak ${k.max_consecutive_losses ?? "—"}`, ""],
    ["Trades", String(k.trades ?? "—"), `${k.trades_long ?? 0} long · ${k.trades_short ?? 0} short`, ""],
    ["Avg holding", holding(k.holding_avg), "per trade", ""],
    ["Expectancy", money(k.expectancy, 2), "avg profit per trade", cls(k.expectancy)],
    ["Market change", pct(k.market_change), "avg of the pairs, same period", cls(k.market_change)],
  ];
  $("#kpis").innerHTML = cards
    .map(([label, value, sub, c]) => `<div class="kpi"><div class="label">${label}</div><div class="value ${c}">${esc(value)}</div><div class="sub">${esc(sub)}</div></div>`)
    .join("");

  const eq = state.data.equity;
  const accent = css("--accent"), pos = css("--pos"), neg = css("--neg");
  chart("equity", "equity-chart", {
    type: "line",
    data: {
      datasets: [
        { data: eq.map((p) => ({ x: p.t, y: p.balance })), borderColor: accent, backgroundColor: css("--accent-soft"), fill: "origin", borderWidth: 2, pointRadius: 0, stepped: "before" },
        { data: [{ x: eq[0].t, y: k.starting_balance }, { x: eq[eq.length - 1].t, y: k.starting_balance }], borderColor: css("--muted"), borderDash: [4, 4], borderWidth: 1, pointRadius: 0 },
      ],
    },
    options: baseOptions({
      parsing: false,
      scales: { x: timeAxis(), y: valueAxis((v) => v.toLocaleString(), { beginAtZero: false }) },
      plugins: { legend: { display: false }, tooltip: { displayColors: false, filter: (i) => i.datasetIndex === 0, callbacks: { label: (c) => `balance ${money(c.parsed.y, 2)}` } } },
    }),
  });
  chart("dd", "dd-chart", {
    type: "line",
    data: { datasets: [{ data: eq.map((p) => ({ x: p.t, y: p.dd * 100 })), borderColor: neg, backgroundColor: `rgba(${css("--neg-rgb")}, .18)`, fill: "origin", borderWidth: 1.5, pointRadius: 0, stepped: "before" }] },
    options: baseOptions({
      parsing: false,
      scales: { x: timeAxis(), y: valueAxis((v) => `${v}%`, { max: 0 }) },
      plugins: { legend: { display: false }, tooltip: { displayColors: false, callbacks: { label: (c) => `drawdown ${c.parsed.y.toFixed(1)}%` } } },
    }),
  });

  const yr = state.data.yearly;
  chart("yearly", "yearly-chart", {
    type: "bar",
    data: { labels: yr.map((y) => y.period), datasets: [{ data: yr.map((y) => y.pct * 100), backgroundColor: yr.map((y) => (y.pct >= 0 ? pos : neg)), borderRadius: 4, maxBarThickness: 56 }] },
    options: baseOptions({
      interaction: { mode: "index", intersect: false },
      scales: { x: { grid: { display: false } }, y: valueAxis((v) => `${v}%`) },
      plugins: { legend: { display: false }, tooltip: { displayColors: false, callbacks: { label: (c) => { const y = yr[c.dataIndex]; return [`${pct(y.pct)}  (${money(y.abs)})`, `${y.trades} trades`]; } } } },
    }),
  });

  $("#sides").innerHTML = [
    ["Long", k.profit_long, k.trades_long],
    ["Short", k.profit_short, k.trades_short],
  ].map(([label, p, n]) => `<div class="side"><div class="label">${label} · ${n ?? 0} trades</div><div class="value ${cls(p)}">${pct(p)}</div></div>`).join("");
  renderSummaryTable("#tags-table", state.data.tags, "Tag");

  renderMonthly();

  const pairs = [...state.data.pairs].sort((a, b) => b.profit_total_abs - a.profit_total_abs);
  $("#pairs-box").style.height = `${Math.max(240, pairs.length * 24 + 40)}px`;
  chart("pairs", "pairs-chart", {
    type: "bar",
    data: { labels: pairs.map((p) => p.key), datasets: [{ data: pairs.map((p) => p.profit_total_abs), backgroundColor: pairs.map((p) => (p.profit_total_abs >= 0 ? pos : neg)), borderRadius: 3, maxBarThickness: 18 }] },
    options: baseOptions({
      indexAxis: "y",
      interaction: { mode: "index", intersect: false },
      scales: { x: valueAxis((v) => v.toLocaleString()), y: { grid: { display: false } } },
      plugins: { legend: { display: false }, tooltip: { displayColors: false, callbacks: { label: (c) => { const p = pairs[c.dataIndex]; return [`${money(p.profit_total_abs, 2)}`, `${p.trades} trades · win ${pct(p.winrate, 0, false)}`]; } } } },
    }),
  });

  const exits = [...state.data.exits].sort((a, b) => b.trades - a.trades);
  chart("exits", "exits-chart", {
    type: "bar",
    data: { labels: exits.map((e) => e.key), datasets: [{ data: exits.map((e) => e.profit_total_abs), backgroundColor: exits.map((e) => (e.profit_total_abs >= 0 ? pos : neg)), borderRadius: 4, maxBarThickness: 48 }] },
    options: baseOptions({
      interaction: { mode: "index", intersect: false },
      scales: { x: { grid: { display: false } }, y: valueAxis((v) => v.toLocaleString()) },
      plugins: { legend: { display: false }, tooltip: { displayColors: false, callbacks: { label: (c) => { const e = exits[c.dataIndex]; return [`${money(e.profit_total_abs, 2)} total`, `${e.trades} trades · avg ${pct(e.profit_mean, 2)}`]; } } } },
    }),
  });
  renderSummaryTable("#exits-table", exits, "Exit reason");
}

function renderSummaryTable(sel, rows, first) {
  const body = rows
    .map((r) => `<tr><td>${esc(r.key)}</td><td>${r.trades}</td><td class="${cls(r.profit_mean)}">${pct(r.profit_mean, 2)}</td><td class="${cls(r.profit_total_abs)}">${money(r.profit_total_abs)}</td><td>${pct(r.winrate, 0, false)}</td><td>${num(r.profit_factor)}</td></tr>`)
    .join("");
  $(sel).innerHTML = `<thead><tr><th>${first}</th><th>Trades</th><th>Avg</th><th>Total</th><th>Win</th><th>PF</th></tr></thead><tbody>${body || '<tr><td colspan="6">no trades</td></tr>'}</tbody>`;
}

function renderMonthly() {
  const byYear = new Map();
  for (const m of state.data.monthly) {
    const [y, mo] = m.period.split("-");
    if (!byYear.has(y)) byYear.set(y, Array(12).fill(null));
    byYear.get(y)[+mo - 1] = m;
  }
  const yearly = Object.fromEntries(state.data.yearly.map((y) => [y.period, y]));
  const head = `<thead><tr><th></th>${MONTHS.map((m) => `<th>${m}</th>`).join("")}<th>Year</th></tr></thead>`;
  const rows = [...byYear.entries()]
    .map(([y, months]) => {
      const cells = months
        .map((m) => (m ? `<td style="background:${rgbaFor(m.pct)}" title="${m.trades} trades · ${esc(money(m.abs, 2))}">${m.trades || m.pct ? pct(m.pct) : "·"}</td>` : '<td class="empty"></td>'))
        .join("");
      const t = yearly[y];
      return `<tr><th>${y}</th>${cells}<td class="year-total" style="background:${rgbaFor(t && t.pct, 0.5)}">${t ? pct(t.pct) : ""}</td></tr>`;
    })
    .join("");
  $("#monthly-table").innerHTML = head + `<tbody>${rows}</tbody>`;
}

// ---------------------------------------------------------------- compare
const COMPARE_COLS = [
  ["strategy", "Strategy", (r) => esc(r.strategy)],
  ["trading_mode", "Mode", (r) => esc(r.trading_mode)],
  ["trades", "Trades", (r) => r.trades],
  ["profit_total", "Profit", (r) => `<span class="${cls(r.profit_total)}">${pct(r.profit_total)}</span>`],
  ["cagr", "CAGR", (r) => `<span class="${cls(r.cagr)}">${pct(r.cagr)}</span>`],
  ["max_drawdown", "Max DD", (r) => pct(r.max_drawdown, 1, false)],
  ["return_dd", "Ret / DD", (r) => `<b class="${cls(r.return_dd)}">${num(r.return_dd)}</b>`],
  ["sharpe", "Sharpe", (r) => num(r.sharpe)],
  ["sortino", "Sortino", (r) => num(r.sortino)],
  ["profit_factor", "PF", (r) => num(r.profit_factor)],
  ["winrate", "Win", (r) => pct(r.winrate, 0, false)],
  ["market_change", "Market", (r) => pct(r.market_change)],
];

function sortRows(rows, { key, asc }) {
  return [...rows].sort((a, b) => {
    const x = a[key], y = b[key];
    if (x == null) return 1;
    if (y == null) return -1;
    const r = typeof x === "string" ? x.localeCompare(y) : x - y;
    return asc ? r : -r;
  });
}

function renderCompare() {
  const rows = state.run.strategies;
  const accent = css("--accent"), muted = css("--muted");
  chart("scatter", "scatter-chart", {
    type: "scatter",
    data: {
      datasets: [{
        data: rows.map((r) => ({ x: (r.max_drawdown || 0) * 100, y: (r.cagr || 0) * 100, name: r.strategy })),
        pointRadius: rows.map((r) => (r.strategy === state.strategy ? 8 : 5)),
        pointHoverRadius: 9,
        backgroundColor: rows.map((r) => (r.strategy === state.strategy ? accent : muted)),
      }],
    },
    plugins: [pointLabels],
    options: baseOptions({
      interaction: { mode: "nearest", intersect: true },
      scales: {
        x: valueAxis((v) => `${v}%`, { title: { display: true, text: "max drawdown" }, beginAtZero: true }),
        y: valueAxis((v) => `${v}%`, { title: { display: true, text: "CAGR" } }),
      },
      onClick: (_, els) => { if (els.length) openStrategy(rows[els[0].index].strategy); },
      plugins: { legend: { display: false }, tooltip: { displayColors: false, callbacks: { label: (c) => { const r = rows[c.dataIndex]; return [r.strategy, `CAGR ${pct(r.cagr)} · max DD ${pct(r.max_drawdown, 1, false)}`, `ret/DD ${num(r.return_dd)} · ${r.trades} trades`]; } } } },
    }),
  });

  const s = state.sort.compare;
  const head = COMPARE_COLS.map(([k, label]) => `<th data-key="${k}" class="${s.key === k ? "sorted" + (s.asc ? " asc" : "") : ""}">${label}</th>`).join("");
  const body = sortRows(rows, s)
    .map((r) => `<tr class="clickable ${r.strategy === state.strategy ? "selected" : ""}" data-name="${esc(r.strategy)}">${COMPARE_COLS.map(([, , f]) => `<td>${f(r)}</td>`).join("")}</tr>`)
    .join("");
  const tbl = $("#compare-table");
  tbl.innerHTML = `<thead><tr>${head}</tr></thead><tbody>${body}</tbody>`;
  tbl.querySelectorAll("th").forEach((th) => (th.onclick = () => {
    const k = th.dataset.key;
    state.sort.compare = { key: k, asc: s.key === k ? !s.asc : k === "strategy" || k === "max_drawdown" };
    renderCompare();
  }));
  tbl.querySelectorAll("tbody tr").forEach((tr) => (tr.onclick = () => openStrategy(tr.dataset.name)));
}

async function openStrategy(name) {
  state.tab = "overview";
  try { await selectStrategy(name); } catch (e) { showError(e); }
}

// ---------------------------------------------------------------- trades
const TRADE_COLS = [
  ["pair", "Pair", (t) => esc(t.pair)],
  ["side", "Side", (t) => `<span class="badge ${t.side}">${t.side}</span>`],
  ["open", "Opened", (t) => stamp(t.open)],
  ["close", "Closed", (t) => stamp(t.close)],
  ["duration_min", "Held", (t) => duration(t.duration_min)],
  ["open_rate", "Entry", (t) => num(t.open_rate, 4)],
  ["close_rate", "Exit", (t) => num(t.close_rate, 4)],
  ["profit_pct", "Profit", (t) => `<span class="${cls(t.profit_pct)}">${pct(t.profit_pct, 2)}</span>`],
  ["profit_abs", "P&L", (t) => `<span class="${cls(t.profit_abs)}">${money(t.profit_abs, 2)}</span>`],
  ["exit_reason", "Exit reason", (t) => esc(t.exit_reason)],
  ["enter_tag", "Tag", (t) => esc(t.enter_tag)],
];

function populatePairs() {
  const counts = {};
  for (const t of state.data.trades) counts[t.pair] = (counts[t.pair] || 0) + 1;
  const pairs = state.data.meta.pairs || [];
  if (!state.pair) state.pair = [...pairs].sort((a, b) => (counts[b] || 0) - (counts[a] || 0))[0] || null;
  const sel = $("#pair-select");
  sel.innerHTML = "";
  for (const p of pairs) sel.appendChild(new Option(`${p} (${counts[p] || 0})`, p));
  sel.value = state.pair || "";
}

async function renderTrades() {
  populatePairs();
  renderTradeTable();
  if (!state.pair) return;
  const key = `${state.runId}|${state.strategy}|${state.pair}`;
  if (!state.candleCache.has(key)) {
    state.candleCache.set(key, await api("candles", { id: state.runId, name: state.strategy, pair: state.pair }));
  }
  const cd = state.candleCache.get(key);
  $("#candle-step").textContent = cd.step !== state.data.meta.timeframe ? `· price shown on ${cd.step} candles` : "";

  const trades = state.data.trades.filter((t) => t.pair === state.pair);
  const pos = css("--pos"), neg = css("--neg");
  const longs = trades.filter((t) => t.side === "long").map((t) => ({ x: t.open, y: t.open_rate, t }));
  const shorts = trades.filter((t) => t.side === "short").map((t) => ({ x: t.open, y: t.open_rate, t }));
  const exits = trades.map((t) => ({ x: t.close, y: t.close_rate, t }));
  const paths = trades.length <= 400
    ? trades.flatMap((t) => [{ x: t.open, y: t.open_rate, win: t.profit_abs >= 0 }, { x: t.close, y: t.close_rate, win: t.profit_abs >= 0 }, { x: t.close + 1, y: null }])
    : [];
  const tip = (t) => [`${t.side} ${t.pair}`, `${stamp(t.open)} → ${stamp(t.close)}`, `${num(t.open_rate, 4)} → ${num(t.close_rate, 4)}  (${pct(t.profit_pct, 2)})`, `exit: ${t.exit_reason}`];

  chart("price", "price-chart", {
    type: "line",
    data: {
      datasets: [
        { label: "close", data: cd.t.map((x, i) => ({ x, y: cd.c[i] })), borderColor: css("--muted"), borderWidth: 1.2, pointRadius: 0, order: 5 },
        { label: "trade", data: paths, borderWidth: 2, pointRadius: 0, spanGaps: false, segment: { borderColor: (c) => (c.p0.raw.win ? pos : neg) }, order: 4 },
        { type: "scatter", label: "long entry", data: longs, pointStyle: "triangle", pointRadius: 7, backgroundColor: pos, borderColor: pos, order: 1 },
        { type: "scatter", label: "short entry", data: shorts, pointStyle: "triangle", rotation: 180, pointRadius: 7, backgroundColor: neg, borderColor: neg, order: 1 },
        { type: "scatter", label: "exit", data: exits, pointStyle: "circle", pointRadius: 4, backgroundColor: exits.map((e) => (e.t.profit_abs >= 0 ? pos : neg)), borderColor: css("--panel"), borderWidth: 1, order: 2 },
      ],
    },
    options: baseOptions({
      parsing: false,
      interaction: { mode: "nearest", intersect: true },
      scales: { x: timeAxis(), y: valueAxis((v) => v.toLocaleString(), { type: "logarithmic" }) },
      plugins: {
        legend: { display: false },
        tooltip: {
          displayColors: false,
          filter: (i) => i.datasetIndex >= 2 || i.datasetIndex === 0,
          callbacks: { label: (c) => (c.raw.t ? tip(c.raw.t) : `close ${num(c.parsed.y, 4)}`) },
        },
      },
    }),
  });
}

function renderTradeTable() {
  const side = $("#side-filter").value, res = $("#result-filter").value, pairOnly = $("#pair-only").checked;
  let rows = state.data.trades.filter(
    (t) => (!pairOnly || t.pair === state.pair) && (!side || t.side === side) && (!res || (res === "win" ? t.profit_abs >= 0 : t.profit_abs < 0)),
  );
  const s = state.sort.trades;
  rows = sortRows(rows, s);
  const shown = rows.slice(0, state.tradeLimit);
  const head = TRADE_COLS.map(([k, label]) => `<th data-key="${k}" class="${s.key === k ? "sorted" + (s.asc ? " asc" : "") : ""}">${label}</th>`).join("");
  const body = shown.map((t) => `<tr>${TRADE_COLS.map(([, , f]) => `<td>${f(t)}</td>`).join("")}</tr>`).join("");
  const tbl = $("#trades-table");
  tbl.innerHTML = `<thead><tr>${head}</tr></thead><tbody>${body || `<tr><td colspan="${TRADE_COLS.length}">no trades match</td></tr>`}</tbody>`;
  tbl.querySelectorAll("th").forEach((th) => (th.onclick = () => {
    const k = th.dataset.key;
    state.sort.trades = { key: k, asc: s.key === k ? !s.asc : false };
    renderTradeTable();
  }));
  const more = $("#more-trades");
  more.hidden = rows.length <= shown.length;
  more.textContent = `Show more (${rows.length - shown.length} hidden)`;
}

// ---------------------------------------------------------------- theme & wiring
function setTheme(theme) {
  if (theme) document.documentElement.dataset.theme = theme;
  try { if (theme) localStorage.setItem("bt-dash-theme", theme); } catch (_) { /* storage unavailable */ }
}
function currentTheme() {
  return document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}

function wire() {
  $("#run-select").onchange = (e) => selectRun(e.target.value, state.strategy).catch(showError);
  $("#strategy-select").onchange = (e) => selectStrategy(e.target.value).catch(showError);
  document.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => {
    state.tab = b.dataset.tab;
    renderTab().catch(showError);
    writeHash();
  }));
  $("#pair-select").onchange = (e) => {
    state.pair = e.target.value;
    state.tradeLimit = 200;
    renderTrades().catch(showError);
    writeHash();
  };
  for (const id of ["#side-filter", "#result-filter", "#pair-only"]) $(id).onchange = () => { state.tradeLimit = 200; renderTradeTable(); };
  $("#more-trades").onclick = () => { state.tradeLimit += 500; renderTradeTable(); };
  $("#theme-toggle").onclick = () => {
    setTheme(currentTheme() === "dark" ? "light" : "dark");
    renderTab().catch(showError);
  };
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => renderTab().catch(showError));
  // a pasted / edited link (#run=...&s=...) reloads that view; our own updates use replaceState
  window.addEventListener("hashchange", () => location.reload());
}

async function init() {
  try { const t = localStorage.getItem("bt-dash-theme"); if (t) setTheme(t); } catch (_) { /* storage unavailable */ }
  if (typeof Chart === "undefined") {
    showError("Chart.js could not be loaded from cdn.jsdelivr.net - check the internet connection and reload.");
    return;
  }
  wire();
  try {
    state.runs = await api("runs");
    if (!state.runs.length) {
      showError("No backtest results found. Run a backtest with --export trades first.");
      return;
    }
    populateRuns();
    const h = readHash();
    if (["overview", "compare", "trades"].includes(h.tab)) state.tab = h.tab;
    if (h.pair) state.pair = h.pair;
    const run = state.runs.find((r) => r.id === h.run) || state.runs[0];
    await selectRun(run.id, h.s);
  } catch (e) {
    showError(e);
  }
}

init();
