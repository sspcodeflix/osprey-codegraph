import { useState } from "react"
import { Hotspot, Symbol as Sym } from "../api"
import { BlastRadius } from "./BlastRadius"
import { Flow } from "./Flow"

// One lens, one gesture (focus a symbol), two directions. Merges the old
// "Blast radius" (what depends on this) and "Sequence" (what this calls)
// so a user never has to choose between two single-symbol views up front.
export function Focus({ snap, preset, onPick }: {
  snap: number
  preset?: { id: number; name: string; path: string | null
             line: number | null } | null
  onPick?: (s: Sym) => void
}) {
  const [dir, setDir] = useState<"used-by" | "uses">("used-by")

  const blastPreset: Hotspot | null = preset
    ? { symbol_id: preset.id, name: preset.name, kind: "function",
        path: preset.path ?? "", line: preset.line, inbound: 0 }
    : null

  return (
    <div className="view" style={{ gap: 8 }}>
      <div className="seg">
        <button className={dir === "used-by" ? "active" : ""}
                onClick={() => setDir("used-by")}>← What uses this</button>
        <button className={dir === "uses" ? "active" : ""}
                onClick={() => setDir("uses")}>What this uses →</button>
      </div>
      {dir === "used-by"
        ? <BlastRadius key={`b:${snap}:${preset?.id ?? "none"}`} snap={snap}
                       preset={blastPreset} onPick={onPick} />
        : <Flow key={`f:${snap}:${preset?.id ?? "none"}`} snap={snap}
                preset={preset} onPick={onPick} />}
    </div>
  )
}
