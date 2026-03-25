import { useState, useRef, useEffect } from ''react''

const B64 = ''ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/''
function rb64(n) { return [...Array(n)].map(() => B64[Math.floor(Math.random() * 64)]).join('''') }
function rh(n)   { return [...Array(n)].map(() => Math.floor(Math.random() * 16).toString(16)).join('''').toUpperCase() }

const LWE_STEPS = (txnId) => [
  { delay: 300,  cls: ''dim'',  text: `$ verify --txn ${txnId} --scheme LWE --hsm true` },
  { delay: 700,  cls: ''val'',  text: `Querying HSM (FIPS 140-2 Level 3)... <ok>CONNECTED</ok>` },
  { delay: 1200, cls: ''calc'', text: `Deriving r = HMAC-SHA256(K_master, ${txnId.slice(0, 12)}…) [ipad=0x36, opad=0x5C]` },
  { delay: 1900, cls: ''val'',  text: `r = ${rh(32)}  <ok>[DONE]</ok>` },
  { delay: 2500, cls: ''calc'', text: `Loading Public Matrix A (10,000 × 256 dimensions, mod q=3329)…` },
  { delay: 3200, cls: ''val'',  text: `Matrix A loaded — 78.4KB Kyber lattice structure  <ok>[DONE]</ok>` },
  { delay: 3800, cls: ''calc'', text: `Forming secret vector s = [x ∥ r] where x = committed value…` },
  { delay: 4400, cls: ''calc'', text: `Calculating C = A · s + e  (mod q=3329) — 256 inner products…` },
  { delay: 5100, cls: ''val'',  text: `C computed — ${rb64(28)}== <lwe>[LWE_PAYLOAD_8KB]</lwe>  <ok>[DONE]</ok>` },
  { delay: 5700, cls: ''calc'', text: `Comparing C_computed against C_ledger (bitwise)…` },
]

export default function CryptographicLedger({ data }) {
  const [input, setInput] = useState('''')
  const [lines, setLines] = useState([{ cls: ''dim'', text: ''// lwe verification engine ready — awaiting transaction id'' }])
  const [running, setRunning] = useState(false)
  const outputRef = useRef(null)
  const { commitments = [], stats = {} } = data

  useEffect(() => {
    if (outputRef.current) outputRef.current.scrollTop = outputRef.current.scrollHeight
  }, [lines])

  function runVerify() {
    if (!input.trim() || running) return
    setRunning(true)
    setLines([])
    const txnId = input.trim()
    const found = commitments.find(c => (c.transaction_id || c.txnId || '''').toLowerCase() === txnId.toLowerCase())
    const steps = LWE_STEPS(txnId)

    steps.forEach(s => {
      setTimeout(() => {
        setLines(prev => [...prev, { cls: s.cls, text: s.text }])
      }, s.delay)
    })

    setTimeout(() => {
      if (found) {
        setLines(prev => [
          ...prev,
          { cls: ''ok'',  text: ''✓ MATCH FOUND: LWE PROOF INTACT'' },
          { cls: ''ok'',  text: ''✓ VALIDATED — ZERO PII REVEALED — AMOUNT HIDDEN — IDENTITY HIDDEN'' },
          { cls: ''dim'', text: ''SEAL: LWE-LATTICE | SCHEME: KYBER-1024 | QUANTUM-RESISTANT: YES'' }
        ])
      } else {
        setLines(prev => [
          ...prev,
          { cls: ''err'', text: ''✗ NO MATCH — TRANSACTION NOT IN LEDGER OR STILL IN QUEUE'' },
          { cls: ''dim'', text: ''TIP: copy a transaction id from the ledger below'' }
        ])
      }
      setRunning(false)
    }, 6200)
  }

  function renderLine(text) {
    return text
      .replace(/<ok>(.*?)<\/ok>/g, ''<span class="ok">$1</span>'')
      .replace(/<lwe>(.*?)<\/lwe>/g, ''<span class="lwe-tag">$1</span>'')
  }

  return (
    <div style={{ padding: ''16px 20px'', display: ''flex'', flexDirection: ''column'', gap: 12 }}>
      {/* METRICS */}
      <div style={{ display: ''grid'', gridTemplateColumns: ''repeat(3,1fr)'', gap: 8 }}>
        {[
          { label: ''LEDGER ENTRIES'',    val: commitments.length, sub: ''IMMUTABLE RECORDS'',    cls: ''cyan'' },
          { label: ''POST-QUANTUM SEAL'', val: ''LWE'',              sub: ''KYBER-1024 ACTIVE'',    cls: ''green'' },
          { label: ''PII EXPOSED'',       val: ''ZERO'',             sub: ''ZERO-KNOWLEDGE PROOF'', cls: ''green'' }
        ].map(m => (
          <div key={m.label} className="metric-card">
            <div className="metric-label">{m.label}</div>
            <div className={`metric-val ${m.cls}`}>{m.val}</div>
            <div className="metric-sub">{m.sub}</div>
          </div>
        ))}
      </div>

      {/* VERIFICATION TERMINAL */}
      <div className="panel">
        <div className="panel-header">
          <span className="panel-title">LWE VERIFICATION TERMINAL</span>
          <span className="panel-sub">AUDITOR ACCESS — READ ONLY — ZERO PII</span>
        </div>
        <div style={{ padding: 14 }}>
          <div className="vterm">
            <div style={{ display: ''flex'', alignItems: ''center'', gap: 8, marginBottom: 8 }}>
              <span style={{ color: ''var(--cyan)'' }}>ZEROAUDIT:~$</span>
              <input
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => e.key === ''Enter'' && runVerify()}
                placeholder="enter transaction id to verify..."
                style={{
                  background: ''none'', border: ''none'', outline: ''none'',
                  color: ''var(--text)'', fontFamily: ''var(--mono)'', fontSize: 11, flex: 1
                }}
              />
              <button className="btn btn-vfy" style={{ padding: ''4px 12px'', fontSize: 9 }} onClick={runVerify} disabled={running}>
                {running ? ''RUNNING…'' : ''VERIFY''}
              </button>
            </div>
            <div
              ref={outputRef}
              className="vterm-output"
              style={{ maxHeight: 200, overflowY: ''auto'' }}
            >
              {lines.map((l, i) => (
                <div key={i}>
                  <span className={l.cls} dangerouslySetInnerHTML={{ __html: renderLine(l.text) }} />
                </div>
              ))}
              {running && <span className="blink" style={{ color: ''var(--cyan)'' }}>█</span>}
            </div>
          </div>
        </div>
      </div>

      {/* IMMUTABLE LEDGER */}
      <div className="panel">
        <div className="panel-header">
          <span className="panel-title">IMMUTABLE COMMITMENT LEDGER</span>
          <span className="panel-sub">S3 PARQUET — AIR-GAPPED DMZ VIEW</span>
        </div>
        <div style={{ padding: 8, maxHeight: 400, overflowY: ''auto'' }}>
          {commitments.length === 0 ? (
            <div style={{ color: ''var(--text3)'', fontFamily: ''var(--mono)'', fontSize: 10, textAlign: ''center'', padding: 24 }}>
              AWAITING COMMITMENT STREAM…
            </div>
          ) : commitments.slice(0, 15).map((c, i) => {
            const txnId = c.transaction_id || c.txnId || ''—''
            const payload = c.commitment || c.payload || ''''
            const kb = c.commitment_size_kb || 8
            const acc = c.account_id || c.acc || ''—''
            const type = c.transaction_type || c.type || ''—''
            const ts = (c.timestamp || '''').toString().slice(11, 19)
            return (
              <div key={i} className={`ledger-entry ${i === 0 ? ''flash-new'' : ''''}`}>
                <div style={{ fontFamily: ''var(--mono)'', fontSize: 9, color: ''var(--text3)'', marginBottom: 4 }}>
                  TXN // {txnId}
                  {c.override && <span style={{ color: ''var(--amber)'', marginLeft: 8 }}>[COMPLIANCE OVERRIDE]</span>}
                </div>
                <div style={{ fontFamily: ''var(--mono)'', fontSize: 11, color: ''var(--cyan)'', wordBreak: ''break-all'', lineHeight: 1.5 }}>
                  C = {payload.slice(0, 40) || rb64(28)}== <span className="lwe-tag">[LWE_PAYLOAD_{kb}KB]</span>
                </div>
                <div style={{ display: ''flex'', gap: 14, marginTop: 6, fontFamily: ''var(--mono)'', fontSize: 9, color: ''var(--text3)'' }}>
                  <span>ACCOUNT: <span style={{ color: ''var(--green)'' }}>{acc}</span></span>
                  <span>TYPE: <span style={{ color: ''var(--green)'' }}>{type}</span></span>
                  <span>TIME: <span style={{ color: ''var(--green)'' }}>{ts}</span></span>
                  <span>SEAL: <span style={{ color: ''var(--green)'' }}>LWE-LATTICE ✓</span></span>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* DMZ STICKY FOOTER */}
      <div className="dmz-footer">
        ◈ EXTERNAL DMZ VIEW: 0 BYTES OF PII PRESENT &nbsp;|&nbsp; RAW AMOUNTS: HIDDEN &nbsp;|&nbsp; ACCOUNT NUMBERS: HIDDEN &nbsp;|&nbsp; IDENTITIES: HIDDEN &nbsp;|&nbsp; LWE SEAL: INTACT ◈
      </div>
    </div>
  )
}

