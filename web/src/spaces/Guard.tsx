import { Snapshot } from "../api"
import { DeadCode } from "../views/DeadCode"
import { DiffView } from "../views/DiffView"
import { Dsm } from "../views/Dsm"

export const GUARD_SUBS = ["Changes", "Health", "Cleanup"] as const
export type GuardSub = (typeof GUARD_SUBS)[number]

export function Guard({ snap, snapshots, sub, setSub }: {
  snap: number
  snapshots: Snapshot[]
  sub: GuardSub
  setSub: (s: GuardSub) => void
}) {
  return (
    <div className="view" style={{ gap: 8 }}>
      <div className="controls">
        <nav className="lenses">
          {GUARD_SUBS.map((s) => (
            <button key={s} className={s === sub ? "active" : ""}
                    onClick={() => setSub(s)}>{s}</button>
          ))}
        </nav>
      </div>
      {sub === "Changes" && <DiffView snapshots={snapshots} />}
      {sub === "Health" && <Dsm key={snap} snap={snap} />}
      {sub === "Cleanup" && <DeadCode key={snap} snap={snap} />}
    </div>
  )
}
