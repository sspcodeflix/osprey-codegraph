import { useState } from "react"
import { Hotspot } from "../api"
import { useFocus } from "../focus"
import { Docs } from "../views/Docs"
import { Overview } from "../views/Overview"

export function Understand({ snap, prevSnap, mode, onGuard, onDocs }: {
  snap: number
  prevSnap: number | null
  mode: "glance" | "docs"
  onGuard: (sub: string) => void
  onDocs: () => void
}) {
  const { setFocus } = useFocus()

  const pickHotspot = (h: Hotspot) =>
    setFocus({ kind: "symbol", id: h.symbol_id, name: h.name, path: h.path,
               line: h.line, module: h.path.replace(/\/[^/]+$/, "") },
             { explore: true })

  return (
    <div className="view" style={{ gap: 8 }}>
      {mode === "glance"
        ? <Overview key={snap} snap={snap} prevSnap={prevSnap}
            onNavigate={(t) => t === "Docs" ? onDocs()
              : t === "Health" || t === "Cleanup" ? onGuard(t) : undefined}
            onPickHotspot={pickHotspot} />
        : <Docs key={snap} snap={snap} />}
    </div>
  )
}
