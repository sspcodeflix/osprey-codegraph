# LinkedIn post (guided-story draft, screenshots inline)

Let me tell you about a moment every engineer knows.

It's your first week on a new team. Someone hands you a link to the wiki
and says "everything's in there." It isn't. The architecture page was
written two years and four refactors ago. The diagram is a photo of a
whiteboard. By Thursday you've learned the real documentation system:
you ask Priya, because Priya wrote it. And you quietly wonder what
happens when Priya leaves.

Maybe you've tried the modern fix: point an AI at the repo and let it
write the wiki. I did. The prose came out beautiful and confident, and
some of it was simply false. An invented function here, a citation to a
file that doesn't exist there. Here's what bothered me most: between
"the model wrote it" and "you read it", there was no checking step at
all. We would never ship code that way. We ship documentation that way
every day.

The code is verified by compilers and tests on every commit. The prose
about the code is verified by nobody. That gap is the whole problem.

So I spent the last stretch building Osprey, a tool with one
non-negotiable rule: if a sentence about your code can't cite the code,
it doesn't get published.

Let me walk you through it the way I'd show a friend.

You paste a repository URL. Osprey fetches it into a sandbox (network
off, install scripts disabled) and builds a compiler-grade map of it:
every symbol resolved by real type checkers, every call and import
recorded as an edge, all of it stored as an immutable snapshot of that
exact commit. A few minutes later you land on this:

**[Screenshot 1: the Overview dashboard]**

That's mlflow, 858 files and about 209,000 lines of Python, distilled to
one screen: what it's made of, where the circular dependencies are, how
much code is likely dead, and which functions the whole codebase leans
on. Every number on that page comes from the graph, not from a model's
impression of the repo.

Click into the map and the graph becomes something you can actually
walk. Folders are circles, arrows show who uses whom, and when you focus
one, Osprey explains it in plain language: mlflow's tracking folder is
used by 22 of the 27 others, which makes it a public-API layer whose
changes ripple widely, and it leans hardest on utils (109 calls). No
graph-reading skills required.

**[Screenshot 2: the dependency map with a focused folder]**

Now the part I built all of this for: the documentation.

Osprey writes docs from that graph, not from vibes. The outline is
computed from real structure. A language model drafts the prose, but it
drafts under supervision: every single file:line citation is checked
against the snapshot before publishing. Claims that fail get one chance
to be corrected, then they're stripped. The diagrams are compiled
directly from the dependency edges; the model is not allowed to draw an
arrow. And the same facts are written three ways, because a new
developer, an on-call SRE, and a QA engineer are not asking the same
questions.

**[Screenshot 3: a documentation page, citations verified, with its
compiled diagram]**

See that "citations verified" badge? That's not decoration. On one
regeneration the verifier caught the model slipping internal jargon into
the prose twice, and fixed both before anyone read it. A full verified
doc set for a small library costs about half a cent in tokens.

There's a chat too, floating on every page, and it plays by the same
rules. It can only answer through typed graph tools, it shows you which
checks it ran, and it knows what you are looking at: select a function
and ask "what breaks if I change this?" and it resolves "this" to your
selection, then actually traces it. I asked exactly that about mlflow's
start_run and it came back with 1,295 affected symbols within three
hops, file and line attached to every claim. Then I asked it to write me
a sorting script, and it politely declined. It answers questions about
your codebase. That's the job. That's the only job.

**[Screenshot 4: the Ask panel, showing its answer and the checks it
ran]**

The graph does one more job most doc tools never attempt: it stands
guard. You declare architecture rules in a small YAML file: these layers
must not import those, this dependency is forbidden, no new circular
dependencies. In CI, osprey-gate diffs two snapshots of your repo and
fails the pull request if the structure regressed, with the offending
file and line as evidence, formatted as a ready-to-post PR comment. I
planted a deliberate violation to test it: a utility module reaching
into a layer above itself. Caught, named, and pointed to the exact line.
Think of it as code review for architecture, running on every PR, and it
never gets tired.

And here's my favorite part, the one that took the longest to earn:
these docs cannot go stale. When a new version of the repo is indexed,
Osprey diffs it against the last documented one and rewrites only the
pages whose underlying facts changed. Everything untouched carries
forward as-is. When I re-indexed an unchanged repo as a test, all 18
pages refreshed for exactly zero LLM tokens. The cost of keeping docs
current scales with your diff, not your repo.

For the architecture-curious, the whole machine looks like this:

**[Screenshot 5: the architecture diagram]**

One more thing that matters to a lot of teams: all of this runs on your
own hardware. The default model is local via Ollama, embeddings never
leave the machine, and air-gapped deployment is the normal case, not the
enterprise edition. It also plugs into the tools you already use: the
same graph is exposed over MCP, so Claude, Cursor, or Copilot in your
editor can ask Osprey "who calls this" and get a verified answer instead
of a guess. There are genuinely good tools in this space: DeepWiki is
impressive for public repos but routes private ones through its cloud
with no verification step; Swimm keeps human-written docs honest but you
still write them yourself. I wanted the combination nobody offered:
generated docs, machine-verified claims, self-healing pages, governed
architecture, all on my own machine.

