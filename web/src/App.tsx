import { useEffect, useRef, useState } from "react"
import { api, JobStatus, Repo, Snapshot } from "./api"
import { Focus, FocusContext } from "./focus"
import { Palette } from "./Palette"
import { DocsIcon, ExploreIcon, OverviewIcon, PlusIcon, SearchIcon } from "./icons"
import { Explore, Lens, LENSES } from "./spaces/Explore"
import { OverviewSub, Understand } from "./spaces/Understand"
import { Ask } from "./views/Ask"
import { Docs, PERSONA_LABELS, PERSONAS } from "./views/Docs"

const SPACES = ["Overview", "Explore", "Documentation"] as const
type Space = (typeof SPACES)[number]

const SPACE_ICONS: Record<Space, () => JSX.Element> = {
  Overview: OverviewIcon, Explore: ExploreIcon, Documentation: DocsIcon,
}

const fmtDate = (iso: string) =>
  new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" })

const refOf = (s: Snapshot): string | null => {
  const r = s.stats?.ref
  return typeof r === "string" && r !== "HEAD" ? r : null
}

export default function App() {
  const [repos, setRepos] = useState<Repo[]>([])
  const [repo, setRepo] = useState<string>("")
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [snap, setSnap] = useState<number | null>(null)
  const [space, setSpace] = useState<Space>("Overview")
  const [overviewSub, setOverviewSub] = useState<OverviewSub>("glance")
  const [exploreLens, setExploreLens] = useState<Lens>("Map")
  const [docsPersona, setDocsPersona] = useState<string>("onboarding")
  const [docsJob, setDocsJob] =
    useState<{ snap: number; persona: string; repo: string
               status: "writing" | "done" } | null>(null)
  const [focus, setFocusState] = useState<Focus | null>(null)
  const [user, setUser] = useState<{ user: string; auth: string } | null>(null)
  const [askOpen, setAskOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [error, setError] = useState("")
  const [adding, setAdding] = useState(false)
  const [addUrl, setAddUrl] = useState("")
  const [addRef, setAddRef] = useState("")
  const [job, setJob] = useState<JobStatus | null>(null)
  const jobTimer = useRef<number | null>(null)
  const docsTimer = useRef<number | null>(null)

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
    api.me().then(setUser).catch(() => setUser(null))
  }, [])

  useEffect(() => {
    if (!repo) return
    api.snapshots(repo).then((ss) => {
      const ready = ss.filter((s) => s.status === "ready")
      setSnapshots(ready)
      setSnap(ready[0]?.id ?? null)
    }).catch((e) => setError(String(e)))
  }, [repo])

  useEffect(() => { setFocusState(null); setOverviewSub("glance") }, [snap])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      const typing = t != null && (t.tagName === "INPUT"
        || t.tagName === "TEXTAREA" || t.tagName === "SELECT"
        || t.isContentEditable)
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault()
        setPaletteOpen((o) => !o)
      }
      if (e.key === "/" && !e.metaKey && !e.ctrlKey && !typing) {
        e.preventDefault()
        setPaletteOpen(true)
      }
      if (e.key === "Escape") { setPaletteOpen(false); setAskOpen(false) }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  // Documentation: picking a persona generates its docs if they don't exist
  // yet, and the job keeps running (and stays visible) across spaces.
  const selectDocsPersona = (p: string, snapId = snap) => {
    setDocsPersona(p)
    setSpace("Documentation")
    if (snapId == null) return
    api.docsTree(snapId, p).then((t) => {
      if (t.length > 0) return   // already written — just open them
      return api.docsGenerate(snapId, p).then(() => {
        setDocsJob({ snap: snapId, persona: p, repo, status: "writing" })
        const poll = () => {
          api.docsTree(snapId, p).then((tt) => {
            if (tt.length > 0) {
              setDocsJob({ snap: snapId, persona: p, repo, status: "done" })
            } else {
              docsTimer.current = window.setTimeout(poll, 4000)
            }
          }).catch(() => { docsTimer.current = window.setTimeout(poll, 4000) })
        }
        docsTimer.current = window.setTimeout(poll, 4000)
      })
    }).catch((e) => setError(String(e)))
  }

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
            setSpace("Overview")
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
    if (docsTimer.current) window.clearTimeout(docsTimer.current)
  }, [])

  const subnav = (s: Space) => {
    if (s !== space) return null
    if (s === "Explore") {
      return LENSES.map((l) => (
        <button key={l} className={`subnav ${exploreLens === l ? "active" : ""}`}
                onClick={() => setExploreLens(l)}>{l}</button>
      ))
    }
    if (s === "Documentation") {
      return PERSONAS.map(([v, label]) => (
        <button key={v} className={`subnav ${docsPersona === v ? "active" : ""}`}
                onClick={() => selectDocsPersona(v)}>{label}</button>
      ))
    }
    return null
  }

  const initials = (user?.user ?? "?")
    .split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase()

  const prevSnap = (() => {
    const i = snapshots.findIndex((s) => s.id === snap)
    return i >= 0 ? snapshots[i + 1]?.id ?? null : null
  })()

  return (
    <FocusContext.Provider value={{ focus, setFocus }}>
      <div className="shell">
        <aside className="sidenav">
          <h1>🦅 <span className="brand">Osprey</span></h1>
          <div className="repo-block">
            <label className="muted small">Repository</label>
            <select value={repo} onChange={(e) => setRepo(e.target.value)}
                    title="Which codebase to explore">
              {repos.map((r) => (
                <option key={r.name} value={r.name}>
                  {r.name}{r.ref && r.ref !== "HEAD" ? ` · ${r.ref}` : ""}
                </option>
              ))}
            </select>
            {snapshots.length > 1 ? (
              <select value={snap ?? ""}
                      onChange={(e) => setSnap(Number(e.target.value))}
                      title="Which analyzed version">
                {snapshots.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.commit_sha.slice(0, 7)}
                    {refOf(s) ? ` · ${refOf(s)}` : ""} · {fmtDate(s.created_at)}
                  </option>
                ))}
              </select>
            ) : snapshots[0] && (
              <span className="muted small snapline"
                    title="the analyzed version: commit and date">
                {snapshots[0].commit_sha.slice(0, 7)}
                {refOf(snapshots[0]) ? ` · ${refOf(snapshots[0])}` : ""} ·{" "}
                {fmtDate(snapshots[0].created_at)}
              </span>
            )}
          </div>
          <nav className="sidenav-spaces">
            {SPACES.map((s) => (
              <div key={s}>
                <button className={`spacebtn ${s === space ? "active" : ""}`}
                        onClick={() => {
                          setSpace(s)
                          if (s === "Overview") setOverviewSub("glance")
                        }}>
                  <span className="spaceicon">{SPACE_ICONS[s]()}</span>{s}
                </button>
                <div className="subnav-group">{subnav(s)}</div>
              </div>
            ))}
          </nav>
          <div className="sidenav-foot">
            <button className="chip addchip" onClick={() => setAdding(!adding)}>
              <PlusIcon /> Add repo</button>
          </div>
        </aside>

        <div className="shell-main">
          <header className="topbar">
            <button className="sidesearch topsearch"
                    onClick={() => setPaletteOpen(true)}
                    title="Jump to any symbol, doc, or action: press / or ⌘K">
              <span><SearchIcon /> Search symbols, docs, actions…</span>
              <kbd>/</kbd>
            </button>
            {focus && (
              <span className="pill focuspill" title={focus.path ?? focus.module}>
                ◉ {focus.name}
                <button className="linkish" onClick={() => setFocus(null)}
                        title="clear selection">✕</button>
              </span>
            )}
            <span style={{ marginLeft: "auto" }} />
            <button className={`chip ask-toggle ${askOpen ? "on" : ""}`}
                    onClick={() => setAskOpen(!askOpen)}
                    title="Ask questions in plain English">✦ Ask</button>
            {user && (
              <span className="userchip" title={`auth: ${user.auth}`}>
                <span className="avatar">{initials}</span>
                {user.user}
              </span>
            )}
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
                  ? <>✅ <b>{job.repo}</b> indexed - explore it above</>
                  : <><span className="spinner" /> Indexing <b>{job.repo}</b>…
                      {" "}{job.snapshot_status === "indexing"
                        ? "analyzing code" : "fetching"}</>}
            </div>
          )}
          {docsJob && (
            <div className={`jobcard ${docsJob.status === "done" ? "done" : "queued"}`}>
              {docsJob.status === "writing"
                ? <><span className="spinner" /> Writing{" "}
                    <b>{PERSONA_LABELS[docsJob.persona]}</b> docs for{" "}
                    <b>{docsJob.repo}</b> - keeps going while you browse</>
                : <>✅ <b>{PERSONA_LABELS[docsJob.persona]}</b> docs for{" "}
                    <b>{docsJob.repo}</b> are ready{" "}
                    <button className="linkish" onClick={() => {
                      setDocsPersona(docsJob.persona)
                      setSpace("Documentation")
                      setDocsJob(null)
                    }}>read them →</button>
                    <button className="linkish" style={{ marginLeft: "auto" }}
                            onClick={() => setDocsJob(null)}>✕</button></>}
            </div>
          )}
          <main className={askOpen ? "with-drawer" : ""}>
            <div className="space-body">
              {error && <div className="error">{error}</div>}
              {snap == null
                ? <div className="cta-card">
                    <div className="cta-icon">🦅</div>
                    <h2>Understand any codebase in minutes</h2>
                    <p className="muted">Paste a repository URL and Osprey maps
                      its structure, finds the risks, and writes grounded
                      documentation - every claim checked against the code.</p>
                    <button className="primary" onClick={() => setAdding(true)}>
                      ＋ Add repository</button>
                    <p className="muted small">or index a local checkout:{" "}
                      <code>osprey index /path --name myrepo</code></p>
                  </div>
                : (
                  <>
                    {space === "Overview" && (
                      <Understand snap={snap} prevSnap={prevSnap}
                        snapshots={snapshots} sub={overviewSub}
                        onSub={setOverviewSub}
                        onDocs={() => setSpace("Documentation")} />
                    )}
                    {space === "Explore" && <Explore snap={snap} lens={exploreLens} />}
                    {space === "Documentation" && (
                      <Docs key={`${snap}:${docsPersona}`} snap={snap}
                        persona={docsPersona}
                        generating={docsJob?.status === "writing"
                          && docsJob.snap === snap
                          && docsJob.persona === docsPersona}
                        onPersona={selectDocsPersona} />
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
        </div>
      </div>
      <Palette snap={snap} open={paletteOpen}
               onClose={() => setPaletteOpen(false)}
               onOpenDoc={() => setSpace("Documentation")}
               actions={[
                 { label: "Add a repository by URL",
                   run: () => setAdding(true) },
                 { label: "Generate docs",
                   run: () => selectDocsPersona(docsPersona) },
                 { label: "Compare two versions (diff)",
                   run: () => { setSpace("Overview"); setOverviewSub("Changes") } },
               ]} />
    </FocusContext.Provider>
  )
}
