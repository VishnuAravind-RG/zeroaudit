export default function Ticker() {
  const msg = ''● CASSANDRA LSM WRITE ACTIVE ● SGX MEE ENCRYPTED RAM LOCKED ● LWE COMMITMENT SIZE: 8KB ● KAFKA CONSUMER LAG: 0ms ● ECDSA VERIFY: PASS ● OFAC WATCHLIST: SYNCED ● RBI FLAG LIST 2024: LOADED ● BENFORD MONITOR: ACTIVE ● FP16 ONNX MODEL: LOADED ● BEHAVIORAL BIOMETRICS: ARMED ● MEMSET(0) PROTOCOL: ARMED ● CHAIN INTEGRITY: 100%''
  const doubled = msg + ''      '' + msg

  return (
    <div style={{
      fontFamily: ''var(--mono)'', fontSize: 10, color: ''var(--text3)'',
      padding: ''5px 0'', background: ''var(--obsidian)'',
      borderTop: ''1px solid var(--border)'', overflow: ''hidden'', whiteSpace: ''nowrap''
    }}>
      <span className="ticker-inner">{doubled}</span>
    </div>
  )
}

