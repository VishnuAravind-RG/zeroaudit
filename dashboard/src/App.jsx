import { useState, useEffect, useRef, useCallback } from ''react''
import TopBar from ''./components/TopBar.jsx''
import Ticker from ''./components/Ticker.jsx''
import PipelineTelemetry from ''./components/PipelineTelemetry.jsx''
import IntentEngine from ''./components/IntentEngine.jsx''
import CryptographicLedger from ''./components/CryptographicLedger.jsx''

const API = ''/api''

export default function App() {
  const [activeTab, setActiveTab] = useState(''telemetry'')
  const [sseData, setSseData] = useState({
    transactions: [],
    anomalies: [],
    commitments: [],
    stats: { total_committed: 0, total_anomalies: 0, tps: 0, chain_integrity: 100 },
    pipeline: [],
    alerts: []
  })
  const [pipelineStatus, setPipelineStatus] = useState([])
  const [connected, setConnected] = useState(false)
  const evsRef = useRef(null)
  const txnBufferRef = useRef([])
  const anomBufferRef = useRef([])
  const commitBufferRef = useRef([])

  // SSE connection
  useEffect(() => {
    function connect() {
      const evs = new EventSource(''/stream'')
      evsRef.current = evs

      evs.addEventListener(''commitment'', (e) => {
        try {
          const d = JSON.parse(e.data)
          commitBufferRef.current.unshift(d)
          if (commitBufferRef.current.length > 50) commitBufferRef.current.pop()
          txnBufferRef.current.unshift({ ...d, isAnom: false })
          if (txnBufferRef.current.length > 60) txnBufferRef.current.pop()
        } catch {}
      })

      evs.addEventListener(''anomaly'', (e) => {
        try {
          const d = JSON.parse(e.data)
          anomBufferRef.current.unshift(d)
          if (anomBufferRef.current.length > 20) anomBufferRef.current.pop()
          txnBufferRef.current.unshift({ ...d, isAnom: true })
          if (txnBufferRef.current.length > 60) txnBufferRef.current.pop()
        } catch {}
      })

      evs.addEventListener(''stats'', (e) => {
        try {
          const stats = JSON.parse(e.data)
          setSseData(prev => ({
            ...prev,
            stats,
            transactions: [...txnBufferRef.current],
            anomalies: [...anomBufferRef.current],
            commitments: [...commitBufferRef.current]
          }))
        } catch {}
      })

      evs.onopen = () => setConnected(true)
      evs.onerror = () => {
        setConnected(false)
        evs.close()
        setTimeout(connect, 3000)
      }
    }
    connect()
    return () => evsRef.current?.close()
  }, [])

  // Poll pipeline status
  useEffect(() => {
    async function fetchPipeline() {
      try {
        const r = await fetch(`${API}/sidebar/pipeline`)
        if (r.ok) {
          const d = await r.json()
          setPipelineStatus(d)
        }
      } catch {}
    }
    fetchPipeline()
    const t = setInterval(fetchPipeline, 5000)
    return () => clearInterval(t)
  }, [])

  const resolveAnomaly = useCallback(async (txnId, action) => {
    try {
      await fetch(`${API}/resolve/${txnId}`, {
        method: ''POST'',
        headers: { ''Content-Type'': ''application/json'' },
        body: JSON.stringify({ action })
      })
      anomBufferRef.current = anomBufferRef.current.filter(a => a.transaction_id !== txnId)
      setSseData(prev => ({
        ...prev,
        anomalies: prev.anomalies.filter(a => a.transaction_id !== txnId)
      }))
    } catch {}
  }, [])

  const tabs = [
    { id: ''telemetry'', label: ''01 / PIPELINE TELEMETRY'' },
    { id: ''intent'',    label: ''02 / INTENT ENGINE'' },
    { id: ''ledger'',    label: ''03 / CRYPTOGRAPHIC LEDGER'' }
  ]

  return (
    <div style={{ display: ''flex'', flexDirection: ''column'', minHeight: ''100vh'', background: ''var(--obsidian)'' }}>
      <TopBar connected={connected} stats={sseData.stats} pipelineStatus={pipelineStatus} />

      {/* NAV TABS */}
      <div style={{ display: ''flex'', gap: 2, padding: ''0 20px'', background: ''var(--slate)'', borderBottom: ''1px solid var(--border)'' }}>
        {tabs.map(t => (
          <button
            key={t.id}
            onClick={() => setActiveTab(t.id)}
            style={{
              padding: ''10px 20px'',
              fontFamily: ''var(--mono)'',
              fontSize: 11,
              letterSpacing: ''1.5px'',
              color: activeTab === t.id ? ''var(--cyan)'' : ''var(--text3)'',
              cursor: ''pointer'',
              background: ''none'',
              border: ''none'',
              borderBottom: activeTab === t.id ? ''2px solid var(--cyan)'' : ''2px solid transparent'',
              transition: ''all .2s''
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* CONTENT */}
      <div style={{ flex: 1, overflow: ''auto'' }}>
        {activeTab === ''telemetry'' && <PipelineTelemetry data={sseData} connected={connected} />}
        {activeTab === ''intent''    && <IntentEngine data={sseData} onResolve={resolveAnomaly} />}
        {activeTab === ''ledger''    && <CryptographicLedger data={sseData} />}
      </div>

      <Ticker />
    </div>
  )
}

