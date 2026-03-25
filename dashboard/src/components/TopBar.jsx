import { useState, useEffect } from ''react''

function StatusDot({ status }) {
  const col = status === ''ok'' ? ''var(--green)'' : status === ''warn'' ? ''var(--amber)'' : ''var(--red)''
  return (
    <span style={{
      width: 6, height: 6, borderRadius: ''50%'',
      background: col, boxShadow: `0 0 6px ${col}`,
      display: ''inline-block''
    }} className="pulse" />
  )
}

export default function TopBar({ connected, stats, pipelineStatus }) {
  const [clock, setClock] = useState('''')

  useEffect(() => {
    const t = setInterval(() => {
      setClock(new Date().toLocaleTimeString(''en-IN'', { hour12: false }))
    }, 1000)
    setClock(new Date().toLocaleTimeString(''en-IN'', { hour12: false }))
    return () => clearInterval(t)
  }, [])

  const services = [
    { label: ''SGX ENCLAVE'', status: connected ? ''ok'' : ''dead'' },
    { label: ''CASSANDRA'',   status: connected ? ''ok'' : ''dead'' },
    { label: ''INTENT ENGINE'', status: stats?.total_anomalies > 0 ? ''warn'' : ''ok'' },
    { label: ''LWE VAULT'',   status: connected ? ''ok'' : ''dead'' }
  ]

  return (
    <div style={{
      display: ''flex'', alignItems: ''center'', justifyContent: ''space-between'',
      padding: ''10px 20px'', background: ''var(--slate)'',
      borderBottom: ''1px solid var(--border)'', position: ''sticky'', top: 0, zIndex: 100
    }}>
      <div style={{ display: ''flex'', alignItems: ''center'', gap: 20 }}>
        <div>
          <div style={{ fontFamily: ''var(--mono)'', fontWeight: 700, fontSize: 16, color: ''var(--cyan)'', letterSpacing: 3 }}>
            ZEROAUDIT
          </div>
          <div style={{ fontFamily: ''var(--mono)'', fontSize: 9, color: ''var(--text3)'', letterSpacing: 2 }}>
            PROVE COMPLIANCE. REVEAL NOTHING.
          </div>
        </div>
        <div style={{ display: ''flex'', gap: 12, alignItems: ''center'' }}>
          {services.map(s => (
            <div key={s.label} style={{ display: ''flex'', alignItems: ''center'', gap: 5 }}>
              <StatusDot status={s.status} />
              <span style={{ fontFamily: ''var(--mono)'', fontSize: 9, color: ''var(--text2)'', letterSpacing: 1 }}>
                {s.label}
              </span>
            </div>
          ))}
        </div>
      </div>
      <div style={{ display: ''flex'', alignItems: ''center'', gap: 20 }}>
        {!connected && (
          <span style={{ fontFamily: ''var(--mono)'', fontSize: 9, color: ''var(--amber)'', letterSpacing: 1 }}>
            ⚠ RECONNECTING...
          </span>
        )}
        <div style={{ fontFamily: ''var(--mono)'', fontSize: 11, color: ''var(--cyan)'', letterSpacing: 1 }}>
          {clock}
        </div>
      </div>
    </div>
  )
}

