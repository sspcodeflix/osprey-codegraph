import { useEffect, useMemo, useState } from "react"
import { api, Hotspot, Overview as Ov } from "../api"
import { FlameIcon } from "../icons"

// categorical hues for the language bar - fixed order, dark-validated
// (dataviz six-checks vs #1a2038); >3 languages fold into a neutral "other"
const LANG_COLORS = ["var(--series-1)", "var(--series-3)", "var(--series-7)"]
const OTHER_COLOR = "var(--text-muted)"

const fmtK = (n: number) =>
  n >= 10000 ? `${Math.round(n / 1000)}k`
    : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : n.toLocaleString()

// delta vs previous snapshot. Direction is colored by MEANING, not by sign:
// growth in dead code is bad, shrinkage is good; file count is just news.
function Delta({ cur, prev, badIsUp }: {
  cur: number; prev: number | undefined | null; badIsUp?: boolean
}) {
  if (prev === undefined || prev === null || prev === cur) return null
  const pct = prev === 0 ? null : ((cur - prev) / prev) * 100
  const up = cur > prev
  const cls = badIsUp === undefined ? "flat" : up === badIsUp ? "bad" : "good"
  const text = pct === null || Math.abs(pct) >= 100
    ? `${up ? "+" : ""}${(cur - prev).toLocaleString()}`
    : `${Math.abs(pct) < 0.1 ? "<0.1" : Math.abs(pct).toFixed(1)}%`
  return <span className={`delta ${cls}`}>{up ? "↑" : "↓"} {text}</span>
}

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="stat">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  )
}

function langParts(languages: Record<string, number>) {
  const sorted = Object.entries(languages).sort((a, b) => b[1] - a[1])
  const top = sorted.slice(0, LANG_COLORS.length)
  const rest = sorted.slice(LANG_COLORS.length)
  return [
    ...top.map(([l, n], i) => ({ name: l, loc: n, color: LANG_COLORS[i] })),
    ...(rest.length > 0
      ? [{ name: "other", loc: rest.reduce((s, [, n]) => s + n, 0),
           color: OTHER_COLOR }]
      : []),
  ]
}

export function Overview({ snap, prevSnap, onNavigate, onPickHotspot }: {
  snap: number
  prevSnap?: number | null
  onNavigate: (tab: string) => void
  onPickHotspot: (h: Hotspot) => void
}) {
  const [data, setData] = useState<Ov | null>(null)
  const [prev, setPrev] = useState<Ov | null>(null)
  const [query, setQuery] = useState("")
  const [error, setError] = useState("")

  useEffect(() => {
    api.overview(snap)
      .then((d) => { setData(d); setError("") })
      .catch((e) => setError(String(e)))
    setPrev(null)
    if (prevSnap != null) {
      api.overview(prevSnap).then(setPrev).catch(() => setPrev(null))
    }
  }, [snap, prevSnap])

  const hotspots = useMemo(() => {
    if (!data) return []
    const q = query.trim().toLowerCase()
    return data.hotspots.filter((h) =>
      q === "" || h.name.toLowerCase().includes(q)
        || h.path.toLowerCase().includes(q))
  }, [data, query])

  if (error) return <div className="view"><div className="error">{error}</div></div>
  if (!data) return <div className="view"><div className="hint">Loading…</div></div>

  const parts = langParts(data.languages)
  const totalSymbols = Object.values(data.symbols).reduce((s, n) => s + n, 0)
  const prevSymbols = prev
    ? Object.values(prev.symbols).reduce((s, n) => s + n, 0) : null
  const entangled = new Set(data.cycles.flat()).size
  const maxIn = Math.max(1, ...hotspots.map((h) => h.inbound))

  return (
    <div className="view overview">
      <div className="ovhead">
        <h2>Overview</h2>
        <span className="pill" title="every number on this page comes from
          this exact commit">
          analyzed at <code>{data.commit.slice(0, 8)}</code></span>
      </div>

      <div className="ovgrid">
        <div className="ovmain">
          <div className="statgrid">
            <Stat value={data.files.toLocaleString()} label="files" />
            <Stat value={data.modules.toLocaleString()} label="folders" />
            <Stat value={data.module_dependencies.toLocaleString()}
                  label="dependencies" />
            <Stat value={data.entry_points.toLocaleString()}
                  label="entry points" />
          </div>

          <div className="card">
            <div className="card-head">
              <div className="head-title">
                <span className="head-ico"><FlameIcon /></span>
                <h3>Most depended-on code</h3>
              </div>
              <input className="tablesearch" placeholder="Filter symbols"
                     value={query} onChange={(e) => setQuery(e.target.value)} />
            </div>
            <p className="muted">Changes here ripple widest. Click a row to
              see its blast radius.</p>
            <ol className="ranklist">
              {hotspots.slice(0, 8).map((h, i) => (
                <li key={h.symbol_id} className="rankrow"
                    onClick={() => onPickHotspot(h)}>
                  <span className={`rank ${i === 0 ? "top" : ""}`}>{i + 1}</span>
                  <div className="rankmain">
                    <div className="rankname">{h.name}</div>
                    <div className="rankpath">{h.path}{h.line != null
                      ? `:${h.line}` : ""}</div>
                  </div>
                  <span className="kindchip">{h.kind}</span>
                  <div className="rankmeter">
                    <div className="rankbar">
                      <div className="rankfill" style={{
                        width: `${Math.max(3, (h.inbound / maxIn) * 100)}%`,
                      }} />
                    </div>
                    <span className="rankcount">{h.inbound.toLocaleString()}
                      <span className="rankunit"> callers</span></span>
                  </div>
                </li>
              ))}
            </ol>
            <div className="card-foot muted">
              Top {Math.min(8, hotspots.length)} of {data.hotspots.length} by
              caller count{query && ` matching “${query}”`}
            </div>
          </div>
        </div>

        <div className="ovside">
          <div className="card">
            <div className="card-head"><h3>Highlights</h3></div>
            <div className="muted small">Lines of code</div>
            <div className="heronum">{fmtK(data.loc)}
              <Delta cur={data.loc} prev={prev?.loc} /></div>
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
                    {Math.round((p.loc / data.loc) * 100)}%</span>
                </span>
              ))}
            </div>
            <div className="hilist">
              <div className="hirow">
                <span className="hi-ico">∑</span>
                <span className="hi-label">Symbols</span>
                <span className="hi-value">{fmtK(totalSymbols)}</span>
                <Delta cur={totalSymbols} prev={prevSymbols} />
              </div>
              <button className="hirow" onClick={() => onNavigate("Health")}>
                <span className="hi-ico">⟳</span>
                <span className="hi-label">Circular groups
                  {entangled > 0 && <span className="muted">
                    {" "}· {entangled} folders</span>}</span>
                <span className="hi-value">{data.cycles.length}</span>
                <Delta cur={data.cycles.length} prev={prev?.cycles.length}
                       badIsUp />
              </button>
              <button className="hirow" onClick={() => onNavigate("Cleanup")}>
                <span className="hi-ico">🧹</span>
                <span className="hi-label">Likely-unused code</span>
                <span className="hi-value">
                  {data.entry_points === 0 ? "n/a"
                    : (data.deadcode ?? 0).toLocaleString()}</span>
                {data.entry_points > 0 &&
                  <Delta cur={data.deadcode ?? 0} prev={prev?.deadcode} badIsUp />}
              </button>
            </div>
            {prevSnap == null && (
              <div className="muted small">Deltas appear once a second
                version of this repo is analyzed.</div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
