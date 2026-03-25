import { useState, useEffect } from ''react''
import { LineChart, Line, ResponsiveContainer, Tooltip } from ''recharts''

export default function TPSChart({ tps }) {
  const [history, setHistory] = useState(Array(20).fill({ v: 0 }))

  useEffect(() => {
    setHistory(prev => {
      const next = [...prev.slice(-19), { v: tps || 0 }]
      return next
    })
  }, [tps])

  return (
    <div style={{ height: 32, width: 120 }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={history}>
          <Line type="monotone" dataKey="v" stroke="var(--cyan)" strokeWidth={1.5} dot={false} isAnimationActive={false} />
          <Tooltip
            contentStyle={{ background: ''var(--panel2)'', border: ''1px solid var(--border)'', fontSize: 9, fontFamily: ''var(--mono)'' }}
            labelStyle={{ display: ''none'' }}
            formatter={v => [`${v} TPS`]}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

