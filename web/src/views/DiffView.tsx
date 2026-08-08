import { useEffect, useState } from "react"
import { api, Diff, Snapshot } from "../api"

// SCIP symbol -> readable descriptor (drop `scheme manager package version`)
const short = (sym: string) => sym.split(" ").slice(4).join(" ") || sym

export function DiffView({ snapshots }: { snapshots: Snapshot[] }) {
  const [base, setBase] = useState<number | null>(null)
  const [head, setHead] = useState<number | null>(null)
  const [diff, setDiff] = useState<Diff | null>(null)
  const [error, setError] = useState("")

  useEffect(() => {
    if (snapshots.length >= 2) {
      setHead(snapshots[0].id)
      setBase(snapshots[1].id)
    }
  }, [snapshots])

  useEffect(() => {
    if (base == null || head == null || base === head) return
    api.diff(base, head)
      .then((d) => { setDiff(d); setError("") })
      .catch((e) => { setDiff(null); setError(String(e)) })
  }, [base, head])

  const pick = (value: number | null, set: (n: number) => void) => (
    <select value={value ?? ""} onChange={(e) => set(Number(e.target.value))}>
      {snapshots.map((s) => (
        <option key={s.id} value={s.id}>#{s.id} {s.commit_sha.slice(0, 8)}</option>
      ))}
    </select>
  )

  if (snapshots.length < 2) {
    return <div className="hint">Diff needs at least two ready snapshots of the
      same repo. Index the repo again after a change to create one.</div>
  }

  return (
    <div className="view">
      <p className="explain">What changed between two versions — not lines of
        text, but <b>relationships</b>: which code started or stopped
        depending on which. Green = added, red = removed.</p>
      <div className="controls">
        <label>Before</label>{pick(base, setBase)}
        <label>After</label>{pick(head, setHead)}
        {diff && (
          <>
            <span className="pill diff-added">+{diff.edges_added.length} edges,
              +{diff.symbols_added.length} symbols</span>
            <span className="pill diff-removed">−{diff.edges_removed.length} edges,
              −{diff.symbols_removed.length} symbols</span>
          </>
        )}
      </div>
      {error && <div className="error">{error}</div>}
      {diff && (
        <div className="scroll">
          <table className="data">
            <thead><tr><th></th><th>Kind</th><th>From</th><th>To</th></tr></thead>
            <tbody>
              {diff.edges_added.map((e, i) => (
                <tr key={`a${i}`}>
                  <td className="diff-added">+</td>
                  <td className="muted">{e.kind}</td>
                  <td>{short(e.src)}</td><td>{short(e.dst)}</td>
                </tr>
              ))}
              {diff.edges_removed.map((e, i) => (
                <tr key={`r${i}`}>
                  <td className="diff-removed">−</td>
                  <td className="muted">{e.kind}</td>
                  <td>{short(e.src)}</td><td>{short(e.dst)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
