import { Hotspot, Snapshot } from "../api"
import { useFocus } from "../focus"
import { DeadCode } from "../views/DeadCode"
import { DiffView } from "../views/DiffView"
import { Dsm } from "../views/Dsm"
import { Overview } from "../views/Overview"

// Overview is the landing space; Health/Cleanup/Changes are drill-ins
// reached from its cards (the old Guard space, folded away)
export type OverviewSub = "glance" | "Health" | "Cleanup" | "Changes"

const SUB_TITLES: Record<Exclude<OverviewSub, "glance">, string> = {
  Health: "Dependency health",
  Cleanup: "Cleanup candidates",
  Changes: "Compare versions",
}

export function Understand({ snap, prevSnap, snapshots, sub, onSub, onDocs }: {
  snap: number
  prevSnap: number | null
  snapshots: Snapshot[]
  sub: OverviewSub
  onSub: (s: OverviewSub) => void
  onDocs: () => void
}) {
  const { setFocus } = useFocus()

  const pickHotspot = (h: Hotspot) =>
    setFocus({ kind: "symbol", id: h.symbol_id, name: h.name, path: h.path,
               line: h.line, module: h.path.replace(/\/[^/]+$/, "") },
             { explore: true })

  if (sub !== "glance") {
    return (
      <div className="view" style={{ gap: 8 }}>
        <div className="backbar">
          <button className="linkish" onClick={() => onSub("glance")}>
            ← Overview</button>
          <b>{SUB_TITLES[sub]}</b>
        </div>
        {sub === "Health" && <Dsm key={snap} snap={snap} />}
        {sub === "Cleanup" && <DeadCode key={snap} snap={snap} />}
        {sub === "Changes" && <DiffView snapshots={snapshots} />}
      </div>
    )
  }

  return (
    <div className="view" style={{ gap: 8 }}>
      <Overview key={snap} snap={snap} prevSnap={prevSnap}
        onNavigate={(t) => t === "Docs" ? onDocs()
          : onSub(t as OverviewSub)}
        onPickHotspot={pickHotspot} />
    </div>
  )
}
