import { useRef, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { AskResponse, api } from "../api"

interface Msg {
  role: "user" | "assistant"
  content: string
  trace?: AskResponse["trace"]
}

const SUGGESTIONS = [
  "What are the most depended-on functions?",
  "Are there any circular dependencies?",
  "What code could we probably delete?",
]

export function Ask({ snap, focusName, context }: {
  snap: number
  focusName?: string | null
  context?: string
}) {
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const bottom = useRef<HTMLDivElement>(null)

  const send = async (q?: string) => {
    const question = (q ?? input).trim()
    if (!question || busy) return
    setInput("")
    setError("")
    const history = messages.map((m) => ({ role: m.role, content: m.content }))
    setMessages((ms) => [...ms, { role: "user", content: question }])
    setBusy(true)
    try {
      const res = await api.ask(snap, question, history, context)
      setMessages((ms) => [...ms, { role: "assistant",
        content: res.answer, trace: res.trace }])
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
      setTimeout(() => bottom.current?.scrollIntoView({ behavior: "smooth" }), 50)
    }
  }

  return (
    <div className="view">
      <p className="explain">Ask about this codebase in plain English. The
        assistant answers only from Osprey's verified graph tools - every
        answer shows which checks it ran. It won't help with anything
        outside this repository.</p>
      <div className="scroll chat">
        {messages.length === 0 && (
          <div className="hint">
            <p>Try one of these:</p>
            <div className="chips" style={{ justifyContent: "center" }}>
              {focusName && (
                <button className="chip"
                        onClick={() => send(`What breaks if we change ${focusName}?`)}>
                  What breaks if we change {focusName}?
                </button>
              )}
              {SUGGESTIONS.map((s) => (
                <button key={s} className="chip" onClick={() => send(s)}>{s}</button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            <div className="bubble">
              {m.role === "assistant" ? (
                <div className="md">
                  {/* no raw HTML (skipHtml) + default URL sanitization */}
                  <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>
                    {m.content}
                  </ReactMarkdown>
                </div>
              ) : m.content}
            </div>
            {m.trace && m.trace.length > 0 && (
              <div className="trace">
                checked:{" "}
                {Object.entries(
                  m.trace.reduce((acc, t) => {
                    acc[t.tool] = (acc[t.tool] ?? 0) + 1
                    return acc
                  }, {} as Record<string, number>),
                ).map(([tool, n]) => (
                  <span key={tool} className="pill">
                    {tool}{n > 1 ? ` ×${n}` : ""}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
        {busy && <div className="msg assistant">
          <div className="bubble"><span className="spinner" /> thinking: running
            graph checks…</div></div>}
        {error && <div className="error">{error}</div>}
        <div ref={bottom} />
      </div>
      <div className="controls">
        <input style={{ flex: 1 }} value={input} disabled={busy}
               placeholder="e.g. what breaks if we change want_bytes?"
               onChange={(e) => setInput(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && send()} />
        <button className="chip" disabled={busy} onClick={() => send()}>Ask</button>
      </div>
    </div>
  )
}
