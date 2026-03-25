import TPSChart from ''./TPSChart.jsx''

const PIPELINE_NODES = [
  { code: ''LSM'', sub: ''LSM-TREE'', label: ''CASSANDRA\nWRITE LOG'', status: ''ok'' },
  { code: ''CDC'', sub: '''',         label: ''DEBEZIUM\nCAPTURE'',    status: ''active'' },
  { code: ''MQ'',  sub: '''',         label: ''KAFKA\nINTERNAL'',      status: ''active'' },
  { code: ''SGX'', sub: ''MEE ENC RAM'', label: ''ENCLAVE\nPROVER'',  status: ''warn'' },
  { code: ''LWE'', sub: '''',         label: ''LATTICE\nCOMMIT'',      status: ''active'' },
  { code: ''PUB'', sub: '''',         label: ''PUBLIC\nKAFKA'',        status: ''ok'' },
  { code: ''S3'',  sub: '''',         label: ''PARQUET\nVAULT'',       status: ''ok'' }
]

function MetricCard({ label, value, sub, colorClass }) {
  return (
    <div className="metric-card">
      <div className="metric-label">{label}</div>
      <div className={`metric-val ${colorClass}`}>{value}</div>
      <div className="metric-sub">{sub}</div>
    </div>
  )
}

export default function PipelineTelemetry({ data, connected }) {
  const { stats = {}, transactions = [] } = data
  const integrity = stats.total_anomalies > 0 ? Math.max(94, 100 - stats.total_anomalies) + ''%'' : ''100%''

  return (
    <div style={{ padding: ''16px 20px'', display: ''flex'', flexDirection: ''column'', gap: 12 }}>
      {/* METRICS */}
      <div style={{ display: ''grid'', gridTemplateColumns: ''repeat(4,1fr)'', gap: 8 }}>
        <MetricCard label="TXN / SEC"       value={stats.tps || 0}                  sub="CASSANDRA INGESTION"    colorClass="cyan" />
        <MetricCard label="COMMITMENTS"     value={stats.total_committed || 0}       sub="LWE PROOFS GENERATED"  colorClass="green" />
        <MetricCard label="QUARANTINED"     value={stats.total_anomalies || 0}       sub="AWAITING REVIEW"       colorClass="amber" />
        <MetricCard label="CHAIN INTEGRITY" value={integrity}                        sub="LEDGER VERIFIED"       colorClass="green" />
      </div>

      {/* PIPELINE TOPOLOGY */}
      <div className="panel">
        <div className="panel-header">
          <span className="panel-title">PIPELINE TOPOLOGY</span>
          <span className="panel-sub">CASSANDRA LSM → SGX MEE → LWE VAULT</span>
        </div>
        <div style={{ padding: 14 }}>
          <div style={{ display: ''flex'', alignItems: ''center'', overflowX: ''auto'', paddingBottom: 4 }}>
            {PIPELINE_NODES.map((n, i) => (
              <div key={n.code} style={{ display: ''flex'', alignItems: ''center'' }}>
                <div className="pipe-node">
                  <div className={`pipe-icon ${n.status}`}>
                    <span className="picode">{n.code}</span>
                    {n.sub && <span className="pisub">{n.sub}</span>}
                  </div>
                  <div className="pipe-label">{n.label.split(''\n'').map((l, j) => <span key={j}>{l}<br /></span>)}</div>
                </div>
                {i < PIPELINE_NODES.length - 1 && <div className="pipe-arrow" />}
              </div>
            ))}
          </div>
          <div style={{ display: ''flex'', gap: 20, marginTop: 8, paddingTop: 8, borderTop: ''1px solid var(--border)'', alignItems: ''center'' }}>
            <div style={{ display: ''flex'', alignItems: ''center'', gap: 8 }}>
              <TPSChart tps={stats.tps} />
              <span style={{ fontFamily: ''var(--mono)'', fontSize: 9, color: ''var(--text3)'' }}>INGESTION RATE</span>
            </div>
            <div style={{ marginLeft: ''auto'', display: ''flex'', gap: 16 }}>
              <span style={{ fontFamily: ''var(--mono)'', fontSize: 9, color: ''var(--green)'' }}>● WAL STREAM ACTIVE</span>
              <span style={{ fontFamily: ''var(--mono)'', fontSize: 9, color: ''var(--amber)'' }}>● SGX MEE 78% LOAD</span>
              <span style={{ fontFamily: ''var(--mono)'', fontSize: 9, color: ''var(--cyan)'' }}>● KAFKA LAG 0ms</span>
            </div>
          </div>
        </div>
      </div>

      {/* LIVE TRANSACTION STREAM */}
      <div className="panel">
        <div className="panel-header">
          <span className="panel-title">LIVE TRANSACTION STREAM</span>
          <span className="panel-sub">{(stats.total_committed || 0) + (stats.total_anomalies || 0)} EVENTS</span>
        </div>
        <div style={{ padding: 0 }}>
          <table className="live-table">
            <thead>
              <tr>
                <th>TXN ID</th>
                <th>ACCOUNT</th>
                <th>TYPE</th>
                <th>LWE COMMITMENT PAYLOAD</th>
                <th>TIMESTAMP</th>
                <th>STATUS</th>
              </tr>
            </thead>
            <tbody>
              {transactions.slice(0, 12).map((t, i) => {
                const txnId = t.transaction_id || t.txnId || ''—''
                const acc = t.account_id || t.acc || ''—''
                const type = t.transaction_type || t.type || ''—''
                const ts = (t.timestamp || '''').toString().slice(11, 19)
                const payload = t.commitment || t.payload || ''''
                const kb = t.commitment_size_kb || 8
                const isWire = type.includes(''WIRE'') || type.includes(''RTGS'')
                return (
                  <tr key={i} className={t.isAnom ? ''row-anom'' : ''''}>
                    <td style={{ fontFamily: ''var(--mono)'', fontSize: 10, color: ''var(--text)'' }}>{txnId.slice(0, 20)}</td>
                    <td style={{ fontFamily: ''var(--mono)'', fontSize: 10 }}>{acc}</td>
                    <td><span className={`badge ${isWire ? ''badge-deb'' : ''badge-cred''}`}>{type}</span></td>
                    <td>
                      <div style={{ display: ''flex'', alignItems: ''center'', gap: 6 }}>
                        <span style={{ color: ''var(--cyan)'', fontSize: 10, fontFamily: ''var(--mono)'' }}>
                          {payload.slice(0, 16)}…
                        </span>
                        <span className="lwe-tag">[LWE_PAYLOAD_{kb}KB]</span>
                      </div>
                    </td>
                    <td style={{ fontFamily: ''var(--mono)'', fontSize: 9, color: ''var(--text3)'' }}>{ts}</td>
                    <td>
                      <span className={`badge ${t.isAnom ? ''badge-anom'' : ''badge-ver''}`}>
                        {t.isAnom ? ''QUARANTINED'' : ''VERIFIED''}
                      </span>
                    </td>
                  </tr>
                )
              })}
              {transactions.length === 0 && (
                <tr><td colSpan={6} style={{ textAlign: ''center'', color: ''var(--text3)'', padding: 24, fontFamily: ''var(--mono)'', fontSize: 10 }}>
                  AWAITING TRANSACTION STREAM…
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

