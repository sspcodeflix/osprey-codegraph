import { useEffect, useRef, useState } from "react"
import { api, Hotspot, JobStatus, Repo, Snapshot } from "./api"
import { BlastRadius } from "./views/BlastRadius"
import { DeadCode } from "./views/DeadCode"
import { DiffView } from "./views/DiffView"
import { Dsm } from "./views/Dsm"
import { Ask } from "./views/Ask"
import { Docs } from "./views/Docs"
import { Flow } from "./views/Flow"
import { ModuleMap } from "./views/ModuleMap"
import { Overview } from "./views/Overview"

const TABS = ["Overview", "Docs", "Ask", "Map", "Call flow", "What breaks?",
  "Health", "Changes", "Cleanup"] as const
type Tab = (typeof TABS)[number]

const fmtDate = (iso: string) =>
  new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" })

export default function App() {
  const [repos, setRepos] = useState<Repo[]>([])
  const [repo, setRepo] = useState<string>("")
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [snap, setSnap] = useState<number | null>(null)
  const [tab, setTab] = useState<Tab>("Overview")
  const [blastPreset, setBlastPreset] = useState<Hotspot | null>(null)
  const [error, setError] = useState("")
  const [adding, setAdding] = useState(false)
  const [addUrl, setAddUrl] = useState("")
  const [addRef, setAddRef] = useState("")
  const [job, setJob] = useState<JobStatus | null>(null)
  const jobTimer = useRef<number | null>(null)

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
            setTab("Overview")
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

  const pickHotspot = (h: Hotspot) => {
    setBlastPreset(h)
    setTab("What breaks?")
  }

  return (
    <>
      <header className="topbar">
        <h1>🦅 <span className="brand">Osprey</span></h1>
        <select value={repo} onChange={(e) => setRepo(e.target.value)}
                title="Which codebase to explore">
          {repos.map((r) => <option key={r.name} value={r.name}>{r.name}</option>)}
        </select>
        <select value={snap ?? ""} onChange={(e) => setSnap(Number(e.target.value))}
                title="Which analyzed version — every analysis is frozen per commit">
          {snapshots.map((s) => (
            <option key={s.id} value={s.id}>
              {s.commit_sha.slice(0, 7)} · {fmtDate(s.created_at)}
            </option>
          ))}
        </select>
        <button className="chip" onClick={() => setAdding(!adding)}
                title="Index a public repository by URL">＋ Add repo</button>
        <nav className="tabs">
          {TABS.map((t) => (
            <button key={t} className={t === tab ? "active" : ""}
                    onClick={() => setTab(t)}>{t}</button>
          ))}
        </nav>
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
                 style={{ width: 160 }}
                 title="A branch, tag, or commit sha — or just paste a
GitHub .../releases/tag/… or .../tree/… URL on the left" />
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
      <main>
        {error && <div className="error">{error}</div>}
        {snap == null ? <div className="hint">No analyzed versions yet. Index a
          repo first: <code>osprey index /path --name myrepo</code></div> : (
          <>
            {tab === "Overview" && <Overview key={snap} snap={snap}
              onNavigate={(t) => setTab(t as Tab)} onPickHotspot={pickHotspot} />}
            {tab === "Docs" && <Docs key={snap} snap={snap} />}
            {tab === "Ask" && <Ask key={snap} snap={snap} />}
            {tab === "Map" && <ModuleMap key={snap} snap={snap} />}
            {tab === "Call flow" && <Flow key={snap} snap={snap} />}
            {tab === "What breaks?" && <BlastRadius key={snap} snap={snap}
              preset={blastPreset} />}
            {tab === "Health" && <Dsm key={snap} snap={snap} />}
            {tab === "Changes" && <DiffView snapshots={snapshots} />}
            {tab === "Cleanup" && <DeadCode key={snap} snap={snap} />}
          </>
        )}
      </main>
    </>
  )
}
