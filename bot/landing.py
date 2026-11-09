"""Landing page (email sign-up) and the admin feedback view."""
from __future__ import annotations

from html import escape

LANDING = r"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Crypto Signal Desk</title>
<meta name="description" content="Multi-timeframe signals for coins and tokenized stocks, sized to your own wallet. Test mode first.">
<style>
  :root{
    color-scheme: dark;
    --plane:#0d0d0d; --surface:#1a1a19; --surface-2:#212120;
    --ink:#ffffff; --ink-2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,0.10);
    --long:#0ca30c; --short:#d03b3b; --watch:#fab219; --accent:#3987e5;
    --radius:12px;
    --font:system-ui,-apple-system,"Segoe UI",sans-serif;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--plane);color:var(--ink);font-family:var(--font);
       font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased}
  a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline}
  .wrap{max-width:1120px;margin:0 auto;padding:0 24px}
  @media (max-width:640px){.wrap{padding:0 16px}}

  nav{display:flex;justify-content:space-between;align-items:center;padding:18px 0}
  .brand{display:flex;align-items:center;gap:10px;font-weight:650;font-size:16px}
  .brand .dot{width:10px;height:10px;border-radius:50%;background:var(--long)}
  nav .cta-link{font-size:14px;font-weight:600}

  .hero{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(0,.9fr);gap:48px;
    align-items:center;padding:56px 0 64px}
  @media (max-width:900px){.hero{grid-template-columns:1fr;gap:36px;padding:32px 0 48px}}
  .eyebrow{display:inline-block;font-size:12px;letter-spacing:.08em;text-transform:uppercase;
    color:#ffce5e;background:rgba(250,178,25,.12);border:1px solid rgba(250,178,25,.35);
    padding:4px 10px;border-radius:999px;margin-bottom:18px}
  h1{font-size:clamp(32px,5vw,50px);line-height:1.08;letter-spacing:-0.025em;margin:0 0 18px;
    font-weight:700}
  .lede{font-size:17.5px;color:var(--ink-2);margin:0 0 28px;max-width:560px}

  form.signup{display:flex;gap:8px;max-width:500px;flex-wrap:wrap}
  form.signup input{flex:1 1 240px;font:inherit;font-size:15px;color:var(--ink);
    background:var(--surface);border:1px solid var(--axis);border-radius:10px;padding:13px 15px}
  form.signup input:focus{outline:2px solid var(--accent);outline-offset:-1px}
  form.signup button{font:inherit;font-size:15px;font-weight:650;color:#fff;
    background:var(--accent);border:0;border-radius:10px;padding:13px 22px;cursor:pointer}
  form.signup button:hover{background:#2a78d6}
  form.signup button:disabled{opacity:.6;cursor:default}
  .fine{font-size:12.5px;color:var(--muted);margin-top:10px;max-width:500px}
  .form-msg{font-size:13.5px;margin-top:10px;min-height:20px}
  .form-msg.err{color:#ff8080} .form-msg.ok{color:#4ede4e}

  /* example card, matches the dashboard */
  .card{background:var(--surface);border:1px solid var(--border);border-left:3px solid var(--long);
    border-radius:var(--radius);padding:18px;display:flex;flex-direction:column;gap:12px;
    box-shadow:0 24px 60px rgba(0,0,0,.45)}
  .card .tagline{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}
  .chead{display:flex;justify-content:space-between;align-items:flex-start}
  .sym{font-size:18px;font-weight:660} .sym span{color:var(--muted);font-weight:400;font-size:13px}
  .badges{display:flex;gap:5px;flex-wrap:wrap;margin-top:5px}
  .badge{font-size:10.5px;padding:2px 7px;border-radius:999px;border:1px solid var(--border);
    color:var(--ink-2);background:var(--surface-2);text-transform:uppercase;letter-spacing:.05em}
  .pill{font-size:12px;font-weight:650;padding:4px 10px;border-radius:999px;
    background:rgba(12,163,12,.16);color:#4ede4e;border:1px solid rgba(12,163,12,.45)}
  .price{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums}
  .chance{display:grid;grid-template-columns:auto 1fr auto;gap:10px;align-items:center;font-size:13px;color:var(--ink-2)}
  .chance .cb{height:6px;border-radius:3px;background:var(--surface-2);overflow:hidden}
  .chance .cb i{display:block;height:100%;width:40%;background:var(--watch)}
  table.lv{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}
  table.lv td{padding:4px 0;border-bottom:1px solid var(--grid)}
  table.lv td:first-child{color:var(--muted)} table.lv td:last-child{text-align:right;font-weight:520}
  table.lv tr:last-child td{border-bottom:none}
  .mine{background:var(--surface-2);border-radius:8px;padding:10px 12px}
  .mine table.lv td{border-bottom-color:var(--axis)}
  .up{color:#4ede4e} .down{color:#ff8080}

  section{padding:56px 0;border-top:1px solid var(--grid)}
  h2{font-size:clamp(24px,3.2vw,32px);letter-spacing:-0.02em;margin:0 0 10px;font-weight:680}
  .sub{color:var(--ink-2);margin:0 0 32px;max-width:640px}
  .grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
  @media (max-width:900px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
  @media (max-width:600px){.grid{grid-template-columns:1fr}}
  .feat{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:20px}
  .feat h3{font-size:16px;margin:0 0 6px;font-weight:640}
  .feat p{margin:0;color:var(--ink-2);font-size:14px}
  .feat .ic{font-size:20px;margin-bottom:10px;display:block}
  .steps{counter-reset:s;display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px}
  .step{position:relative;padding:20px 20px 20px 62px;background:var(--surface);
    border:1px solid var(--border);border-radius:var(--radius)}
  .step::before{counter-increment:s;content:counter(s);position:absolute;left:20px;top:20px;
    width:28px;height:28px;border-radius:50%;background:var(--accent);color:#fff;font-weight:700;
    display:flex;align-items:center;justify-content:center;font-size:14px}
  .step h3{font-size:15.5px;margin:0 0 4px} .step p{margin:0;color:var(--ink-2);font-size:14px}
  .honest{background:rgba(250,178,25,.07);border:1px solid rgba(250,178,25,.3);border-radius:var(--radius);
    padding:24px 26px}
  .honest ul{margin:12px 0 0;padding-left:20px;color:var(--ink-2)} .honest li{margin:6px 0}
  .final{text-align:center} .final .sub{margin-left:auto;margin-right:auto}
  .final form.signup{margin:0 auto} .final .fine{margin:10px auto 0}
  footer{border-top:1px solid var(--grid);padding:28px 0 48px;color:var(--muted);font-size:12.5px}
</style>
</head>
<body>
<div class="wrap">
  <nav>
    <div class="brand"><span class="dot"></span>Crypto Signal Desk</div>
    <a class="cta-link" href="#get-access">Get access →</a>
  </nav>

  <div class="hero">
    <div>
      <span class="eyebrow">Free · test mode first</span>
      <h1>See what the market is doing before you trade.</h1>
      <p class="lede">Crypto Signal Desk scans coins and tokenized stocks across five timeframes,
        checks the news, and gives you an entry, a stop and targets, sized to the money in your own
        wallet.</p>
      <form class="signup" id="form1" novalidate>
        <input type="email" name="email" placeholder="you@example.com" autocomplete="email"
          aria-label="Email address" required>
        <button type="submit">Get access</button>
      </form>
      <div class="form-msg" id="msg1" role="status"></div>
      <div class="fine">We use your email to let you in and, now and then, to ask what to improve.
        No spam, and it is never sold.</div>
    </div>

    <div class="card" aria-label="Example signal card">
      <div class="tagline">Example signal card</div>
      <div class="chead">
        <div><div class="sym">SOL<span>/USDT</span></div>
          <div class="badges"><span class="badge">large cap</span><span class="badge">trending</span>
            <span class="badge">5m→1h</span></div></div>
        <span class="pill">↑ LONG</span>
      </div>
      <div class="price">93.28</div>
      <div class="chance"><span>Success chance</span><div class="cb"><i></i></div><strong>~40%</strong></div>
      <table class="lv">
        <tr><td>Entry</td><td>93.28</td></tr>
        <tr><td>Stop loss</td><td class="down">92.71 (0.61%)</td></tr>
        <tr><td>Target 1 / 2</td><td>93.85 / 94.42</td></tr>
      </table>
      <div class="mine">
        <div style="font-weight:600;font-size:13px;margin-bottom:4px">With your $100 wallet</div>
        <table class="lv">
          <tr><td>Buy</td><td>0.2144 SOL · $20.00</td></tr>
          <tr><td>If the stop is hit</td><td class="down">−$0.15</td></tr>
          <tr><td>At target 2</td><td class="up">+$0.22</td></tr>
        </table>
      </div>
    </div>
  </div>
</div>

<section>
  <div class="wrap">
    <h2>What you get</h2>
    <p class="sub">One page that shows what a dozen indicators, three timeframes and the news are
      saying right now, and why.</p>
    <div class="grid">
      <div class="feat"><span class="ic">📈</span><h3>Multi-timeframe signals</h3>
        <p>Scalp (5m → 1h) and swing (4h → 1d) calls, each with its score, the reasoning behind it,
          and what would prove it wrong.</p></div>
      <div class="feat"><span class="ic">🔎</span><h3>Search coins &amp; stocks</h3>
        <p>Type "solana", "apple" or "nvidia". Over 500 pairs, including tokenized stocks and
          gold, analysed on demand.</p></div>
      <div class="feat"><span class="ic">👛</span><h3>Sized to your wallet</h3>
        <p>Enter your balance and spend limit. Every card shows what to buy, what you lose if the
          stop is hit, and what fees cost you.</p></div>
      <div class="feat"><span class="ic">⚡</span><h3>Best for small, fast trades</h3>
        <p>A scalp-fit rating that checks liquidity, spread, movement and whether fees eat the
          move, not only whether the chart looks good.</p></div>
      <div class="feat"><span class="ic">📰</span><h3>News cross-check</h3>
        <p>Eight crypto news feeds scored per coin. A hack, exploit or delisting headline blocks
          the signal outright.</p></div>
      <div class="feat"><span class="ic">🧪</span><h3>Paper trading built in</h3>
        <p>Every signal is traded with play money first, so you can see how it does before you
          trust it with real money.</p></div>
    </div>
  </div>
</section>

<section>
  <div class="wrap">
    <h2>How it works</h2>
    <p class="sub">Three steps, and the first trades are never with real money.</p>
    <div class="steps">
      <div class="step"><h3>Enter your email</h3><p>You're in straight away. No password, no card.</p></div>
      <div class="step"><h3>Set your wallet</h3><p>Balance, the most you'll spend per trade, and how much
        you're willing to lose if a stop is hit.</p></div>
      <div class="step"><h3>Watch, test, then decide</h3><p>Read the signals, let paper trading run,
        and use the TradingView links to learn as you go.</p></div>
    </div>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="honest">
      <h2 style="font-size:22px">What it isn't</h2>
      <p style="margin:0;color:var(--ink-2)">We'd rather you trust us for being straight with you.</p>
      <ul>
        <li><strong>Not financial advice, and not a guarantee.</strong> A high score means several
          independent measurements agree right now. It doesn't predict the future.</li>
        <li><strong>The success chance starts at ~40%,</strong> an estimate, and updates from real
          paper-trading results as trades close. Each card shows how many trades its number is
          based on.</li>
        <li><strong>Most retail traders lose money.</strong> Fees and slippage hit small, fast
          trades hardest. The wallet panel shows you exactly how much.</li>
        <li><strong>Start in test mode.</strong> Real orders are off unless you deliberately switch
          them on yourself.</li>
      </ul>
    </div>
  </div>
</section>

<section class="final" id="get-access">
  <div class="wrap">
    <h2>Try it free</h2>
    <p class="sub">Enter your email to open the desk. Tell us what to improve; there's a feedback
      button inside.</p>
    <form class="signup" id="form2" novalidate>
      <input type="email" name="email" placeholder="you@example.com" autocomplete="email"
        aria-label="Email address" required>
      <button type="submit">Get access</button>
    </form>
    <div class="form-msg" id="msg2" role="status"></div>
  </div>
</section>

<footer>
  <div class="wrap">Crypto Signal Desk shows analysis, not advice. Trading crypto and tokenized
    stocks carries a high risk of loss. Only trade money you can afford to lose.</div>
</footer>

<script>
const EMAIL_RE=/^[^@\s]{1,64}@[^@\s]{1,255}\.[A-Za-z]{2,}$/;
function wire(formId,msgId){
  const f=document.getElementById(formId), m=document.getElementById(msgId);
  const input=f.querySelector("input"), btn=f.querySelector("button");
  f.addEventListener("submit",async e=>{
    e.preventDefault();
    const email=input.value.trim();
    if(!EMAIL_RE.test(email)){ m.className="form-msg err"; m.textContent="Please enter a valid email address."; input.focus(); return; }
    btn.disabled=true; btn.textContent="Opening…"; m.textContent="";
    try{
      const r=await fetch("/api/signup",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({email})});
      const d=await r.json();
      if(!r.ok||!d.ok) throw new Error(d.error||"Something went wrong");
      m.className="form-msg ok"; m.textContent=d.new?"You're in — opening the desk…":"Welcome back — opening the desk…";
      location.href="/app";
    }catch(err){
      m.className="form-msg err"; m.textContent=err.message;
      btn.disabled=false; btn.textContent="Get access";
    }
  });
}
wire("form1","msg1"); wire("form2","msg2");
</script>
</body>
</html>
"""


def render_landing() -> str:
    return LANDING


def render_admin(summary: dict, signups: list[dict], feedback: list[dict],
                 users: list[dict] | None = None) -> str:
    e = lambda v: escape(str(v if v is not None else ""))
    user_rows = "".join(
        f"<tr><td>{e(u['email'])}</td><td>${u['equity']:,.2f}</td>"
        f"<td style=\"color:{'#3fb37f' if u['return_pct'] >= 0 else '#e66767'}\">{u['return_pct']:+.2f}%</td>"
        f"<td>{u['wins']} of {u['closed']}</td><td>{u['open']}</td>"
        f"<td>{u['settings']['risk_per_trade_pct']:g}% · {u['settings']['max_open_positions']} at once · "
        f"${u['settings']['max_notional_per_trade']:,.0f} max · goal +{u['settings']['profit_goal_pct']:g}%</td></tr>"
        for u in (users or [])
    ) or '<tr><td colspan="6">No user has opened the desk yet.</td></tr>'

    votes = "".join(f"<li>{e(k)} — {v}</li>" for k, v in summary["improve_votes"].items()) \
        or "<li>none yet</li>"
    fb_rows = "".join(
        f"<tr><td>{e(f.get('at', '')[:16].replace('T', ' '))}</td><td>{e(f.get('email'))}</td>"
        f"<td>{'★' * (f.get('rating') or 0) or '–'}</td><td>{e(', '.join(f.get('improve') or []))}</td>"
        f"<td>{e(f.get('liked'))}</td><td>{e(f.get('message'))}</td></tr>"
        for f in reversed(feedback)
    ) or '<tr><td colspan="6">No feedback yet.</td></tr>'
    su_rows = "".join(
        f"<tr><td>{e(s.get('at', '')[:16].replace('T', ' '))}</td><td>{e(s.get('email'))}</td></tr>"
        for s in reversed(signups)
    ) or '<tr><td colspan="2">No sign-ups yet.</td></tr>'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Feedback admin</title>
<style>
  :root{{color-scheme:dark}}
  body{{margin:0;background:#0d0d0d;color:#fff;font:14px/1.5 system-ui,-apple-system,sans-serif}}
  .wrap{{max-width:1200px;margin:0 auto;padding:24px 16px 60px}}
  h1{{font-size:20px}} h2{{font-size:15px;margin:28px 0 8px}}
  .tiles{{display:flex;gap:10px;flex-wrap:wrap}}
  .tile{{background:#1a1a19;border:1px solid rgba(255,255,255,.1);border-radius:10px;padding:12px 16px;min-width:150px}}
  .tile b{{display:block;font-size:24px}} .tile span{{color:#898781;font-size:12px}}
  .scroll{{overflow-x:auto}}
  table{{width:100%;border-collapse:collapse;background:#1a1a19;border-radius:10px}}
  th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid #2c2c2a;vertical-align:top}}
  th{{color:#898781;font-weight:500;font-size:12px;text-transform:uppercase}}
  a{{color:#3987e5}}
</style></head><body><div class="wrap">
<h1>Feedback &amp; sign-ups</h1><a href="/app">← back to the desk</a>
<div class="tiles" style="margin-top:14px">
  <div class="tile"><b>{summary['signups']}</b><span>sign-ups</span></div>
  <div class="tile"><b>{len(users or [])}</b><span>practice accounts</span></div>
  <div class="tile"><b>{summary['feedback']}</b><span>feedback replies</span></div>
  <div class="tile"><b>{summary['avg_rating'] if summary['avg_rating'] is not None else '–'}</b><span>average rating / 5</span></div>
</div>
<h2>Users' practice accounts</h2><div class="scroll"><table><thead><tr><th>Email</th><th>Balance</th>
<th>Return</th><th>Trades won</th><th>Open</th><th>Their settings</th></tr></thead><tbody>{user_rows}</tbody></table></div>
<h2>What people want improved</h2><ul>{votes}</ul>
<h2>Feedback</h2><div class="scroll"><table><thead><tr><th>When (UTC)</th><th>Email</th><th>Rating</th>
<th>Improve</th><th>What works</th><th>Message</th></tr></thead><tbody>{fb_rows}</tbody></table></div>
<h2>Sign-ups</h2><div class="scroll"><table><thead><tr><th>When (UTC)</th><th>Email</th></tr></thead>
<tbody>{su_rows}</tbody></table></div>
<p style="color:#898781;margin-top:20px">Raw files: state/signups.jsonl and state/feedback.jsonl.
<a href="/admin/feedback.json">JSON export</a></p>
</div></body></html>"""
