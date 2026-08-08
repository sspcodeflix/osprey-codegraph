import { useEffect, useState } from "react"
import { api, Hotspot, Overview as Ov } from "../api"

// categorical hues for the language bar — fixed order, dark-validated
// (dataviz six-checks vs #141417); >3 languages fold into a neutral "other"
const LANG_COLORS = ["var(--series-1)", "var(--series-3)", "var(--series-7)"]
const OTHER_COLOR = "var(--text-muted)"

const fmtK = (n: number) =>
  n >= 10000 ? `${Math.round(n / 1000)}k`
    : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : n.toLocaleString()

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="stat">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  )
}

// horizontal proportion bar: identity via legend + direct labels, 2px gaps
function LangBar({ languages, loc }: {
  languages: Record<string, number>; loc: number
}) {
  const sorted = Object.entries(languages).sort((a, b) => b[1] - a[1])
  const top = sorted.slice(0, LANG_COLORS.length)
  const rest = sorted.slice(LANG_COLORS.length)
  const parts = [
    ...top.map(([l, n], i) => ({ name: l, loc: n, color: LANG_COLORS[i] })),
    ...(rest.length > 0
      ? [{ name: "other", loc: rest.reduce((s, [, n]) => s + n, 0),
           color: OTHER_COLOR }]
      : []),
  ]
  return (
    <>
      <div className="propbar">
        {parts.map((p) => (
          <div key={p.name} className="propbar-seg"
               style={{ flexGrow: p.loc, background: p.color }}
               title={`${p.name}: ${p.loc.toLocaleString()} lines`} />
        ))}
      </div>
      <div className="legend">
        {parts.map((p) => (
          <span key={p.name} className="legend-item">
            <span className="dot" style={{ background: p.color }} />
            {p.name} <span className="muted">
              {fmtK(p.loc)} · {Math.round((p.loc / loc) * 100)}%</span>
          </span>
        ))}
      </div>
    </>
  )
}

// ranked single-hue bars — identity is carried by the row label, so one
// series color; values always direct-labeled (never color-alone)
function HBars({ rows, color, unit, onPick }: {
  rows: { key: string; label: string; sub?: string; value: number }[]
  color: string
  unit: (v: number) => string
  onPick?: (key: string) => void
}) {
  const max = Math.max(1, ...rows.map((r) => r.value))
  return (
    <div className="hbars">
      {rows.map((r) => {
        const inner = (
          <>
            <span className="hbar-label" title={r.sub}>{r.label}</span>
            <span className="hbar-track">
              <span className="hbar-fill" style={{
                width: `${Math.max(1.5, (r.value / max) * 100)}%`,
                background: color,
              }} />
            </span>
            <span className="hbar-value">{unit(r.value)}</span>
          </>
        )
        return onPick ? (
          <button key={r.key} className="hbar-row clickable"
                  title={r.sub} onClick={() => onPick(r.key)}>{inner}</button>
        ) : (
          <div key={r.key} className="hbar-row" title={r.sub}>{inner}</div>
        )
      })}
    </div>
  )
}

export function Overview({ snap, onNavigate, onPickHotspot }: {
  snap: number
  onNavigate: (tab: string) => void
  onPickHotspot: (h: Hotspot) => void
}) {
  const [data, setData] = useState<Ov | null>(null)
  const [error, setError] = useState("")

  useEffect(() => {
    api.overview(snap)
      .then((d) => { setData(d); setError("") })
      .catch((e) => setError(String(e)))
  }, [snap])

  if (error) return <div className="view"><div className="error">{error}</div></div>
  if (!data) return <div className="view"><div className="hint">Loading…</div></div>

  const symbolRows = Object.entries(data.symbols)
    .sort((a, b) => b[1] - a[1])
    .map(([kind, n]) => ({ key: kind, label: kind, value: n }))

  const entangled = new Set(data.cycles.flat()).size

  return (
    <div className="view overview">
      <div className="statgrid">
        <Stat value={data.files.toLocaleString()} label="files" />
        <Stat value={fmtK(data.loc)} label="lines of code" />
        <Stat value={data.modules.toLocaleString()} label="folders" />
        <Stat value={data.module_dependencies.toLocaleString()}
              label="dependencies between them" />
      </div>
      <div className="muted commitline">
        Analyzed at commit <code>{data.commit.slice(0, 8)}</code>
      </div>

      <div className="cardgrid">
        <div className="card">
          <h3>Written in</h3>
          <LangBar languages={data.languages} loc={data.loc} />
        </div>
        <div className="card">
          <h3>Made of</h3>
          <HBars rows={symbolRows} color="var(--series-1)" unit={fmtK} />
        </div>
      </div>

      <div className="cardgrid">
        <div className={`card finding ${data.cycles.length ? "warn" : "good"}`}>
          {data.cycles.length === 0 ? (
            <>
              <div className="bignum good">0 <span>circular dependencies</span></div>
              <p className="muted">No folder group depends on itself in a
                loop — changes flow one way.</p>
            </>
          ) : (
            <>
              <div className="bignum warn">⚠ {data.cycles.length}
                <span>circular dependency
                  group{data.cycles.length > 1 ? "s" : ""}</span></div>
              <p className="muted">{entangled} folders are entangled — none
                can change safely without the others.</p>
              <HBars color="var(--series-2)" unit={(v) => `${v} folders`}
                rows={data.cycles.slice(0, 4).map((c, i) => ({
                  key: String(i),
                  label: `${c[0]} ↔ …`,
                  sub: c.slice(0, 8).join(" → ") + (c.length > 8 ? " → …" : ""),
                  value: c.length,
                }))} />
              <button className="linkish" onClick={() => onNavigate("Health")}>
                see the evidence →</button>
            </>
          )}
        </div>
        <div className={data.entry_points === 0 ? "card" : "card finding"}>
          {data.entry_points === 0 ? (
            <>
              <div className="bignum"><span>cleanup analysis
                unavailable</span></div>
              <p className="muted">No entry points (web routes, commands,
                main scripts) detected — usually a library whose code is
                called from outside.</p>
            </>
          ) : (
            <>
              <div className="bignum">🧹 {(data.deadcode ?? 0).toLocaleString()}
                <span>pieces of likely-unused code</span></div>
              <p className="muted">Unreachable from any of the{" "}
                {data.entry_points} entry points. Candidates for deletion
                after review.</p>
              <button className="linkish" onClick={() => onNavigate("Cleanup")}>
                see the list →</button>
            </>
          )}
        </div>
      </div>

      <div className="card finding">
        <h3>🔥 Most depended-on code</h3>
        <p className="muted">Changes here ripple widest — click one to see
          its blast radius.</p>
        <HBars color="var(--series-2)" unit={(v) => `${v} callers`}
          onPick={(key) => {
            const h = data.hotspots.find((x) => String(x.symbol_id) === key)
            if (h) onPickHotspot(h)
          }}
          rows={data.hotspots.slice(0, 8).map((h) => ({
            key: String(h.symbol_id),
            label: h.name,
            sub: `${h.path}:${h.line ?? ""}`,
            value: h.inbound,
          }))} />
      </div>
    </div>
  )
}
