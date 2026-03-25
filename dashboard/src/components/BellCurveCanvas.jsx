import { useRef, useEffect } from ''react''

export default function BellCurveCanvas({ anomalyScore = 0.89 }) {
  const canvasRef = useRef(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const W = canvas.offsetWidth || 380
    const H = 130
    canvas.width = W
    canvas.height = H
    const ctx = canvas.getContext(''2d'')
    ctx.clearRect(0, 0, W, H)

    const mu = 0.12, sigma = 0.04
    function gauss(x) {
      return Math.exp(-0.5 * ((x - mu) / sigma) ** 2) / (sigma * Math.sqrt(2 * Math.PI))
    }

    const xs = [], ys = []
    for (let i = 0; i <= 200; i++) {
      const x = i / 200
      xs.push(x); ys.push(gauss(x))
    }
    const maxY = Math.max(...ys)

    function toCanvas(x, y) {
      return { cx: x * W, cy: H - 14 - (y / maxY) * (H - 30) }
    }

    // Fill under curve
    ctx.beginPath()
    const p0 = toCanvas(xs[0], ys[0])
    ctx.moveTo(p0.cx, H - 14); ctx.lineTo(p0.cx, p0.cy)
    xs.forEach((x, i) => { const p = toCanvas(x, ys[i]); ctx.lineTo(p.cx, p.cy) })
    const pL = toCanvas(xs[xs.length - 1], ys[xs.length - 1])
    ctx.lineTo(pL.cx, H - 14); ctx.closePath()
    ctx.fillStyle = ''rgba(0,212,255,0.07)''; ctx.fill()

    // Curve line
    ctx.beginPath()
    xs.forEach((x, i) => {
      const p = toCanvas(x, ys[i])
      i === 0 ? ctx.moveTo(p.cx, p.cy) : ctx.lineTo(p.cx, p.cy)
    })
    ctx.strokeStyle = ''rgba(0,212,255,0.55)''; ctx.lineWidth = 1.5; ctx.stroke()

    // X axis
    ctx.beginPath(); ctx.moveTo(0, H - 14); ctx.lineTo(W, H - 14)
    ctx.strokeStyle = ''rgba(30,42,58,1)''; ctx.lineWidth = 1; ctx.stroke()

    // Axis labels
    ctx.fillStyle = ''rgba(71,85,105,0.9)''
    ctx.font = ''9px JetBrains Mono, monospace''
    ctx.textAlign = ''center''
    ;[''0.0'', ''0.2'', ''0.4'', ''0.6'', ''0.8'', ''1.0''].forEach((l, i) => {
      ctx.fillText(l, i * (W / 5), H - 3)
    })

    // Normal label
    const normP = toCanvas(mu, gauss(mu))
    ctx.fillStyle = ''rgba(0,212,255,0.7)''
    ctx.font = ''8px JetBrains Mono, monospace''
    ctx.textAlign = ''center''
    ctx.fillText(''NORMAL'', normP.cx, normP.cy - 12)

    // Anomaly dot
    const aScore = Math.min(0.97, Math.max(0.80, anomalyScore))
    const aP = toCanvas(aScore, gauss(aScore) * 0.3 + 0.015)
    // Halo
    ctx.beginPath(); ctx.arc(aP.cx, aP.cy, 10, 0, 2 * Math.PI)
    ctx.strokeStyle = ''rgba(239,68,68,0.3)''; ctx.lineWidth = 8; ctx.stroke()
    // Dot
    ctx.beginPath(); ctx.arc(aP.cx, aP.cy, 5, 0, 2 * Math.PI)
    ctx.fillStyle = ''#ef4444''; ctx.fill()
    // Label
    ctx.fillStyle = ''#ef4444''
    ctx.font = ''8px JetBrains Mono, monospace''
    ctx.textAlign = ''center''
    ctx.fillText(''99.9th PCTL'', aP.cx, aP.cy - 18)
    ctx.fillText(''ANOMALY'', aP.cx, aP.cy - 8)
  }, [anomalyScore])

  return <canvas ref={canvasRef} style={{ width: ''100%'', height: 130, display: ''block'' }} height={130} />
}

