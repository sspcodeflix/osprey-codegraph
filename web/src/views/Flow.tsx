import { useEffect, useState } from "react"
import { api, Hotspot, Sequence, Symbol as Sym } from "../api"
import { Mermaid } from "../Mermaid"

export function Flow({ snap, preset, onPick }: {
  snap: number
  preset?: { id: number; name: string; path: string | null;
             line: number | null } | null
  onPick?: (s: Sym) => void
}) {
  const [q, setQ] = useState("")
  const [results, setResults] = useState<Sym[]>([])
  const [target, setTarget] = useState<Sym | null>(
    preset ? { id: preset.id, name: preset.name, kind: "function",
               path: preset.path, line: preset.line, is_external: false }
    : null)
  const [starters, setStarters] = useState<Hotspot[]>([])
  const [depth] = useState(3)   // fixed to keep the lens simple
  const [seq, setSeq] = useState<Sequence | null>(null)
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
    api.sequence(snap, target.id, depth)
      .then((s) => { setSeq(s); setError("") })
      .catch((e) => { setSeq(null); setError(String(e)) })
  }, [target, depth, snap])

  const pick = (id: number, name: string, path: string, line: number | null) => {
    const s: Sym = { id, name, kind: "function", path, line,
                     is_external: false }
    setTarget(s)
    onPick?.(s)
  }

  return (
    <div className="view">
      <p className="explain">What this function calls, in source order. A
        static view from the graph, not a runtime trace.</p>
      <div className="controls searchbox">
        <input placeholder="Pick a function to trace…" value={q}
               onChange={(e) => setQ(e.target.value)} style={{ width: 320 }} />
        {target && <span className="pill">{target.name}</span>}
        {seq?.truncated && <span className="pill">longest paths truncated</span>}
      </div>
      {results.length > 0 && (
        <div className="scroll" style={{ maxHeight: 180, flex: "none" }}>
          <table className="data"><tbody>
            {results.map((r) => (
              <tr key={r.id} style={{ cursor: "pointer" }}
                  onClick={() => { pick(r.id, r.name, r.path ?? "", r.line); setResults([]); setQ("") }}>
                <td>{r.name}</td>
                <td className="muted">{r.kind}</td>
                <td className="muted">{r.path}:{r.line}</td>
              </tr>
            ))}
          </tbody></table>
        </div>
      )}
      {error && <div className="error">{error}</div>}
      {!target && (
        <div className="hint">
          <p>Search above, or pick a busy function:</p>
          <div className="chips" style={{ justifyContent: "center" }}>
            {starters.slice(0, 6).map((h) => (
              <button key={h.symbol_id} className="chip"
                      title={`${h.path}:${h.line}`}
                      onClick={() => pick(h.symbol_id, h.name, h.path, h.line)}>
                {h.name} <span className="muted">· {h.inbound} callers</span>
              </button>
            ))}
          </div>
        </div>
      )}
      {target && seq && (
        <div className="scroll" style={{ padding: 16 }}>
          {seq.steps.length === 0
            ? <div className="hint">The graph records no outgoing calls from
                this function within {depth} hops: it may be a leaf, or its
                calls target external libraries.</div>
            : <>
                <Mermaid code={seq.mermaid} />
                <table className="data" style={{ marginTop: 12 }}>
                  <thead><tr><th>#</th><th>From</th><th>Calls</th><th>In</th>
                    <th>At</th></tr></thead>
                  <tbody>
                    {seq.steps.map((s, i) => (
                      <tr key={i}>
                        <td className="muted">{i + 1}</td>
                        <td>{s.from_module}</td>
                        <td><code>{s.call}()</code></td>
                        <td>{s.to_module}</td>
                        <td className="muted">{s.site}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>}
        </div>
      )}
    </div>
  )
}
