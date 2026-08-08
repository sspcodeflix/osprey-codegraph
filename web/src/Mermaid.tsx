import mermaid from "mermaid"
import { useEffect, useId, useState } from "react"

mermaid.initialize({
  startOnLoad: false,
  securityLevel: "strict",
  theme: "base",
  themeVariables: {
    darkMode: true,
    background: "#12162b",
    primaryColor: "#232a4d",
    primaryBorderColor: "#7269ef",
    primaryTextColor: "#eef0fb",
    lineColor: "#737a9e",
    fontFamily: "system-ui, sans-serif",
    fontSize: "13px",
  },
})

export function Mermaid({ code }: { code: string }) {
  const id = useId().replace(/:/g, "m")
  const [svg, setSvg] = useState("")
  const [err, setErr] = useState("")

  useEffect(() => {
    let live = true
    mermaid.render(`mmd${id}`, code)
      .then((r) => { if (live) setSvg(r.svg) })
      .catch((e) => { if (live) setErr(String(e)) })
    return () => { live = false }
  }, [code])

  if (err) return <pre className="muted">{code}</pre>
  return <div className="mermaid-box"
              dangerouslySetInnerHTML={{ __html: svg }} />
}
