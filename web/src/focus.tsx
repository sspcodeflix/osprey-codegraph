// The one-selection-many-lenses primitive: pick a symbol or folder once,
// every lens follows. This is the app's answer to context amnesia.
import { createContext, useContext } from "react"

export interface Focus {
  kind: "symbol" | "module"
  id?: number            // symbol_id when kind === 'symbol'
  name: string
  path?: string | null
  line?: number | null
  module?: string        // module path (kind === 'module', or symbol's home)
}

export interface FocusApi {
  focus: Focus | null
  setFocus: (f: Focus | null, opts?: { explore?: boolean }) => void
}

export const FocusContext = createContext<FocusApi>({
  focus: null,
  setFocus: () => undefined,
})

export const useFocus = () => useContext(FocusContext)
