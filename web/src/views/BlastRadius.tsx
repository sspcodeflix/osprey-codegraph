import Graph from "graphology"
import { useEffect, useState } from "react"
import { api, cssVar, EDGE_KIND_VAR, Hotspot, Symbol as Sym } from "../api"
import { GraphCanvas } from "../GraphCanvas"

export function BlastRadius({ snap, preset }: {
  snap: number
  preset?: Hotspot | null
}) {
  const [q, setQ] = useState("")
  const [results, setResults] = useState<Sym[]>([])
  const [target, setTarget] = useState<Sym | null>(
    preset ? { id: preset.symbol_id, name: preset.name, kind: preset.kind,
               path: preset.path, line: preset.line, is_external: false }
    : null)
  const [starters, setStarters] = useState<Hotspot[]>([])
  const [depth, setDepth] = useState(3)
  const [graph, setGraph] = useState<Graph | null>(null)
  const [summary, setSummary] = useState("")
  const [error, setError] = useState("")

  useEffect(() => {
    if (target) return
    api.hotspots(snap).then(setStarters).catch(() => setStarters([]))
  }, [snap, target])

  useEffect(() => {
    if (q.length < 2) { setResults([]); return }
    const t = setTimeout(() => {
      api.symbols(snap, q)
        .then((rs) => setResults(rs.filter((r) => !r.is_external).slice(0, 30)))
        .catch((e) => setError(String(e)))
    }, 250)
    return () => clearTimeout(t)
  }, [q, snap])

  useEffect(() => {
    if (!target) return
    Promise.all([
      api.impact(snap, target.id, depth),
      api.subgraph(snap, target.id, Math.min(depth, 3)),
    ]).then(([imp, sub]) => {
      const g = new Graph()
      // depth = magnitude -> sequential ramp, darkest nearest the root
      const ring = [cssVar("--series-2"), cssVar("--seq-6"),
                    cssVar("--seq-4"), cssVar("--seq-3"), cssVar("--seq-2")]
      g.addNode(String(target.id), {
        label: target.name, x: 0, y: 0, size: 14, color: ring[0],
      })
      const byDepth = new Map<number, typeof imp.impacted>()
      for (const n of imp.impacted) {
        if (!byDepth.has(n.depth)) byDepth.set(n.depth, [])
        byDepth.get(n.depth)!.push(n)
      }
      for (const [d, nodes] of byDepth) {
        nodes.forEach((n, i) => {
          const angle = (2 * Math.PI * i) / nodes.length + d * 0.35
          g.addNode(String(n.id), {
            label: n.name,
            x: d * Math.cos(angle), y: d * Math.sin(angle),
            size: Math.max(4, 11 - 2.5 * d),
            color: ring[Math.min(d, ring.length - 1)],
          })
        })
      }
      const present = new Set(g.nodes())
      for (const e of sub.edges) {
        const s = String(e.src_id), t2 = String(e.dst_id)
        if (present.has(s) && present.has(t2) && !g.hasEdge(s, t2)) {
          g.addEdge(s, t2, {
            size: 0.8,
            color: cssVar(EDGE_KIND_VAR[e.kind] ?? "--series-1") + "55",
          })
        }
      }
      setGraph(g)
      setSummary(`${imp.count} symbols affected within ${depth} hops`
        + (imp.truncated ? " (truncated)" : ""))
      setError("")
    }).catch((e) => setError(String(e)))
  }, [target, depth, snap])

  return (
    <div className="view">
      <p className="explain">Pick a piece of code and see everything that
        would be affected if it changed. The orange dot is your pick; each
        ring outward is one more step removed — inner rings break first.</p>
      <div className="controls searchbox">
        <input placeholder="Search a function, class, or method…" value={q}
               onChange={(e) => setQ(e.target.value)} style={{ width: 320 }} />
        <label>Depth</label>
        <select value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
          {[1, 2, 3, 4, 5].map((d) => <option key={d}>{d}</option>)}
        </select>
        {target && <span className="pill">{target.name} · {target.path}</span>}
        {summary && <span className="pill">{summary}</span>}
      </div>
      {results.length > 0 && !target?.name.includes(q) && (
        <div className="scroll" style={{ maxHeight: 180, flex: "none" }}>
          <table className="data"><tbody>
            {results.map((r) => (
              <tr key={r.id} style={{ cursor: "pointer" }}
                  onClick={() => { setTarget(r); setResults([]) }}>
                <td>{r.name}</td>
                <td className="muted">{r.kind}</td>
                <td className="muted">{r.path}:{r.line}</td>
              </tr>
            ))}
          </tbody></table>
        </div>
      )}
      {error && <div className="error">{error}</div>}
      {target
        ? <GraphCanvas graph={graph} />
        : (
          <div className="hint">
            <p>Not sure where to start? These are the most depended-on
              functions in this codebase:</p>
            <div className="chips">
              {starters.map((h) => (
                <button key={h.symbol_id} className="chip"
                        title={`${h.path}:${h.line}`}
                        onClick={() => setTarget({
                          id: h.symbol_id, name: h.name, kind: h.kind,
                          path: h.path, line: h.line, is_external: false })}>
                  {h.name} <span className="muted">· {h.inbound} callers</span>
                </button>
              ))}
            </div>
          </div>
        )}
    </div>
  )
}
