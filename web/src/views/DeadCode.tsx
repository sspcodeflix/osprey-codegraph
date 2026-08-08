import { useEffect, useState } from "react"
import { api, Deadcode } from "../api"

export function DeadCode({ snap }: { snap: number }) {
  const [data, setData] = useState<Deadcode | null>(null)
  const [error, setError] = useState("")

  useEffect(() => {
    api.deadcode(snap)
      .then((d) => { setData(d); setError("") })
      .catch((e) => { setData(null); setError(String(e)) })
  }, [snap])

  return (
    <div className="view">
      <p className="explain">Code that nothing can reach from any of the
        app's entry points (web routes, commands, main scripts) - likely safe
        to delete after a human look. We only list what we're confident
        about: anything analyzed approximately is excluded.</p>
      {data && (
        <div className="controls">
          <span className="pill">{data.count} candidates</span>
          <span className="pill">{data.entry_points} entry points checked</span>
        </div>
      )}
      {error && (error.includes("no entry points")
        ? <div className="hint">
            <p>ℹ️ <b>Cleanup analysis doesn't apply here.</b></p>
            <p className="muted">No entry points (web routes, commands, main
              scripts) were detected - this looks like a library, whose code
              is called from outside. Reachability from entry points would
              wrongly flag everything.</p>
          </div>
        : <div className="error">{error}</div>)}
      {data && (
        <div className="scroll">
          <table className="data">
            <thead><tr><th>Symbol</th><th>Kind</th><th>Location</th></tr></thead>
            <tbody>
              {data.candidates.map((c, i) => (
                <tr key={i}>
                  <td>{c.name}</td>
                  <td className="muted">{c.kind}</td>
                  <td className="muted">{c.path}:{c.line}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
