import { useRef, useEffect } from ''react''

export default function GraphProximity({ account = ''ACC-4821'', hops = 2, flagLabel = ''OFAC_SANCTION_LIST'' }) {
  const svgRef = useRef(null)

  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return
    const W = svg.parentElement?.offsetWidth || 340
    const H = 160
    svg.setAttribute(''viewBox'', `0 0 ${W} ${H}`)

    const cx = W / 2, cy = H / 2 - 10
    const nodes = [
      { x: cx,       y: cy,      r: 18, col: ''#00d4ff'', label: account.slice(-4), sub: '''' },
      { x: cx - 72,  y: cy - 38, r: 16, col: ''#f59e0b'', label: ''PROXY'',  sub: ''HIGH-RISK'' },
      { x: cx + 72,  y: cy - 38, r: hops <= 2 ? 18 : 14, col: hops <= 2 ? ''#ef4444'' : ''#f59e0b'', label: hops <= 2 ? ''OFAC'' : ''RISK'', sub: flagLabel.slice(0, 10) },
      { x: cx - 55,  y: cy + 50, r: 10, col: ''#475569'', label: ''FI-03'',  sub: '''' },
      { x: cx + 58,  y: cy + 48, r: 10, col: ''#475569'', label: ''FI-07'',  sub: '''' }
    ]
    const edges = [[0,1],[0,2],[0,3],[0,4],[1,2]]

    let html = ''''
    edges.forEach(([a, b]) => {
      html += `<line x1="${nodes[a].x}" y1="${nodes[a].y}" x2="${nodes[b].x}" y2="${nodes[b].y}" stroke="#1e2a3a" stroke-width="1"/>`
    })
    nodes.forEach(n => {
      html += `<circle cx="${n.x}" cy="${n.y}" r="${n.r}" fill="${n.col}22" stroke="${n.col}" stroke-width="1"/>`
      html += `<text x="${n.x}" y="${n.y}" text-anchor="middle" dominant-baseline="central" fill="${n.col}" font-family="JetBrains Mono,monospace" font-size="7" font-weight="700">${n.label}</text>`
      if (n.sub) html += `<text x="${n.x}" y="${n.y + n.r + 10}" text-anchor="middle" fill="${n.col}" font-family="JetBrains Mono,monospace" font-size="6" opacity="0.8">${n.sub}</text>`
    })
    if (hops <= 2) {
      html += `<text x="${W / 2}" y="${H - 8}" text-anchor="middle" fill="#ef4444" font-family="JetBrains Mono,monospace" font-size="8">${flagLabel} — ${hops} HOP${hops > 1 ? ''S'' : ''''} AWAY</text>`
    }
    svg.innerHTML = html
  }, [account, hops, flagLabel])

  return (
    <div className="ngraph">
      <svg ref={svgRef} style={{ position: ''absolute'', top: 0, left: 0, width: ''100%'', height: ''100%'' }} />
    </div>
  )
}

