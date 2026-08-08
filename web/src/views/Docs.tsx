import { useEffect, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { api, DocPage, DocPageMeta } from "../api"
import { Mermaid } from "../Mermaid"

const PERSONAS = [["onboarding", "New engineer"]] as const

export function Docs({ snap }: { snap: number }) {
  const [persona, setPersona] = useState<string>("onboarding")
  const [tree, setTree] = useState<DocPageMeta[] | null>(null)
  const [slug, setSlug] = useState<string | null>(null)
  const [page, setPage] = useState<DocPage | null>(null)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState("")

  const loadTree = () => {
    api.docsTree(snap, persona).then((t) => {
      setTree(t)
      if (t.length && slug === null) setSlug(t[0].slug)
    }).catch((e) => setError(String(e)))
  }

  useEffect(() => { setSlug(null); setPage(null); loadTree() },
    [snap, persona])

  useEffect(() => {
    if (slug === null) return
    api.docsPage(snap, persona, slug)
      .then((p) => { setPage(p); setError("") })
      .catch((e) => setError(String(e)))
  }, [snap, persona, slug])

  const generate = async () => {
    setGenerating(true)
    setError("")
    try {
      await api.docsGenerate(snap, persona)
      const poll = () => {
        api.docsTree(snap, persona).then((t) => {
          if (t.length) {
            setTree(t)
            setSlug(t[0].slug)
            setGenerating(false)
          } else {
            window.setTimeout(poll, 4000)
          }
        }).catch(() => window.setTimeout(poll, 4000))
      }
      window.setTimeout(poll, 4000)
    } catch (e) {
      setError(String(e))
      setGenerating(false)
    }
  }

  if (tree !== null && tree.length === 0) {
    return (
      <div className="view">
        <div className="hint">
          {generating ? (
            <p><span className="spinner" /> Writing documentation — reading
              the graph, drafting sections, verifying every citation. This
              takes a few minutes.</p>
          ) : (
            <>
              <p>No documentation generated for this version yet.</p>
              <p className="muted">Osprey writes docs grounded in the code
                graph: diagrams are compiled from real dependencies, and
                every <code>file:line</code> citation is verified against
                the snapshot before publishing.</p>
              <button className="chip" onClick={generate}>
                Generate onboarding docs</button>
            </>
          )}
          {error && <div className="error">{error}</div>}
        </div>
      </div>
    )
  }

  return (
    <div className="view docs">
      <div className="docs-body">
        <nav className="docs-tree">
          <select value={persona} onChange={(e) => setPersona(e.target.value)}>
            {PERSONAS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
          {(tree ?? []).map((p) => (
            <button key={p.slug}
                    className={`docs-link ${p.slug === slug ? "active" : ""} ${p.parent_slug ? "child" : ""}`}
                    onClick={() => setSlug(p.slug)}>
              {p.title}
              {p.status !== "verified" && (
                <span className="muted" title="some citations could not be verified"> ◦</span>
              )}
            </button>
          ))}
        </nav>
        <article className="docs-content">
          {error && <div className="error">{error}</div>}
          {page && (
            <>
              <div className="docs-head">
                <h1>{page.title}</h1>
                {page.status === "verified"
                  ? <span className="pill verified">✓ citations verified</span>
                  : <span className="pill">draft</span>}
              </div>
              <div className="md">
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
                  code({ className, children, ...props }) {
                    const text = String(children ?? "")
                    if (className?.includes("language-mermaid")) {
                      return <Mermaid code={text} />
                    }
                    return <code className={className} {...props}>{children}</code>
                  },
                }}>
                  {page.content_md}
                </ReactMarkdown>
              </div>
              <p className="muted docs-foot">Describes commit{" "}
                <code>{page.commit.slice(0, 8)}</code> · diagrams compiled
                from the dependency graph · citations verified against the
                snapshot</p>
            </>
          )}
        </article>
      </div>
    </div>
  )
}
