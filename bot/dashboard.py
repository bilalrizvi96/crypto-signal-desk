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
  .mood-v{display:flex;align-items:center;gap:8px;font-size:21px}
  .mood-ic{font-size:22px;line-height:1}
  .mood-meter{position:relative;height:6px;border-radius:3px;margin:9px 0 3px;
    background:linear-gradient(90deg,#d03b3b,#5a5a55 50%,#0ca30c)}
  .mood-meter i{position:absolute;top:50%;width:12px;height:12px;border-radius:50%;background:#fff;
    border:2px solid var(--surface);transform:translate(-50%,-50%);box-shadow:0 0 0 1px rgba(0,0,0,.4)}
  .mood-ends{display:flex;justify-content:space-between;font-size:10px;color:var(--muted);
    text-transform:uppercase;letter-spacing:.06em}
  .mood-m{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:6px}
  .mood-m a{color:var(--ink-2)}
  .ai-box{border-radius:8px;padding:8px 10px;font-size:12.5px;border:1px solid var(--border);
    background:var(--surface-2)}
  .ai-box .ai-h{display:flex;justify-content:space-between;align-items:center;gap:8px;font-weight:640}
  .ai-box .ai-s{color:var(--ink-2);font-size:12px;margin-top:3px;line-height:1.4}
  .ai-box.approved{border-color:rgba(12,163,12,.5);background:rgba(12,163,12,.09)}
  .ai-box.approved .ai-h{color:#4ede4e}
  .ai-box.rejected,.ai-box.error{border-color:rgba(208,59,59,.4);background:rgba(208,59,59,.08)}
  .ai-box.rejected .ai-h,.ai-box.error .ai-h{color:#ff9c9c}
  .ai-box.reviewing .ai-h{color:#8fc1ff}
  .ai-box.unavailable,.ai-box.budget{color:var(--muted)}
  .ai-box details{margin-top:5px} .ai-box summary{cursor:pointer;color:var(--ink-2);font-size:11.5px}
  .ai-box ul{margin:4px 0 2px;padding-left:16px} .ai-box li{margin:2px 0;color:var(--ink-2);font-size:11.5px}
  .ai-box .ai-lbl{font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin-top:5px}
  .ai-dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:#8fc1ff;
    margin-right:5px;animation:aipulse 1.2s ease-in-out infinite}
  @keyframes aipulse{50%{opacity:.25}}
  @media (prefers-reduced-motion:reduce){.ai-dot{animation:none}}
  .live-badge{font-size:12px;font-weight:650;padding:5px 10px;border-radius:999px;white-space:nowrap;cursor:help}
  .live-badge.test{color:#ffce5e;background:rgba(250,178,25,.13);border:1px solid rgba(250,178,25,.45)}
  .live-badge.real{color:#fff;background:#d03b3b;border:1px solid #ff8080}
  .macro-pill{font-size:11px;font-weight:600;color:#ffce5e;background:rgba(250,178,25,.13);
    border:1px solid rgba(250,178,25,.4);border-radius:999px;padding:1px 8px;cursor:help}

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

  .test-banner{background:rgba(250,178,25,.12);border:1px solid rgba(250,178,25,.4);
    color:#ffce5e;border-radius:var(--radius);padding:10px 14px;margin-bottom:14px;
    font-size:13px;line-height:1.5}
  .chance{display:grid;grid-template-columns:auto 1fr auto;gap:10px;align-items:center;
    font-size:12px;color:var(--ink-2)}
  .chance .cb{height:6px;border-radius:3px;background:var(--surface-2);overflow:hidden}
  .chance .cb i{display:block;height:100%;background:var(--watch)}
  .chance strong{color:var(--ink);font-size:14px;font-variant-numeric:tabular-nums}
  .chance-note{font-size:11px;color:var(--muted);margin-top:3px}
  .search{position:relative;flex:1 1 300px;max-width:440px}
  .search input{width:100%;font:inherit;font-size:13.5px;color:var(--ink);
    background:var(--surface-2);border:1px solid var(--border);border-radius:8px;
    padding:8px 12px}
  .search input:focus{outline:2px solid var(--series-1);outline-offset:-1px}
  .search .dd{position:absolute;left:0;right:0;top:calc(100% + 4px);z-index:40;
    background:var(--surface);border:1px solid var(--axis);border-radius:8px;
    max-height:360px;overflow-y:auto;display:none;box-shadow:0 8px 24px rgba(0,0,0,.5)}
  .search .dd.open{display:block}
  .dd .opt{display:flex;justify-content:space-between;gap:10px;align-items:center;
    padding:8px 12px;cursor:pointer;border-bottom:1px solid var(--grid);font-size:13px}
  .dd .opt:last-child{border-bottom:none}
  .dd .opt:hover,.dd .opt.active{background:var(--surface-2)}
  .dd .opt .nm{color:var(--muted);font-size:11.5px}
  .dd .msg{padding:10px 12px;color:var(--muted);font-size:12.5px}
  .badge.stock{color:#8fc1ff;border-color:rgba(57,135,229,.5)}
  .badge.commodity{color:#ffce5e;border-color:rgba(250,178,25,.45)}
  .section{background:var(--surface);border:1px solid var(--border);
    border-radius:var(--radius);padding:14px;margin-bottom:14px}
  .section h2{font-size:14px;margin:0 0 10px;font-weight:640;display:flex;
    justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}
  .section h2 .c{color:var(--muted);font-weight:400;font-size:12px}
  .best{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:8px}
  .best button{text-align:left;padding:10px 12px;display:flex;flex-direction:column;gap:3px}
  .best .fit{font-size:20px;font-weight:650;font-variant-numeric:tabular-nums}
  .verdict-good{color:#4ede4e} .verdict-fair{color:#ffce5e} .verdict-poor{color:#ff8080}
  .fit-reasons{margin:6px 0 12px;padding-left:18px;font-size:12.5px;color:var(--ink-2)}
  .links{display:flex;gap:8px;flex-wrap:wrap}
  .links a{font-size:12px;padding:5px 10px;border:1px solid var(--border);
    border-radius:7px;background:var(--surface-2)}
  .wallet label{display:block;font-size:11.5px;color:var(--muted);margin:8px 0 3px}
  .wallet input{width:100%;font:inherit;font-size:14px;color:var(--ink);
    background:var(--surface-2);border:1px solid var(--border);border-radius:7px;padding:7px 10px}
  .wallet .hint{font-size:11.5px;color:var(--muted);margin-top:8px;line-height:1.45}
  .wallet .row2{display:grid;grid-template-columns:1fr 1fr;gap:8px}
  .wallet .modes{display:flex;gap:12px;flex-wrap:wrap;font-size:13px;margin-top:2px}
  .wallet .modes label,.wallet label.chk{display:inline-flex;align-items:center;gap:5px;margin:0;font-size:13px;color:var(--ink-2)}
  .wallet .modes input,.wallet label.chk input{width:auto}
  .wallet .save{margin-top:10px;display:flex;gap:10px;align-items:center}
  .goal-bar{height:7px;border-radius:4px;background:rgba(255,255,255,.08);overflow:hidden;margin:6px 0 3px}
  .goal-bar i{display:block;height:100%;background:var(--series-1)}
  .goal-bar i.done{background:#3fb37f}
  .mine{background:var(--surface-2);border-radius:7px;padding:8px 10px;font-size:12px}
  .mine table.lv td{border-bottom-color:var(--axis)}
  @media (max-width:640px){.wrap{padding:12px 16px 50px}.search{max-width:none}}
  /* ---------- detail view ---------- */
  .detail{background:linear-gradient(180deg,#1d1d1c 0%,var(--surface) 140px);
    border:1px solid var(--border);border-radius:14px;padding:22px;margin-bottom:16px}
  .d-head{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}
  .d-id{display:flex;gap:14px;align-items:center;min-width:0}
  .d-logo{width:46px;height:46px;border-radius:12px;display:flex;align-items:center;justify-content:center;
    font-weight:700;font-size:15px;background:var(--surface-2);border:1px solid var(--axis);flex:none}
  .d-logo.stock{background:rgba(57,135,229,.14);border-color:rgba(57,135,229,.45);color:#8fc1ff}
  .d-logo.commodity{background:rgba(250,178,25,.12);border-color:rgba(250,178,25,.4);color:#ffce5e}
  .d-ticker{font-size:24px;font-weight:700;letter-spacing:-0.02em;line-height:1.1}
  .d-ticker .q{color:var(--muted);font-weight:400;font-size:15px}
  .d-name{color:var(--ink-2);font-size:13.5px;margin-top:3px;display:flex;gap:7px;align-items:center;flex-wrap:wrap}
  .d-price{text-align:right}
  .d-price .p{font-size:28px;font-weight:650;letter-spacing:-0.02em;font-variant-numeric:tabular-nums}
  .d-price .c{font-size:13.5px;font-variant-numeric:tabular-nums}
  .d-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:16px}
  .d-actions a.btn{font-size:13px;font-weight:600;padding:8px 14px;border-radius:9px;
    border:1px solid var(--border);background:var(--surface-2);color:var(--ink)}
  .d-actions a.btn.primary{background:var(--series-1);border-color:transparent;color:#fff}
  .d-actions a.btn:hover{text-decoration:none;filter:brightness(1.12)}
  .d-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:20px 0 18px}
  @media (max-width:900px){.d-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
  .kpi{background:var(--plane);border:1px solid var(--border);border-radius:11px;padding:14px;
    display:flex;gap:12px;align-items:center;min-width:0}
  .kpi .k{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
  .kpi .v{font-size:20px;font-weight:650;letter-spacing:-0.01em;line-height:1.2}
  .kpi .m{font-size:12px;color:var(--ink-2);margin-top:2px}
  .ring{flex:none}
  .d-tabs{display:flex;gap:4px;background:var(--plane);padding:4px;border:1px solid var(--border);
    border-radius:10px;width:max-content;max-width:100%;margin-bottom:14px}
  .d-tabs button{border:0;background:transparent;padding:7px 14px}
  .d-tabs button[aria-pressed="true"]{background:var(--surface-2)}
  .d-grid{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(0,1fr);gap:14px;margin-bottom:14px}
  @media (max-width:1000px){.d-grid{grid-template-columns:1fr}}
  .d-panel{background:var(--plane);border:1px solid var(--border);border-radius:11px;padding:16px;min-width:0}
  .d-panel h3{font-size:13px;margin:0 0 12px;font-weight:640;display:flex;justify-content:space-between;
    align-items:center;gap:8px;color:var(--ink)}
  .d-panel h3 .c{color:var(--muted);font-weight:400;font-size:11.5px}
  .plan-chart{height:320px;position:relative}
  .plan-chart svg{display:block}
  .plan-legend{display:flex;gap:14px;flex-wrap:wrap;font-size:11.5px;color:var(--muted);margin-top:8px}
  .plan-legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
  .factor{display:grid;grid-template-columns:120px minmax(0,1fr) 40px;gap:10px;align-items:center;
    padding:7px 0;border-bottom:1px solid var(--grid);font-size:12.5px}
  .factor:last-child{border-bottom:none}
  .factor .fb{height:7px;border-radius:4px;background:var(--surface-2);overflow:hidden}
  .factor .fb i{display:block;height:100%;border-radius:4px}
  .factor .fn{color:var(--ink-2);font-weight:540}
  .factor .fv{text-align:right;font-variant-numeric:tabular-nums;color:var(--ink-2)}
  .factor .fr{grid-column:1/-1;color:var(--muted);font-size:11.5px;margin-top:-3px}
  .d-votes .vote{grid-template-columns:130px 90px 1fr}
  .d-news a{color:var(--ink)} .d-news .news-item:first-child{padding-top:0}
  dialog#fbDialog{background:var(--surface);color:var(--ink);border:1px solid var(--axis);
    border-radius:var(--radius);padding:20px;width:min(520px,calc(100vw - 32px));
    max-height:calc(100vh - 32px)}
  dialog#fbDialog::backdrop{background:rgba(0,0,0,.6)}
  #fbForm h2{font-size:17px;margin:0 0 4px}
  .fb-sub{color:var(--muted);font-size:12.5px;margin:0 0 12px}
  #fbForm fieldset{border:0;padding:0;margin:0 0 12px}
  #fbForm legend,#fbForm label{font-size:12.5px;color:var(--ink-2);margin-bottom:5px;display:block;padding:0}
  #fbForm textarea{width:100%;font:inherit;font-size:13.5px;color:var(--ink);background:var(--surface-2);
    border:1px solid var(--border);border-radius:7px;padding:8px 10px;resize:vertical;margin-bottom:12px}
  #fbStars{display:flex;gap:6px}
  #fbStars label{margin:0;cursor:pointer;font-size:13px;padding:6px 11px;border:1px solid var(--border);
    border-radius:7px;background:var(--surface-2);color:var(--ink)}
  #fbStars input,#fbAreas input{position:absolute;opacity:0;pointer-events:none}
  #fbStars input:checked+span,#fbAreas input:checked+span{color:#fff}
  #fbStars label:has(input:checked),#fbAreas label:has(input:checked){background:var(--series-1);border-color:transparent}
  #fbStars label:has(input:focus-visible),#fbAreas label:has(input:focus-visible){outline:2px solid var(--series-1);outline-offset:2px}
  #fbAreas{display:flex;flex-wrap:wrap;gap:6px}
  #fbAreas label{margin:0;cursor:pointer;font-size:12.5px;padding:5px 10px;border:1px solid var(--border);
    border-radius:999px;background:var(--surface-2);color:var(--ink)}
  .fb-msg{font-size:13px;min-height:18px;margin-bottom:8px}
  .fb-actions{display:flex;justify-content:flex-end;gap:8px}
  .pp-sub{font-size:11.5px;color:var(--muted)}
  .pp-row{padding:9px 0;border-bottom:1px solid var(--grid)} .pp-row:last-of-type{border-bottom:none}
  .pp-line{display:flex;justify-content:space-between;align-items:baseline;gap:8px;font-size:13px}
  .pp-close{padding:7px 0;border-bottom:1px solid var(--grid)}
  .pp-track{position:relative;height:6px;border-radius:3px;margin:8px 0 3px;
    background:linear-gradient(90deg,rgba(224,86,91,.55),rgba(255,255,255,.12) 50%,rgba(63,191,95,.55))}
  .pp-entry{position:absolute;top:-3px;width:2px;height:12px;background:#fff;opacity:.7;transform:translateX(-1px)}
  .pp-dot{position:absolute;top:50%;width:11px;height:11px;border-radius:50%;transform:translate(-50%,-50%);
    border:2px solid var(--surface)} .pp-dot.up{background:#4ede4e} .pp-dot.down{background:#ff8080}
  .pp-ends{display:flex;justify-content:space-between;font-size:10.5px;color:var(--muted)}
  .pp-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:10px 0 4px}
  .pp-stats div{background:var(--surface-2);border-radius:8px;padding:8px 9px}
  .pp-stats b{display:block;font-size:15px;font-weight:650}
  .pp-stats span{font-size:10.5px;color:var(--muted)}
  .pp-note{font-size:12px;color:var(--ink-2);background:rgba(250,178,25,.08);border:1px solid rgba(250,178,25,.25);
    border-radius:8px;padding:8px 10px;margin-top:8px}
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
    <div class="search" id="searchBox">
      <input id="searchInput" type="search" autocomplete="off" spellcheck="false"
        placeholder="Search coins &amp; stocks — e.g. BTC, Solana, Apple, NVIDIA"
        aria-label="Search coins and stocks" aria-controls="searchDd">
      <div class="dd" id="searchDd" role="listbox"></div>
    </div>
    <div class="top-right">
      <a href="https://www.tradingview.com/education/" target="_blank" rel="noopener"
        style="font-size:13px">Learn trading on TradingView ↗</a>
      <span class="sub" id="countdown" style="color:var(--muted);font-size:12px"></span>
      <span id="liveBadge"></span>
      <button id="feedbackBtn" class="live-only">Feedback</button>
      <button id="refreshBtn" class="primary">Rescan now</button>
      <a href="/logout" class="live-only" style="font-size:12.5px;color:var(--muted)">Sign out</a>
    </div>
  </header>

  <dialog id="fbDialog" aria-labelledby="fbTitle">
    <form id="fbForm" method="dialog">
      <h2 id="fbTitle">How is the desk working for you?</h2>
      <p class="fb-sub">Every reply is read. Tell us what to fix first.</p>
      <fieldset class="stars"><legend>Overall rating</legend><div id="fbStars"></div></fieldset>
      <label for="fbLiked">What's working well?</label>
      <textarea id="fbLiked" rows="2" maxlength="2000" placeholder="e.g. the wallet sizing, the search…"></textarea>
      <fieldset class="areas"><legend>What should we improve?</legend><div id="fbAreas"></div></fieldset>
      <label for="fbMsg">Anything else? Ideas, bugs, coins we're missing…</label>
      <textarea id="fbMsg" rows="3" maxlength="4000"></textarea>
      <div class="fb-msg" id="fbStatus" role="status"></div>
      <div class="fb-actions">
        <button type="button" id="fbCancel">Cancel</button>
        <button type="submit" class="primary" id="fbSend">Send feedback</button>
      </div>
    </form>
  </dialog>

  <div class="test-banner" id="testBanner"><strong>TEST MODE</strong> — trades here are
    simulated with paper money. Signals have roughly a 40% chance of closing in
    profit; each card shows its current estimate. This is not financial advice.</div>
  <div id="errBox"></div>
  <div id="searchResult"></div>
  <div class="tiles" id="tiles"></div>
  <div class="section" id="bestScalp"></div>

  <div class="bar">
    <span class="lbl">Mode</span>
    <div class="grp" id="modeGrp"></div>
    <span class="lbl" id="modeHint" style="margin-right:6px"></span>
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
      <div class="panel wallet" id="walletPanel">
        <h2>Your wallet</h2>
        <label for="wBalance">Wallet balance (USDT)</label>
        <input id="wBalance" type="number" min="0" step="any" inputmode="decimal" placeholder="e.g. 500">
        <label for="wSpend">Most you'll spend on one trade (USDT)</label>
        <input id="wSpend" type="number" min="0" step="any" inputmode="decimal" placeholder="e.g. 50">
        <label for="wRisk">Risk per trade (% of wallet you can lose if stopped)</label>
        <input id="wRisk" type="number" min="0.1" max="10" step="0.1" inputmode="decimal" value="1">
        <div class="hint" id="wHint">Every card will show what to buy with your money, what you lose
          if the stop is hit, and what you make at each target. Saved in this browser only.</div>
      </div>
      <div class="panel wallet" id="botPanel">
        <h2>Bot settings <span class="c" id="bScope">how the bot trades</span></h2>
        <div class="row2">
          <div><label for="bRisk">Risk per trade (%)</label>
            <input id="bRisk" type="number" min="0.1" max="5" step="0.1" inputmode="decimal"></div>
          <div><label for="bOpen">Trades at once</label>
            <input id="bOpen" type="number" min="1" max="20" step="1" inputmode="numeric"></div>
        </div>
        <label for="bNotional">Most money in one trade (USDT)</label>
        <input id="bNotional" type="number" min="5" step="any" inputmode="decimal">
        <div id="bModesRow"><label>Send testnet orders for</label>
        <div class="modes" id="bModes">
          <label><input type="checkbox" value="scalp"> Scalp</label>
          <label><input type="checkbox" value="swing"> Swing</label>
          <label><input type="checkbox" value="position"> Long-term</label>
        </div></div>
        <div class="row2" style="align-items:end">
          <div><label for="bGoal">Profit goal (%)</label>
            <input id="bGoal" type="number" min="0" max="1000" step="1" inputmode="decimal"></div>
          <label class="chk" style="padding-bottom:8px"><input id="bLock" type="checkbox"> Lock profit at goal</label>
        </div>
        <div class="save"><button class="primary" id="bSave">Save</button><span class="hint" id="bMsg" style="margin:0"></span></div>
        <div class="hint" id="bHint"></div>
      </div>
      <div class="panel" id="paperPanel"></div>
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
const MODES = {
  scalp:    {label:"Scalp",     hold:"minutes to hours"},
  swing:    {label:"Swing",     hold:"hours to days"},
  position: {label:"Long-term", hold:"weeks to months"},
};
const modeLabel = m => (MODES[m]||{label:m}).label;

/* ---------- wallet (per-browser, never sent anywhere) ---------- */
const ROUND_TRIP_COST = 0.0014;     // taker fee + slippage, both sides — matches paper.py
const MIN_ORDER_USD = 5;            // most venues reject orders below roughly this
let wallet = {balance:0, spend:0, risk:1};
try{ Object.assign(wallet, JSON.parse(localStorage.getItem("csd:wallet")||"{}")); }catch(e){}

function walletPlan(s){
  if(!(wallet.balance>0) || !s.entry || !s.stop) return null;
  const perUnit=Math.abs(s.entry-s.stop);
  if(!perUnit) return null;
  const budget=wallet.balance*wallet.risk/100;
  let units=budget/perUnit, limitedBy="risk";
  const needed=units*s.entry;                     // spend that would use the full risk budget
  const spendCap=wallet.spend>0?Math.min(wallet.spend,wallet.balance):wallet.balance;
  if(needed>spendCap){
    units=spendCap/s.entry;
    limitedBy=wallet.spend>0&&wallet.spend<wallet.balance?"spend":"balance";
  }
  const spend=units*s.entry, fees=spend*ROUND_TRIP_COST;
  const gain=t=>units*Math.abs(t-s.entry)-fees;
  return {units, spend, fees, loss:units*perUnit+fees, budget, needed, limitedBy,
          targets:(s.targets||[]).map(gain)};
}

/* ---------- outbound links ---------- */
const TRADE_URLS = {
  binance: (b,q)=>`https://www.binance.com/en/trade/${b}_${q}?type=spot`,
};
function links(s, exchange){
  const [b,q]=s.symbol.split("/"), ex=(exchange||state.exchange||"binance").toLowerCase();
  const trade=TRADE_URLS[ex];
  return `<div class="links">
    ${trade?`<a href="${trade(b,q)}" target="_blank" rel="noopener">Trade on ${esc(ex[0].toUpperCase()+ex.slice(1))} ↗</a>`:""}
    <a href="https://www.tradingview.com/chart/?symbol=${encodeURIComponent(ex.toUpperCase()+":"+b+q)}"
      target="_blank" rel="noopener">Chart on TradingView ↗</a>
  </div>`;
}
function classBadge(c){
  return c && c!=="crypto" ? `<span class="badge ${esc(c)}">${esc(c)}</span>` : "";
}

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
  t.push(moodTile(mood));
  const ai=state.ai||{};
  if(ai.enabled){
    t.push(tile("AI reviews today",
      ai.available?`${ai.reviews_today||0}<span style="font-size:15px;color:var(--muted)"> / ${ai.cap}</span>`
                  :`<span style="font-size:18px;color:var(--muted)">off</span>`,
      ai.available?`≈ $${(ai.spend_today_usd||0).toFixed(2)} today · ${ai.web_search?"with web search":"desk data only"}`
                  :esc(ai.reason||"")));
  }
  const goal=state.goal||{};
  if(goal.enabled){
    t.push(tile(`Goal: +${goal.target_pct}%`,
      goal.reached?`<span class="up">Reached</span>`:`${Math.round(goal.progress_pct)}<span style="font-size:15px;color:var(--muted)">% of the way</span>`,
      `<div class="goal-bar"><i class="${goal.reached?"done":""}" style="width:${goal.progress_pct}%"></i></div>`+
      (goal.locked?"profit banked — no new trades":`practice return ${pct(goal.return_pct)}`)));
  }
  if(paper){
    const r=paper.total_return_pct||0;
    t.push(tile("Practice balance", fmtUsd(paper.equity),
      `<span class="${r>=0?"up":"down"}">${pct(r)}</span> from ${fmtUsd(paper.starting_equity)} play money`));
    t.push(tile("Practice trades won",
      paper.closed_trades?`${paper.wins||0}<span style="font-size:15px;color:var(--muted)"> of ${paper.closed_trades}</span>`:"–",
      paper.avg_pnl!=null?`average ${paper.avg_pnl>=0?"+":""}${fmtUsd(paper.avg_pnl)} per trade`:"none finished yet"));
    t.push(tile("Open practice trades", `${paper.open_positions||0}`,
      paper.halted_reason ? `<span class="down">paused for today</span>` : "see the panel on the right"));
  }
  document.getElementById("tiles").innerHTML=t.join("");
}
function moodTile(mood){
  const ms=mood.sentiment||0, label=mood.label||"no data";
  const cls=ms>0.12?"up":(ms<-0.12?"down":"neutral");
  const icon=label==="no data"?"📰":(ms>0.12?"🐂":(ms<-0.12?"🐻":"⚖️"));
  const pos=((Math.max(-1,Math.min(1,ms))+1)/2*100).toFixed(1);
  const nice=label.charAt(0).toUpperCase()+label.slice(1);
  return `<div class="tile mood-tile"><div class="k">News mood</div>
    <div class="v mood-v"><span class="mood-ic" aria-hidden="true">${icon}</span><span class="${cls}">${esc(nice)}</span></div>
    <div class="mood-meter" role="img" aria-label="News sentiment ${esc(label)}, ${(ms*100).toFixed(0)} on a scale from -100 bearish to +100 bullish">
      <i style="left:${pos}%"></i></div>
    <div class="mood-ends"><span>Bear</span><span>Bull</span></div>
    <div class="m mood-m"><a href="#newsPanel">${mood.item_count||0} headlines →</a>
      ${mood.macro_alert?`<span class="macro-pill" title="Fed, inflation, ETF or regulation headlines in the last 6 hours. These move the whole market at once.">⚡ Big macro news</span>`:""}</div>
  </div>`;
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
    return `<button data-mode="${m}" aria-pressed="${m===filters.mode}"
      title="Trades last ${(MODES[m]||{}).hold||""}">${modeLabel(m)}${tfs}</button>`;
  }).join("");
  document.getElementById("modeHint").textContent =
    MODES[filters.mode] ? "trades last "+MODES[filters.mode].hold : "";
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
  el.innerHTML=list.map(s=>card(s)).join("");
  document.querySelectorAll(".card details.votes").forEach(d=>{
    d.addEventListener("toggle",()=>{ if(d.open) sessionStorage.setItem("v:"+d.dataset.k,"1");
      else sessionStorage.removeItem("v:"+d.dataset.k); });
    try{ if(sessionStorage.getItem("v:"+d.dataset.k)) d.open=true; }catch(e){}
  });
}

function card(s, exchange){
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

  const sc=s.success||{}, scPct=sc.pct!=null?sc.pct:40;
  const scNote = sc.basis==="paper"
    ? `from ${sc.trades} closed ${esc(s.mode)} paper trade${sc.trades===1?"":"s"} (${sc.wins} won), blended with the 40% starting estimate`
    : "starting estimate — no paper trades have closed yet";
  const chance=`<div><div class="chance"><span>Success chance</span>
      <div class="cb"><i style="width:${Math.max(0,Math.min(100,scPct))}%"></i></div>
      <strong>~${Math.round(scPct)}%</strong></div>
      <div class="chance-note">${scNote}</div></div>`;

  const plan=walletPlan(s);
  const spotShort = s.direction==="short" && (state.market_type||"spot")==="spot";
  const mine = !plan ? `<div class="mine" style="color:var(--muted)">
      Enter your wallet balance in <strong>Your wallet</strong> to see how much to buy
      and what you could make or lose.</div>`
    : `<div class="mine"><div style="font-weight:600;margin-bottom:2px">With your wallet</div>
      <div class="chance-note" style="margin:0 0 4px">${plan.limitedBy==="risk"
        ? `Sized by your ${wallet.risk}% risk — the stop costs the full ${fmtUsd(plan.budget)} budget.`
        : `Sized by your ${fmtUsd(plan.spend)} ${plan.limitedBy==="spend"?"spend limit":"wallet balance"} — the stop is
           only ${(Math.abs(s.entry-s.stop)/s.entry*100).toFixed(2)}% away, so you risk ${fmtUsd(plan.loss)}
           of your ${fmtUsd(plan.budget)} budget. Using all of it would mean spending ${fmtUsd(plan.needed)}.`}</div>
      <table class="lv">
        <tr><td>${s.direction==="short"?"Sell":"Buy"}</td>
            <td>${fmtPrice(plan.units)} ${esc(s.base)} · ${fmtUsd(plan.spend)}</td></tr>
        <tr><td>If the stop is hit</td><td class="down">−${fmtUsd(plan.loss)}
            (${(()=>{const p=plan.loss/wallet.balance*100; return p<0.1?p.toFixed(2):p.toFixed(1);})()}% of wallet)</td></tr>
        ${plan.targets.map((g,i)=>`<tr><td>At target ${i+1}</td>
            <td class="${g>0?"up":"down"}">${g>0?"+":""}${fmtUsd(g)}</td></tr>`).join("")}
        <tr><td>Fees + slippage</td><td>≈ ${fmtUsd(plan.fees)}</td></tr>
      </table>
      ${plan.spend<MIN_ORDER_USD?`<div class="chance-note" style="color:#ffb694">
        Below the ~$${MIN_ORDER_USD} minimum most exchanges accept — this order would be rejected.</div>`:""}
      ${plan.targets.length && plan.targets[0]<=0?`<div class="chance-note" style="color:#ffb694">
        At this size fees cost more than target 1 pays.</div>`:""}
    </div>`;
  const shortNote = spotShort ? `<div class="chance-note" style="color:#ffb694">
      You can't short on a spot account — this needs futures or margin, which add liquidation risk.</div>` : "";

  return `<article class="card ${cardCls}">
    <div class="chead">
      <div>
        <div class="sym">${esc(s.base)}<span class="q">/${esc(s.symbol.split("/")[1]||"")}</span></div>
        ${s.name?`<div style="font-size:11.5px;color:var(--muted)">${esc(s.name)}</div>`:""}
        <div class="badges">
          ${classBadge(s.asset_class)}
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

    ${chance}
    ${aiBlock(s)}

    <table class="lv">
      <tr><td>Entry</td><td>${fmtPrice(s.entry)}</td></tr>
      <tr><td>Stop loss</td><td style="color:#ff8080">${fmtPrice(s.stop)} (${s.risk_pct_move}%)</td></tr>
      ${tgts}
      <tr><td>Risk / reward</td><td>${s.rr}R to final</td></tr>
      ${plan?"":`<tr><td>Size (${(state.config&&state.config.risk&&state.config.risk.risk_per_trade_pct)||1}% risk)</td>
          <td>${fmtPrice(s.position_size_units)} ${esc(s.base)} · ${fmtUsd(s.position_notional)}</td></tr>`}
      <tr><td>ATR (${esc(s.timeframes.entry)})</td><td>${s.atr_pct}% of price</td></tr>
      <tr><td>${esc(s.timeframes.confirm)} confirm</td><td>${esc((s.confirm&&s.confirm.label)||"–")}</td></tr>
      <tr><td>${esc(s.timeframes.bias)} bias</td><td>${esc((s.bias&&s.bias.label)||"–")}</td></tr>
      <tr><td>Invalidation</td><td style="font-weight:400;color:var(--ink-2);font-size:11.5px">${esc(s.invalidation)}</td></tr>
    </table>

    ${mine}
    ${shortNote}
    ${links(s, exchange)}

    ${warns}
    ${heads?`<div><div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em">News on ${esc(s.base)} · sentiment ${(s.news.sentiment>=0?"+":"")}${s.news.sentiment}</div>${heads}</div>`:""}

    <details class="votes" data-k="${esc(s.symbol)}|${esc(s.mode)}">
      <summary>Why — ${(s.votes||[]).length} indicator reads</summary>
      ${votes}
    </details>
  </article>`;
}

/* ---------- AI review ---------- */
function aiRequired(){ return !!(state&&state.ai&&state.ai.required); }
function aiBlock(s, opts){
  const a=s.ai||{}; opts=opts||{};
  if(!a.state || (a.state==="none" && s.action!=="enter")) return "";
  const conf=a.confidence!=null?` · ${a.confidence}%`:"";
  const list=(xs)=>(xs||[]).length?`<ul>${xs.map(x=>`<li>${esc(x)}</li>`).join("")}</ul>`:"";
  const news=(a.key_news||[]).length?`<div class="ai-lbl">News it used</div><ul>${a.key_news.map(n=>
    `<li>${n.impact==="bullish"?"▲":(n.impact==="bearish"?"▼":"•")} ${/^https?:\/\//.test(n.url||"")
      ?`<a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a>`:esc(n.title)}</li>`).join("")}</ul>`:"";
  const details=(a.reasons_for||a.reasons_against)?`<details${opts.open?" open":""}><summary>Why — the AI's research</summary>
      ${(a.reasons_for||[]).length?`<div class="ai-lbl">For</div>${list(a.reasons_for)}`:""}
      ${(a.reasons_against||[]).length?`<div class="ai-lbl">Against</div>${list(a.reasons_against)}`:""}
      ${news}${a.watch_for?`<div class="ai-lbl">Watch for</div><div class="ai-s">${esc(a.watch_for)}</div>`:""}
      <div class="ai-s" style="margin-top:5px;color:var(--muted)">${esc(a.model||"")} · ${a.seconds||"?"}s · ≈$${(a.cost_usd||0).toFixed(3)}</div>
    </details>`:"";
  const head={
    approved:   ["✓ AI approved this trade"+conf, a.summary],
    rejected:   ["✗ AI says skip"+conf, a.summary],
    error:      [aiRequired()?"✗ AI review failed — trade skipped":"AI review failed — the desk's rules decide", a.error||a.summary],
    reviewing:  [`<span class="ai-dot" aria-hidden="true"></span>AI is researching this trade…`, "It checks live news and the charts; usually under two minutes."],
    unavailable:["AI review is off", (a.reason||"")+(aiRequired()
                  ?". Trades wait for AI approval, so none will open."
                  :". Trades open on the desk's own rules until it's on.")],
    budget:     ["Daily AI review limit reached", aiRequired()?"No new trades open until tomorrow (UTC)."
                  :"Trades open on the desk's own rules until tomorrow (UTC)."],
    none:       ["Not reviewed", "Already holding this or no free trade slot, so no review was needed."],
  }[a.state]||["AI", ""];
  return `<div class="ai-box ${esc(a.state)}" role="status"><div class="ai-h"><span>${head[0]}</span></div>
    ${head[1]?`<div class="ai-s">${esc(head[1])}</div>`:""}
    ${details}</div>`;
}

/* ---------- best for scalping ---------- */
function fitLine(f){
  return `<span class="verdict-${esc(f.verdict)}">${Math.round(f.score)}/100 · ${esc(f.verdict)}</span>`;
}
function renderBest(){
  const best=state.best_scalp||[], el=document.getElementById("bestScalp");
  if(!best.length){ el.style.display="none"; return; }
  el.style.display="";
  el.innerHTML=`<h2>Best for small, fast trades right now
      <span class="c">scalp fit = liquidity, spread, movement, fees vs. stop, signal strength</span></h2>
    <div class="best">${best.map(b=>`<button data-sym="${esc(b.symbol)}"
        title="${esc((b.fit.reasons||[]).join(" · "))}">
      <span style="display:flex;justify-content:space-between;gap:6px;align-items:center">
        <strong>${esc(b.base)}</strong>${classBadge(b.asset_class)}</span>
      <span class="fit">${fitLine(b.fit)}</span>
      <span style="font-size:11.5px;color:var(--muted)">${b.action==="none"?"no signal":
        `${arrow(b.direction)} ${b.action==="enter"?"firing":"watch"} ${b.direction}`}
        · ${fmtPrice(b.price)}</span>
    </button>`).join("")}</div>`;
  el.querySelectorAll("button[data-sym]").forEach(b=>b.onclick=()=>analyse(b.dataset.sym));
}

/* ---------- search ---------- */
let searchTimer=null, searchHits=[], searchActive=-1, analysed=null;
const sIn=document.getElementById("searchInput"), sDd=document.getElementById("searchDd");

function showDd(html){ sDd.innerHTML=html; sDd.classList.add("open"); }
function hideDd(){ sDd.classList.remove("open"); searchActive=-1; }

async function runSearch(q){
  try{
    const r=await fetch("/api/search?q="+encodeURIComponent(q),{cache:"no-store"});
    const d=await r.json();
    if(sIn.value.trim()!==q) return;          // a newer keystroke won
    searchHits=d.results||[]; searchActive=-1;
    if(!searchHits.length){ showDd(`<div class="msg">Nothing on ${esc(state.exchange||"the exchange")} matches “${esc(q)}”.</div>`); return; }
    showDd(searchHits.map((h,i)=>`<div class="opt" role="option" data-i="${i}">
        <span><strong>${esc(h.base)}</strong> <span class="nm">${esc(h.name||h.symbol)}</span></span>
        <span style="display:flex;gap:6px;align-items:center">${classBadge(h.asset_class)}
          <span class="nm">${fmtVol(h.quote_volume)}</span></span></div>`).join(""));
    sDd.querySelectorAll(".opt").forEach(o=>o.onmousedown=e=>{
      e.preventDefault(); pick(+o.dataset.i); });
  }catch(e){ showDd('<div class="msg">Search failed — is the server still running?</div>'); }
}
function pick(i){
  const h=searchHits[i]; if(!h) return;
  sIn.value=h.base+(h.name?" — "+h.name:""); hideDd(); analyse(h.symbol);
}
sIn.addEventListener("input",()=>{
  clearTimeout(searchTimer);
  const q=sIn.value.trim();
  if(!q){ hideDd(); return; }
  searchTimer=setTimeout(()=>runSearch(q),220);
});
sIn.addEventListener("keydown",e=>{
  const opts=[...sDd.querySelectorAll(".opt")];
  if(e.key==="ArrowDown"||e.key==="ArrowUp"){
    if(!opts.length) return;
    e.preventDefault();
    searchActive=(searchActive+(e.key==="ArrowDown"?1:-1)+opts.length)%opts.length;
    opts.forEach((o,i)=>o.classList.toggle("active",i===searchActive));
  }else if(e.key==="Enter"){
    e.preventDefault(); pick(searchActive>=0?searchActive:0);
  }else if(e.key==="Escape"){ hideDd(); }
});
sIn.addEventListener("blur",()=>setTimeout(hideDd,120));

async function analyse(symbol){
  const el=document.getElementById("searchResult");
  el.innerHTML=`<div class="section"><div class="empty">Analysing ${esc(symbol)} for short, medium and long-term trades…</div></div>`;
  el.scrollIntoView({behavior:"smooth",block:"start"});
  try{
    const r=await fetch("/api/analyze?symbol="+encodeURIComponent(symbol),{cache:"no-store"});
    analysed=await r.json();
  }catch(e){ analysed={symbol, error:"request failed — is the server still running?"}; }
  renderAnalysed();
}
function renderAnalysed(){
  const el=document.getElementById("searchResult"), a=analysed;
  if(!a){ el.innerHTML=""; return; }
  const close=`<button id="closeResult" aria-label="Close search result">Close</button>`;
  if(a.error){
    el.innerHTML=`<div class="section"><h2>${esc(a.symbol)} ${close}</h2>
      <div class="warn"><span>⚠</span><div>${esc(a.error)}</div></div></div>`;
  }else{
    el.innerHTML=detailView(a, close);
    drawPlanChart();
    el.querySelectorAll(".d-tabs button").forEach(b=>b.onclick=()=>{ detailMode=b.dataset.mode; renderAnalysed(); });
    const aiBtn=document.getElementById("aiResearchBtn");
    if(aiBtn) aiBtn.onclick=async()=>{
      const sig=(analysed.signals||[]).find(x=>x.mode===aiBtn.dataset.mode);
      if(!sig) return;
      sig.ai={state:"reviewing"}; renderAnalysed();
      try{
        const r=await fetch("/api/ai/research",{method:"POST",headers:{"Content-Type":"application/json"},
          body:JSON.stringify({symbol:analysed.symbol, mode:sig.mode})});
        sig.ai=await r.json();
      }catch(e){ sig.ai={state:"error", error:"request failed — is the server still running?"}; }
      renderAnalysed();
    };
  }
  document.getElementById("closeResult").onclick=()=>{ analysed=null; sIn.value=""; renderAnalysed(); };
}

/* ---------- detail view ---------- */
let detailMode=null;
const VERDICT_COL={good:"var(--long)", fair:"var(--watch)", poor:"var(--short)"};
const FACTOR_NAMES={liquidity:"Liquidity", spread:"Spread", volatility:"Movement",
                    fees:"Fees vs. stop", signal:"Signal strength"};

function ring(value, max, col, label){
  const r=21, c=2*Math.PI*r, frac=Math.max(0,Math.min(1,value/max));
  return `<svg class="ring" width="54" height="54" viewBox="0 0 54 54" role="img" aria-label="${esc(label)}">
    <circle cx="27" cy="27" r="${r}" fill="none" stroke="var(--surface-2)" stroke-width="6"/>
    <circle cx="27" cy="27" r="${r}" fill="none" stroke="${col}" stroke-width="6" stroke-linecap="round"
      stroke-dasharray="${(c*frac).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 27 27)"/>
    <text x="27" y="31.5" text-anchor="middle" fill="var(--ink)" font-size="13" font-weight="700">${Math.round(value)}</text>
  </svg>`;
}

/* Trade-plan chart: candles + a position box, drawn at the container's real
   pixel size so text stays crisp and never scales with the screen. */
function chartCandles(s){
  const c=(analysed&&analysed.candles&&analysed.candles[s.mode])||[];
  if(c.length>=3) return c;
  // no OHLC (e.g. a saved snapshot): rebuild rough candles from closes
  const v=s.spark||[];
  return v.slice(1).map((cl,i)=>[null,v[i],Math.max(v[i],cl),Math.min(v[i],cl),cl]);
}
function drawPlanChart(){
  const host=document.getElementById("planChart");
  if(!host||!analysed||analysed.error) return;
  const s=(analysed.signals||[]).find(x=>x.mode===detailMode);
  const bars=s?chartCandles(s):[];
  if(bars.length<3){ host.innerHTML='<div class="empty">Not enough price history to draw.</div>'; return; }

  const W=Math.max(280,host.clientWidth), H=host.clientHeight||320, narrow=W<520;
  const last=bars[bars.length-1][4];
  // tag column sized to its longest "label price" so tags never overlap their text
  const tagDefs=[
    ...(s.targets||[]).map((t,i)=>({v:t,label:`T${i+1}`,bg:"#3fbf5f",fg:"#06210f"})),
    {v:s.entry,label:narrow?"In":"Entry",bg:"#ffffff",fg:"#111"},
    {v:s.stop,label:narrow?"SL":"Stop",bg:"#e0565b",fg:"#2a0607"},
    // the live price only earns a tag once it has moved off the entry
    ...(Math.abs(last-s.entry)/s.entry>0.0005?[{v:last,label:"Now",bg:"#3987e5",fg:"#fff"}]:[]),
  ];
  const CH=narrow?5.9:6.3;
  const AX=Math.min(132,Math.max(...tagDefs.map(t=>(t.label.length+fmtPrice(t.v).length+1)*CH))+22);
  const T=14, B=22, plotR=W-AX;
  const boxW=Math.max(70,Math.min(170,plotR*0.2));
  const candR=plotR-boxW-10;
  const levels=[s.stop,s.entry,...(s.targets||[])].filter(v=>v);
  const lo=Math.min(...bars.map(b=>b[3]),...levels), hi=Math.max(...bars.map(b=>b[2]),...levels);
  const pad=(hi-lo)*0.07||1, min=lo-pad, max=hi+pad;
  const Y=v=>T+(1-(v-min)/(max-min))*(H-T-B);
  const step=candR/bars.length, bw=Math.max(1.5,Math.min(9,step*0.64));
  const X=i=>step*i+step/2;
  const UP="#3fbf5f", DN="#e0565b";
  const short=s.direction==="short", tEnd=(s.targets||[]).slice(-1)[0];

  // faint price grid
  let g="";
  for(let k=1;k<=4;k++){ const y=(T+(H-T-B)*k/5).toFixed(1);
    g+=`<line x1="0" x2="${plotR}" y1="${y}" y2="${y}" stroke="var(--grid)" stroke-width="1"/>`; }

  // candles
  const cs=bars.map((b,i)=>{ const [ ,o,h,l,c]=b, col=c>=o?UP:DN, x=X(i);
    const yT=Y(Math.max(o,c)), yB=Y(Math.min(o,c));
    return `<line x1="${x.toFixed(1)}" x2="${x.toFixed(1)}" y1="${Y(h).toFixed(1)}" y2="${Y(l).toFixed(1)}" stroke="${col}" stroke-width="1"/>
      <rect x="${(x-bw/2).toFixed(1)}" y="${yT.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(1,yB-yT).toFixed(1)}" rx="1" fill="${col}"/>`;
  }).join("");

  // level guides across history (faint) and the position box (solid)
  const bx=candR+6, bx2=plotR;
  const guide=(v,col)=>`<line x1="0" x2="${bx}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}"
      stroke="${col}" stroke-width="1" stroke-dasharray="3 4" opacity=".45"/>`;
  const pctTxt=v=>((v-s.entry)/s.entry*100*(short?-1:1));
  const rewardH=tEnd?Math.abs(Y(tEnd)-Y(s.entry)):0, riskH=Math.abs(Y(s.stop)-Y(s.entry));
  const box=`
    ${tEnd?`<rect x="${bx}" y="${Math.min(Y(tEnd),Y(s.entry)).toFixed(1)}" width="${bx2-bx}" height="${rewardH.toFixed(1)}" fill="rgba(63,191,95,.16)" stroke="rgba(63,191,95,.55)"/>`:""}
    <rect x="${bx}" y="${Math.min(Y(s.stop),Y(s.entry)).toFixed(1)}" width="${bx2-bx}" height="${riskH.toFixed(1)}" fill="rgba(224,86,91,.16)" stroke="rgba(224,86,91,.55)"/>
    ${(s.targets||[]).slice(0,-1).map(t=>`<line x1="${bx}" x2="${bx2}" y1="${Y(t).toFixed(1)}" y2="${Y(t).toFixed(1)}" stroke="${UP}" stroke-width="1" stroke-dasharray="4 3"/>`).join("")}
    <line x1="${bx}" x2="${bx2}" y1="${Y(s.entry).toFixed(1)}" y2="${Y(s.entry).toFixed(1)}" stroke="#fff" stroke-width="1.5"/>
    ${tEnd&&rewardH>=30?`<text x="${(bx+bx2)/2}" y="${((Y(tEnd)+Y(s.entry))/2-2).toFixed(1)}" text-anchor="middle" paint-order="stroke" stroke="#141414" stroke-width="4" stroke-linejoin="round" fill="#7ee29a" font-size="12" font-weight="700">+${pctTxt(tEnd).toFixed(2)}%</text>
      <text x="${(bx+bx2)/2}" y="${((Y(tEnd)+Y(s.entry))/2+13).toFixed(1)}" text-anchor="middle" paint-order="stroke" stroke="#141414" stroke-width="4" stroke-linejoin="round" fill="#7ee29a" font-size="10.5" opacity=".85">reward · ${s.rr}R</text>`:""}
    ${riskH>=30?`<text x="${(bx+bx2)/2}" y="${((Y(s.stop)+Y(s.entry))/2-2).toFixed(1)}" text-anchor="middle" paint-order="stroke" stroke="#141414" stroke-width="4" stroke-linejoin="round" fill="#ff9a9d" font-size="12" font-weight="700">${pctTxt(s.stop).toFixed(2)}%</text>
      <text x="${(bx+bx2)/2}" y="${((Y(s.stop)+Y(s.entry))/2+13).toFixed(1)}" text-anchor="middle" paint-order="stroke" stroke="#141414" stroke-width="4" stroke-linejoin="round" fill="#ff9a9d" font-size="10.5" opacity=".85">risk · 1R</text>`
      :riskH>=14?`<text x="${(bx+bx2)/2}" y="${((Y(s.stop)+Y(s.entry))/2+4).toFixed(1)}" text-anchor="middle" paint-order="stroke" stroke="#141414" stroke-width="4" stroke-linejoin="round" fill="#ff9a9d" font-size="11" font-weight="700">${pctTxt(s.stop).toFixed(2)}%</text>`:""}`;

  // price tags on the axis, pushed apart so they never overlap
  const tags=tagDefs.map(t=>({...t,y:Y(t.v)})).sort((a,b)=>a.y-b.y);
  const TH=19, GAP=3;
  for(let i=1;i<tags.length;i++) if(tags[i].y-tags[i-1].y<TH+GAP) tags[i].y=tags[i-1].y+TH+GAP;
  const over=tags.length?tags[tags.length-1].y+TH/2-(H-4):0;
  if(over>0) tags.forEach(t=>t.y-=over);
  const tagSvg=tags.map(t=>`<g transform="translate(${plotR+5},${(t.y-TH/2).toFixed(1)})">
      <rect width="${AX-8}" height="${TH}" rx="4" fill="${t.bg}"/>
      <text x="6" y="13.2" fill="${t.fg}" font-size="${narrow?10:10.5}" font-weight="700">${t.label}</text>
      <text x="${AX-14}" y="13.2" text-anchor="end" fill="${t.fg}" font-size="${narrow?10:10.5}" font-weight="600">${fmtPrice(t.v)}</text></g>`).join("");

  // live price line from the last candle to its tag
  const nowY=Y(last).toFixed(1);
  const now=tagDefs.some(t=>t.label==="Now")?`<line x1="${X(bars.length-1).toFixed(1)}" x2="${plotR}" y1="${nowY}" y2="${nowY}" stroke="#3987e5" stroke-width="1" stroke-dasharray="2 3"/>`:"";

  // time labels: first, middle, last candle
  const tfmt=ms=>{ if(!ms) return ""; const d=new Date(ms);
    return bars.length&&(bars[bars.length-1][0]-bars[0][0])>2*864e5
      ? d.toLocaleDateString([], {month:"short",day:"numeric"})
      : d.toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"}); };
  const tl=(candR<360?[0,bars.length-1]:[0,Math.floor(bars.length/2),bars.length-1]).map(i=>bars[i][0]?`<text x="${X(i).toFixed(1)}" y="${H-6}"
      text-anchor="${i===0?"start":(i===bars.length-1?"end":"middle")}" fill="var(--muted)" font-size="10.5">${tfmt(bars[i][0])}</text>`:"").join("");

  host.innerHTML=`<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img"
      aria-label="Last ${bars.length} ${esc(s.timeframes.entry)} candles with entry ${fmtPrice(s.entry)}, stop ${fmtPrice(s.stop)} and targets ${(s.targets||[]).map(fmtPrice).join(", ")}">
    ${g}${guide(s.stop,DN)}${(s.targets||[]).map(t=>guide(t,UP)).join("")}${guide(s.entry,"#ffffff")}
    ${cs}${box}${now}${tagSvg}${tl}
    <line id="pcCross" x1="0" x2="0" y1="${T}" y2="${H-B}" stroke="var(--muted)" stroke-width="1" opacity="0"/>
    <rect id="pcHit" x="0" y="0" width="${candR}" height="${H}" fill="transparent"/>
  </svg>`;

  // hover: crosshair + OHLC tooltip
  const hit=document.getElementById("pcHit"), cross=document.getElementById("pcCross"), tip=document.getElementById("tip");
  hit.addEventListener("mousemove",e=>{
    const r=hit.getBoundingClientRect(), i=Math.max(0,Math.min(bars.length-1,Math.floor((e.clientX-r.left)/step)));
    const [ms,o,h,l,c]=bars[i], x=X(i).toFixed(1);
    cross.setAttribute("x1",x); cross.setAttribute("x2",x); cross.setAttribute("opacity",".6");
    tip.innerHTML=`${ms?`<div style="color:var(--muted);margin-bottom:2px">${new Date(ms).toLocaleString([], {month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"})}</div>`:""}
      O ${fmtPrice(o)} · H ${fmtPrice(h)}<br>L ${fmtPrice(l)} · C <strong class="${c>=o?"up":"down"}">${fmtPrice(c)}</strong>`;
    tip.style.opacity="1";
    tip.style.left=Math.min(innerWidth-190,e.clientX+14)+"px"; tip.style.top=(e.clientY-58)+"px";
  });
  hit.addEventListener("mouseleave",()=>{ tip.style.opacity="0"; cross.setAttribute("opacity","0"); });
}
let chartResize=null;
window.addEventListener("resize",()=>{ clearTimeout(chartResize); chartResize=setTimeout(drawPlanChart,120); });

function detailView(a, close){
  const sigs=a.signals||[];
  if(!detailMode || !sigs.some(s=>s.mode===detailMode))
    detailMode=(sigs.find(s=>s.mode==="scalp")||sigs[0]).mode;
  const s=sigs.find(x=>x.mode===detailMode), f=a.scalp_fit;
  const [b,q]=a.symbol.split("/"), ex=(a.exchange||"binance").toLowerCase(), trade=TRADE_URLS[ex];
  const chg=a.change_24h||0, sc=s.success||{}, scPct=sc.pct!=null?sc.pct:40;
  const dirCol=s.direction==="long"?"#4ede4e":(s.direction==="short"?"#ff8080":"var(--ink-2)");
  const actionTxt=s.action==="enter"?"Firing":(s.action==="watch"?"On watch":"No trade");

  const kpis=`<div class="d-kpis">
    ${f?`<div class="kpi">${ring(f.score,100,VERDICT_COL[f.verdict],"Scalp fit "+f.score+" of 100")}
      <div><div class="k">Scalp fit</div><div class="v" style="color:${VERDICT_COL[f.verdict]}">${esc(f.verdict)}</div>
      <div class="m">for small, fast trades</div></div></div>`:""}
    <div class="kpi"><div><div class="k">${esc(modeLabel(s.mode))} signal</div>
      <div class="v" style="color:${dirCol}">${arrow(s.direction)} ${esc(s.direction==="flat"?"none":s.direction)}</div>
      <div class="m">${actionTxt} · score ${s.score>=0?"+":""}${s.score.toFixed(0)} · ${esc(s.confidence)}</div></div></div>
    <div class="kpi">${ring(scPct,100,"var(--watch)","Success chance "+Math.round(scPct)+" percent")}
      <div><div class="k">Success chance</div><div class="v">~${Math.round(scPct)}%</div>
      <div class="m">${sc.basis==="paper"?`${sc.trades} paper trade${sc.trades===1?"":"s"} so far`:"starting estimate"}</div></div></div>
    <div class="kpi"><div><div class="k">Market</div><div class="v">${fmtVol(a.quote_volume)}</div>
      <div class="m">24h volume · spread ${(a.spread_pct||0).toFixed(3)}%</div></div></div>
  </div>`;

  const tabs=sigs.length>1?`<div class="d-tabs" role="group" aria-label="Trading mode">${sigs.map(x=>
    `<button data-mode="${esc(x.mode)}" aria-pressed="${x.mode===detailMode}">${modeLabel(x.mode)} ${esc(x.timeframes.entry)}</button>`).join("")}</div>`:"";

  const plan=walletPlan(s);
  const planRows=`<table class="lv">
      <tr><td>Entry</td><td>${fmtPrice(s.entry)}</td></tr>
      <tr><td>Stop loss</td><td style="color:#ff8080">${fmtPrice(s.stop)} (−${s.risk_pct_move}%)</td></tr>
      ${(s.targets||[]).map((t,i)=>`<tr><td>Target ${i+1}</td><td style="color:#4ede4e">${fmtPrice(t)}
        (${pct((t-s.entry)/s.entry*100*(s.direction==="short"?-1:1))})</td></tr>`).join("")}
      <tr><td>Risk / reward</td><td>${s.rr}R to final</td></tr>
      <tr><td>Expected hold</td><td>${esc(s.hold_estimate||"–")}</td></tr>
    </table>
    <div class="chance-note" style="margin-top:8px">${esc(s.invalidation)}</div>`;
  const walletRows=!plan?`<div class="mine" style="color:var(--muted);margin-top:12px">Enter your wallet in
      <strong>Your wallet</strong> to see what to buy and what you could make or lose.</div>`
    :`<div class="mine" style="margin-top:12px"><div style="font-weight:600;margin-bottom:4px">With your wallet</div>
      <table class="lv">
        <tr><td>${s.direction==="short"?"Sell":"Buy"}</td><td>${fmtPrice(plan.units)} ${esc(s.base)} · ${fmtUsd(plan.spend)}</td></tr>
        <tr><td>If the stop is hit</td><td class="down">−${fmtUsd(plan.loss)}</td></tr>
        ${plan.targets.map((g,i)=>`<tr><td>At target ${i+1}</td><td class="${g>0?"up":"down"}">${g>0?"+":""}${fmtUsd(g)}</td></tr>`).join("")}
        <tr><td>Fees + slippage</td><td>≈ ${fmtUsd(plan.fees)}</td></tr>
      </table></div>`;

  // same order scalp_fit() writes its reasons in; JSON key order is not guaranteed
  const factors=f?["liquidity","spread","volatility","fees","signal"]
    .filter(k=>k in (f.factors||{})).map((k,i)=>{ const v=f.factors[k];
    const col=v>=0.7?"var(--long)":(v>=0.4?"var(--watch)":"var(--short)");
    return `<div class="factor"><span class="fn">${esc(FACTOR_NAMES[k]||k)}</span>
      <div class="fb"><i style="width:${Math.round(v*100)}%;background:${col}"></i></div>
      <span class="fv">${Math.round(v*100)}</span>
      <span class="fr">${esc((f.reasons||[])[i]||"")}</span></div>`;
  }).join(""):"";

  const votes=(s.votes||[]).slice(0,10).map(v=>{
    const w=Math.min(50,Math.abs(v.contribution)*38);
    const st=v.score>=0?`left:50%;width:${w}%;background:var(--long)`:`right:50%;width:${w}%;background:var(--short)`;
    return `<div class="vote"><div class="vn">${esc(v.name)}</div>
      <div class="vb"><i style="${st}"></i></div><div class="vt">${esc(v.note)}</div></div>`;
  }).join("");

  const blocked=(s.warnings||[]).some(w=>w.startsWith("BLOCKED"));
  const warns=(s.warnings||[]).length?`<div class="warn${blocked?" block":""}" style="margin-bottom:14px">
      <span>${blocked?"⛔":"⚠"}</span><ul>${s.warnings.map(w=>`<li>${esc(w)}</li>`).join("")}</ul></div>`:"";
  const spotShort=s.direction==="short"&&(state.market_type||"spot")==="spot";
  const heads=(s.news&&s.news.headlines)||[];

  return `<div class="detail">
    <div class="d-head">
      <div class="d-id"><div class="d-logo ${esc(a.asset_class)}">${esc(b.slice(0,3))}</div>
        <div><div class="d-ticker">${esc(b)}<span class="q">/${esc(q)}</span></div>
          <div class="d-name">${esc(a.name||a.symbol)} ${classBadge(a.asset_class)}
            <span class="badge">${esc(s.regime)}</span></div></div></div>
      <div style="display:flex;gap:16px;align-items:flex-start">
        <div class="d-price"><div class="p">${fmtPrice(a.last||s.price)}</div>
          <div class="c ${chg>=0?"up":"down"}">${pct(chg)} in 24h</div></div>
        ${close}</div>
    </div>
    <div class="d-actions">
      ${trade?`<a class="btn primary" href="${trade(b,q)}" target="_blank" rel="noopener">Trade on ${esc(ex[0].toUpperCase()+ex.slice(1))} ↗</a>`:""}
      <a class="btn" href="https://www.tradingview.com/chart/?symbol=${encodeURIComponent(ex.toUpperCase()+":"+b+q)}" target="_blank" rel="noopener">Chart on TradingView ↗</a>
      <a class="btn" href="https://www.tradingview.com/education/" target="_blank" rel="noopener">Learn ↗</a>
    </div>
    ${kpis}
    ${tabs}
    ${warns}
    ${spotShort?`<div class="warn" style="margin-bottom:14px"><span>⚠</span><div>You can't short on a spot account —
      this needs futures or margin, which add liquidation risk.</div></div>`:""}
    <div class="d-grid">
      <div class="d-panel"><h3>Trade plan on the chart <span class="c">last ${(s.spark||[]).length} × ${esc(s.timeframes.entry)} candles</span></h3>
        <div class="plan-chart" id="planChart"></div>
        <div class="plan-legend"><span>Hover the candles for prices</span>
          <span><i style="background:rgba(63,191,95,.6)"></i>reward to final target</span>
          <span><i style="background:rgba(224,86,91,.6)"></i>risk if the stop is hit</span></div></div>
      <div class="d-panel"><h3>The plan <span class="c">${esc(s.timeframes.entry)}→${esc(s.timeframes.confirm)}→${esc(s.timeframes.bias)}</span></h3>
        ${planRows}${walletRows}</div>
    </div>
    <div class="d-panel" style="margin-bottom:14px"><h3>AI research <span class="c">${esc(modeLabel(s.mode))} trade</span></h3>
      ${s.ai&&s.ai.state&&s.ai.state!=="none"?aiBlock(s,{open:true})
        :`<div class="ai-s" style="font-size:12.5px;color:var(--ink-2);margin-bottom:8px">Ask Claude to research this ${esc(modeLabel(s.mode)).toLowerCase()} setup:
            live news, the five timeframes, fees against the stop and the desk's record. It says take or skip, with reasons.</div>
          <button class="primary" id="aiResearchBtn" data-mode="${esc(s.mode)}">Research this trade with AI</button>
          <span class="pp-sub" style="margin-left:8px">uses one of today's reviews · ~$0.15–0.30</span>`}
    </div>
    <div class="d-grid">
      <div class="d-panel d-votes"><h3>Why this signal <span class="c">${(s.votes||[]).length} indicator reads · strongest first</span></h3>
        ${votes||'<div class="empty">No indicator reads.</div>'}</div>
      ${f?`<div class="d-panel"><h3>Why this scalp fit <span class="c">${Math.round(f.score)}/100</span></h3>${factors}</div>`
        :`<div class="d-panel"><h3>Scalp fit</h3><div class="empty">Scalp mode is off.</div></div>`}
    </div>
    ${heads.length?`<div class="d-panel d-news"><h3>News on ${esc(b)} <span class="c">sentiment ${(s.news.sentiment>=0?"+":"")}${s.news.sentiment}</span></h3>
      ${heads.slice(0,5).map(h=>`<div class="news-item"><div class="t">${h.link?`<a href="${esc(h.link)}" target="_blank" rel="noopener">`:""}${esc(h.title)}${h.link?"</a>":""}</div>
        <div class="m"><span class="sent ${h.score>=0?"up":"down"}">${h.score>=0?"+":""}${(h.score||0).toFixed(1)}</span>${h.source?`<span>${esc(h.source)}</span>`:""}</div></div>`).join("")}</div>`:""}
  </div>`;
}

/* ---------- wallet wiring ---------- */
function wireWallet(){
  const ids={wBalance:"balance", wSpend:"spend", wRisk:"risk"};
  Object.entries(ids).forEach(([id,key])=>{
    const inp=document.getElementById(id);
    if(wallet[key]) inp.value=wallet[key];
    inp.addEventListener("input",()=>{
      const v=parseFloat(inp.value);
      wallet[key]=isFinite(v)&&v>0?v:(key==="risk"?1:0);
      try{ localStorage.setItem("csd:wallet",JSON.stringify(wallet)); }catch(e){}
      renderWalletHint(); renderCards(); renderAnalysed();
    });
  });
  renderWalletHint();
}
function renderWalletHint(){
  const h=document.getElementById("wHint");
  if(!(wallet.balance>0)){
    h.textContent="Every card will show what to buy with your money, what you lose if the stop is hit, "+
      "and what you make at each target. Saved in this browser only.";
    return;
  }
  const riskUsd=wallet.balance*wallet.risk/100;
  const warn=[];
  if(wallet.spend>wallet.balance) warn.push("Spend per trade is more than your wallet — capped at the balance.");
  if(wallet.risk>2) warn.push("Risking more than 1–2% per trade can empty a wallet in a short losing streak.");

  // what these limits actually do to the signals on screen right now
  const sigs=((state.signals&&state.signals[filters.mode])||[])
    .filter(s=>s.action==="enter"||s.action==="watch");
  const plans=sigs.map(walletPlan).filter(Boolean);
  let actual="";
  if(plans.length){
    const losses=plans.map(p=>p.loss), capped=plans.filter(p=>p.limitedBy!=="risk");
    const lo=Math.min(...losses), hi=Math.max(...losses);
    actual=`<div style="margin-top:6px;color:var(--ink-2)">On the ${plans.length} ${esc(filters.mode||"")} signals
      showing, a stopped trade would lose <strong>${fmtUsd(lo)}${hi-lo>0.005?`–${fmtUsd(hi)}`:""}</strong> including fees.`;
    if(capped.length){
      const need=capped.map(p=>p.needed), byBalance=capped.some(p=>p.limitedBy==="balance");
      actual+=` Your ${byBalance?"wallet balance":"spend limit"} is the tighter rule on ${capped.length} of them —
        their stops are close, so using your full ${fmtUsd(riskUsd)} risk would take about
        ${fmtUsd(Math.min(...need))}${Math.max(...need)-Math.min(...need)>1?`–${fmtUsd(Math.max(...need))}`:""} per trade.
        Raising the risk % won't change those trades${byBalance?"":"; raising the spend limit will"}.`;
    }
    actual+="</div>";
  }
  h.innerHTML=`Risk budget: up to <strong>${fmtUsd(riskUsd)}</strong> per trade
    (${wallet.risk}% of ${fmtUsd(wallet.balance)})${wallet.spend>0?`, never spending more than <strong>${fmtUsd(Math.min(wallet.spend,wallet.balance))}</strong>`:""}.
    ${actual}<div style="margin-top:6px">Saved in this browser only.</div>
    ${warn.map(w=>`<div style="color:#ffb694;margin-top:4px">${w}</div>`).join("")}`;
}

/* ---------- bot settings (saved on the server, used by the bot) ---------- */
let botLoaded=false, botCanEdit=false, botScope="desk";
const bEl=id=>document.getElementById(id);
function fillBot(v){
  bEl("bRisk").value=v.risk_per_trade_pct; bEl("bOpen").value=v.max_open_positions;
  bEl("bNotional").value=v.max_notional_per_trade; bEl("bGoal").value=v.profit_goal_pct;
  bEl("bLock").checked=!!v.lock_at_goal;
  document.querySelectorAll("#bModes input").forEach(c=>c.checked=(v.live_modes||[]).includes(c.value));
  renderBotHint();
}
function botForm(){
  return {risk_per_trade_pct:parseFloat(bEl("bRisk").value), max_open_positions:parseInt(bEl("bOpen").value,10),
    max_notional_per_trade:parseFloat(bEl("bNotional").value), profit_goal_pct:parseFloat(bEl("bGoal").value||"0"),
    lock_at_goal:bEl("bLock").checked,
    live_modes:[...document.querySelectorAll("#bModes input:checked")].map(c=>c.value)};
}
function renderBotHint(){
  const f=botForm(), eq=(state&&state.settings&&state.settings.account_equity)||1000;
  if(!(f.risk_per_trade_pct>0&&f.max_open_positions>0&&f.max_notional_per_trade>0)){ bEl("bHint").textContent=""; return; }
  const inUse=Math.min(eq, f.max_open_positions*f.max_notional_per_trade);
  const worst=f.max_open_positions*eq*f.risk_per_trade_pct/100;
  bEl("bHint").innerHTML=`Up to <strong>${fmtUsd(inUse)}</strong> of the ${fmtUsd(eq)} account can be in trades at once
    (${Math.round(inUse/eq*100)}%). If every open trade hit its stop together you'd lose about
    <strong>${fmtUsd(worst)}</strong> (${(worst/eq*100).toFixed(1)}%).
    ${f.risk_per_trade_pct>2?`<div style="color:#ffb694;margin-top:4px">Above 2% per trade, a short losing streak takes a big bite.</div>`:""}
    <div style="margin-top:6px">${!botCanEdit?"Only the desk owner can change these."
      :botScope==="user"?"These are yours alone: they size the trades in your own practice account, from the next scan."
      :"Applies from the next scan."}</div>`;
}
async function loadBot(){
  try{
    const r=await fetch("/api/settings",{cache:"no-store"}); if(!r.ok) return;
    const v=await r.json(); botCanEdit=!!v.can_edit; botScope=v.scope||"desk"; botLoaded=true;
    bEl("bModesRow").style.display=botScope==="user"?"none":"";
    bEl("bScope").textContent=botScope==="user"?"your practice account":"how the bot trades";
    fillBot(v);
    document.querySelectorAll("#botPanel input,#bSave").forEach(x=>x.disabled=!botCanEdit);
  }catch(e){}
}
function wireBot(){
  if(!LIVE){ bEl("botPanel").style.display="none"; return; }
  document.querySelectorAll("#botPanel input").forEach(i=>i.addEventListener("input",renderBotHint));
  bEl("bSave").onclick=async()=>{
    const msg=bEl("bMsg"); msg.textContent="Saving…";
    try{
      const r=await fetch("/api/settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(botForm())});
      const d=await r.json();
      if(!r.ok||!d.ok){ msg.innerHTML=`<span style="color:#ffb694">${esc(d.error||"Could not save")}</span>`; return; }
      fillBot(d); msg.textContent="Saved ✓";
      if(state&&state.settings) Object.assign(state.settings,d);
    }catch(e){ msg.innerHTML='<span style="color:#ffb694">Could not reach the desk</span>'; }
  };
  loadBot();
}

/* ---------- feedback ---------- */
const FB_AREAS=["signals","search","wallet","success chance","news","speed","design","explanations","other"];
function wireFeedback(){
  const dlg=document.getElementById("fbDialog"), status=document.getElementById("fbStatus");
  document.getElementById("fbStars").innerHTML=[1,2,3,4,5].map(n=>
    `<label><input type="radio" name="fbRating" value="${n}"><span>${"★".repeat(n)}</span></label>`).join("");
  document.getElementById("fbAreas").innerHTML=FB_AREAS.map(a=>
    `<label><input type="checkbox" value="${a}"><span>${a}</span></label>`).join("");
  document.getElementById("feedbackBtn").onclick=()=>{ status.textContent=""; dlg.showModal(); };
  document.getElementById("fbCancel").onclick=()=>dlg.close();
  document.getElementById("fbForm").addEventListener("submit",async e=>{
    e.preventDefault();
    const send=document.getElementById("fbSend");
    const body={
      rating:(document.querySelector('input[name="fbRating"]:checked')||{}).value||null,
      liked:document.getElementById("fbLiked").value,
      improve:[...document.querySelectorAll("#fbAreas input:checked")].map(i=>i.value),
      message:document.getElementById("fbMsg").value,
    };
    send.disabled=true; status.style.color="var(--ink-2)"; status.textContent="Sending…";
    try{
      const r=await fetch("/api/feedback",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify(body)});
      const d=await r.json();
      if(!r.ok||!d.ok) throw new Error(d.error||"Could not send");
      status.style.color="#4ede4e"; status.textContent="Thank you, it's been sent.";
      document.getElementById("fbForm").reset();
      setTimeout(()=>dlg.close(),1200);
    }catch(err){ status.style.color="#ff8080"; status.textContent=err.message; }
    finally{ send.disabled=false; }
  });
}

/* ---------- side panels ---------- */
const EXIT_WORDS={
  "stopped out":["Hit stop loss","down"],
  "breakeven stop":["Closed at entry — no loss","neutral"],
  "final target":["Hit target 2 ✓","up"],
  "timed out":["Closed — took too long","neutral"],
};
function exitWords(reason){ return EXIT_WORDS[reason]||[reason,"neutral"]; }

/* where the price sits between the stop (left) and the final target (right) */
function progressBar(x){
  const tEnd=(x.targets||[]).slice(-1)[0];
  if(x.mark==null||!tEnd||!x.stop) return "";
  const lo=x.direction==="short"?tEnd:x.stop, hi=x.direction==="short"?x.stop:tEnd;
  const span=hi-lo||1, at=v=>Math.max(0,Math.min(100,(v-lo)/span*100));
  const flip=x.direction==="short";
  const pos=v=>flip?100-at(v):at(v);               // stop always on the left
  return `<div class="pp-track" aria-hidden="true">
      <span class="pp-entry" style="left:${pos(x.entry).toFixed(1)}%"></span>
      <span class="pp-dot ${(x.unrealised||0)>=0?"up":"down"}" style="left:${pos(x.mark).toFixed(1)}%"></span></div>
    <div class="pp-ends"><span>stop ${fmtPrice(x.stop)}</span><span>target ${fmtPrice(tEnd)}</span></div>`;
}

function renderPaper(){
  const p=state.paper, el=document.getElementById("paperPanel");
  if(!p){ el.innerHTML='<h2>Practice account</h2><div class="empty">Practice trading is off in config.</div>'; return; }
  const diff=(p.equity||0)-(p.starting_equity||0), r=p.total_return_pct||0;
  const n=p.closed_trades||0, wins=p.wins||0;
  const avg=n&&p.avg_pnl!=null?p.avg_pnl:null;

  const pos=(p.positions||[]).map(x=>{
    const u=x.unrealised;
    return `<div class="pp-row">
      <div class="pp-line"><span><strong>${arrow(x.direction)} ${esc(x.base)}</strong>
          <span class="pp-sub">${x.direction==="short"?"sold":"bought"} at ${fmtPrice(x.entry)}</span></span>
        <span class="${u==null?"neutral":(u>=0?"up":"down")}" style="font-weight:640">${u==null?"–":(u>=0?"+":"")+fmtUsd(u)}</span></div>
      <div class="pp-sub">${x.mark!=null?`now ${fmtPrice(x.mark)}`:"waiting for a price"}${x.tp1_hit?" · half sold at target 1, stop moved to entry":""}</div>
      ${progressBar(x)}
    </div>`;
  }).join("");

  const closes=(p.recent_trades||[]).slice(0,6).map(t=>{
    const [words,cls]=exitWords(t.reason);
    return `<div class="pp-line pp-close" title="${esc(t.r_multiple)}R · fees ${fmtUsd(t.fees)}">
      <span><strong>${arrow(t.direction)} ${esc(t.base)}</strong> <span class="pp-sub ${cls}">${esc(words)}</span></span>
      <span class="${t.pnl>=0?"up":"down"}" style="font-weight:640">${t.pnl>=0?"+":""}${fmtUsd(t.pnl)}</span></div>`;
  }).join("");

  el.innerHTML=`<h2>Practice account <span class="c">play money</span></h2>
    <div style="display:flex;justify-content:space-between;align-items:baseline">
      <span style="font-size:23px;font-weight:650">${fmtUsd(p.equity)}</span>
      <span class="${diff>=0?"up":"down"}" style="font-size:13px;font-weight:600">${pct(r)}</span></div>
    <div class="pp-sub" style="margin-bottom:8px">${Math.abs(diff)<0.005?"Even":(diff>0?"Up ":"Down ")+fmtUsd(Math.abs(diff))}
      since it started with ${fmtUsd(p.starting_equity)}. No real money is used.</div>
    ${equityChart(p.equity_curve_points||[], p.starting_equity)}
    <div class="pp-stats">
      <div><b>${n?`${wins} of ${n}`:"–"}</b><span>trades won</span></div>
      <div><b class="${avg==null?"":(avg>=0?"up":"down")}">${avg==null?"–":(avg>=0?"+":"")+fmtUsd(avg)}</b><span>average per trade</span></div>
      <div><b>${fmtUsd(p.total_fees)}</b><span>fees paid</span></div>
    </div>
    ${state.ai&&state.ai.enabled?`<div class="pp-sub" style="margin:6px 0 2px">${p.ai_closed
      ?`AI-approved trades: <strong style="color:var(--ink)">${p.ai_wins} of ${p.ai_closed} won</strong>`
      :"No AI-approved trade has finished yet. This line will show whether the AI's picks do better."}</div>`:""}
    ${n&&!wins?`<div class="pp-note">No winning trades yet. That's what practice money is for:
      it shows how the signals really perform before anyone risks savings on them.</div>`:""}
    ${state.goal&&state.goal.locked?`<div class="pp-note" style="margin-top:9px">🎯 Profit goal of +${state.goal.target_pct}% reached.
      Open trades were closed to bank it and no new ones open. Raise the goal in <strong>Bot settings</strong> to keep going.</div>`:""}
    ${p.halted_reason?`<div class="warn block" style="margin-top:9px"><span>⛔</span><div>Paused for today:
      losses hit the daily limit, so no new practice trades open until tomorrow.</div></div>`:""}
    <h2 style="margin-top:16px">Open trades <span class="c">${(p.positions||[]).length}</span></h2>
    ${pos||'<div class="empty">No open practice trades.</div>'}
    ${closes?`<h2 style="margin-top:16px">Finished trades</h2>${closes}`:""}
    <details class="votes" style="margin-top:12px"><summary>Advanced stats</summary>
      <table class="lv" style="margin-top:6px">
        <tr><td title="Average result in multiples of the amount risked">Avg / best / worst R</td>
            <td>${(p.avg_r||0).toFixed(2)} / ${p.best_r} / ${p.worst_r}</td></tr>
        <tr><td title="Money won divided by money lost">Profit factor</td><td>${p.profit_factor===null?"–":p.profit_factor}</td></tr>
        <tr><td title="Largest drop from a peak">Max drawdown</td><td>${p.max_drawdown_pct}%</td></tr>
      </table></details>`;
  wireEquityHover(p.equity_curve_points||[]);
}

function renderLive(){
  // the lock-by-lock panel is hidden for now; while orders are actually being
  // sent a header badge stays, so the page never hides that orders go out
  const l=(state.live&&state.live.gate)||{}, el=document.getElementById("liveBadge");
  if(!l.armed){ el.innerHTML=""; return; }
  el.innerHTML = l.testnet
    ? `<span class="live-badge test" title="Play-money orders are being sent to the ${esc(l.exchange)} testnet">● Testnet orders on</span>`
    : `<span class="live-badge real" title="Real orders with real money are being sent to ${esc(l.exchange)}">● REAL-MONEY ORDERS ON</span>`;
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
  if(!state || !state.generated_at){ return; }   // nothing scanned yet
  const dot=document.getElementById("statusDot");
  const errs=(state.errors||[]);
  document.getElementById("errBox").innerHTML = state.error
    ? `<div class="err-box"><strong>Scan failed.</strong> ${esc(state.error)}</div>` : "";
  dot.className="dot"+(state.error?" err":(errs.length?" stale":""));

  document.getElementById("subline").textContent =
    `${state.exchange||"–"} ${state.market_type||""} · ${(state.counts&&state.counts.symbols)||0} pairs · `+
    `scan #${state.scan_number||0} ${ago(state.generated_at)} (${state.duration_seconds||0}s)`;

  renderTiles(); renderBest(); renderCards(); renderWalletHint();
  renderPaper(); renderLive(); renderNews();

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
wireWallet();
wireBot();
wireFeedback();
// search, feedback and sign-out need the live server; a saved snapshot has nothing to ask
if(!LIVE){
  document.getElementById("searchBox").style.display="none";
  document.querySelectorAll(".live-only").forEach(el=>el.style.display="none");
}

render();
if(LIVE){
  nextScanAt=Date.now()+POLL_MS;
  setInterval(()=>fetchState(false), POLL_MS);
  // a freshly started server serves the page before its first scan finishes
  if(!(state&&state.generated_at)){
    document.getElementById("subline").textContent="first market scan running — about 30 seconds…";
    const warm=setInterval(async()=>{ await fetchState(false);
      if(state&&state.generated_at) clearInterval(warm); }, 3000);
  }
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
