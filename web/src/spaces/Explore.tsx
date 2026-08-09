import { Symbol as Sym } from "../api"
import { useFocus } from "../focus"
import { Focus as FocusLens } from "../views/Focus"
import { ModuleMap } from "../views/ModuleMap"

export const LENSES = ["Map", "Focus"] as const
export type Lens = (typeof LENSES)[number]

export function Explore({ snap, lens }: { snap: number; lens: Lens }) {
  const { focus, setFocus } = useFocus()

  const symbolPreset = focus?.kind === "symbol" && focus.id != null
    ? { id: focus.id, name: focus.name, path: focus.path ?? null,
        line: focus.line ?? null }
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
      {lens === "Focus" && (
        <FocusLens snap={snap} preset={symbolPreset} onPick={pickedSymbol} />
      )}
    </div>
  )
}
