# ADR 012: MCP Sampling Proxy for Host-Delegated LLM Inference

**Status:** Accepted
**Date:** 2026-03-09

## Context

We deploy MCP (Model Context Protocol) servers in the homelab cluster to give
Claude Code access to Kubernetes operations, Grafana, Cloudflare, and other
platform tools. Most MCP servers are pure tool providers — they execute
commands and return results. The LLM client (Claude Code) does all the
reasoning.

[dot-ai](https://github.com/vfarcic/dot-ai) is different. It's an **agentic
MCP server** that runs its own multi-step reasoning loops internally. When you
call its `remediate` or `operate` tools, dot-ai autonomously chains 10–20 tool
calls with LLM reasoning between each step — investigating pods, reading logs,
correlating events — before returning a final answer.

This internal reasoning requires LLM access. dot-ai supports two modes:

1. **Direct API** (`AI_PROVIDER=anthropic`): dot-ai calls the Anthropic API
   with its own key. Simple, but duplicates cost on top of the Claude
   subscription.
2. **Host delegation** (`AI_PROVIDER=host`): dot-ai sends
   `sampling/createMessage` requests back to the MCP client, which makes the
   LLM call using the host's existing model and credentials. Zero extra cost.

The MCP specification defines sampling as a first-class capability, but
**Claude Code does not implement `sampling/createMessage` yet** (tracked as
[anthropics/claude-code#1785](https://github.com/anthropics/claude-code/issues/1785)).
This means `AI_PROVIDER=host` fails with "Host provider is not connected to
MCP server".

## Decision

Build `diixtra-mcp-sampling-proxy` — a lightweight Python proxy that sits
between Claude Code and dot-ai, implementing the sampling capability that
Claude Code lacks.

### Architecture

```
Claude Code ←stdio→ mcp-sampling-proxy ←HTTP→ dot-ai (AI_PROVIDER=host)
                          |
                          | sampling/createMessage
                          ↓
                    claude -p (subprocess)
```

The proxy acts as two things simultaneously:

- **MCP server** (stdio transport) — Claude Code connects to it and sees
  dot-ai's tools as if connecting directly.
- **MCP client** (streamable HTTP) — connects to dot-ai's HTTP endpoint,
  discovers and proxies tools, and handles sampling requests.

When dot-ai's `HostProvider` emits a `sampling/createMessage` request, the
proxy intercepts it and shells out to `claude -p --output-format json
--permission-mode bypassPermissions` with the prompt. This pattern is proven
in production by [OpenClaw](https://github.com/openclaw/openclaw), which uses
the same `claude -p` subprocess approach to delegate LLM inference to Claude
Code's subscription.

### Key design choices

- **`claude -p` over API**: Uses the local Claude Code installation's auth
  (subscription or API key). No separate `ANTHROPIC_API_KEY` needed.
- **Stdio transport**: Claude Code connects to the proxy as a local process,
  avoiding network transport complexity.
- **Generic proxy**: Not coupled to dot-ai. Any MCP server that uses sampling
  can work through this proxy.
- **Runs locally**: Since the dev cluster (k3s) runs on the same machine,
  the proxy can reach dot-ai on `localhost:30106`.

## Consequences

### Positive

- dot-ai's full agentic capabilities (remediate, operate) work without a
  separate API key.
- Uses the existing Claude subscription — no additional LLM billing.
- The proxy is generic and reusable for any future MCP server that needs
  sampling.
- No fork of dot-ai required — the proxy is a standalone component.
- When Claude Code eventually implements native sampling, the proxy becomes
  unnecessary and can be removed with no changes to dot-ai.

### Negative

- Additional component to maintain, though it's small (~300 lines of Python).
- Slightly higher latency: each sampling request spawns a `claude -p`
  subprocess (cold start ~1–2s).
- `claude -p` must be available on the machine running the proxy.

### Neutral

- The current PR (#739) deploys dot-ai with `AI_PROVIDER=anthropic` and an
  API key as a working baseline. The proxy will be an alternative that can
  replace the API key approach once validated.

## References

- [MCP Sampling specification](https://modelcontextprotocol.io/specification/draft/client/sampling)
- [Claude Code sampling support request](https://github.com/anthropics/claude-code/issues/1785)
- [dot-ai HostProvider](https://github.com/vfarcic/dot-ai/blob/main/src/core/providers/host-provider.ts)
- [OpenClaw CLI runner](https://github.com/openclaw/openclaw/blob/main/src/agents/cli-runner.ts) — prior art for `claude -p` subprocess pattern
