import { useEffect, useRef, useState } from "react"
import { api, DocPageMeta, Symbol as Sym } from "./api"
import { useFocus } from "./focus"

interface Action { label: string; run: () => void }

interface Item {
  kind: "symbol" | "doc" | "action"
  label: string
  hint: string
  run: () => void
}

export function Palette({ snap, open, onClose, actions, onOpenDoc }: {
  snap: number | null
  open: boolean
  onClose: () => void
  actions: Action[]
  onOpenDoc: (slug: string) => void
}) {
  const { setFocus } = useFocus()
  const [q, setQ] = useState("")
  const [symbols, setSymbols] = useState<Sym[]>([])
  const [docs, setDocs] = useState<DocPageMeta[]>([])
  const [sel, setSel] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!open) return
    setQ("")
    setSymbols([])
    setSel(0)
    setTimeout(() => inputRef.current?.focus(), 30)
    if (snap != null) {
      api.docsTree(snap, "onboarding").then(setDocs).catch(() => setDocs([]))
    }
  }, [open, snap])

  useEffect(() => {
    if (!open || snap == null || q.length < 2) { setSymbols([]); return }
    const t = setTimeout(() => {
      api.symbols(snap, q)
        .then((rs) => setSymbols(rs.filter((r) => !r.is_external).slice(0, 8)))
        .catch(() => setSymbols([]))
    }, 200)
    return () => clearTimeout(t)
  }, [q, open, snap])

  const lower = q.toLowerCase()
  const items: Item[] = [
    ...symbols.map((s): Item => ({
      kind: "symbol",
      label: s.name,
      hint: `${s.kind} · ${s.path}:${s.line}`,
      run: () => {
        setFocus({ kind: "symbol", id: s.id, name: s.name, path: s.path,
                   line: s.line,
                   module: s.path?.replace(/\/[^/]+$/, "") ?? "" },
                 { explore: true })
        onClose()
      },
    })),
    ...docs
      .filter((d) => q.length < 2 || d.title.toLowerCase().includes(lower))
      .slice(0, 4)
      .map((d): Item => ({
        kind: "doc",
        label: d.title,
        hint: "documentation",
        run: () => { onOpenDoc(d.slug); onClose() },
      })),
    ...actions
      .filter((a) => q.length < 2 || a.label.toLowerCase().includes(lower))
      .map((a): Item => ({
        kind: "action", label: a.label, hint: "action",
        run: () => { a.run(); onClose() },
      })),
  ]

  useEffect(() => { setSel(0) }, [q, symbols.length])

  if (!open) return null
  return (
    <div className="palette-veil" onMouseDown={onClose}>
      <div className="palette" onMouseDown={(e) => e.stopPropagation()}>
        <input ref={inputRef} value={q} placeholder="Jump to a function, class, doc page, or action…"
               onChange={(e) => setQ(e.target.value)}
               onKeyDown={(e) => {
                 if (e.key === "Escape") onClose()
                 if (e.key === "ArrowDown") setSel((s) => Math.min(s + 1, items.length - 1))
                 if (e.key === "ArrowUp") setSel((s) => Math.max(s - 1, 0))
                 if (e.key === "Enter" && items[sel]) items[sel].run()
               }} />
        <div className="palette-list">
          {items.length === 0 && (
            <div className="hint">{q.length < 2
              ? "Type to search the codebase, or pick an action below."
              : "Nothing matches."}</div>
          )}
          {items.map((it, i) => (
            <button key={`${it.kind}${it.label}${i}`}
                    className={`palette-item ${i === sel ? "sel" : ""}`}
                    onMouseEnter={() => setSel(i)} onClick={it.run}>
              <span className="palette-kind">{
                it.kind === "symbol" ? "ƒ" : it.kind === "doc" ? "📄" : "▸"}</span>
              <span>{it.label}</span>
              <span className="muted" style={{ marginLeft: "auto" }}>{it.hint}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
