import { Symbol as Sym } from "../api"
import { useFocus } from "../focus"
import { BlastRadius } from "../views/BlastRadius"
import { Flow } from "../views/Flow"
import { ModuleMap } from "../views/ModuleMap"

export const LENSES = ["Map", "Blast radius", "Sequence"] as const
export type Lens = (typeof LENSES)[number]

export function Explore({ snap, lens }: { snap: number; lens: Lens }) {
  const { focus, setFocus } = useFocus()

  const symbolPreset = focus?.kind === "symbol" && focus.id != null
    ? { symbol_id: focus.id, id: focus.id, name: focus.name, kind: "function",
        path: focus.path ?? "", line: focus.line ?? null, inbound: 0 }
    : null

  const pickedSymbol = (s: Sym) =>
    setFocus({ kind: "symbol", id: s.id, name: s.name, path: s.path,
               line: s.line,
               module: s.path?.replace(/\/[^/]+$/, "") ?? "" })

  return (
    <div className="view" style={{ gap: 8 }}>
      {!focus && <p className="explain">Select anything - a folder on the
        map, a function via ⌘K - and every lens follows it.</p>}
      {lens === "Map" && (
        <ModuleMap key={snap} snap={snap}
          focusModule={focus?.kind === "module" ? focus.module
            : focus?.kind === "symbol" ? (focus.module || null) : null}
          onModuleFocus={(m) => m
            ? setFocus({ kind: "module", name: m.split("/").pop() || m,
                         module: m })
            : setFocus(null)} />
      )}
      {lens === "Blast radius" && (
        <BlastRadius key={`${snap}:${focus?.id ?? "none"}`} snap={snap}
                     preset={symbolPreset} onPick={pickedSymbol} />
      )}
      {lens === "Sequence" && (
        <Flow key={`${snap}:${focus?.id ?? "none"}`} snap={snap}
              preset={focus?.kind === "symbol" && focus.id != null
                ? { id: focus.id, name: focus.name, path: focus.path ?? null,
                    line: focus.line ?? null } : null}
              onPick={pickedSymbol} />
      )}
    </div>
  )
}
