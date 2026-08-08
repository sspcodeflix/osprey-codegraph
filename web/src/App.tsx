import { useEffect, useRef, useState } from "react"
import { api, JobStatus, Repo, Snapshot } from "./api"
import { Focus, FocusContext } from "./focus"
import { Palette } from "./Palette"
import { Explore } from "./spaces/Explore"
import { Guard, GuardSub } from "./spaces/Guard"
import { Understand } from "./spaces/Understand"
import { Ask } from "./views/Ask"

const SPACES = ["Understand", "Explore", "Guard"] as const
type Space = (typeof SPACES)[number]

const fmtDate = (iso: string) =>
  new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" })

export default function App() {
  const [repos, setRepos] = useState<Repo[]>([])
  const [repo, setRepo] = useState<string>("")
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [snap, setSnap] = useState<number | null>(null)
  const [space, setSpace] = useState<Space>("Understand")
  const [understandMode, setUnderstandMode] = useState<"glance" | "docs">("glance")
  const [guardSub, setGuardSub] = useState<GuardSub>("Changes")
  const [focus, setFocusState] = useState<Focus | null>(null)
  const [askOpen, setAskOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [error, setError] = useState("")
  const [adding, setAdding] = useState(false)
  const [addUrl, setAddUrl] = useState("")
  const [addRef, setAddRef] = useState("")
  const [job, setJob] = useState<JobStatus | null>(null)
  const jobTimer = useRef<number | null>(null)

  const setFocus = (f: Focus | null, opts?: { explore?: boolean }) => {
    setFocusState(f)
    if (f && opts?.explore) setSpace("Explore")
  }

  useEffect(() => {
    api.repos().then((rs) => {
      setRepos(rs)
      const first = rs.find((r) => r.latest_snapshot != null)
      if (first) setRepo(first.name)
    }).catch((e) => setError(String(e)))
  }, [])

  useEffect(() => {
    if (!repo) return
    api.snapshots(repo).then((ss) => {
      const ready = ss.filter((s) => s.status === "ready")
      setSnapshots(ready)
      setSnap(ready[0]?.id ?? null)
    }).catch((e) => setError(String(e)))
  }, [repo])

  // a focus is snapshot-specific (symbol ids don't transfer); clear it
  // whenever the viewed version changes
  useEffect(() => { setFocusState(null) }, [snap])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault()
        setPaletteOpen((o) => !o)
      }
      if (e.key === "Escape") { setPaletteOpen(false); setAskOpen(false) }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  const submitAdd = async () => {
    try {
      const j = await api.indexRepo(addUrl.trim(), addRef.trim())
      setAdding(false)
      setAddUrl("")
      setAddRef("")
      setJob({ id: j.job_id, status: "queued", error: null, repo: j.repo,
               snapshot_status: null })
      const poll = async () => {
        const s = await api.jobStatus(j.job_id).catch(() => null)
        if (s) setJob(s)
        if (s && (s.status === "done" || s.status === "failed")) {
          if (s.status === "done") {
            const rs = await api.repos()
            setRepos(rs)
            setRepo(s.repo)
            setSpace("Understand")
            setUnderstandMode("glance")
            window.setTimeout(() => setJob(null), 4000)
          }
          return
        }
        jobTimer.current = window.setTimeout(poll, 2500)
      }
      poll()
    } catch (e) {
      setError(String(e))
    }
  }

  useEffect(() => () => {
    if (jobTimer.current) window.clearTimeout(jobTimer.current)
  }, [])

  return (
    <FocusContext.Provider value={{ focus, setFocus }}>
      <header className="topbar">
        <h1>🦅 <span className="brand">Osprey</span></h1>
        <select value={repo} onChange={(e) => setRepo(e.target.value)}
                title="Which codebase to explore">
          {repos.map((r) => <option key={r.name} value={r.name}>{r.name}</option>)}
        </select>
        <select value={snap ?? ""} onChange={(e) => setSnap(Number(e.target.value))}
                title="Which analyzed version">
          {snapshots.map((s) => (
            <option key={s.id} value={s.id}>
              {s.commit_sha.slice(0, 7)} · {fmtDate(s.created_at)}
            </option>
          ))}
        </select>
        <button className="chip" onClick={() => setAdding(!adding)}
                title="Index a public repository by URL">＋ Add repo</button>
        {focus && (
          <span className="pill focuspill" title={focus.path ?? focus.module}>
            ◉ {focus.name}
            <button className="linkish" onClick={() => setFocus(null)}
                    title="clear selection">✕</button>
          </span>
        )}
        <nav className="tabs spaces">
          {SPACES.map((s) => (
            <button key={s} className={s === space ? "active" : ""}
                    onClick={() => setSpace(s)}>{s}</button>
          ))}
        </nav>
        <button className="chip" onClick={() => setPaletteOpen(true)}
                title="Jump to anything (Ctrl/⌘ K)">⌘K</button>
        <button className={`chip ask-toggle ${askOpen ? "on" : ""}`}
                onClick={() => setAskOpen(!askOpen)}
                title="Ask questions in plain English">✦ Ask</button>
      </header>
      {adding && (
        <div className="addbar">
          <input autoFocus placeholder="https://github.com/owner/repo"
                 value={addUrl} onChange={(e) => setAddUrl(e.target.value)}
                 onKeyDown={(e) => e.key === "Enter" && submitAdd()}
                 style={{ width: 380 }} />
          <input placeholder="tag / branch (optional)"
                 value={addRef} onChange={(e) => setAddRef(e.target.value)}
                 onKeyDown={(e) => e.key === "Enter" && submitAdd()}
                 style={{ width: 160 }} />
          <button className="chip" onClick={submitAdd}>Index it</button>
          <span className="muted">Tag and branch URLs work directly.
            Analyzed sandboxed (no scripts). Public repos up to 500 MB.</span>
        </div>
      )}
      {job && (
        <div className={`jobcard ${job.status}`}>
          {job.status === "failed"
            ? <>❌ <b>{job.repo}</b>: {job.error ?? "indexing failed"}</>
            : job.status === "done"
              ? <>✅ <b>{job.repo}</b> indexed — explore it above</>
              : <><span className="spinner" /> Indexing <b>{job.repo}</b>…
                  {" "}{job.snapshot_status === "indexing"
                    ? "analyzing code" : "fetching"}</>}
        </div>
      )}
      <main className={askOpen ? "with-drawer" : ""}>
        <div className="space-body">
          {error && <div className="error">{error}</div>}
          {snap == null
            ? <div className="hint">No analyzed versions yet. Paste a GitHub
                URL via <b>＋ Add repo</b>, or index a local checkout:
                {" "}<code>osprey index /path --name myrepo</code></div>
            : (
              <>
                {space === "Understand" && (
                  <Understand snap={snap} mode={understandMode}
                    setMode={setUnderstandMode}
                    onGuard={(sub) => { setSpace("Guard"); setGuardSub(sub as GuardSub) }} />
                )}
                {space === "Explore" && <Explore snap={snap} />}
                {space === "Guard" && (
                  <Guard snap={snap} snapshots={snapshots} sub={guardSub}
                         setSub={setGuardSub} />
                )}
              </>
            )}
        </div>
        {askOpen && snap != null && (
          <aside className="ask-drawer">
            <div className="drawer-head">
              <b>✦ Ask</b>
              {focus && <span className="pill">◉ {focus.name}
                <button className="linkish" onClick={() => setFocus(null)}
                        title="clear selection">✕</button></span>}
              <button className="linkish" style={{ marginLeft: "auto" }}
                      onClick={() => setAskOpen(false)}>✕</button>
            </div>
            <Ask key={`${snap}:${focus?.name ?? ""}`} snap={snap}
                 focusName={focus?.name} />
          </aside>
        )}
      </main>
      <Palette snap={snap} open={paletteOpen}
               onClose={() => setPaletteOpen(false)}
               onOpenDoc={() => { setSpace("Understand"); setUnderstandMode("docs") }}
               actions={[
                 { label: "Add a repository by URL",
                   run: () => setAdding(true) },
                 { label: "Generate onboarding docs",
                   run: () => { setSpace("Understand"); setUnderstandMode("docs") } },
                 { label: "Compare two versions (diff)",
                   run: () => { setSpace("Guard"); setGuardSub("Changes") } },
               ]} />
    </FocusContext.Provider>
  )
}
