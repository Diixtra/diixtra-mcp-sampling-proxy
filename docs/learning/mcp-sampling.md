# MCP Sampling: How MCP Servers Delegate LLM Inference to the Host

## What is MCP Sampling?

MCP (Model Context Protocol) defines a **sampling** capability that allows an
MCP server to request the host client to make an LLM call on its behalf. This
inverts the normal flow where the client calls the server.

### Normal MCP flow

```
User → LLM Client (e.g. Claude Code)
         → calls MCP server tool (e.g. kubectl_get)
         ← gets structured result
         → reasons about result
         → calls next tool
```

The client does all the reasoning. MCP servers are passive tool providers.

### With sampling

```
User → LLM Client
         → calls MCP server tool (e.g. remediate)
              MCP server internally:
                → gathers data (kubectl, logs, events)
                → sends sampling/createMessage to client
                ← client makes LLM call, returns response
                → server uses response to decide next step
                → repeats until investigation is complete
              ← returns final answer
```

The MCP server runs its own reasoning loop, but delegates the actual LLM
inference back to the client. The server never needs its own API key.

## Why it matters

Without sampling, an MCP server that needs AI reasoning must bring its own LLM
credentials. This means:

- **Duplicate billing**: You pay for the host's LLM subscription AND the
  server's API calls.
- **Key management**: Each agentic MCP server needs its own API key, rotation,
  and secret management.
- **Model mismatch**: The server picks its own model (often a cheaper one),
  which may be less capable than the model the host is running.

With sampling:

- **Zero extra cost**: LLM calls route through the host's existing
  subscription.
- **No keys**: The server has no LLM credentials to manage.
- **Host controls the model**: The host decides which model handles the
  request.
- **Human-in-the-loop**: The host can optionally show sampling requests to the
  user for approval.

## The protocol

The MCP specification defines sampling as a client capability. During the MCP
handshake, the client declares:

```json
{
  "capabilities": {
    "sampling": {}
  }
}
```

The server can then send `sampling/createMessage` requests:

```json
{
  "method": "sampling/createMessage",
  "params": {
    "messages": [
      { "role": "user", "content": { "type": "text", "text": "Analyze this pod crash..." } }
    ],
    "systemPrompt": "You are a Kubernetes operations expert...",
    "maxTokens": 4096
  }
}
```

The client processes the request, makes the LLM call, and returns:

```json
{
  "role": "assistant",
  "content": { "type": "text", "text": "Based on the OOMKilled status..." },
  "model": "claude-opus-4-6"
}
```

## Current support (March 2026)

| Client | Sampling support |
|--------|-----------------|
| Claude Code | Not yet ([#1785](https://github.com/anthropics/claude-code/issues/1785)) |
| Claude Desktop | Not yet |
| OpenClaw | Uses `claude -p` subprocess instead (different approach) |
| Zed | Partial |
| MCP Inspector | Yes (for testing) |

## Real-world example: dot-ai

[dot-ai](https://github.com/vfarcic/dot-ai) is an agentic MCP server for
Kubernetes operations. Its `HostProvider` (`AI_PROVIDER=host`) implements the
server side of sampling:

```typescript
// dot-ai's HostProvider sends messages via the sampling handler
const result = await HostProvider.samplingHandler!(messages, systemPrompt);
```

When the MCP client registers a sampling handler, dot-ai's internal agentic
loops work without an API key. Without it, the handler is undefined and calls
fail.

## Workaround: The `claude -p` pattern

Until clients implement sampling natively, there's a proven workaround:
shell out to `claude -p` (Claude Code's print/non-interactive mode) as a
subprocess.

[OpenClaw](https://github.com/openclaw/openclaw) pioneered this approach:

```bash
claude -p \
  --output-format json \
  --permission-mode bypassPermissions \
  --model opus \
  "Analyze this Kubernetes pod crash: ..."
```

This uses the local Claude Code installation's auth (subscription or API key),
returns structured JSON, and avoids needing a separate `ANTHROPIC_API_KEY`.

### Building a sampling proxy

We built `diixtra-mcp-sampling-proxy` to bridge this gap:

```
Claude Code ←stdio→ mcp-sampling-proxy ←HTTP→ dot-ai (AI_PROVIDER=host)
                          |
                          | sampling/createMessage
                          ↓
                    claude -p (subprocess)
```

The proxy is simultaneously:
- An **MCP server** (stdio) that Claude Code connects to
- An **MCP client** (HTTP) that connects to dot-ai

When dot-ai sends a `sampling/createMessage`, the proxy catches it, runs
`claude -p` with the prompt, and returns the response. dot-ai's `HostProvider`
works as designed — it just doesn't know the "client" is a proxy shelling out
to a CLI.

See [ADR 012](/adr/012-mcp-sampling-proxy.md) for the full architectural
decision and rationale.

## Key takeaways

1. **Most MCP servers don't need sampling** — they're pure tool providers
   (kubectl, Grafana, Cloudflare). The LLM client does the reasoning.
2. **Agentic MCP servers do** — servers that run autonomous multi-step
   investigations (dot-ai's remediate, operate) need LLM access internally.
3. **Sampling is the clean solution** — defined in the MCP spec, zero extra
   cost, no key management. But client support is still emerging.
4. **`claude -p` is the practical bridge** — proven by OpenClaw at scale,
   works today, and can be removed once native sampling ships.
