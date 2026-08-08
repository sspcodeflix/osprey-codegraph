// Typed client for the Osprey query API (mirrors osprey/api/models.py).

export interface Repo { name: string; org: string; latest_snapshot: number | null; ref: string | null }
export interface Snapshot { id: number; commit_sha: string; status: string; stats: Record<string, unknown>; created_at: string }
export interface Hotspot { symbol_id: number; name: string; kind: string; path: string; line: number | null; inbound: number }
export interface Overview {
  commit: string; files: number; loc: number
  languages: Record<string, number>; symbols: Record<string, number>
  modules: number; module_dependencies: number
  cycles: string[][]; entry_points: number; deadcode: number | null
  hotspots: Hotspot[]
}
export interface Symbol { id: number; name: string; kind: string; path: string | null; line: number | null; is_external: boolean }
export interface TraverseNode { id: number; depth: number; name: string; kind: string; path: string | null; line?: number | null }
export interface Impact { symbol_id: number; depth: number; count: number; truncated: boolean; impacted: TraverseNode[] }
export interface SubgraphNode { id: number; depth: number; name: string; kind: string; path: string | null; is_external: boolean }
export interface SubgraphEdge { src_id: number; dst_id: number; kind: string; weight: number }
export interface Subgraph { root: number; hops: number; nodes: SubgraphNode[]; edges: SubgraphEdge[]; truncated: boolean }
export interface ModuleNode { module: string; loc: number; files: number }
export interface ModuleEdge { src_module: string; dst_module: string; kind: string; weight: number }
export interface Modules { nodes: ModuleNode[]; edges: ModuleEdge[] }
export interface DiffEdge { src: string; dst: string; kind: string }
export interface Diff {
  base: { snapshot: number; commit: string }
  head: { snapshot: number; commit: string }
  edges_added: DiffEdge[]; edges_removed: DiffEdge[]
  symbols_added: string[]; symbols_removed: string[]
}
export interface DeadCandidate { name: string; kind: string; path: string; line: number | null }
export interface Deadcode { entry_points: number; candidates: DeadCandidate[]; count: number }

const TOKEN_KEY = "osprey_token"
export const getToken = () => localStorage.getItem(TOKEN_KEY)
export const setToken = (t: string) => localStorage.setItem(TOKEN_KEY, t)
export const clearToken = () => localStorage.removeItem(TOKEN_KEY)

const authHeaders = (): Record<string, string> => {
  const t = getToken()
  return t ? { Authorization: `Bearer ${t}` } : {}
}

export class Unauthorized extends Error {}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: authHeaders() })
  if (res.status === 401) throw new Unauthorized("access code required")
  if (!res.ok) throw new Error(`${res.status}: ${(await res.json().catch(() => ({})) as { detail?: string }).detail ?? res.statusText}`)
  return res.json() as Promise<T>
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  })
  if (res.status === 401) throw new Unauthorized("access code required")
  if (!res.ok) throw new Error(`${(await res.json().catch(() => ({})) as { detail?: string }).detail ?? res.statusText}`)
  return res.json() as Promise<T>
}

export interface IndexJob { job_id: number; repo: string }
export interface DocPageMeta { slug: string; title: string; position: number; parent_slug: string | null; status: string }
export interface SequenceStep { from_module: string; to_module: string; call: string; site: string }
export interface Sequence { root: string; mermaid: string; steps: SequenceStep[]; truncated: boolean }
export interface DocPage { slug: string; title: string; status: string; content_md: string; commit: string; persona: string }
export interface AskResponse { answer: string; trace: { tool: string; args: Record<string, unknown> }[] }
export interface JobStatus {
  id: number; status: string; error: string | null
  repo: string; snapshot_status: string | null
}

export const api = {
  repos: () => get<Repo[]>("/v1/repos"),
  snapshots: (repo: string) => get<Snapshot[]>(`/v1/repos/${encodeURIComponent(repo)}/snapshots`),
  symbols: (snap: number, q: string) => get<Symbol[]>(`/v1/snapshots/${snap}/symbols?q=${encodeURIComponent(q)}`),
  impact: (snap: number, symbolId: number, depth: number) =>
    get<Impact>(`/v1/snapshots/${snap}/impact?symbol_id=${symbolId}&depth=${depth}`),
  subgraph: (snap: number, root: number, hops: number) =>
    get<Subgraph>(`/v1/snapshots/${snap}/subgraph?root=${root}&hops=${hops}`),
  modules: (snap: number, kind = "") => get<Modules>(`/v1/snapshots/${snap}/modules?kind=${kind}`),
  diff: (base: number, head: number) => get<Diff>(`/v1/diff?base=${base}&head=${head}`),
  deadcode: (snap: number) => get<Deadcode>(`/v1/snapshots/${snap}/deadcode?limit=500`),
  overview: (snap: number) => get<Overview>(`/v1/snapshots/${snap}/overview`),
  cycles: (snap: number) => get<{ count: number; cycles: string[][] }>(`/v1/snapshots/${snap}/modules/cycles`),
  hotspots: (snap: number) => get<Hotspot[]>(`/v1/snapshots/${snap}/hotspots?limit=10`),
  me: () => get<{ user: string; auth: string; demo: boolean }>("/v1/me"),
  indexRepo: (git_url: string, ref?: string) =>
    post<IndexJob>("/v1/repos/index", { git_url, ref: ref || null }),
  requestRepo: (git_url: string, ref: string, contact: string, note: string) =>
    post<{ id: number; status: string }>("/v1/repo-requests",
      { git_url, ref: ref || null, contact, note }),
  ask: (snap: number, question: string, history: { role: string; content: string }[]) =>
    post<AskResponse>("/v1/ask", { snapshot_id: snap, question, history }),
  sequence: (snap: number, symbolId: number, depth: number) =>
    get<Sequence>(`/v1/snapshots/${snap}/sequence?symbol_id=${symbolId}&depth=${depth}`),
  docsTree: (snap: number, persona: string) =>
    get<DocPageMeta[]>(`/v1/snapshots/${snap}/docs/${persona}`),
  docsPage: (snap: number, persona: string, slug: string) =>
    get<DocPage>(`/v1/snapshots/${snap}/docs/${persona}/${slug}`),
  docsGenerate: (snap: number, persona: string) =>
    post<IndexJob>("/v1/docs/generate", { snapshot_id: snap, persona }),
  jobStatus: (id: number) => get<JobStatus>(`/v1/jobs/${id}`),
  graphmlUrl: (snap: number) => `/v1/snapshots/${snap}/export/graphml`,
  mermaidUrl: (snap: number, kind: string) => `/v1/snapshots/${snap}/export/mermaid?kind=${kind}`,
}

export function downloadText(name: string, text: string, type = "text/plain") {
  const a = document.createElement("a")
  a.href = URL.createObjectURL(new Blob([text], { type }))
  a.download = name
  a.click()
  URL.revokeObjectURL(a.href)
}

export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

export const EDGE_KIND_VAR: Record<string, string> = {
  CALLS: "--series-1",
  IMPORTS: "--series-3",
  INHERITS: "--series-7",
}
