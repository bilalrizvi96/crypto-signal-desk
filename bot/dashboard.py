"""Dashboard renderer — a single self-contained HTML page.

The page is a shell plus a JS renderer. It hydrates from data embedded at
render time and then polls /api/state, so the same file works both as a live
page served by the bot and as a static snapshot you can open offline.

Colour follows the data-viz reference palette (dark surface #1a1a19). Direction
is never carried by colour alone — every long/short mark ships an arrow glyph
and the word itself.
"""
from __future__ import annotations

import json

PAGE = r"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Crypto Signal Desk</title>
<style>
  :root{
    color-scheme: dark;
    --plane:#0d0d0d; --surface:#1a1a19; --surface-2:#212120;
    --ink:#ffffff; --ink-2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,0.10);
    --long:#0ca30c; --short:#d03b3b; --watch:#fab219; --serious:#ec835a;
    --series-1:#3987e5; --series-2:#d95926; --series-3:#199e70;
    --radius:10px;
    --font:system-ui,-apple-system,"Segoe UI",sans-serif;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--plane);color:var(--ink);font-family:var(--font);
       font-size:14px;line-height:1.45;-webkit-font-smoothing:antialiased}
  a{color:var(--series-1);text-decoration:none}
  a:hover{text-decoration:underline}
  .wrap{max-width:1500px;margin:0 auto;padding:18px 20px 60px}

  header.top{display:flex;flex-wrap:wrap;gap:14px;align-items:center;
    justify-content:space-between;padding:14px 16px;background:var(--surface);
    border:1px solid var(--border);border-radius:var(--radius);margin-bottom:16px}
  .brand{display:flex;align-items:center;gap:11px;min-width:0}
  .brand h1{font-size:17px;margin:0;font-weight:650;letter-spacing:-0.01em}
  .brand .sub{color:var(--muted);font-size:12px}
  .dot{width:9px;height:9px;border-radius:50%;background:var(--long);flex:none}
  .dot.stale{background:var(--watch)} .dot.err{background:var(--short)}
  .top-right{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  button{font-family:inherit;font-size:13px;color:var(--ink);background:var(--surface-2);
    border:1px solid var(--border);border-radius:8px;padding:7px 13px;cursor:pointer}
  button:hover{background:#2a2a28}
  button.primary{background:var(--series-1);border-color:transparent;color:#fff;font-weight:600}
  button.primary:hover{background:#2a78d6}
  button[aria-pressed="true"]{background:#33332f;border-color:#55554f}
  button:disabled{opacity:.5;cursor:default}

  .tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));
    gap:10px;margin-bottom:16px}
  .tile{background:var(--surface);border:1px solid var(--border);
    border-radius:var(--radius);padding:12px 14px}
  .tile .k{color:var(--muted);font-size:11px;text-transform:uppercase;
    letter-spacing:.06em;margin-bottom:5px}
  .tile .v{font-size:24px;font-weight:640;letter-spacing:-0.02em}
  .tile .m{color:var(--ink-2);font-size:12px;margin-top:2px}

  .bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
  .bar .grp{display:flex;gap:4px;background:var(--surface);padding:4px;
    border:1px solid var(--border);border-radius:9px}
  .bar .lbl{color:var(--muted);font-size:12px;margin-right:2px}

  .layout{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:16px;align-items:start}
  @media (max-width:1080px){.layout{grid-template-columns:1fr}}

  .cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px}
  .card{background:var(--surface);border:1px solid var(--border);
    border-radius:var(--radius);padding:14px;display:flex;flex-direction:column;gap:10px}
  .card.enter-long{border-left:3px solid var(--long)}
  .card.enter-short{border-left:3px solid var(--short)}
  .card.watch{border-left:3px solid var(--watch)}
  .chead{display:flex;justify-content:space-between;align-items:flex-start;gap:10px}
  .sym{font-size:16px;font-weight:660;letter-spacing:-0.01em}
  .sym .q{color:var(--muted);font-weight:400;font-size:12px}
  .badges{display:flex;gap:5px;flex-wrap:wrap;margin-top:4px}
  .badge{font-size:10.5px;padding:2px 7px;border-radius:999px;
    border:1px solid var(--border);color:var(--ink-2);background:var(--surface-2);
    text-transform:uppercase;letter-spacing:.05em;white-space:nowrap}
  .pill{font-size:12px;font-weight:640;padding:4px 10px;border-radius:999px;
    display:inline-flex;align-items:center;gap:5px;white-space:nowrap}
  .pill.long{background:rgba(12,163,12,.16);color:#4ede4e;border:1px solid rgba(12,163,12,.45)}
  .pill.short{background:rgba(208,59,59,.16);color:#ff8080;border:1px solid rgba(208,59,59,.45)}
  .pill.flat{background:var(--surface-2);color:var(--ink-2);border:1px solid var(--border)}
  .pill.watch{background:rgba(250,178,25,.14);color:#ffce5e;border:1px solid rgba(250,178,25,.4)}

  .price-row{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
  .price{font-size:19px;font-weight:600;font-variant-numeric:tabular-nums}
  .chg{font-size:12.5px;font-variant-numeric:tabular-nums}
  .up{color:#4ede4e} .down{color:#ff8080} .neutral{color:var(--ink-2)}

  .scorewrap{margin-top:2px}
  .scorebar{position:relative;height:7px;background:var(--surface-2);
    border-radius:4px;overflow:hidden;border:1px solid var(--grid)}
  .scorebar .mid{position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--axis)}
  .scorebar .fill{position:absolute;top:0;bottom:0;border-radius:3px}
  .scoretxt{display:flex;justify-content:space-between;font-size:11px;
    color:var(--muted);margin-top:4px;font-variant-numeric:tabular-nums}

  table.lv{width:100%;border-collapse:collapse;font-size:12.5px;
    font-variant-numeric:tabular-nums}
  table.lv td{padding:3.5px 0;border-bottom:1px solid var(--grid)}
  table.lv td:first-child{color:var(--muted);white-space:nowrap;padding-right:8px}
  table.lv td:last-child{text-align:right;font-weight:520}
  table.lv tr:last-child td{border-bottom:none}

  details.votes{border-top:1px solid var(--grid);padding-top:8px}
  details.votes summary{cursor:pointer;color:var(--ink-2);font-size:12.5px;
    list-style:none;display:flex;justify-content:space-between;align-items:center}
  details.votes summary::-webkit-details-marker{display:none}
  details.votes summary::after{content:"▾";color:var(--muted);font-size:11px}
  details.votes[open] summary::after{content:"▴"}
  .vote{display:grid;grid-template-columns:106px 54px 1fr;gap:8px;align-items:center;
    padding:4px 0;font-size:12px;border-bottom:1px solid var(--grid)}
  .vote:last-child{border-bottom:none}
  .vote .vn{color:var(--ink-2);font-weight:540}
  .vote .vb{position:relative;height:5px;background:var(--surface-2);border-radius:3px}
  .vote .vb i{position:absolute;top:0;bottom:0;border-radius:3px}
  .vote .vt{color:var(--muted);font-size:11.5px;line-height:1.35}

  .warn{background:rgba(236,131,90,.10);border:1px solid rgba(236,131,90,.32);
    color:#ffb694;border-radius:7px;padding:7px 9px;font-size:11.5px;
    display:flex;gap:6px;align-items:flex-start}
  .warn.block{background:rgba(208,59,59,.12);border-color:rgba(208,59,59,.4);color:#ff9c9c}
  .warn ul{margin:0;padding-left:15px} .warn li{margin:1px 0}

  aside .panel{background:var(--surface);border:1px solid var(--border);
    border-radius:var(--radius);padding:14px;margin-bottom:14px}
  aside h2{font-size:13px;margin:0 0 10px;font-weight:640;letter-spacing:.01em;
    display:flex;justify-content:space-between;align-items:center}
  aside h2 span.c{color:var(--muted);font-weight:400;font-size:11.5px}
  .news-item{padding:8px 0;border-bottom:1px solid var(--grid)}
  .news-item:last-child{border-bottom:none}
  .news-item .t{font-size:12.5px;line-height:1.4;color:var(--ink)}
  .news-item .m{font-size:11px;color:var(--muted);margin-top:3px;
    display:flex;gap:7px;flex-wrap:wrap;align-items:center}
  .sent{font-weight:640;font-variant-numeric:tabular-nums}
  .tag{background:var(--surface-2);border:1px solid var(--border);
    border-radius:4px;padding:0 5px;font-size:10px;letter-spacing:.03em}

  .pos-table{width:100%;border-collapse:collapse;font-size:11.5px;
    font-variant-numeric:tabular-nums}
  .pos-table th{text-align:left;color:var(--muted);font-weight:500;
    font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;
    padding:0 6px 5px 0;border-bottom:1px solid var(--axis)}
  .pos-table td{padding:5px 6px 5px 0;border-bottom:1px solid var(--grid)}
  .pos-table th:last-child,.pos-table td:last-child{text-align:right;padding-right:0}

  .empty{color:var(--muted);font-size:12.5px;padding:14px 0;text-align:center}
  .err-box{background:rgba(208,59,59,.12);border:1px solid rgba(208,59,59,.4);
    color:#ff9c9c;border-radius:var(--radius);padding:12px 14px;margin-bottom:14px;font-size:13px}
  .foot{color:var(--muted);font-size:11.5px;margin-top:22px;line-height:1.6;
    border-top:1px solid var(--grid);padding-top:14px}
  .tip{position:fixed;pointer-events:none;background:#000;border:1px solid var(--axis);
    border-radius:7px;padding:6px 9px;font-size:11.5px;color:var(--ink);
    opacity:0;transition:opacity .1s;z-index:50;font-variant-numeric:tabular-nums}
  .sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
</style>
</head>
<body>
<div class="wrap">
  <header class="top">
    <div class="brand">
      <span class="dot" id="statusDot"></span>
      <div>
        <h1>Crypto Signal Desk</h1>
        <div class="sub" id="subline">loading…</div>
      </div>
    </div>
    <div class="top-right">
      <span class="sub" id="countdown" style="color:var(--muted);font-size:12px"></span>
      <button id="refreshBtn" class="primary">Rescan now</button>
    </div>
  </header>

  <div id="errBox"></div>
  <div class="tiles" id="tiles"></div>

  <div class="bar">
    <span class="lbl">Mode</span>
    <div class="grp" id="modeGrp"></div>
    <span class="lbl" style="margin-left:6px">Show</span>
    <div class="grp" id="actionGrp">
      <button data-action="enter" aria-pressed="true">Firing</button>
      <button data-action="watch" aria-pressed="true">Watch</button>
      <button data-action="none" aria-pressed="false">Everything</button>
    </div>
    <span class="lbl" style="margin-left:6px">Cap</span>
    <div class="grp" id="bucketGrp">
      <button data-bucket="large" aria-pressed="true">Large</button>
      <button data-bucket="small" aria-pressed="true">Small</button>
    </div>
    <span class="lbl" style="margin-left:6px">Side</span>
    <div class="grp" id="sideGrp">
      <button data-side="long" aria-pressed="true">↑ Long</button>
      <button data-side="short" aria-pressed="true">↓ Short</button>
    </div>
  </div>

  <div class="layout">
    <main>
      <div class="cards" id="cards"></div>
      <div class="foot" id="foot"></div>
    </main>
    <aside>
      <div class="panel" id="paperPanel"></div>
      <div class="panel" id="livePanel"></div>
      <div class="panel" id="newsPanel"></div>
    </aside>
  </div>
</div>
<div class="tip" id="tip" role="status"></div>

<script>
const INITIAL = __INITIAL_DATA__;
const POLL_MS = __POLL_MS__;
const LIVE = __LIVE__;

let state = INITIAL;
let filters = {mode:null, actions:new Set(["enter","watch"]),
               buckets:new Set(["large","small"]), sides:new Set(["long","short"])};
let nextScanAt = null;

/* ---------- formatting helpers ---------- */
function fmtPrice(n){
  if(n===null||n===undefined||isNaN(n)) return "–";
  const a=Math.abs(n);
  if(a>=1000) return n.toLocaleString(undefined,{maximumFractionDigits:2});
  if(a>=1) return n.toFixed(4).replace(/0+$/,"").replace(/\.$/,"");
  if(a>=0.01) return n.toFixed(5);
  if(a>=0.0001) return n.toFixed(7);
  return n.toExponential(3);
}
function fmtUsd(n){
  if(n===null||n===undefined||isNaN(n)) return "–";
  const s=n<0?"-":"";
  return s+"$"+Math.abs(n).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
}
function fmtVol(n){
  if(!n) return "–";
  if(n>=1e9) return "$"+(n/1e9).toFixed(2)+"B";
  if(n>=1e6) return "$"+(n/1e6).toFixed(1)+"M";
  if(n>=1e3) return "$"+(n/1e3).toFixed(0)+"K";
  return "$"+n.toFixed(0);
}
function pct(n,d){ if(n===null||n===undefined||isNaN(n)) return "–";
  return (n>=0?"+":"")+n.toFixed(d===undefined?2:d)+"%"; }
function esc(s){ return String(s==null?"":s).replace(/[&<>"']/g,
  c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
function ago(iso){
  if(!iso) return "–";
  const s=Math.max(0,(Date.now()-new Date(iso).getTime())/1000);
  if(s<60) return Math.floor(s)+"s ago";
  if(s<3600) return Math.floor(s/60)+"m ago";
  if(s<86400) return Math.floor(s/3600)+"h ago";
  return Math.floor(s/86400)+"d ago";
}
const arrow = d => d==="long"?"↑":(d==="short"?"↓":"→");

/* ---------- sparkline ---------- */
function sparkline(vals, dir){
  if(!vals || vals.length<3) return "";
  const w=308,h=40,p=2;
  const min=Math.min(...vals), max=Math.max(...vals), span=(max-min)||1;
  const pts=vals.map((v,i)=>{
    const x=p+(i/(vals.length-1))*(w-2*p);
    const y=h-p-((v-min)/span)*(h-2*p);
    return x.toFixed(1)+","+y.toFixed(1);
  });
  const stroke = dir==="long"?"var(--long)":(dir==="short"?"var(--short)":"var(--muted)");
  const first=vals[0], last=vals[vals.length-1];
  const delta=((last-first)/(first||1))*100;
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" height="40" preserveAspectRatio="none"
      role="img" aria-label="Recent price path, ${delta>=0?"up":"down"} ${Math.abs(delta).toFixed(2)} percent">
      <polyline points="${pts.join(" ")}" fill="none" stroke="${stroke}"
        stroke-width="2" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>
    </svg>`;
}

/* ---------- equity curve with hover ---------- */
function equityChart(curve, start){
  if(!curve || curve.length<2)
    return '<div class="empty">No equity history yet — it builds as trades close.</div>';
  const w=312,h=72,p=4;
  const vals=curve.map(c=>c.equity);
  const min=Math.min(...vals,start), max=Math.max(...vals,start), span=(max-min)||1;
  const X=i=>p+(i/(curve.length-1))*(w-2*p);
  const Y=v=>h-p-((v-min)/span)*(h-2*p);
  const pts=curve.map((c,i)=>X(i).toFixed(1)+","+Y(c.equity).toFixed(1));
  const baseY=Y(start).toFixed(1);
  const up=vals[vals.length-1]>=start;
  const col=up?"var(--long)":"var(--short)";
  return `<svg id="eqSvg" viewBox="0 0 ${w} ${h}" width="100%" height="72"
      preserveAspectRatio="none" role="img"
      aria-label="Paper equity over time, currently ${fmtUsd(vals[vals.length-1])}">
      <line x1="0" y1="${baseY}" x2="${w}" y2="${baseY}" stroke="var(--axis)"
        stroke-width="1" stroke-dasharray="3 3" vector-effect="non-scaling-stroke"/>
      <polyline points="${pts.join(" ")}" fill="none" stroke="${col}" stroke-width="2"
        stroke-linejoin="round" vector-effect="non-scaling-stroke"/>
      <line id="eqCross" x1="0" y1="0" x2="0" y2="${h}" stroke="var(--muted)"
        stroke-width="1" opacity="0" vector-effect="non-scaling-stroke"/>
    </svg>`;
}

function wireEquityHover(curve){
  const svg=document.getElementById("eqSvg");
  if(!svg||!curve||curve.length<2) return;
  const tip=document.getElementById("tip"), cross=document.getElementById("eqCross");
  svg.addEventListener("mousemove", e=>{
    const r=svg.getBoundingClientRect();
    const frac=Math.min(1,Math.max(0,(e.clientX-r.left)/r.width));
    const i=Math.round(frac*(curve.length-1));
    const pt=curve[i];
    cross.setAttribute("x1", (frac*312).toFixed(1));
    cross.setAttribute("x2", (frac*312).toFixed(1));
    cross.setAttribute("opacity","0.7");
    tip.innerHTML = `<strong>${fmtUsd(pt.equity)}</strong><br>${new Date(pt.t).toLocaleString()}`;
    tip.style.opacity="1";
    tip.style.left=Math.min(window.innerWidth-170, e.clientX+12)+"px";
    tip.style.top=(e.clientY-46)+"px";
  });
  svg.addEventListener("mouseleave", ()=>{
    tip.style.opacity="0"; cross.setAttribute("opacity","0");
  });
}

/* ---------- tiles ---------- */
function renderTiles(){
  const c=state.counts||{}, mood=state.market_mood||{}, paper=state.paper;
  const t=[];
  t.push(tile("Signals firing", (c.enter||0), `${c.watch||0} on watch · ${c.symbols||0} scanned`));
  t.push(tile("Long / Short",
    `<span class="up">↑${c.long||0}</span> <span style="color:var(--muted)">/</span> <span class="down">↓${c.short||0}</span>`,
    "of the firing signals"));
  const ms=mood.sentiment;
  const mcls = ms>0.12?"up":(ms<-0.12?"down":"neutral");
  t.push(tile("News mood",
    `<span class="${mcls}">${mood.label||"–"}</span>`,
    `${mood.item_count||0} headlines${mood.macro_alert?" · ⚠ macro event":""}`));
  if(paper){
    const r=paper.total_return_pct||0;
    t.push(tile("Paper equity", fmtUsd(paper.equity),
      `<span class="${r>=0?"up":"down"}">${pct(r)}</span> from ${fmtUsd(paper.starting_equity)}`));
    t.push(tile("Paper record",
      `${paper.win_rate||0}<span style="font-size:15px;color:var(--muted)">%</span>`,
      `${paper.wins||0}W / ${paper.losses||0}L · avg ${(paper.avg_r||0).toFixed(2)}R`));
    t.push(tile("Open positions", `${paper.open_positions||0}`,
      paper.halted_reason ? `<span class="down">halted</span>` :
      `max drawdown ${(paper.max_drawdown_pct||0).toFixed(1)}%`));
  }
  document.getElementById("tiles").innerHTML=t.join("");
}
function tile(k,v,m){
  return `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div>
          <div class="m">${m||""}</div></div>`;
}

/* ---------- signal cards ---------- */
function renderCards(){
  const modes=Object.keys(state.signals||{});
  if(!filters.mode || !modes.includes(filters.mode)){
    // open on whichever mode actually has something to show, so the page
    // never greets you with "no signals" while the tiles say otherwise
    let best=modes[0]||null, bestN=-1;
    modes.forEach(m=>{
      const sigs=state.signals[m]||[];
      const n=sigs.filter(s=>s.action==="enter").length*100
             + sigs.filter(s=>s.action==="watch").length;
      if(n>bestN){ bestN=n; best=m; }
    });
    filters.mode=best;
  }

  document.getElementById("modeGrp").innerHTML = modes.map(m=>{
    const cfg=(state.config&&state.config[m])||{};
    const tfs = cfg.entry?` ${cfg.entry}`:"";
    return `<button data-mode="${m}" aria-pressed="${m===filters.mode}">${
      m==="scalp"?"Scalp":"Swing"}${tfs}</button>`;
  }).join("");
  document.querySelectorAll("#modeGrp button").forEach(b=>{
    b.onclick=()=>{filters.mode=b.dataset.mode; render();};
  });

  let list=(state.signals&&state.signals[filters.mode])||[];
  list=list.filter(s=>{
    if(!filters.actions.has(s.action)) return false;
    if(!filters.buckets.has(s.bucket)) return false;
    if(s.action!=="none" && s.direction!=="flat" && !filters.sides.has(s.direction)) return false;
    return true;
  });

  const el=document.getElementById("cards");
  if(!list.length){
    el.innerHTML=`<div class="empty" style="grid-column:1/-1">
      No signals match these filters right now. That is a normal state —
      most of the time the honest answer is "nothing worth trading".</div>`;
    return;
  }
  el.innerHTML=list.map(card).join("");
  document.querySelectorAll(".card details.votes").forEach(d=>{
    d.addEventListener("toggle",()=>{ if(d.open) sessionStorage.setItem("v:"+d.dataset.k,"1");
      else sessionStorage.removeItem("v:"+d.dataset.k); });
    try{ if(sessionStorage.getItem("v:"+d.dataset.k)) d.open=true; }catch(e){}
  });
}

function card(s){
  const dirCls = s.direction==="long"?"long":(s.direction==="short"?"short":"flat");
  const cardCls = s.action==="enter" ? ("enter-"+dirCls) : (s.action==="watch"?"watch":"");
  const pillCls = s.action==="watch" ? "watch" : dirCls;
  const label = s.action==="enter" ? s.direction.toUpperCase()
              : (s.action==="watch" ? "WATCH "+s.direction.toUpperCase() : "NO TRADE");

  const mag=Math.abs(s.score), half=Math.min(50, mag/2);
  const fill = s.score>=0
    ? `left:50%;width:${half}%;background:var(--long)`
    : `right:50%;width:${half}%;background:var(--short)`;

  const votes=(s.votes||[]).slice(0,6).map(v=>{
    const w=Math.min(50,Math.abs(v.contribution)*38);
    const st=v.score>=0?`left:50%;width:${w}%;background:var(--long)`
                       :`right:50%;width:${w}%;background:var(--short)`;
    return `<div class="vote"><div class="vn">${esc(v.name)}</div>
      <div class="vb"><i style="${st}"></i></div>
      <div class="vt">${esc(v.note)}</div></div>`;
  }).join("");

  const blocked=(s.warnings||[]).some(w=>w.startsWith("BLOCKED"));
  const warns=(s.warnings||[]).length ? `<div class="warn${blocked?" block":""}">
      <span>${blocked?"⛔":"⚠"}</span><ul>${
      s.warnings.map(w=>`<li>${esc(w)}</li>`).join("")}</ul></div>` : "";

  const heads=(s.news&&s.news.headlines||[]).slice(0,2).map(h=>
    `<div style="font-size:11.5px;color:var(--muted);margin-top:3px">
      ${h.score>=0?"▲":"▼"} ${esc(h.title.slice(0,84))}${h.title.length>84?"…":""}</div>`).join("");

  const tgts=(s.targets||[]).map((t,i)=>
    `<tr><td>Target ${i+1}</td><td>${fmtPrice(t)}</td></tr>`).join("");

  const chgCls = s.change_24h>0?"up":(s.change_24h<0?"down":"neutral");

  return `<article class="card ${cardCls}">
    <div class="chead">
      <div>
        <div class="sym">${esc(s.base)}<span class="q">/${esc(s.symbol.split("/")[1]||"")}</span></div>
        <div class="badges">
          <span class="badge">${s.bucket} cap</span>
          <span class="badge">${esc(s.regime)}</span>
          <span class="badge">${esc(s.timeframes.entry)}→${esc(s.timeframes.bias)}</span>
          <span class="badge">${esc(s.confidence)} conf</span>
        </div>
      </div>
      <span class="pill ${pillCls}">${arrow(s.direction)} ${label}</span>
    </div>

    <div class="price-row">
      <span class="price">${fmtPrice(s.price)}</span>
      <span class="chg ${chgCls}">${pct(s.change_24h)} 24h · ${fmtVol(s.quote_volume)}</span>
    </div>

    ${sparkline(s.spark, s.direction)}

    <div class="scorewrap">
      <div class="scorebar"><div class="mid"></div><div class="fill" style="${fill}"></div></div>
      <div class="scoretxt"><span>short</span>
        <span>score ${s.score>=0?"+":""}${s.score.toFixed(0)} · ${s.confidence}</span>
        <span>long</span></div>
    </div>

    <table class="lv">
      <tr><td>Entry</td><td>${fmtPrice(s.entry)}</td></tr>
      <tr><td>Stop loss</td><td style="color:#ff8080">${fmtPrice(s.stop)} (${s.risk_pct_move}%)</td></tr>
      ${tgts}
      <tr><td>Risk / reward</td><td>${s.rr}R to final</td></tr>
      <tr><td>Size (${(state.config&&state.config.risk&&state.config.risk.risk_per_trade_pct)||1}% risk)</td>
          <td>${fmtPrice(s.position_size_units)} ${esc(s.base)} · ${fmtUsd(s.position_notional)}</td></tr>
      <tr><td>ATR (${esc(s.timeframes.entry)})</td><td>${s.atr_pct}% of price</td></tr>
      <tr><td>${esc(s.timeframes.confirm)} confirm</td><td>${esc((s.confirm&&s.confirm.label)||"–")}</td></tr>
      <tr><td>${esc(s.timeframes.bias)} bias</td><td>${esc((s.bias&&s.bias.label)||"–")}</td></tr>
      <tr><td>Invalidation</td><td style="font-weight:400;color:var(--ink-2);font-size:11.5px">${esc(s.invalidation)}</td></tr>
    </table>

    ${warns}
    ${heads?`<div><div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em">News on ${esc(s.base)} · sentiment ${(s.news.sentiment>=0?"+":"")}${s.news.sentiment}</div>${heads}</div>`:""}

    <details class="votes" data-k="${esc(s.symbol)}|${esc(s.mode)}">
      <summary>Why — ${(s.votes||[]).length} indicator reads</summary>
      ${votes}
    </details>
  </article>`;
}

/* ---------- side panels ---------- */
function renderPaper(){
  const p=state.paper, el=document.getElementById("paperPanel");
  if(!p){ el.innerHTML='<h2>Paper trading</h2><div class="empty">Disabled in config.</div>'; return; }
  const r=p.total_return_pct||0;
  const pos=(p.positions||[]).map(x=>`<tr>
      <td>${arrow(x.direction)} ${esc(x.base)}</td>
      <td><span class="badge">${esc(x.mode)}</span></td>
      <td>${fmtPrice(x.entry)}</td>
      <td>${x.tp1_hit?"½ out":"full"}</td></tr>`).join("");
  const trades=(p.recent_trades||[]).slice(0,8).map(t=>`<tr>
      <td>${arrow(t.direction)} ${esc(t.base)}</td>
      <td style="color:var(--muted)">${esc(t.reason)}</td>
      <td class="${t.pnl>=0?"up":"down"}">${t.r_multiple>=0?"+":""}${t.r_multiple.toFixed(2)}R</td>
      <td class="${t.pnl>=0?"up":"down"}">${fmtUsd(t.pnl)}</td></tr>`).join("");

  el.innerHTML=`<h2>Paper trading <span class="c">${p.closed_trades||0} closed</span></h2>
    <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px">
      <span style="font-size:21px;font-weight:640">${fmtUsd(p.equity)}</span>
      <span class="${r>=0?"up":"down"}" style="font-size:13px;font-weight:600">${pct(r)}</span>
    </div>
    ${equityChart(p.equity_curve_points||[], p.starting_equity)}
    <table class="lv" style="margin-top:8px">
      <tr><td>Win rate</td><td>${p.win_rate}% (${p.wins}W/${p.losses}L)</td></tr>
      <tr><td>Profit factor</td><td>${p.profit_factor===null?"–":p.profit_factor}</td></tr>
      <tr><td>Avg / best / worst</td><td>${(p.avg_r||0).toFixed(2)}R / ${p.best_r}R / ${p.worst_r}R</td></tr>
      <tr><td>Max drawdown</td><td>${p.max_drawdown_pct}%</td></tr>
      <tr><td>Fees paid</td><td>${fmtUsd(p.total_fees)}</td></tr>
    </table>
    ${p.halted_reason?`<div class="warn block" style="margin-top:9px"><span>⛔</span><div>${esc(p.halted_reason)}</div></div>`:""}
    ${pos?`<h2 style="margin-top:14px">Open</h2><table class="pos-table">
      <thead><tr><th>Pair</th><th>Mode</th><th>Entry</th><th>State</th></tr></thead>
      <tbody>${pos}</tbody></table>`:'<div class="empty">No open paper positions.</div>'}
    ${trades?`<h2 style="margin-top:14px">Recent closes</h2><table class="pos-table">
      <thead><tr><th>Pair</th><th>Exit</th><th>R</th><th>P&L</th></tr></thead>
      <tbody>${trades}</tbody></table>`:""}`;
  wireEquityHover(p.equity_curve_points||[]);
}

function renderLive(){
  const l=(state.live&&state.live.gate)||{}, el=document.getElementById("livePanel");
  if(!l.enabled){
    el.innerHTML=`<h2>Live trading <span class="c">off</span></h2>
      <div style="font-size:12.5px;color:var(--ink-2)">
        Disabled in config. Signals and paper trading run; no order can leave this machine.
      </div>`;
    return;
  }
  const row=(ok,label)=>`<tr><td>${ok?"✓":"✗"} ${label}</td>
    <td class="${ok?"up":"neutral"}">${ok?"open":"locked"}</td></tr>`;
  el.innerHTML=`<h2>Live trading
      <span class="c ${l.armed?"down":""}">${l.armed?"● ARMED":"safe"}</span></h2>
    <table class="lv">
      ${row(l.enabled,"enabled in config")}
      ${row(!l.dry_run,"dry_run off")}
      ${row(l.has_credentials,"API credentials")}
      ${row(l.confirm_phrase_ok,"LIVE_CONFIRM set")}
      <tr><td>Venue</td><td>${esc(l.exchange)}${l.testnet?" (testnet)":""}</td></tr>
    </table>
    ${l.armed?`<div class="warn block" style="margin-top:9px"><span>⛔</span>
      <div>Real orders are being sent. Every lock is open.</div></div>`
    :`<div style="font-size:11.5px;color:var(--muted);margin-top:8px">
      Orders are simulated and written to state/orders.jsonl.</div>`}
    ${l.blocked_reason?`<div style="font-size:11.5px;color:#ffb694;margin-top:7px">${esc(l.blocked_reason)}</div>`:""}`;
}

function renderNews(){
  const items=state.news||[], el=document.getElementById("newsPanel");
  if(!items.length){
    el.innerHTML='<h2>Market news</h2><div class="empty">No headlines pulled yet.</div>';
    return;
  }
  const mood=state.market_mood||{};
  el.innerHTML=`<h2>Market news <span class="c">${mood.label||""}</span></h2>
    ${items.slice(0,16).map(n=>{
      const cls=n.score>0.5?"up":(n.score<-0.5?"down":"neutral");
      return `<div class="news-item">
        <div class="t">${n.critical?"⛔ ":""}${n.link?`<a href="${esc(n.link)}" target="_blank" rel="noopener">`:""}${esc(n.title)}${n.link?"</a>":""}</div>
        <div class="m">
          <span class="sent ${cls}">${n.score>=0?"+":""}${n.score.toFixed(1)}</span>
          <span>${esc(n.source)}</span><span>${n.age_minutes}m</span>
          ${(n.coins||[]).slice(0,4).map(c=>`<span class="tag">${esc(c)}</span>`).join("")}
          ${n.macro?'<span class="tag">macro</span>':""}
        </div></div>`;
    }).join("")}`;
}

/* ---------- shell ---------- */
function render(){
  if(!state){ return; }
  const dot=document.getElementById("statusDot");
  const errs=(state.errors||[]);
  document.getElementById("errBox").innerHTML = state.error
    ? `<div class="err-box"><strong>Scan failed.</strong> ${esc(state.error)}</div>` : "";
  dot.className="dot"+(state.error?" err":(errs.length?" stale":""));

  document.getElementById("subline").textContent =
    `${state.exchange||"–"} ${state.market_type||""} · ${(state.counts&&state.counts.symbols)||0} pairs · `+
    `scan #${state.scan_number||0} ${ago(state.generated_at)} (${state.duration_seconds||0}s)`;

  renderTiles(); renderCards(); renderPaper(); renderLive(); renderNews();

  document.getElementById("foot").innerHTML =
    `Scores combine multi-timeframe trend, momentum, volatility, volume, market structure and news
     sentiment into one number per pair. A high score means several independent measures currently agree —
     it is not a prediction, and it carries no guarantee. Sizing assumes
     ${(state.config&&state.config.risk&&state.config.risk.risk_per_trade_pct)||1}% of
     ${fmtUsd((state.config&&state.config.risk&&state.config.risk.equity)||0)} risked per trade.
     ${errs.length?`<br><span style="color:#ffb694">${errs.length} symbol(s) failed this scan: ${esc(errs.slice(0,3).join("; "))}</span>`:""}`;
}

/* ---------- polling ---------- */
async function fetchState(force){
  if(!LIVE) return;
  const btn=document.getElementById("refreshBtn");
  try{
    if(force){ btn.disabled=true; btn.textContent="Scanning…"; }
    const r=await fetch(force?"/api/scan":"/api/state",{cache:"no-store"});
    if(!r.ok) throw new Error("HTTP "+r.status);
    const d=await r.json();
    if(d && d.generated_at){ state=d; render(); }
    nextScanAt=Date.now()+POLL_MS;
  }catch(e){
    document.getElementById("statusDot").className="dot err";
  }finally{
    btn.disabled=false; btn.textContent="Rescan now";
  }
}

function tickCountdown(){
  const el=document.getElementById("countdown");
  if(!LIVE || !nextScanAt){ el.textContent = LIVE?"":"static snapshot"; return; }
  const s=Math.max(0,Math.round((nextScanAt-Date.now())/1000));
  el.textContent = "next scan in "+s+"s";
}

/* ---------- filter wiring ---------- */
function wireToggles(id, set, key){
  document.querySelectorAll(`#${id} button`).forEach(b=>{
    b.onclick=()=>{
      const v=b.dataset[key];
      if(id==="actionGrp" && v==="none"){
        const on=b.getAttribute("aria-pressed")==="true";
        if(on){ set.delete("none"); } else { set.add("none"); }
        b.setAttribute("aria-pressed", (!on).toString());
      }else{
        if(set.has(v)){ if(set.size>1||id==="actionGrp"){ set.delete(v); b.setAttribute("aria-pressed","false"); } }
        else { set.add(v); b.setAttribute("aria-pressed","true"); }
      }
      renderCards();
    };
  });
}
wireToggles("actionGrp", filters.actions, "action");
wireToggles("bucketGrp", filters.buckets, "bucket");
wireToggles("sideGrp", filters.sides, "side");
document.getElementById("refreshBtn").onclick=()=>fetchState(true);

render();
if(LIVE){
  nextScanAt=Date.now()+POLL_MS;
  setInterval(()=>fetchState(false), POLL_MS);
}
setInterval(tickCountdown, 1000);
tickCountdown();
</script>
</body>
</html>
"""


def render_page(state: dict | None, poll_ms: int = 60000, live: bool = True) -> str:
    """Build the dashboard HTML with `state` embedded as the initial payload."""
    payload = state or {}
    # the equity curve lives under paper.* in the scan result; surface it for the chart
    if payload.get("paper") is not None:
        payload = dict(payload)
        payload["paper"] = dict(payload["paper"])
    data = json.dumps(payload, default=str).replace("</", "<\\/")
    return (
        PAGE.replace("__INITIAL_DATA__", data)
        .replace("__POLL_MS__", str(int(poll_ms)))
        .replace("__LIVE__", "true" if live else "false")
    )