The facts I care about most, all measured on real repositories:

- **Verified, not plausible.** Every doc claim is checked `file:line`
  against the graph before publishing; unverifiable claims are stripped.
- **Self-healing.** Re-indexing an unchanged repo refreshed all 18 doc
  pages for zero LLM tokens; cost scales with the diff, not the repo.
- **Cheap.** A full verified doc set for a small library: about half a
  cent.
- **Grounded chat.** "What breaks if I change this?" traced 1,295
  affected symbols with file and line on every claim.
- **Governance in CI.** A planted architecture violation was caught with
  the exact offending line.
- **Private by default.** Local model, local embeddings, sandboxed
  indexing, air-gap ready; clean on `bandit` and `npm audit`.

I'm hosting a small demo instance now. If you'd like to poke around,
comment or DM me and I'll send you an invite code. And if you want to
see your own repository in it, there's a request form right inside the
app: paste the URL, leave a contact, and I'll index it for you
personally. White-glove on purpose. At this stage I want the
conversation more than the automation.

The code graph is the source of truth. Documentation is just its most
readable projection.

#SoftwareEngineering #DeveloperTools #AI #Documentation #LocalFirst

---

*Attachment order for the markers above:*
*1 = 1-overview.png · 2 = 2-explore-map.png · 3 = 3-docs-diagram.png
(or 3b-docs-verified.png) · 4 = 4-ask-guardrail.png ·
5 = 0-architecture.png. Optional extras: 5-demo-login.png,
6-demo-request.png.*

---

## ChatGPT prompt: detailed architecture diagram

Paste the block below into ChatGPT (image generation) to produce a
richer architecture diagram. Adjust the size or style lines to taste.

```
Create a detailed software architecture diagram as a wide (16:9),
high-resolution image. Style: modern dark dashboard aesthetic; deep
navy background (#12162b); rounded rectangular cards in slightly
lighter navy (#1a2038) with subtle borders (#2c3254); primary accent
purple (#7269ef) for arrows and highlighted borders; red (#f0555a)
reserved ONLY for the verification step and one footer note; white to
light-lavender text (#eef0fb headings, #a3aacb secondary). Clean
sans-serif labels, generous spacing, left-to-right flow. No clip art,
no 3D, no gradients except a subtle purple gradient on the title.

Title (top left): "Osprey: how it works", subtitle: "Compiler-grade
code graph in, verified documentation and governed answers out.
Self-hosted, local-first."

Draw these components left to right, connected by purple arrows:

1. Card "Repositories": lines "GitHub / GitLab URL (tag or branch)"
   and "Local checkout".

2. Large card "Indexing worker" with a red caption "sandboxed: network
   off, no install scripts, 500 MB cap" and four numbered inner chips:
   "1. shallow fetch", "2. SCIP indexers (scip-python, scip-typescript:
   real type checkers)", "3. tree-sitter classifier (CALLS vs
   REFERENCES)", "4. atomic snapshot publish".

3. Database cylinder "Postgres + pgvector: one system of record" with
   lines: "immutable per-commit snapshots", "symbols, CALLS/IMPORTS
   edges", "module graph, entry points", "doc pages, local embeddings",
   "snapshot delete = one cascade".

4. Below the database, a card "Docs pipeline (per persona: Developer,
   SRE/On-call, QA Tester)" with a four-step flow inside: "outline from
   graph" -> "synthesize (LLM)" -> "verify every file:line citation"
   (this chip outlined in red) -> "publish + embed". Two notes under
   it: "unverifiable claims: retried, then stripped" (red) and
   "diagrams compiled from edges, never drawn by the model". Add a
   small side card "LLM provider: Ollama (local, air-gap OK), DeepSeek
   / Anthropic (opt-in)" feeding into the synthesize step. Draw a
   circular arrow pair between this pipeline and the database labeled
   "facts out, verified pages in". Add one more note chip on this card:
   "staleness loop: on every new index, a structural diff rewrites only
   pages whose inputs changed; unchanged pages carry at zero tokens".

5. Card "Typed read-only API": lines "bounded traversals", "statement
   timeouts", "the LLM never writes a query".

6. Rightmost column, three stacked cards fed by the API:
   - "Web UI" (purple border): "Overview dashboard", "dependency map
     with drill-down", "blast radius", "persona docs", "Ask: guarded,
     cite-backed chat".
   - "osprey-gate (CI)": "layers, deny rules, no new cycles",
     "violations with file:line evidence", "PR-comment output".
   - "MCP server (AI agents)": "11 typed graph tools", "capped
     results with provenance".

Footer, small red square bullet then white text: "Every documentation
claim is verified against the graph before it publishes. If it can't
cite, it doesn't ship."
```
