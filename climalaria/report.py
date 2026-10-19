"""Self-contained HTML report of the pipeline outputs (outputs/index.html), viewable offline or via a local server."""
from __future__ import annotations

import html
import json
from pathlib import Path

import pandas as pd

LEVELS = ["ALERT", "WARNING", "WATCH", "NORMAL"]
ACTION = {"ALERT": "Execute", "WARNING": "Prepare", "WATCH": "Monitor", "NORMAL": "Routine"}
RANGE = {"ALERT": "60% or higher chance", "WARNING": "35% to 60% chance", "WATCH": "15% to 35% chance", "NORMAL": "under 15% chance"}

CSS = """
:root{--bg:#f6f7f9;--panel:#fff;--fg:#1c2430;--muted:#5d6878;--line:#dde2e8;--accent:#2e6fa8;
  --alert:#c0392b;--warning:#e67e22;--watch:#d9ae16;--normal:#e3e8ee;--bad:#b42318}
@media (prefers-color-scheme:dark){:root{--bg:#12161c;--panel:#1a2029;--fg:#e6eaf0;--muted:#9aa5b4;--line:#2c3542;
  --accent:#6aa7dc;--normal:#2a323d;--bad:#f07167;color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:82rem;margin-inline:auto;padding:28px 16px 48px;display:flex;flex-direction:column;gap:32px}
h1{font:400 2rem/1.2 Georgia,"Times New Roman",serif;margin:0;text-wrap:balance}
h2{font-size:1.15rem;font-weight:600;margin:0}
h3{font-size:1rem;font-weight:600;margin:0}
p{margin:0}
.take{font-size:1.1rem;max-width:62rem}
.note{font-size:.85rem;color:var(--muted);border:1px dashed var(--line);border-radius:8px;padding:8px 12px;max-width:62rem}
.note b{color:var(--fg)}
.head{display:flex;flex-direction:column;gap:10px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,12rem),1fr));gap:14px}
.tile{background:var(--panel);border:1px solid var(--line);border-top:4px solid var(--c,var(--line));border-radius:8px;padding:16px;display:flex;flex-direction:column;gap:4px}
.tile .lbl{font-size:.75rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.tile .big{font:400 2.4rem/1.1 Georgia,serif;font-variant-numeric:tabular-nums}
.tile .sub{font-size:.8rem;color:var(--muted)}
section{display:flex;flex-direction:column;gap:14px}
.intro{color:var(--muted);max-width:62rem}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,26rem),1fr));gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:16px;display:flex;flex-direction:column;gap:10px;min-width:0}
.cap{font-size:.8rem;color:var(--muted)}
.card img{width:100%;height:auto;border-radius:4px;background:#fff}
.scroll{overflow:auto}
.scroll.tall{max-height:34rem}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:.9rem}
th{position:sticky;top:0;background:var(--panel);text-align:left;font-weight:600;font-size:.78rem;color:var(--muted);
  border-bottom:1px solid var(--line);padding:6px 8px;white-space:nowrap}
th button{all:unset;cursor:pointer}
th button:focus-visible{outline:2px solid var(--accent)}
td{padding:7px 8px;border-bottom:1px solid var(--line)}
.n{text-align:right}
.chip{display:inline-block;padding:1px 8px;border-radius:999px;font-size:.75rem;font-weight:600;white-space:nowrap}
.chip.ALERT{background:var(--alert);color:#fff}.chip.WARNING{background:var(--warning);color:#1d1306}
.chip.WATCH{background:var(--watch);color:#1d1606}.chip.NORMAL{background:var(--normal);color:var(--muted)}
.pbar{display:flex;align-items:center;gap:8px;min-width:8rem}
.pbar i{flex:1;height:8px;border-radius:4px;background:var(--normal);overflow:hidden;display:block}
.pbar u{display:block;height:100%;border-radius:4px}
.pbar span{width:2.6rem;text-align:right}
.bad{color:var(--bad)}
.bars{display:flex;flex-direction:column;gap:14px}
.bars .row{display:grid;grid-template-columns:8rem 1fr 3rem;gap:8px;align-items:center;font-size:.85rem}
.bars .track{height:14px;background:var(--normal);border-radius:7px;overflow:hidden}
.bars .fill{height:100%;border-radius:7px}
.bars .mt small{color:var(--muted);font-weight:400}
.controls{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
input,select{font:inherit;padding:5px 8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg)}
a{color:var(--accent)}
.files{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,18rem),1fr));gap:6px 18px;font-size:.9rem}
@media (max-width:640px){.sec{display:none}}
"""

