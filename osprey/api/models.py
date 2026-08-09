"""Response models (§7): the typed contract all three consumers build on."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RepoOut(BaseModel):
    name: str
    org: str
    latest_snapshot: int | None
    ref: str | None = None   # tag/branch of the most recent index job


class SnapshotOut(BaseModel):
    id: int
    commit_sha: str
    status: str
    stats: dict
    created_at: datetime
    ready_at: datetime | None


class SymbolOut(BaseModel):
    id: int
    name: str
    kind: str
    path: str | None
    line: int | None
    is_external: bool


class TraverseNode(BaseModel):
    id: int
    depth: int
    name: str
    kind: str
    path: str | None
    line: int | None = None


class TraverseOut(BaseModel):
    symbol_id: int
    depth: int
    count: int
    truncated: bool
    callers: list[TraverseNode] | None = None
    callees: list[TraverseNode] | None = None


class ImpactOut(BaseModel):
    symbol_id: int
    depth: int
    count: int
    truncated: bool
    impacted: list[TraverseNode]


class SubgraphNode(BaseModel):
    id: int
    depth: int
    name: str
    kind: str
    path: str | None
    is_external: bool


class SubgraphEdge(BaseModel):
    src_id: int
    dst_id: int
    kind: str
    weight: int


class SubgraphOut(BaseModel):
    root: int
    hops: int
    nodes: list[SubgraphNode]
    edges: list[SubgraphEdge]
    truncated: bool


class SequenceStep(BaseModel):
    from_module: str
    to_module: str
    call: str
    site: str


class SequenceOut(BaseModel):
    root: str
    mermaid: str
    steps: list[SequenceStep]
    truncated: bool


class EndpointOut(BaseModel):
    name: str
    kind: str
    entry_kind: str
    path: str
    line: int | None


class DeadCandidate(BaseModel):
    name: str
    kind: str
    path: str
    line: int | None


class DeadcodeOut(BaseModel):
    entry_points: int
    candidates: list[DeadCandidate]
    count: int


class ModuleEdgeOut(BaseModel):
    src_module: str
    dst_module: str
    kind: str
    weight: int


class ModuleNodeOut(BaseModel):
    module: str
    loc: int
    files: int


class ModulesOut(BaseModel):
    nodes: list[ModuleNodeOut]
    edges: list[ModuleEdgeOut]


class CyclesOut(BaseModel):
    count: int
    cycles: list[list[str]]


class EdgeSiteOut(BaseModel):
    src_name: str
    dst_name: str
    kind: str
    path: str | None
    line: int | None


class HotspotOut(BaseModel):
    symbol_id: int
    name: str
    kind: str
    path: str
    line: int | None
    inbound: int


class OverviewOut(BaseModel):
    commit: str
    files: int
    loc: int
    languages: dict[str, int]
    symbols: dict[str, int]
    modules: int
    module_dependencies: int
    cycles: list[list[str]]
    entry_points: int
    deadcode: int | None
    hotspots: list[HotspotOut]


class DiffRef(BaseModel):
    snapshot: int
    commit: str


class DiffEdge(BaseModel):
    src: str
    dst: str
    kind: str


class IndexRequestIn(BaseModel):
    git_url: str = Field(max_length=500)
    name: str | None = Field(default=None, max_length=100)
    ref: str | None = Field(default=None, max_length=120)


class IndexJobOut(BaseModel):
    job_id: int
    repo: str


class JobOut(BaseModel):
    id: int
    status: str
    error: str | None
    repo: str
    snapshot_status: str | None


class DocPageMeta(BaseModel):
    slug: str
    title: str
    position: int
    parent_slug: str | None
    status: str


class DocPageOut(BaseModel):
    slug: str
    title: str
    status: str
    content_md: str
    commit: str
    persona: str


class MeOut(BaseModel):
    user: str
    auth: str
    demo: bool = False


class RepoRequestIn(BaseModel):
    git_url: str = Field(max_length=500)
    ref: str | None = Field(default=None, max_length=120)
    contact: str = Field(min_length=3, max_length=200)
    note: str = Field(default="", max_length=1000)


class RepoRequestOut(BaseModel):
    id: int
    status: str


class DocsGenerateIn(BaseModel):
    snapshot_id: int
    persona: str = "onboarding"


class DocSearchHit(BaseModel):
    source: str
    content: str
    score: float


class AskMessage(BaseModel):
    # role is a closed set: a client must not be able to inject a 'system'
    # turn and overwrite the scope guardrail, nor fabricate 'tool' results
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class AskIn(BaseModel):
    snapshot_id: int
    question: str = Field(min_length=1, max_length=4000)
    history: list[AskMessage] = Field(default_factory=list, max_length=20)


class AskTraceStep(BaseModel):
    tool: str
    args: dict


class AskOut(BaseModel):
    answer: str
    trace: list[AskTraceStep]


class DiffOut(BaseModel):
    base: DiffRef
    head: DiffRef
    edges_added: list[DiffEdge]
    edges_removed: list[DiffEdge]
    symbols_added: list[str]
    symbols_removed: list[str]
