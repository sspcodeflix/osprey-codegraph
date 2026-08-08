import { useEffect, useState } from "react"
import { api, Hotspot, Overview as Ov } from "../api"

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

  const langs = Object.entries(data.languages)
    .map(([l, n]) => `${l} (${(n / 1000).toFixed(0)}k lines)`)
    .join(" · ")

  return (
    <div className="view overview">
      <div className="card">
        <h2>At a glance</h2>
        <p>
          <b>{data.files.toLocaleString()} files</b>, about{" "}
          <b>{Math.round(data.loc / 1000).toLocaleString()}k lines</b> — {langs}.
          Organized into <b>{data.modules} folders</b> connected by{" "}
          <b>{data.module_dependencies} dependencies</b>.
          Analyzed at commit <code>{data.commit.slice(0, 8)}</code>.
        </p>
      </div>

      <div className="card finding">
        {data.cycles.length === 0 ? (
          <p>✅ <b>No circular dependencies.</b> No group of folders depends
            on itself in a loop — changes flow one way, which keeps the
            codebase easy to modify.</p>
        ) : (
          <p>⚠️ <b>{data.cycles.length} circular dependency
            group{data.cycles.length > 1 ? "s" : ""}.</b> These folders
            depend on each other in a loop, so none can change safely without
            the others: <code>{data.cycles[0].slice(0, 5).join(" → ")}
            {data.cycles[0].length > 5 ? " → …" : ""}</code>
            {"  "}
            <button className="linkish" onClick={() => onNavigate("Health")}>
              see the evidence →</button></p>
        )}
      </div>

      <div className="card finding">
        {data.entry_points === 0 ? (
          <p>ℹ️ <b>Cleanup analysis unavailable</b> — no entry points (web
            routes, commands, main scripts) were detected, which usually
            means this is a library whose code is called from outside.</p>
        ) : (
          <p>🧹 <b>{data.deadcode ?? 0} pieces of likely-unused code</b>,
            unreachable from any of the {data.entry_points} entry points.
            Candidates for deletion after review.{"  "}
            <button className="linkish" onClick={() => onNavigate("Cleanup")}>
              see the list →</button></p>
        )}
      </div>

      <div className="card finding">
        <p>🔥 <b>Most depended-on code</b> — changes here ripple widest.
          Click one to see its blast radius:</p>
        <div className="chips">
          {data.hotspots.map((h) => (
            <button key={h.symbol_id} className="chip"
                    title={`${h.path}:${h.line}`}
                    onClick={() => onPickHotspot(h)}>
              {h.name} <span className="muted">· {h.inbound} callers</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
