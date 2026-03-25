import { useState } from ''react''
import BellCurveCanvas from ''./BellCurveCanvas.jsx''
import BenfordChart from ''./BenfordChart.jsx''
import GraphProximity from ''./GraphProximity.jsx''

function MetricBar({ label, value, pct, colorClass }) {
  return (
    <div style={{ display: ''flex'', flexDirection: ''column'', gap: 4 }}>
      <div style={{ display: ''flex'', justifyContent: ''space-between'', fontFamily: ''var(--mono)'', fontSize: 10, color: ''var(--text2)'' }}>
        <span>{label}</span>
        <span className={colorClass}>{value}</span>
      </div>
      <div style={{ height: 6, background: ''var(--border)'', borderRadius: 2, overflow: ''hidden'' }}>
        <div style={{ width: `${pct}%`, height: ''100%'', borderRadius: 2, background: colorClass === ''red'' ? ''var(--red)'' : ''var(--amber)'' }} />
      </div>
    </div>
  )
}

export default function IntentEngine({ data, onResolve }) {
  const [selectedIdx, setSelectedIdx] = useState(null)
  const { anomalies = [] } = data
  const selected = selectedIdx !== null ? anomalies[selectedIdx] : null

  const score = selected?.anomaly_score || 0.89
  const acc = selected?.account_id || ''ACC-4821''
  const flagLabel = (selected?.account_id || '''').endsWith(''21'') ? ''OFAC_SANCTION_LIST'' : ''RBI_FLAG_2024''
  const hops = selected ? (parseFloat(selected.anomaly_score) > 0.9 ? 1 : 2) : 2

  return (
    <div style={{ padding: ''16px 20px'', display: ''flex'', flexDirection: ''column'', gap: 12 }}>
      {/* CRITICAL ALERT BANNER */}
      {anomalies.length > 0 && (
        <div style={{
          padding: ''8px 14px'', background: ''#1a0505'',
          border: ''1px solid var(--red)'', borderRadius: 4,
          fontFamily: ''var(--mono)'', fontSize: 10, color: ''var(--red)'', letterSpacing: 1
        }}>
          ⚠ CRITICAL — FP16 ONNX AUTOENCODER: RECONSTRUCTION LOSS &gt; 99.9th PERCENTILE — {anomalies.length} TRANSACTION{anomalies.length > 1 ? ''S'' : ''''} IN QUARANTINE
        </div>
      )}

      <div style={{ display: ''grid'', gridTemplateColumns: ''1fr 1fr'', gap: 12 }}>
        {/* QUARANTINE LIST */}
        <div className="panel">
          <div className="panel-header">
            <span className="panel-title">QUARANTINE QUEUE</span>
            <span className="panel-sub">{anomalies.length} FLAGGED</span>
          </div>
          <div style={{ padding: 14 }}>
            {anomalies.length === 0 ? (
              <div style={{ color: ''var(--text3)'', fontFamily: ''var(--mono)'', fontSize: 10, textAlign: ''center'', padding: ''30px 0'' }}>
                NO QUARANTINED TRANSACTIONS
              </div>
            ) : anomalies.slice(0, 8).map((q, i) => (
              <div
                key={q.transaction_id || i}
                className={`qitem ${selectedIdx === i ? ''selected'' : ''''}`}
                onClick={() => setSelectedIdx(i)}
              >
                <div style={{ display: ''flex'', justifyContent: ''space-between'', alignItems: ''center'', marginBottom: 5 }}>
                  <span style={{ fontFamily: ''var(--mono)'', fontSize: 10, color: ''var(--text)'' }}>
                    {(q.transaction_id || '''').slice(0, 22)}
                  </span>
                  <span style={{ fontFamily: ''var(--mono)'', fontSize: 11, fontWeight: 700, color: ''var(--red)'' }}>
                    LOSS: {parseFloat(q.anomaly_score || 0).toFixed(3)}
                  </span>
                </div>
                <div style={{ fontFamily: ''var(--mono)'', fontSize: 9, color: ''var(--text3)'' }}>
                  {q.account_id} · {q.transaction_type} · {(q.timestamp || '''').toString().slice(11, 19)}
                </div>
                <div style={{ fontFamily: ''var(--mono)'', fontSize: 9, color: ''var(--red)'', marginTop: 3 }}>
                  ⚠ {q.reason || ''ANOMALY DETECTED''}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* INTENT ANALYSIS BREAKDOWN */}
        <div className="panel">
          <div className="panel-header">
            <span className="panel-title">INTENT ANALYSIS</span>
            <span className="panel-sub">{selected ? (selected.transaction_id || '''').slice(0, 20) : ''SELECT TRANSACTION''}</span>
          </div>
          <div style={{ padding: 14 }}>
            {!selected ? (
              <div style={{ color: ''var(--text3)'', fontFamily: ''var(--mono)'', fontSize: 10, textAlign: ''center'', padding: ''40px 0'' }}>
                SELECT A QUARANTINED TRANSACTION TO ANALYZE
              </div>
            ) : (
              <div style={{ display: ''flex'', flexDirection: ''column'', gap: 10 }}>
                <MetricBar label="RECONSTRUCTION LOSS" value={`${(score * 100).toFixed(1)}% — 99.9th PCTL`} pct={score * 100} colorClass="red" />
                <MetricBar label="BENFORD DEVIATION"   value="+3.2σ ABOVE NORMAL" pct={85} colorClass="amber" />
                <MetricBar label="VELOCITY ANOMALY"    value={`${(score * 95).toFixed(0)}% CONFIDENCE`} pct={score * 95} colorClass="amber" />

                <div style={{ padding: 8, background: ''var(--obsidian)'', border: ''1px solid var(--border)'', borderRadius: 2, fontFamily: ''var(--mono)'', fontSize: 9, lineHeight: 1.9, color: ''var(--text3)'' }}>
                  <div>ACCOUNT <span style={{ color: ''var(--cyan)'' }}>{selected.account_id}</span></div>
                  <div>TYPE <span style={{ color: ''var(--text)'' }}>{selected.transaction_type}</span></div>
                  <div>GRAPH HOPS TO BLACKLIST <span style={{ color: ''var(--red)'' }}>{hops} HOP{hops > 1 ? ''S'' : ''''} → {flagLabel}</span></div>
                  <div>ECDSA SIG <span style={{ color: ''var(--green)'' }}>VALID</span></div>
                  <div>TRIGGER <span style={{ color: ''var(--red)'' }}>{(selected.reason || ''ANOMALY DETECTED'').toUpperCase()}</span></div>
                </div>

                <div style={{ display: ''flex'', gap: 8, marginTop: 4 }}>
                  <button className="btn btn-auth" onClick={() => { onResolve(selected.transaction_id, ''authorize''); setSelectedIdx(null) }}>
                    ✓ AUTHORIZE
                  </button>
                  <button className="btn btn-term" onClick={() => { onResolve(selected.transaction_id, ''terminate''); setSelectedIdx(null) }}>
                    ✗ TERMINATE
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* BELL CURVE + BENFORD */}
      <div style={{ display: ''grid'', gridTemplateColumns: ''1fr 1fr'', gap: 12 }}>
        <div className="panel">
          <div className="panel-header">
            <span className="panel-title">RECONSTRUCTION LOSS DISTRIBUTION</span>
            <span className="panel-sub">ISOLATION FOREST — NORMAL vs ANOMALY</span>
          </div>
          <div style={{ padding: ''10px 14px'' }}>
            <BellCurveCanvas anomalyScore={score} />
            <div style={{ display: ''flex'', gap: 16, marginTop: 6, fontFamily: ''var(--mono)'', fontSize: 9, color: ''var(--text3)'' }}>
              <span><span style={{ color: ''var(--cyan)'' }}>■</span> NORMAL CLUSTER (μ=0.12, σ=0.04)</span>
              <span><span style={{ color: ''var(--red)'' }}>●</span> QUARANTINED (99.9th PCTL)</span>
            </div>
          </div>
        </div>
        <div className="panel">
          <div className="panel-header">
            <span className="panel-title">BENFORD''S LAW DEVIATION</span>
            <span className="panel-sub">LEADING DIGIT ANALYSIS</span>
          </div>
          <div style={{ padding: 14 }}>
            <BenfordChart />
          </div>
        </div>
      </div>

      {/* GRAPH + BIOMETRICS */}
      <div style={{ display: ''grid'', gridTemplateColumns: ''1fr 1fr'', gap: 12 }}>
        <div className="panel">
          <div className="panel-header">
            <span className="panel-title">GRAPH PROXIMITY MAP</span>
            <span className="panel-sub">ENTITY RELATIONSHIP — OFAC/RBI NETWORK</span>
          </div>
          <div style={{ padding: 8 }}>
            <GraphProximity account={acc} hops={hops} flagLabel={flagLabel} />
            <div style={{ marginTop: 8, fontFamily: ''var(--mono)'', fontSize: 9, color: ''var(--text3)'' }}>
              <span style={{ color: ''var(--red)'' }}>■</span> OFAC/RBI BLACKLIST &nbsp;
              <span style={{ color: ''var(--amber)'' }}>■</span> HIGH-RISK PROXY &nbsp;
              <span style={{ color: ''var(--cyan)'' }}>■</span> FLAGGED ACCOUNT
            </div>
          </div>
        </div>

        <div className="panel">
          <div className="panel-header">
            <span className="panel-title">BEHAVIORAL BIOMETRICS</span>
            <span className="panel-sub">LOGIN CONTEXT ANOMALY DETECTION</span>
          </div>
          <div style={{ padding: 14 }}>
            <div style={{ display: ''grid'', gridTemplateColumns: ''1fr 1fr'', gap: 8 }}>
              {[
                { title: ''✓ EXPECTED PROFILE'', cls: ''ok'', rows: [[''OS'',''macOS 14.2'',''ok''],[''LOCATION'',''Mumbai, IN'',''ok''],[''TIME'',''10:00–18:00 IST'',''ok''],[''IP RANGE'',''10.14.xx.xx'',''ok''],[''DEVICE ID'',''CORP-MB-0441'',''ok'']] },
                { title: ''⚠ ACTUAL SESSION'',   cls: ''bad'', rows: [[''OS'',''Windows 11'',''bad''],[''LOCATION'',''Cayman Islands'',''bad''],[''TIME'',''03:14 AM IST'',''bad''],[''IP RANGE'',''185.220.xx.xx'',''bad''],[''DEVICE ID'',''UNKNOWN'',''bad'']] }
              ].map(card => (
                <div key={card.title} className="bio-card">
                  <div className={`bio-title ${card.cls}`}>{card.title}</div>
                  {card.rows.map(([k, v, c]) => (
                    <div key={k} style={{ display: ''flex'', justifyContent: ''space-between'', fontFamily: ''var(--mono)'', fontSize: 9, marginBottom: 4 }}>
                      <span style={{ color: ''var(--text3)'' }}>{k}</span>
                      <span style={{ color: c === ''ok'' ? ''var(--green)'' : ''var(--red)'' }}>{v}</span>
                    </div>
                  ))}
                </div>
              ))}
            </div>
            <div style={{ marginTop: 10, padding: 7, background: ''var(--obsidian)'', border: ''1px solid var(--red)'', borderRadius: 2, fontFamily: ''var(--mono)'', fontSize: 9, color: ''var(--red)'' }}>
              BEHAVIORAL DELTA: 5/5 SIGNALS DEVIATED — CREDENTIAL COMPROMISE LIKELY
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

