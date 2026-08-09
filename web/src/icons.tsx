import { ReactNode } from "react"

// inline stroke icons: crisp at any DPI, colored via currentColor, and no
// icon-font or network dependency
function Icon({ children, size = 17 }: { children: ReactNode; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth={1.8} strokeLinecap="round"
         strokeLinejoin="round" aria-hidden="true">
      {children}
    </svg>
  )
}

// dashboard tiles
export function OverviewIcon() {
  return (
    <Icon>
      <rect x="3" y="3" width="8" height="10" rx="2" />
      <rect x="13" y="3" width="8" height="6" rx="2" />
      <rect x="13" y="11" width="8" height="10" rx="2" />
      <rect x="3" y="15" width="8" height="6" rx="2" />
    </Icon>
  )
}

// nodes joined by edges: the graph
export function ExploreIcon() {
  return (
    <Icon>
      <circle cx="6" cy="12" r="3" />
      <circle cx="18" cy="5" r="3" />
      <circle cx="18" cy="19" r="3" />
      <line x1="8.7" y1="10.4" x2="15.3" y2="6.6" />
      <line x1="8.7" y1="13.6" x2="15.3" y2="17.4" />
    </Icon>
  )
}

// open book
export function DocsIcon() {
  return (
    <Icon>
      <path d="M2 4.5h6.5A3.5 3.5 0 0 1 12 8v12.5a3 3 0 0 0-3-3H2z" />
      <path d="M22 4.5h-6.5A3.5 3.5 0 0 0 12 8v12.5a3 3 0 0 1 3-3h7z" />
    </Icon>
  )
}

export function SearchIcon() {
  return (
    <Icon size={14}>
      <circle cx="11" cy="11" r="7" />
      <line x1="16.2" y1="16.2" x2="21" y2="21" />
    </Icon>
  )
}

export function PlusIcon() {
  return (
    <Icon size={14}>
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </Icon>
  )
}

// collapse the sidebar: panel with a left-pointing chevron
export function CollapseIcon() {
  return (
    <Icon size={16}>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <line x1="9" y1="4" x2="9" y2="20" />
      <path d="M16 9l-3 3 3 3" />
    </Icon>
  )
}

// hotspots section marker (replaces the 🔥 emoji)
export function FlameIcon() {
  return (
    <Icon size={16}>
      <path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.07-2.14-.22-4.05 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.15.43-2.29 1-3a2.5 2.5 0 0 0 2.5 2.5z" />
    </Icon>
  )
}

// reopen the sidebar (shown in the topbar when hidden)
export function PanelIcon() {
  return (
    <Icon size={16}>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <line x1="9" y1="4" x2="9" y2="20" />
    </Icon>
  )
}
