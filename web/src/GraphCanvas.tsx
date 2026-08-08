import Graph from "graphology"
import Sigma from "sigma"
import { MutableRefObject, useEffect, useRef } from "react"
import { cssVar } from "./api"

export interface GraphApi {
  exportPng: (filename: string) => void
}

export function GraphCanvas({ graph, onNodeClick, onNodeDoubleClick,
  onStageClick, apiRef }: {
  graph: Graph | null
  onNodeClick?: (id: string) => void
  onNodeDoubleClick?: (id: string) => void
  onStageClick?: () => void
  apiRef?: MutableRefObject<GraphApi | null>
}) {
  const ref = useRef<HTMLDivElement>(null)
  const clickRef = useRef(onNodeClick)
  const dblRef = useRef(onNodeDoubleClick)
  const stageRef = useRef(onStageClick)
  clickRef.current = onNodeClick
  dblRef.current = onNodeDoubleClick
  stageRef.current = onStageClick

  useEffect(() => {
    if (!ref.current || !graph) return
    // sigma's default hover drawer paints a WHITE pill behind the label —
    // invisible with our white label text on the dark theme. Draw our own:
    // dark pill, accent border, light text.
    const drawNodeHover = (ctx: CanvasRenderingContext2D, data: {
      x: number; y: number; size: number; label?: string | null
    }, settings: { labelSize: number; labelFont?: string }) => {
      const label = data.label
      if (!label) return
      const size = settings.labelSize
      ctx.font = `600 ${size}px ${settings.labelFont ?? "system-ui"}`
      const w = ctx.measureText(label).width + 14
      const h = size + 10
      const x = data.x + data.size + 4
      const y = data.y - h / 2
      ctx.beginPath()
      ctx.roundRect(x, y, w, h, 6)
      ctx.fillStyle = cssVar("--surface-2")
      ctx.fill()
      ctx.strokeStyle = cssVar("--accent")
      ctx.lineWidth = 1
      ctx.stroke()
      ctx.fillStyle = cssVar("--text-primary")
      ctx.fillText(label, x + 7, y + h / 2 + size / 3)
    }
    const sigma = new Sigma(graph, ref.current, {
      labelColor: { color: cssVar("--text-primary") },
      labelSize: 12,
      labelRenderedSizeThreshold: 5,
      minCameraRatio: 0.05,
      maxCameraRatio: 8,
      allowInvalidContainer: true,
      defaultEdgeType: "arrow",   // edges are directed: A uses B
      defaultDrawNodeHover: drawNodeHover,
    })
    sigma.on("clickNode", (e) => clickRef.current?.(e.node))
    sigma.on("doubleClickNode", (e) => {
      e.preventSigmaDefault()
      dblRef.current?.(e.node)
    })
    sigma.on("clickStage", () => stageRef.current?.())
    if (apiRef) {
      apiRef.current = {
        exportPng: (filename: string) => {
          const canvases = Object.values(sigma.getCanvases())
          if (!canvases.length) return
          const out = document.createElement("canvas")
          out.width = canvases[0].width
          out.height = canvases[0].height
          const ctx = out.getContext("2d")!
          ctx.fillStyle = cssVar("--surface-1")
          ctx.fillRect(0, 0, out.width, out.height)
          for (const c of canvases) ctx.drawImage(c, 0, 0)
          out.toBlob((blob) => {
            if (!blob) return
            const a = document.createElement("a")
            a.href = URL.createObjectURL(blob)
            a.download = filename
            a.click()
            URL.revokeObjectURL(a.href)
          })
        },
      }
    }
    return () => {
      if (apiRef) apiRef.current = null
      sigma.kill()
    }
  }, [graph])

  return (
    <div className="graph-box">
      <div ref={ref} className="sigma-container" />
    </div>
  )
}
