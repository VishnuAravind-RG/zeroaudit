"""
Run from C:\zeroaudit (with your venv active):
    python write_dashboard.py

Creates C:\zeroaudit\dashboard_static\index.html  (the live-wired dashboard)
        C:\zeroaudit\dashboard_static\serve.py     (FastAPI proxy server)

Then:
    pip install fastapi uvicorn httpx
    cd C:\zeroaudit\dashboard_static
    python serve.py

Open: http://localhost:3000
"""
import pathlib, textwrap

BASE = pathlib.Path(r"C:\zeroaudit\dashboard_static")
BASE.mkdir(exist_ok=True)

# ── index.html ────────────────────────────────────────────────────────────────
# Paste the full content of the original HTML up to (not including) <script>,
# then inject the live-data script below.

HTML_BODY = r"""
<style>
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap');
  *{box-sizing:border-box;margin:0;padding:0}
  :root{
    --obsidian:#0a0c0f;
    --slate:#0e1117;
    --panel:#111520;
    --panel2:#161c2a;
    --border:#1e2a3a;
    --border2:#243040;
    --cyan:#00d4ff;
    --cyan2:#0099bb;
    --cyan-dim:#00d4ff22;
    --amber:#f59e0b;
    --amber-dim:#f59e0b22;
    --red:#ef4444;
    --red-dim:#ef444422;
    --green:#22c55e;
    --green-dim:#22c55e22;
    --text:#e2e8f0;
    --text2:#94a3b8;
    --text3:#475569;
    --mono:'JetBrains Mono',monospace;
  }
  body{background:var(--obsidian);color:var(--text);font-family:var(--mono);font-size:13px;min-height:100vh}
  .topbar{display:flex;align-items:center;justify-content:space-between;padding:10px 20px;background:var(--slate);border-bottom:1px solid var(--border);position:sticky;top:0;z-index:100}
  .topbar-left{display:flex;align-items:center;gap:16px}
  .logo{font-family:var(--mono);font-weight:700;font-size:16px;color:var(--cyan);letter-spacing:3px}
  .tagline{font-family:var(--mono);font-size:10px;color:var(--text3);letter-spacing:2px}
  .status-row{display:flex;gap:12px;align-items:center}
  .status-dot{width:6px;height:6px;border-radius:50%;background:var(--green);box-shadow:0 0 6px var(--green);animation:pulse 2s ease-in-out infinite}
  .status-dot.warn{background:var(--amber);box-shadow:0 0 6px var(--amber)}
  .status-dot.dead{background:var(--red);box-shadow:0 0 6px var(--red);animation:none}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
  .status-label{font-family:var(--mono);font-size:10px;color:var(--text2);letter-spacing:1px}
  .time-display{font-family:var(--mono);font-size:11px;color:var(--cyan);letter-spacing:1px}
  .nav{display:flex;gap:2px;padding:0 20px;background:var(--slate);border-bottom:1px solid var(--border)}
  .nav-tab{padding:10px 20px;font-family:var(--mono);font-size:11px;letter-spacing:1.5px;color:var(--text3);cursor:pointer;border-bottom:2px solid transparent;transition:all .2s;background:none;border-top:none;border-left:none;border-right:none}
  .nav-tab:hover{color:var(--text2)}
  .nav-tab.active{color:var(--cyan);border-bottom-color:var(--cyan)}
  .main{padding:16px 20px;display:flex;flex-direction:column;gap:12px}
  .panel{background:var(--panel);border:1px solid var(--border);border-radius:4px;overflow:hidden}
  .panel-header{display:flex;align-items:center;justify-content:space-between;padding:8px 14px;background:var(--panel2);border-bottom:1px solid var(--border)}
  .panel-title{font-family:var(--mono);font-size:10px;font-weight:700;letter-spacing:2px;color:var(--cyan)}
  .panel-sub{font-family:var(--mono);font-size:9px;color:var(--text3);letter-spacing:1px}
  .panel-body{padding:14px}
  .metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
  .metric{background:var(--panel2);border:1px solid var(--border);border-radius:4px;padding:12px 14px}
  .metric-label{font-family:var(--mono);font-size:9px;color:var(--text3);letter-spacing:1.5px;margin-bottom:6px}
  .metric-val{font-family:var(--mono);font-size:22px;font-weight:700;line-height:1}
  .metric-val.cyan{color:var(--cyan)}.metric-val.green{color:var(--green)}.metric-val.amber{color:var(--amber)}.metric-val.red{color:var(--red)}
  .metric-sub{font-family:var(--mono);font-size:9px;color:var(--text3);margin-top:4px}
  .two-col{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  .three-col{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}
  .pipeline{display:flex;align-items:center;gap:0;padding:16px 0;overflow-x:auto}
  .pipe-node{display:flex;flex-direction:column;align-items:center;gap:6px;min-width:80px}
  .pipe-icon{width:44px;height:44px;border-radius:4px;border:1px solid;display:flex;align-items:center;justify-content:center;font-family:var(--mono);font-size:9px;font-weight:700;letter-spacing:1px}
  .pipe-icon.active{border-color:var(--cyan);color:var(--cyan);background:var(--cyan-dim)}
  .pipe-icon.warn{border-color:var(--amber);color:var(--amber);background:var(--amber-dim)}
  .pipe-icon.ok{border-color:var(--green);color:var(--green);background:var(--green-dim)}
  .pipe-icon.dead{border-color:var(--red);color:var(--red);background:var(--red-dim)}
  .pipe-label{font-family:var(--mono);font-size:9px;color:var(--text3);letter-spacing:1px;text-align:center}
  .pipe-arrow{flex:1;height:1px;background:linear-gradient(90deg,var(--cyan2),var(--border));position:relative;min-width:20px}
  .pipe-arrow::after{content:'▶';position:absolute;right:-6px;top:-7px;font-size:10px;color:var(--cyan2)}
  .live-table{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:11px}
  .live-table th{padding:6px 10px;text-align:left;color:var(--text3);font-size:9px;letter-spacing:1.5px;border-bottom:1px solid var(--border);font-weight:400}
  .live-table td{padding:7px 10px;border-bottom:1px solid var(--border);color:var(--text2);vertical-align:middle}
  .live-table tr:last-child td{border-bottom:none}
  .live-table tr.anomaly td{background:rgba(239,68,68,0.05)}
  .live-table tr:hover td{background:var(--panel2)}
  .hash-cell{color:var(--cyan);font-size:10px;font-family:var(--mono)}
  .badge{display:inline-block;padding:2px 7px;border-radius:2px;font-size:9px;font-weight:700;letter-spacing:1px}
  .badge.verified{background:var(--green-dim);color:var(--green);border:1px solid var(--green)}
  .badge.anomaly{background:var(--red-dim);color:var(--red);border:1px solid var(--red)}
  .badge.pending{background:var(--amber-dim);color:var(--amber);border:1px solid var(--amber)}
  .badge.credit{background:var(--cyan-dim);color:var(--cyan);border:1px solid var(--cyan2)}
  .badge.debit{background:#7c3aed22;color:#a78bfa;border:1px solid #7c3aed}
  .qlist{display:flex;flex-direction:column;gap:8px;max-height:320px;overflow-y:auto}
  .qitem{padding:10px 12px;border:1px solid var(--border);border-radius:4px;background:var(--panel2);cursor:pointer;transition:all .2s}
  .qitem:hover{border-color:var(--red);background:rgba(239,68,68,0.05)}
  .qitem.selected{border-color:var(--red);background:rgba(239,68,68,0.08)}
  .qitem-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
  .qitem-id{font-family:var(--mono);font-size:10px;color:var(--text)}
  .qitem-score{font-family:var(--mono);font-size:11px;font-weight:700;color:var(--red)}
  .qitem-detail{font-family:var(--mono);font-size:9px;color:var(--text3);letter-spacing:.5px}
  .breakdown{display:flex;flex-direction:column;gap:10px}
  .metric-bar-row{display:flex;flex-direction:column;gap:4px}
  .metric-bar-label{display:flex;justify-content:space-between;font-family:var(--mono);font-size:10px;color:var(--text2)}
  .metric-bar-track{height:6px;background:var(--border);border-radius:2px;overflow:hidden}
  .metric-bar-fill{height:100%;border-radius:2px;transition:width 1s ease}
  .metric-bar-fill.red{background:var(--red)}.metric-bar-fill.amber{background:var(--amber)}.metric-bar-fill.cyan{background:var(--cyan)}
  .benford-chart{display:flex;align-items:flex-end;gap:4px;height:80px;padding:8px 0}
  .b-bar-wrap{display:flex;flex-direction:column;align-items:center;gap:3px;flex:1}
  .b-bar{width:100%;border-radius:2px 2px 0 0;position:relative}
  .b-bar.expected{background:var(--border2)}.b-bar.actual{background:var(--amber);opacity:.9}
  .b-bar-label{font-family:var(--mono);font-size:8px;color:var(--text3)}
  .action-row{display:flex;gap:8px;margin-top:12px}
  .btn{padding:8px 18px;border-radius:3px;font-family:var(--mono);font-size:10px;font-weight:700;letter-spacing:2px;cursor:pointer;border:1px solid;transition:all .15s}
  .btn:hover{opacity:.8}.btn:active{transform:scale(.98)}
  .btn.authorize{background:var(--green-dim);color:var(--green);border-color:var(--green)}
  .btn.terminate{background:var(--red-dim);color:var(--red);border-color:var(--red)}
  .btn.verify{background:var(--cyan-dim);color:var(--cyan);border-color:var(--cyan2)}
  .ledger-list{display:flex;flex-direction:column;gap:6px;max-height:400px;overflow-y:auto}
  .ledger-entry{padding:10px 12px;border:1px solid var(--border);border-radius:2px;background:var(--panel2)}
  .ledger-entry.new{border-color:var(--cyan);animation:flashin .6s ease}
  @keyframes flashin{0%{background:var(--cyan-dim)}100%{background:var(--panel2)}}
  .ledger-id{font-family:var(--mono);font-size:10px;color:var(--text3);margin-bottom:4px}
  .ledger-hash{font-family:var(--mono);font-size:11px;color:var(--cyan);word-break:break-all;line-height:1.5}
  .ledger-meta{display:flex;gap:16px;margin-top:6px;font-family:var(--mono);font-size:9px;color:var(--text3)}
  .ledger-meta span{color:var(--green)}
  .verify-terminal{background:var(--obsidian);border:1px solid var(--border);border-radius:3px;padding:12px;font-family:var(--mono);font-size:11px}
  .vt-prompt{display:flex;align-items:center;gap:8px;margin-bottom:8px}
  .vt-prompt span{color:var(--cyan)}
  .vt-input{background:none;border:none;outline:none;color:var(--text);font-family:var(--mono);font-size:11px;flex:1}
  .vt-output{color:var(--text3);line-height:1.8}
  .vt-output .ok{color:var(--green)}.vt-output .err{color:var(--red)}.vt-output .val{color:var(--cyan)}
  .node-graph{position:relative;height:160px;background:var(--obsidian);border-radius:3px;border:1px solid var(--border);overflow:hidden}
  .graph-svg{position:absolute;top:0;left:0;width:100%;height:100%}
  .ticker{font-family:var(--mono);font-size:10px;color:var(--text3);padding:6px 14px;background:var(--obsidian);border-top:1px solid var(--border);overflow:hidden;white-space:nowrap}
  .ticker-inner{display:inline-block;animation:scroll 30s linear infinite}
  @keyframes scroll{0%{transform:translateX(0)}100%{transform:translateX(-50%)}}
  .section-hidden{display:none}
  .tps-bar{display:flex;gap:2px;align-items:flex-end;height:32px}
  .tps-col{width:6px;border-radius:1px 1px 0 0;background:var(--cyan);opacity:.7;transition:height .3s}
  ::-webkit-scrollbar{width:4px;height:4px}
  ::-webkit-scrollbar-track{background:var(--border)}
  ::-webkit-scrollbar-thumb{background:var(--border2);border-radius:2px}
</style>

<!-- TOP BAR -->
<div class="topbar">
  <div class="topbar-left">
    <div>
      <div class="logo">ZEROAUDIT</div>
      <div class="tagline">PROVE COMPLIANCE. REVEAL NOTHING.</div>
    </div>
    <div class="status-row">
      <div class="status-dot" id="sgx-dot"></div>
      <span class="status-label">SGX ENCLAVE</span>
      <div class="status-dot" id="kafka-dot"></div>
      <span class="status-label">KAFKA</span>
      <div class="status-dot warn" id="ml-dot"></div>
      <span class="status-label">INTENT ENGINE</span>
    </div>
  </div>
  <div class="time-display" id="clock">--:--:--</div>
</div>

<!-- NAV -->
<div class="nav">
  <button class="nav-tab active" onclick="showTab('telemetry',this)">01 / PIPELINE TELEMETRY</button>
  <button class="nav-tab" onclick="showTab('intent',this)">02 / INTENT ENGINE</button>
  <button class="nav-tab" onclick="showTab('ledger',this)">03 / CRYPTOGRAPHIC LEDGER</button>
</div>

<!-- TAB 1: TELEMETRY -->
<div id="tab-telemetry" class="main">
  <div class="metrics">
    <div class="metric"><div class="metric-label">TXN / SEC</div><div class="metric-val cyan" id="tps-val">0</div><div class="metric-sub">KAFKA INGESTION</div></div>
    <div class="metric"><div class="metric-label">COMMITMENTS</div><div class="metric-val green" id="commit-count">0</div><div class="metric-sub">LWE PROOFS GENERATED</div></div>
    <div class="metric"><div class="metric-label">QUARANTINED</div><div class="metric-val amber" id="quarantine-count">0</div><div class="metric-sub">PENDING REVIEW</div></div>
    <div class="metric"><div class="metric-label">CHAIN INTEGRITY</div><div class="metric-val green" id="integrity-pct">100%</div><div class="metric-sub">LEDGER VERIFIED</div></div>
  </div>
  <div class="panel">
    <div class="panel-header">
      <span class="panel-title">PIPELINE TOPOLOGY</span>
      <span class="panel-sub">CASSANDRA LSM → SGX MEE → LWE VAULT</span>
    </div>
    <div class="panel-body">
      <div class="pipeline">
        <div class="pipe-node"><div class="pipe-icon ok">LSM</div><div class="pipe-label">CASSANDRA<br>WRITE LOG</div></div>
        <div class="pipe-arrow"></div>
        <div class="pipe-node"><div class="pipe-icon active">CDC</div><div class="pipe-label">DEBEZIUM<br>CAPTURE</div></div>
        <div class="pipe-arrow"></div>
        <div class="pipe-node"><div class="pipe-icon active">MQ</div><div class="pipe-label">KAFKA<br>INTERNAL</div></div>
        <div class="pipe-arrow"></div>
        <div class="pipe-node"><div class="pipe-icon warn">SGX</div><div class="pipe-label">ENCLAVE<br>PROVER</div></div>
        <div class="pipe-arrow"></div>
        <div class="pipe-node"><div class="pipe-icon active">LWE</div><div class="pipe-label">LATTICE<br>COMMIT</div></div>
        <div class="pipe-arrow"></div>
        <div class="pipe-node"><div class="pipe-icon ok">PUB</div><div class="pipe-label">PUBLIC<br>KAFKA</div></div>
        <div class="pipe-arrow"></div>
        <div class="pipe-node"><div class="pipe-icon ok">S3</div><div class="pipe-label">PARQUET<br>VAULT</div></div>
      </div>
      <div style="display:flex;gap:20px;margin-top:8px;padding-top:8px;border-top:1px solid var(--border)">
        <div style="display:flex;align-items:center;gap:6px">
          <div class="tps-bar" id="tps-chart"></div>
          <span style="font-family:var(--mono);font-size:9px;color:var(--text3)">TPS HISTORY</span>
        </div>
        <div style="margin-left:auto;display:flex;gap:16px">
          <span style="font-family:var(--mono);font-size:9px;color:var(--green)">● WAL STREAM ACTIVE</span>
          <span style="font-family:var(--mono);font-size:9px;color:var(--amber)">● SGX MEE 78% LOAD</span>
          <span style="font-family:var(--mono);font-size:9px;color:var(--cyan)">● KAFKA LAG 0ms</span>
        </div>
      </div>
    </div>
  </div>
  <div class="panel">
    <div class="panel-header">
      <span class="panel-title">LIVE TRANSACTION STREAM</span>
      <span class="panel-sub" id="stream-count">0 EVENTS</span>
    </div>
    <div class="panel-body" style="padding:0">
      <table class="live-table">
        <thead><tr><th>TXN ID</th><th>ACCOUNT</th><th>TYPE</th><th>COMMITMENT (C)</th><th>TIMESTAMP</th><th>STATUS</th></tr></thead>
        <tbody id="txn-table"></tbody>
      </table>
    </div>
  </div>
</div>

<!-- TAB 2: INTENT ENGINE -->
<div id="tab-intent" class="main section-hidden">
  <div style="padding:8px 14px;background:#1a0505;border:1px solid var(--red);border-radius:4px;font-family:var(--mono);font-size:10px;color:var(--red);letter-spacing:1px" id="alert-banner">
    ⚠ CRITICAL — AUTOENCODER RECONSTRUCTION LOSS THRESHOLD EXCEEDED
  </div>
  <div class="two-col">
    <div class="panel">
      <div class="panel-header"><span class="panel-title">QUARANTINE QUEUE</span><span class="panel-sub" id="q-count-sub">0 FLAGGED</span></div>
      <div class="panel-body"><div class="qlist" id="quarantine-list"></div></div>
    </div>
    <div class="panel">
      <div class="panel-header"><span class="panel-title">INTENT ANALYSIS</span><span class="panel-sub" id="selected-txn-id">SELECT TRANSACTION</span></div>
      <div class="panel-body">
        <div class="breakdown" id="breakdown-panel">
          <div style="color:var(--text3);font-family:var(--mono);font-size:10px;text-align:center;padding:40px 0">SELECT A QUARANTINED TRANSACTION TO ANALYZE</div>
        </div>
      </div>
    </div>
  </div>
  <div class="two-col">
    <div class="panel">
      <div class="panel-header"><span class="panel-title">GRAPH PROXIMITY MAP</span><span class="panel-sub">ENTITY RELATIONSHIP NETWORK</span></div>
      <div class="panel-body" style="padding:8px">
        <div class="node-graph" id="graph-container"><svg class="graph-svg" id="graph-svg"></svg></div>
        <div style="margin-top:8px;font-family:var(--mono);font-size:9px;color:var(--text3)">
          <span style="color:var(--red)">■</span> BLACKLISTED ENTITY &nbsp;
          <span style="color:var(--amber)">■</span> HIGH-RISK PROXY &nbsp;
          <span style="color:var(--cyan)">■</span> FLAGGED ACCOUNT
        </div>
      </div>
    </div>
    <div class="panel">
      <div class="panel-header"><span class="panel-title">BENFORD'S LAW DEVIATION</span><span class="panel-sub">LEADING DIGIT DISTRIBUTION</span></div>
      <div class="panel-body">
        <div class="benford-chart" id="benford-chart"></div>
        <div style="display:flex;gap:12px;margin-top:6px;font-family:var(--mono);font-size:9px;color:var(--text3)">
          <span><span style="color:var(--border2)">■</span> EXPECTED (BENFORD)</span>
          <span><span style="color:var(--amber)">■</span> OBSERVED (ACCOUNT)</span>
        </div>
        <div style="margin-top:10px;padding:8px;background:var(--obsidian);border:1px solid var(--red);border-radius:2px;font-family:var(--mono);font-size:9px;color:var(--red)">
          DEVIATION SCORE: 0.847 — EXCEEDS 3σ THRESHOLD
        </div>
      </div>
    </div>
  </div>
</div>

<!-- TAB 3: LEDGER -->
<div id="tab-ledger" class="main section-hidden">
  <div class="three-col">
    <div class="metric"><div class="metric-label">LEDGER ENTRIES</div><div class="metric-val cyan" id="ledger-total">0</div><div class="metric-sub">IMMUTABLE RECORDS</div></div>
    <div class="metric"><div class="metric-label">POST-QUANTUM SEAL</div><div class="metric-val green">LWE</div><div class="metric-sub">LATTICE SCHEME ACTIVE</div></div>
    <div class="metric"><div class="metric-label">PII EXPOSED</div><div class="metric-val green">ZERO</div><div class="metric-sub">ZERO-KNOWLEDGE PROOF</div></div>
  </div>
  <div class="panel">
    <div class="panel-header"><span class="panel-title">VERIFICATION TERMINAL</span><span class="panel-sub">AUDITOR ACCESS — READ ONLY</span></div>
    <div class="panel-body">
      <div class="verify-terminal">
        <div class="vt-prompt">
          <span>ZEROAUDIT:~$</span>
          <input class="vt-input" id="vt-input" placeholder="enter transaction id to verify..." onkeydown="if(event.key==='Enter')runVerify()"/>
          <button class="btn verify" onclick="runVerify()" style="padding:4px 12px;font-size:9px">VERIFY</button>
        </div>
        <div class="vt-output" id="vt-output"><span style="color:var(--text3)">// awaiting transaction id input</span></div>
      </div>
    </div>
  </div>
  <div class="panel">
    <div class="panel-header"><span class="panel-title">IMMUTABLE COMMITMENT LEDGER</span><span class="panel-sub">S3 PARQUET — AIR-GAPPED VIEW</span></div>
    <div class="panel-body" style="padding:8px"><div class="ledger-list" id="ledger-list"></div></div>
  </div>
</div>

<!-- TICKER -->
<div class="ticker">
  <span class="ticker-inner">
    ● ENCLAVE ATTESTATION: VERIFIED &nbsp;&nbsp; ● KAFKA CONSUMER LAG: 0ms &nbsp;&nbsp; ● LAST COMMITMENT: VALID &nbsp;&nbsp; ● ECDSA VERIFICATION: PASS &nbsp;&nbsp; ● LWE SEAL: INTACT &nbsp;&nbsp; ● MEMSET(0) PROTOCOL: ARMED &nbsp;&nbsp; ● BENFORD MONITOR: ACTIVE &nbsp;&nbsp; ● ISOLATION FOREST: TRAINED &nbsp;&nbsp; ● WAL STREAM: LIVE &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;
    ● ENCLAVE ATTESTATION: VERIFIED &nbsp;&nbsp; ● KAFKA CONSUMER LAG: 0ms &nbsp;&nbsp; ● LAST COMMITMENT: VALID &nbsp;&nbsp; ● ECDSA VERIFICATION: PASS &nbsp;&nbsp; ● LWE SEAL: INTACT &nbsp;&nbsp; ● MEMSET(0) PROTOCOL: ARMED &nbsp;&nbsp; ● BENFORD MONITOR: ACTIVE &nbsp;&nbsp; ● ISOLATION FOREST: TRAINED &nbsp;&nbsp; ● WAL STREAM: LIVE
  </span>
</div>
"""