JS = """
const rows = JSON.parse(document.getElementById('data').textContent);
const fmt = new Intl.NumberFormat('en');
const pct = (v) => Math.round(v * 100) + '%';
const color = {ALERT:'var(--alert)', WARNING:'var(--warning)', WATCH:'var(--watch)', NORMAL:'var(--muted)'};
const sort = {k:'p_exceed', dir:-1};
const q = document.getElementById('q'), region = document.getElementById('region'), body = document.querySelector('#all tbody');
[...new Set(rows.map(r => r.region))].sort().forEach(r => region.add(new Option(r)));
function cell(tr, text, cls) { const td = tr.insertCell(); td.textContent = text; if (cls) td.className = cls; return td; }
function draw() {
  const s = q.value.trim().toLowerCase();
  const txt = sort.k === 'district' || sort.k === 'region';
  const shown = rows.filter(r => (!region.value || r.region === region.value) && r.district.toLowerCase().includes(s))
    .sort((a, b) => sort.dir * (txt ? a[sort.k].localeCompare(b[sort.k]) : a[sort.k] - b[sort.k]));
  document.getElementById('count').textContent = shown.length + ' of ' + rows.length + ' districts';
  body.replaceChildren();
  for (const r of shown) {
    const tr = body.insertRow();
    cell(tr, r.district); cell(tr, r.region, 'sec');
    const chip = document.createElement('span'); chip.className = 'chip ' + r.level;
    chip.textContent = r.level[0] + r.level.slice(1).toLowerCase(); cell(tr, '').appendChild(chip);
    const bar = document.createElement('div'); bar.className = 'pbar';
    bar.innerHTML = '<i><u></u></i><span></span>';
    bar.querySelector('u').style.cssText = 'width:' + r.p_exceed * 100 + '%;background:' + color[r.level];
    bar.querySelector('span').textContent = pct(r.p_exceed); cell(tr, '').appendChild(bar);
    cell(tr, fmt.format(r.predicted), 'n'); cell(tr, fmt.format(r.threshold), 'n sec');
    const ex = r.predicted - r.threshold;
    cell(tr, (ex > 0 ? '+' : '') + fmt.format(ex), ex > 0 ? 'n bad' : 'n');
    cell(tr, Math.round(r.rain_lag1), 'n sec'); cell(tr, fmt.format(r.population), 'n sec');
  }
  document.querySelectorAll('#all th').forEach(th => th.setAttribute('aria-sort',
    th.dataset.k === sort.k ? (sort.dir > 0 ? 'ascending' : 'descending') : 'none'));
}
document.querySelectorAll('#all th button').forEach(b => b.addEventListener('click', () => {
  const k = b.parentElement.dataset.k;
  if (sort.k === k) sort.dir = -sort.dir; else { sort.k = k; sort.dir = (k === 'district' || k === 'region') ? 1 : -1; }
  draw();
}));
q.addEventListener('input', draw); region.addEventListener('change', draw);
draw();
"""


def _e(x) -> str:
    return html.escape(str(x))


def _chip(level: str) -> str:
    return f'<span class="chip {level}">{level.title()}</span>'


def _pbar(p: float, level: str) -> str:
    c = f"var(--{level.lower()})" if level != "NORMAL" else "var(--muted)"
    return f'<div class="pbar"><i><u style="width:{p * 100:.1f}%;background:{c}"></u></i><span>{p:.0%}</span></div>'


