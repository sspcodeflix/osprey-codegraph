import { useState } from "react"
import { Hotspot } from "../api"
import { useFocus } from "../focus"
import { Docs } from "../views/Docs"
import { Overview } from "../views/Overview"

export function Understand({ snap, mode, setMode, onGuard }: {
  snap: number
  mode: "glance" | "docs"
  setMode: (m: "glance" | "docs") => void
  onGuard: (sub: string) => void
}) {
  const { setFocus } = useFocus()

  const pickHotspot = (h: Hotspot) =>
    setFocus({ kind: "symbol", id: h.symbol_id, name: h.name, path: h.path,
               line: h.line, module: h.path.replace(/\/[^/]+$/, "") },
             { explore: true })

  return (
    <div className="view" style={{ gap: 8 }}>
      <div className="controls">
        <nav className="lenses">
          <button className={mode === "glance" ? "active" : ""}
                  onClick={() => setMode("glance")}>At a glance</button>
          <button className={mode === "docs" ? "active" : ""}
                  onClick={() => setMode("docs")}>Documentation</button>
        </nav>
      </div>
      {mode === "glance"
        ? <Overview key={snap} snap={snap}
            onNavigate={(t) => t === "Health" || t === "Cleanup"
              ? onGuard(t) : setMode("docs")}
            onPickHotspot={pickHotspot} />
        : <Docs key={snap} snap={snap} />}
    </div>
  )
}