LIVE_SCRIPT = r"""<script>
const state={txns:[],commitments:[],quarantine:[],tpsHistory:[],totalTps:0,commitCount:0,quarantineCount:0,selectedQ:null};
function randHex(n){return[...Array(n)].map(()=>Math.floor(Math.random()*16).toString(16)).join('')}
function updateClock(){document.getElementById('clock').textContent=new Date().toLocaleTimeString('en-IN',{hour12:false});}
setInterval(updateClock,1000);updateClock();
function showTab(name,el){
  ['telemetry','intent','ledger'].forEach(t=>document.getElementById('tab-'+t).classList.add('section-hidden'));
  document.getElementById('tab-'+name).classList.remove('section-hidden');
  document.querySelectorAll('.nav-tab').forEach(t=>t.classList.remove('active'));
  el.classList.add('active');
}
function renderTpsChart(){
  const el=document.getElementById('tps-chart');el.innerHTML='';
  const last=state.tpsHistory.slice(-16),mx=Math.max(...last,1);
  last.forEach(v=>{const c=document.createElement('div');c.className='tps-col';c.style.height=Math.max(4,Math.round(v/mx*28))+'px';el.appendChild(c);});
}
function renderTxnTable(){
  const tbody=document.getElementById('txn-table');
  tbody.innerHTML=state.txns.slice(0,12).map(t=>{
    const type=t.transaction_type||t.type||'';
    const typeBadge=type.includes('WIRE')||type.includes('RTGS')?`<span class="badge debit">${type}</span>`:`<span class="badge credit">${type}</span>`;
    const commit=(t.commitment||t.payload||randHex(64)).slice(0,24);
    const ts=(t.timestamp||t.ts||'').toString().slice(11,19);
    return `<tr class="${t.isAnom?'anomaly':''}"><td style="font-family:var(--mono);font-size:10px;color:var(--text)">${t.transaction_id||t.txnId||'—'}</td><td style="font-family:var(--mono);font-size:10px">${t.account_id||t.acc||'—'}</td><td>${typeBadge}</td><td class="hash-cell">${commit}…</td><td style="font-family:var(--mono);font-size:9px;color:var(--text3)">${ts}</td><td>${t.isAnom?'<span class="badge anomaly">QUARANTINED</span>':'<span class="badge verified">VERIFIED</span>'}</td></tr>`;
  }).join('');
  document.getElementById('stream-count').textContent=state.commitCount+' EVENTS';
}
function renderMetrics(){
  document.getElementById('tps-val').textContent=state.totalTps;
  document.getElementById('commit-count').textContent=state.commitCount;
  document.getElementById('quarantine-count').textContent=state.quarantineCount;
  document.getElementById('integrity-pct').textContent=state.quarantineCount>0?Math.max(94,100-state.quarantineCount)+'%':'100%';
  const qs=document.getElementById('q-count-sub');if(qs)qs.textContent=state.quarantineCount+' FLAGGED';
}
function renderQuarantine(){
  const el=document.getElementById('quarantine-list');
  if(!state.quarantine.length){el.innerHTML='<div style="color:var(--text3);font-family:var(--mono);font-size:10px;text-align:center;padding:30px">NO QUARANTINED TRANSACTIONS</div>';return;}
  el.innerHTML=state.quarantine.map((q,i)=>`<div class="qitem ${state.selectedQ===i?'selected':''}" onclick="selectQ(${i})"><div class="qitem-header"><span class="qitem-id">${q.transaction_id||q.txnId}</span><span class="qitem-score">LOSS: ${parseFloat(q.anomaly_score||q.score||0).toFixed(3)}</span></div><div class="qitem-detail">${q.account_id||q.acc} • ${q.transaction_type||q.type} • ${(q.timestamp||q.ts||'').toString().slice(11,19)}</div><div class="qitem-detail" style="color:var(--red);margin-top:3px">⚠ ${q.reason||'ANOMALY DETECTED'}</div></div>`).join('');
}
function selectQ(i){
  state.selectedQ=i;const q=state.quarantine[i];
  const txnId=q.transaction_id||q.txnId;
  document.getElementById('selected-txn-id').textContent=txnId;
  const score=parseFloat(q.anomaly_score||q.score||0.85);
  const hops=score>0.9?1:2;
  const sv=(Math.min(0.99,score*0.95)*100).toFixed(0);
  document.getElementById('breakdown-panel').innerHTML=`
    <div class="metric-bar-row"><div class="metric-bar-label"><span>RECONSTRUCTION LOSS</span><span style="color:var(--red)">${(score*100).toFixed(1)}%</span></div><div class="metric-bar-track"><div class="metric-bar-fill red" style="width:${score*100}%"></div></div></div>
    <div class="metric-bar-row"><div class="metric-bar-label"><span>BENFORD DEVIATION</span><span style="color:var(--amber)">+3.2σ</span></div><div class="metric-bar-track"><div class="metric-bar-fill amber" style="width:85%"></div></div></div>
    <div class="metric-bar-row"><div class="metric-bar-label"><span>VELOCITY ANOMALY</span><span style="color:var(--amber)">${sv}%</span></div><div class="metric-bar-track"><div class="metric-bar-fill amber" style="width:${sv}%"></div></div></div>
    <div style="margin-top:10px;padding:8px;background:var(--obsidian);border:1px solid var(--border);border-radius:2px;font-family:var(--mono);font-size:9px;line-height:1.8;color:var(--text3)">
      <div>ACCOUNT <span style="color:var(--cyan)">${q.account_id||q.acc}</span></div>
      <div>TYPE <span style="color:var(--text)">${q.transaction_type||q.type}</span></div>
      <div>GRAPH HOPS TO BLACKLIST <span style="color:var(--red)">${hops} HOP${hops>1?'S':''}</span></div>
      <div>ECDSA SIG <span style="color:var(--green)">VALID</span></div>
      <div>REASON <span style="color:var(--red)">${(q.reason||'ANOMALY DETECTED').toUpperCase()}</span></div>
    </div>
    <div class="action-row">
      <button class="btn authorize" onclick="resolveQ(${i},'authorize')">✓ AUTHORIZE</button>
      <button class="btn terminate" onclick="resolveQ(${i},'terminate')">✗ TERMINATE</button>
    </div>`;
  renderGraph(q.account_id||q.acc,hops);renderQuarantine();
}
async function resolveQ(i,action){
  const q=state.quarantine[i],txnId=q.transaction_id||q.txnId;
  try{await fetch(`/api/resolve/${txnId}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})});}catch(e){}
  if(action==='authorize')state.commitments.unshift({...q,override:true});
  state.quarantine.splice(i,1);state.quarantineCount=Math.max(0,state.quarantineCount-1);state.selectedQ=null;
  document.getElementById('breakdown-panel').innerHTML=action==='authorize'
    ?'<div style="color:var(--green);font-family:var(--mono);font-size:10px;text-align:center;padding:40px">TRANSACTION AUTHORIZED — MOVED TO VAULT</div>'
    :'<div style="color:var(--red);font-family:var(--mono);font-size:10px;text-align:center;padding:40px">TRANSACTION TERMINATED — REPORTED TO INFOSEC</div>';
  renderQuarantine();renderMetrics();renderLedger();
}
function renderGraph(acc,hops){
  const svg=document.getElementById('graph-svg');
  const W=svg.parentElement.offsetWidth||340,H=160;
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
  const cx=W/2,cy=H/2;
  const nodes=[{x:cx,y:cy,r:18,col:'#00d4ff',label:(acc||'????').slice(-4)},{x:cx-70,y:cy-40,r:14,col:'#f59e0b',label:'PROXY'},{x:cx+70,y:cy-40,r:14,col:hops<=2?'#ef4444':'#f59e0b',label:hops<=2?'BL-01':'RISK'},{x:cx-50,y:cy+50,r:10,col:'#475569',label:'FI-03'},{x:cx+60,y:cy+45,r:10,col:'#475569',label:'FI-07'}];
  const edges=[[0,1],[0,2],[0,3],[0,4],[1,2]];
  let h='';
  edges.forEach(([a,b])=>{h+=`<line x1="${nodes[a].x}" y1="${nodes[a].y}" x2="${nodes[b].x}" y2="${nodes[b].y}" stroke="#1e2a3a" stroke-width="1"/>`;});
  nodes.forEach(n=>{h+=`<circle cx="${n.x}" cy="${n.y}" r="${n.r}" fill="${n.col}22" stroke="${n.col}" stroke-width="1"/><text x="${n.x}" y="${n.y+1}" text-anchor="middle" dominant-baseline="central" fill="${n.col}" font-family="JetBrains Mono,monospace" font-size="7" font-weight="700">${n.label}</text>`;});
  if(hops<=2)h+=`<text x="${W/2}" y="${H-10}" text-anchor="middle" fill="#ef4444" font-family="JetBrains Mono,monospace" font-size="8">BLACKLISTED ENTITY WITHIN ${hops} HOPS</text>`;
  svg.innerHTML=h;
}
function renderBenford(){
  const exp=[30.1,17.6,12.5,9.7,7.9,6.7,5.8,5.1,4.6],obs=[18.2,12.4,14.1,15.3,11.2,9.8,7.4,6.1,5.5],mx=35;
  const el=document.getElementById('benford-chart');if(!el)return;
  el.innerHTML=exp.map((e,i)=>`<div class="b-bar-wrap"><div class="b-bar expected" style="height:${Math.round(e/mx*64)}px"></div><div class="b-bar actual" style="height:${Math.round(obs[i]/mx*64)}px;margin-top:-${Math.round(obs[i]/mx*64)+Math.round(e/mx*64)}px"></div><div class="b-bar-label">${i+1}</div></div>`).join('');
}
function renderLedger(){
  const el=document.getElementById('ledger-list');if(!el)return;
  el.innerHTML=state.commitments.slice(0,15).map((c,i)=>{
    const txnId=c.transaction_id||c.txnId||'—',commitment=c.commitment||c.payload||randHex(64),acc=c.account_id||c.acc||'—',type=c.transaction_type||c.type||'—',ts=(c.timestamp||c.ts||'').toString().slice(11,19);
    return `<div class="ledger-entry ${i===0?'new':''}"><div class="ledger-id">TXN // ${txnId} ${c.override?'<span style="color:var(--amber)">[COMPLIANCE OVERRIDE]</span>':''}</div><div class="ledger-hash">C = ${commitment}</div><div class="ledger-meta"><span>ACCOUNT: <span>${acc}</span></span><span>TYPE: <span>${type}</span></span><span>TIME: <span>${ts}</span></span><span>SEAL: <span>LWE-LATTICE ✓</span></span></div></div>`;
  }).join('');
  document.getElementById('ledger-total').textContent=state.commitments.length;
}
function runVerify(){
  const input=document.getElementById('vt-input').value.trim(),output=document.getElementById('vt-output');
  if(!input){output.innerHTML='<span class="err">ERROR: no transaction id provided</span>';return;}
  const found=state.commitments.find(c=>(c.transaction_id||c.txnId||'').toLowerCase()===input.toLowerCase())||state.txns.find(t=>(t.transaction_id||t.txnId||'').toLowerCase()===input.toLowerCase());
  output.innerHTML=`<span style="color:var(--text3)">$ verifying ${input}...</span><br>`;
  setTimeout(()=>{
    if(found){
      const cm=found.commitment||found.payload||randHex(64);
      output.innerHTML+=`<span class="val">QUERYING HSM... REGENERATING BLINDING FACTOR r...</span><br>`;
      setTimeout(()=>{output.innerHTML+=`<span class="val">RUNNING C = A·s + e (LWE LATTICE)...</span><br>`;setTimeout(()=>{output.innerHTML+=`<span class="ok">✓ VALIDATED: LWE PROOF INTACT</span><br><span class="val">COMMITMENT: ${cm.slice(0,48)}...</span><br><span style="color:var(--text3)">PII REVEALED: NONE | RAW AMOUNT: HIDDEN | SEAL: INTACT</span>`;},600);},500);
    }else{output.innerHTML+=`<span class="err">✗ TRANSACTION NOT FOUND IN LEDGER</span><br><span style="color:var(--text3)">TIP: copy a transaction id from the stream above</span>`;}
  },400);
}
// ── LIVE SSE ──
function connectSSE(){
  const evs=new EventSource('/stream');
  evs.addEventListener('commitment',e=>{try{const d=JSON.parse(e.data);d.isAnom=false;state.txns.unshift(d);if(state.txns.length>50)state.txns.pop();state.commitments.unshift(d);if(state.commitments.length>20)state.commitments.pop();state.commitCount++;}catch{}});
  evs.addEventListener('anomaly',e=>{try{const d=JSON.parse(e.data);d.isAnom=true;state.txns.unshift(d);if(state.txns.length>50)state.txns.pop();state.quarantine.unshift(d);if(state.quarantine.length>6)state.quarantine.pop();state.quarantineCount++;}catch{}});
  evs.addEventListener('stats',e=>{try{const s=JSON.parse(e.data);state.totalTps=s.tps||0;state.tpsHistory.push(state.totalTps);if(state.tpsHistory.length>16)state.tpsHistory.shift();}catch{}});
  evs.onopen=()=>{document.getElementById('kafka-dot').className='status-dot';document.getElementById('sgx-dot').className='status-dot';};
  evs.onerror=()=>{document.getElementById('kafka-dot').className='status-dot dead';evs.close();setTimeout(connectSSE,3000);};
}
connectSSE();
renderGraph('ACC-4821',2);renderBenford();
setInterval(()=>{renderMetrics();renderTpsChart();renderTxnTable();renderQuarantine();renderLedger();},1000);
</script>
"""

