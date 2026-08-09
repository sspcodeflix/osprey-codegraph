# Security

Osprey is designed to analyze untrusted source code without trusting it,
and to serve enterprises that cannot send code to a third party. This
document describes the trust boundaries, the controls at each, and the
operator's responsibilities for a hardened deployment.

## Trust boundaries and controls

### Ingesting a repository (the untrusted-code boundary)

- **Host allowlist.** Only hosts in `OSPREY_ALLOWED_GIT_HOSTS`
  (default `github.com,gitlab.com`) may be fetched. Internal hosts and
  link-local addresses (e.g. `169.254.169.254`) are rejected, closing
  the obvious SSRF path.
- **URL and ref validation.** URLs must match a strict pattern; refs are
  constrained to a charset that starts with an alphanumeric, which blocks
  git option injection (`--upload-pack=...`) through the ref.
- **Repo names are path-safe.** A repo name becomes a filesystem path
  segment on the worker, so it is validated (`osprey/names.py`) to a
  single segment with no separators, no `..`, and no leading dot or dash,
  at every entry point (API and CLI).
- **Sandboxed indexing.** Remote repositories are indexed in a container
  with the network disabled, install scripts disabled, capabilities
  dropped, and a size cap (`OSPREY_MAX_REPO_MB`, default 500). Set
  `OSPREY_EXECUTOR=container` to sandbox every stage.
- **No shell.** Every subprocess (git, indexers) is invoked with an
  argument vector, never a shell string.

### The API (the query boundary)

- **Read-only by construction.** Every request runs in a `READ ONLY`
  Postgres transaction with a statement timeout; traversals carry depth
  and node caps and report `truncated` rather than failing. No endpoint
  accepts query-language text.
- **Bearer auth, timing-safe.** When `OSPREY_API_TOKEN` is set, every
  endpoint requires it, compared with `hmac.compare_digest`. With no
  token set (dev only), auth is open; **always set a token in any real
  deployment.**
- **Bounded inputs.** Request bodies have length limits (URL, question,
  history size and per-message length, and the on-screen context hint)
  to bound token spend and memory.
- **Parameterized SQL only.** All queries use bound parameters.

### The AI surfaces (the prompt boundary)

- **The model never writes a query.** Ask and MCP expose typed tools;
  the model fills validated arguments and nothing else.
- **History cannot rewrite the rules.** Client-supplied chat history is
  constrained to `user`/`assistant` turns and re-filtered server-side,
  so a caller cannot inject a `system` turn to bypass the scope guardrail
  or fabricate tool results.
- **Scope guardrail.** Ask refuses anything that is not about the indexed
  codebase, and refuses to write new code.
- **Grounded output.** Generated docs are cite-checked against the graph
  before publishing; diagrams are compiled from edges, never drawn by the
  model.
- **Rendering.** Model output is rendered as Markdown with raw HTML
  disabled and URL protocols sanitized; server-compiled diagrams render
  with Mermaid `securityLevel: strict`.

### Local-first data handling

- The default chat model runs locally via Ollama; hosted providers
  (DeepSeek, Anthropic) are opt-in per deployment. Doc-search embeddings
  are always computed locally. Nothing about a repository leaves the host
  unless an operator explicitly configures a hosted provider.

## Operator responsibilities

These are deployment-level controls Osprey cannot enforce for you:

1. **Set `OSPREY_API_TOKEN`.** An unset token means no auth. For the
   public demo posture, also set `OSPREY_DEMO_MODE=true` (disables direct
   indexing and doc generation; visitors file requests instead).
2. **Terminate TLS at a reverse proxy.** Osprey binds loopback by
   default; expose it only through a proxy that adds HTTPS.
3. **Rate-limit at the proxy.** Osprey does not rate-limit itself. Put a
   limit on `/v1/ask`, `/v1/repos/index`, and `/v1/repo-requests` to
   bound abuse and token spend.
4. **Keep `OSPREY_EXECUTOR=container`** for any instance that indexes
   repositories you do not control.
5. **Rotate the token / access code** if it is shared for a demo.

## Reporting

Found something? Please report privately to the maintainer rather than
opening a public issue.
