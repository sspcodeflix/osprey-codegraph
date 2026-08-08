import { Snapshot } from "../api"
import { DeadCode } from "../views/DeadCode"
import { DiffView } from "../views/DiffView"
import { Dsm } from "../views/Dsm"

export const GUARD_SUBS = ["Changes", "Health", "Cleanup"] as const
export type GuardSub = (typeof GUARD_SUBS)[number]

export function Guard({ snap, snapshots, sub }: {
  snap: number
  snapshots: Snapshot[]
  sub: GuardSub
}) {
  return (
    <div className="view" style={{ gap: 8 }}>
      {sub === "Changes" && <DiffView snapshots={snapshots} />}
      {sub === "Health" && <Dsm key={snap} snap={snap} />}
      {sub === "Cleanup" && <DeadCode key={snap} snap={snap} />}
    </div>
  )
}