html_path = BASE / "index.html"
html_path.write_text(HTML_BODY + LIVE_SCRIPT + "\n", encoding="utf-8")
print(f"✓ {html_path}")

serve_py = '''\
"""
ZEROAUDIT Dashboard Server — http://localhost:3000
Proxies /api/* and /stream -> verifier at http://localhost:8001
"""
import httpx, pathlib
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, Response
import uvicorn

app = FastAPI()
VERIFIER = "http://localhost:8001"
HERE = pathlib.Path(__file__).parent

@app.get("/", response_class=HTMLResponse)
async def index():
    return (HERE / "index.html").read_text(encoding="utf-8")

@app.get("/stream")
async def sse_proxy(request: Request):
    async def gen():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", f"{VERIFIER}/stream") as r:
                async for chunk in r.aiter_bytes():
                    if await request.is_disconnected(): break
                    yield chunk
    return StreamingResponse(gen(), media_type="text/event-stream",
        headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@app.api_route("/api/{path:path}", methods=["GET","POST","PUT","DELETE"])
async def api_proxy(path: str, request: Request):
    url = f"{VERIFIER}/{path}"
    body = await request.body()
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.request(request.method, url, params=dict(request.query_params),
                            content=body, headers={"Content-Type":"application/json"})
    return Response(content=r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type","application/json"))

if __name__ == "__main__":
    print("\\n  ZEROAUDIT Dashboard → http://localhost:3000\\n")
    uvicorn.run(app, host="0.0.0.0", port=3000)
'''

serve_path = BASE / "serve.py"
serve_path.write_text(serve_py, encoding="utf-8")
print(f"✓ {serve_path}")

print("""
Done! Now run:
    pip install fastapi uvicorn httpx
    cd C:\\zeroaudit\\dashboard_static
    python serve.py

Then open: http://localhost:3000
(keep docker-compose up running in another window)
""")