def _names(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def write_report(out: Path, period: str, forecast: pd.DataFrame, skill: pd.DataFrame, lags: pd.DataFrame,
                 effects: dict, cap_files: list[Path], simulated: bool = True, tradeoff: pd.DataFrame | None = None) -> Path:
    """Write outputs/index.html. `forecast` has one row per district with district, region, population,
    rain_lag1, predicted, threshold, p_exceed and level."""
    f = forecast.sort_values("p_exceed", ascending=False).reset_index(drop=True)
    cuts = {name: c for c, name in effects.get("cuts") or [(0.6, "ALERT"), (0.35, "WARNING"), (0.15, "WATCH")]}
    rng = {"ALERT": f"{cuts['ALERT']:.0%} or higher chance", "WARNING": f"{cuts['WARNING']:.0%} to {cuts['ALERT']:.0%} chance",
           "WATCH": f"{cuts['WATCH']:.0%} to {cuts['WARNING']:.0%} chance", "NORMAL": f"under {cuts['WATCH']:.0%} chance"}
    month = pd.Period(period).strftime("%B %Y")
    act = f[f.level.isin(["ALERT", "WARNING"])]
    counts = f.level.value_counts().reindex(LEVELS, fill_value=0)

    if len(act):
        top_region = act.region.value_counts()
        parts = [f"{_names(list(act[act.level == lv].district))} {'is' if (act.level == lv).sum() == 1 else 'are'} on {lv.title()}"
                 for lv in ["ALERT", "WARNING"] if (act.level == lv).any()]
        take = (f"{len(act)} district{'s' if len(act) != 1 else ''} need action in {month}. " + "; ".join(parts) + ". "
                f"{top_region.iloc[0]} of the {len(act)} are in {top_region.index[0]} region.")
    else:
        take = f"No district is at Warning or Alert level for {month}."

    tiles = "".join(
        f'<div class="tile" style="--c:var(--{lv.lower()})"><span class="lbl">{lv.title()} · {ACTION[lv].lower()}</span>'
        f'<span class="big">{counts[lv]}</span><span class="sub">districts, {rng[lv]} of passing the epidemic threshold</span></div>'
        for lv in ["ALERT", "WARNING", "WATCH"])
    tiles += (f'<div class="tile"><span class="lbl">People in alert or warning districts</span>'
              f'<span class="big">{int(act.population.sum()):,}</span>'
              f'<span class="sub">forecast {act.predicted.sum():,.0f} confirmed cases in {month}</span></div>')

    pri = "".join(
        f"<tr><td>{_e(r.district)}</td><td class='sec'>{_e(r.region)}</td><td>{_chip(r.level)}</td><td>{_pbar(r.p_exceed, r.level)}</td>"
        f"<td class='n'>{r.predicted:,.0f}</td><td class='n sec'>{r.threshold:,.0f}</td><td class='n sec'>{r.rain_lag1:.0f}</td>"
        f"<td>{ACTION[r.level]}</td></tr>"
        for r in f[f.level != "NORMAL"].itertuples())

    sk = skill.reset_index(drop=True)
    used = len(sk) - 1  # the last row is the model used for the forecast
    names = {"seasonal only (no climate)": "Seasonal only", "climate-informed (rain lag 1-2, temperature)": "With climate",
             "seasonal only, no trend": "Seasonal, no trend", "climate-informed, no trend": "Climate, no trend",
             "seasonal + trend": "Seasonal + trend", "climate-informed + trend": "Climate + trend",
             "climate + trend (previous model)": "Previous model", "+ regional seasons": "+ regional seasons",
             "+ recent cases": "+ recent cases", "final without climate": "Final, no climate",
             "+ calibrated cut-offs (final)": "Final model", "final": "Final model"}
    bars = ""
    for key, label, note in [("hit_rate", "Hit rate", "share of epidemic months warned the month before; higher is better"),
                             ("false_alarm_ratio", "False-alarm ratio", "share of warnings with no epidemic after; lower is better")]:
        bars += f'<div><div class="mt"><b>{label}</b> <small>{note}</small></div>'
        for i, r in enumerate(sk.itertuples()):
            c = "var(--accent)" if i == used else "var(--muted)"
            bars += (f'<div class="row"><span>{_e(names.get(r.model, r.model))}{" ✓" if i == used else ""}</span>'
                     f'<div class="track"><div class="fill" style="width:{getattr(r, key) * 100:.0f}%;background:{c}"></div></div>'
                     f'<b class="n">{getattr(r, key):.0%}</b></div>')
        bars += "</div>"
    sk_rows = "".join(f"<tr><td>{_e(r.model)}</td><td class='n'>{r.epidemic_months}</td><td class='n'>{r.detected}</td>"
                      f"<td class='n'>{r.missed}</td></tr>" for r in sk.itertuples())
    best = lags.loc[lags.correlation.idxmax()]

    tag = "simulated" if simulated else "real"
    data_files = [(f"district_climate_malaria_{tag}.csv", "District monthly climate and cases (CSV)"),
                  (f"malaria_risk_forecast_{period}.geojson", "Forecast with district boundaries (GeoJSON)"),
                  ("warning_skill_2023_2026.csv", "Warning skill table (CSV)"),
                  ("warning_skill.csv", "Warning skill table (CSV)"),
                  (f"uganda_climate_monthly_{tag}.nc", "Gridded climate (NetCDF)")]
    files = "".join(f'<a href="{_e(n)}">{_e(label)}</a>' for n, label in data_files if (out / n).exists())
    caps = "".join(f'<a href="cap_alerts/{_e(p.name)}">{_e(" ".join(p.stem.split("_")[1:-1]))}</a>' for p in cap_files)

    table_rows = f[["district", "region", "population", "rain_lag1", "predicted", "threshold", "p_exceed", "level"]].copy()
    table_rows[["predicted", "threshold"]] = table_rows[["predicted", "threshold"]].round(0)
    data_json = json.dumps(table_rows.to_dict("records")).replace("</", "<\\/")

    note = ("<b>Simulated data, for demonstration and training.</b> District names and boundaries are real (Natural Earth). "
            "Rainfall, temperature and malaria case figures are simulated to look like CHIRPS, ERA5 and HMIS data. "
            "CAP alerts are marked <i>Exercise</i>.") if simulated else (
            "<b>Data:</b> confirmed malaria from DHIS2 (HMIS 105, 105-EP01c), UBOS 2024 census population, CHIRPS v2.0 rainfall "
            "and NASA POWER (MERRA-2) or ERA5 temperature. Forecasts support, but do not replace, the judgement of district health teams; "
            "CAP alerts stay marked <i>Exercise</i> until the designated authority approves them.")

    trend_note = (f"Malaria cases have been changing by about {effects['trend']:+.0%} a year, so the model follows that trend and "
                  "each district's threshold is set relative to its expected level today, not to past levels. ") if effects.get("trend") is not None else ""

    thr_def = ("its expected level for the month, adjusted for the long-term trend, raised by the usual size of past upsurges "
               "(mean plus 2 standard deviations)") if effects.get("trend") is not None else               "mean plus 2 standard deviations of past cases for that calendar month"

    rolling = (f" (refitted every {effects['step']} months on the data available at the time, as it would be run)"
               if effects.get("step") else "")
    trade_html = ""
    if tradeoff is not None and len(tradeoff):
        tr = "".join(f"<tr><td class='n'>{r.cut:.0%}</td><td class='n'>{r.warnings:,}</td><td class='n'>{r.hit_rate:.0%}</td>"
                     f"<td class='n'>{r.false_alarm_ratio:.0%}</td></tr>" for r in tradeoff.itertuples())
        trade_html = ("<h3>Choosing the cut-off</h3><p class='cap'>For the final model on the test months: if a warning were issued "
                      "whenever the chance is at least the cut-off. A lower cut-off catches more upsurges but gives more false alarms.</p>"
                      "<div class='scroll'><table><thead><tr><th class='n'>Cut-off</th><th class='n'>Warnings</th><th class='n'>Upsurges caught</th>"
                      f"<th class='n'>False alarms</th></tr></thead><tbody>{tr}</tbody></table></div>")

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Uganda Malaria Early Warning</title><style>{CSS}</style></head>
<body><div class="wrap">
<header class="head">
  <h1>Malaria upsurge forecast for Uganda districts, {month}</h1>
  <p class="take">{_e(take)}</p>
  <p class="note">{note}</p>
</header>

<div class="tiles">{tiles}</div>

<section>
  <h2>Where to act in {month}</h2>
  <p class="intro">A district's level is the forecast chance that next month's confirmed cases go above its epidemic threshold
  ({thr_def}).</p>
  <div class="grid">
    <div class="card"><h3>Rainfall anomaly and warning level</h3>
      <p class="cap">Left: last month's rainfall compared with normal. Right: warning level for {month}.</p>
      <img src="rainfall_and_malaria_warning_map.png" alt="Map of rainfall anomaly and malaria warning level by district"></div>
    <div class="card"><h3>Priority districts</h3>
      <p class="cap">Every district above Normal, highest chance first. Alert and Warning districts each have a CAP alert.</p>
      <div class="scroll tall"><table><thead><tr><th>District</th><th class="sec">Region</th><th>Level</th><th>Chance above threshold</th>
        <th class="n">Forecast cases</th><th class="n sec">Threshold</th><th class="n sec">Rain last month (mm)</th><th>Action</th></tr></thead>
        <tbody>{pri}</tbody></table></div>
      <p class="cap"><b>Execute:</b> check ACT and RDT stocks, intensify case management and vector control, report weekly.
      <b>Prepare:</b> pre-position stock and alert health facilities. <b>Monitor:</b> follow weekly mTrac/eIDSR reports.</p></div>
  </div>
</section>

<section>
  <h2>How far to trust the warnings</h2>
  <p class="intro">The model was trained on {effects.get('train', '2015–2022')} and tested on {effects.get('test', '2023–2026')}, data it had not seen{rolling},
  against a model that only knows the seasonal pattern. {trend_note}The model marked ✓ produces the forecast above.</p>
  <div class="grid">
    <div class="card"><h3>Warning skill, {effects.get('test', '2023–2026')}</h3>
      <p class="cap">A Warning or Alert counts as a hit when cases went above the threshold the next month</p>
      <div class="bars">{bars}</div>
      <div class="scroll"><table><thead><tr><th>Model</th><th class="n">Epidemic months</th><th class="n">Warned</th><th class="n">Missed</th></tr></thead>
      <tbody>{sk_rows}</tbody></table></div>{trade_html}</div>
    <div class="card"><h3>Rainfall leads malaria by about {int(best.lag_months)} month{'s' if best.lag_months != 1 else ''}</h3>
      <p class="cap">Correlation between rainfall and case anomalies at each lag. In the model, 50 mm more rain raises next month's
      cases by about {effects['lag1'] - 1:.0%} and cases two months later by about {effects['lag2'] - 1:.0%}.</p>
      <img src="rainfall_malaria_lag.png" alt="Bar chart of rainfall-malaria correlation by lag"></div>
  </div>
  <div class="card"><h3>One district's record: forecast against what happened</h3>
    <p class="cap">The district with the most epidemic months in {effects.get('test', '2023–2026')}. Red dots mark months a warning was issued.</p>
    <img src="district_forecast.png" alt="Observed cases, forecast and epidemic threshold for one district"></div>
</section>

<section>
  <h2>All districts</h2>
  <div class="card">
    <div class="controls"><input type="search" id="q" placeholder="Search districts" aria-label="Search districts">
      <select id="region" aria-label="Region"><option value="">All regions</option></select>
      <span class="cap" id="count"></span></div>
    <div class="scroll tall"><table id="all"><thead><tr>
      <th data-k="district"><button>District</button></th><th class="sec" data-k="region"><button>Region</button></th>
      <th>Level</th><th data-k="p_exceed"><button>Chance above threshold</button></th>
      <th class="n" data-k="predicted"><button>Forecast cases</button></th><th class="n sec" data-k="threshold"><button>Threshold</button></th>
      <th class="n">Above (+) or below threshold</th><th class="n sec" data-k="rain_lag1"><button>Rain last month (mm)</button></th>
      <th class="n sec" data-k="population"><button>Population</button></th></tr></thead><tbody></tbody></table></div>
  </div>
</section>

<section>
  <h2>Files</h2>
  <div class="card"><h3>Data</h3><div class="files">{files}</div>
    <h3>CAP alerts ({len(cap_files)}, XML)</h3><div class="files">{caps or '<span class="cap">None issued</span>'}</div></div>
</section>
</div>
<script type="application/json" id="data">{data_json}</script>
<script>{JS}</script>
</body></html>
"""
    path = out / "index.html"
    path.write_text(page, encoding="utf-8")
    return path
