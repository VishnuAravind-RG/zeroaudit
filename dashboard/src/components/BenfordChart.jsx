export default function BenfordChart({ data }) {
  const expected = [30.1, 17.6, 12.5, 9.7, 7.9, 6.7, 5.8, 5.1, 4.6]
  const observed = data?.observed || [18.2, 12.4, 14.1, 15.3, 11.2, 9.8, 7.4, 6.1, 5.5]
  const maxV = 35

  return (
    <div>
      <div style={{ display: ''flex'', alignItems: ''flex-end'', gap: 3, height: 72, padding: ''4px 0'' }}>
        {expected.map((e, i) => (
          <div key={i} style={{ display: ''flex'', flexDirection: ''column'', alignItems: ''center'', gap: 2, flex: 1 }}>
            <div style={{ width: ''100%'', position: ''relative'', height: Math.round(e / maxV * 60) }}>
              <div style={{
                width: ''100%'', height: Math.round(e / maxV * 60),
                background: ''var(--border2)'', borderRadius: ''1px 1px 0 0'', position: ''absolute'', bottom: 0
              }} />
              <div style={{
                width: ''100%'', height: Math.round(observed[i] / maxV * 60),
                background: ''var(--amber)'', opacity: 0.85,
                borderRadius: ''1px 1px 0 0'', position: ''absolute'', bottom: 0
              }} />
            </div>
            <span style={{ fontFamily: ''var(--mono)'', fontSize: 8, color: ''var(--text3)'' }}>{i + 1}</span>
          </div>
        ))}
      </div>
      <div style={{ display: ''flex'', gap: 12, marginTop: 6, fontFamily: ''var(--mono)'', fontSize: 9, color: ''var(--text3)'' }}>
        <span><span style={{ color: ''var(--border2)'' }}>■</span> EXPECTED (BENFORD)</span>
        <span><span style={{ color: ''var(--amber)'' }}>■</span> OBSERVED</span>
      </div>
      <div style={{
        marginTop: 8, padding: ''7px'', background: ''var(--obsidian)'',
        border: ''1px solid var(--red)'', borderRadius: 2,
        fontFamily: ''var(--mono)'', fontSize: 9, color: ''var(--red)''
      }}>
        DEVIATION SCORE: {data?.deviation_score?.toFixed(3) || ''0.847''} — EXCEEDS 3σ THRESHOLD
      </div>
    </div>
  )
}

