# mcp-sampling-proxy — Implementation Spec

**Issue:** [Diixtra/diixtra-forge#742](https://github.com/Diixtra/diixtra-forge/issues/742)
**ADR:** [012-mcp-sampling-proxy](https://github.com/Diixtra/diixtra-docs/blob/main/adr/012-mcp-sampling-proxy.md)
**Date:** 2026-03-09
**Updated:** 2026-03-10 (migrated from Node.js/TypeScript to Python)

## Overview

A lightweight MCP proxy that sits between Claude Code and an upstream agentic
MCP server (e.g. dot-ai), implementing the `sampling/createMessage` capability
by delegating LLM inference to `claude -p`.

```
Claude Code ←stdio→ mcp-sampling-proxy ←streamable HTTP→ upstream (AI_PROVIDER=host)
                           ↓
                    claude -p (subprocess per sampling request)
```

## Architecture

Single Python asyncio process running two MCP protocol stacks:

- **MCP Server (stdio)** — exposes upstream tools to Claude Code
- **MCP Client (streamable HTTP)** — connects to upstream, discovers tools,
  handles `sampling/createMessage` requests

### Data flow

1. Claude Code connects to proxy via stdio
2. Proxy connects to upstream via streamable HTTP, discovers tools
3. Proxy re-registers each upstream tool on its stdio server
4. When Claude Code calls a tool, proxy forwards to upstream via `call_tool`
5. During tool execution, upstream may send `sampling/createMessage`
6. Proxy spawns `claude -p` subprocess, returns LLM response to upstream
7. Upstream may send more sampling requests (tool loop) — proxy handles each
8. Eventually upstream returns the tool result, proxy returns it to Claude Code

## Project Structure

```
src/mcp_sampling_proxy/
├── __init__.py        # Package entry, re-exports main()
├── __main__.py        # Entrypoint — wires components, signal handling
├── config.py          # CLI args + env var parsing
├── upstream.py        # MCP Client → upstream (connect, discover tools, call_tool)
├── proxy_server.py    # MCP Server → Claude Code (register tools, stdio transport)
├── sampling.py        # sampling/createMessage → claude -p → response
└── types.py           # Shared dataclasses and re-exports
```

## Components

### config.py

```python
@dataclass(frozen=True)
class Config:
    upstream_url: str           # Required. URL of upstream MCP server
    claude_path: str = "claude" # Path to claude binary
    sampling_timeout_s: int = 120
    debug: bool = False
```

**Sources (in priority order):**

| Config | CLI flag | Env var | Default |
|--------|----------|---------|---------|
| upstream_url | `--upstream-url` | `UPSTREAM_URL` | (required) |
| claude_path | `--claude-path` | `CLAUDE_PATH` | `claude` |
| sampling_timeout_s | — | `SAMPLING_TIMEOUT_S` | `120` |
| debug | `--debug` | `DEBUG=1` | `false` |

Fail fast with clear error if `upstream_url` is missing.

### upstream.py — UpstreamClient

Owns the `ClientSession` connected to the upstream MCP server.

```python
class UpstreamClient:
    async def connect(self, sampling: SamplingExecutor) -> list[DiscoveredTool]
    async def call_tool(self, name: str, arguments: dict) -> CallToolResult
    async def disconnect(self) -> None
```

**Key details:**
- `sampling_callback` passed to `ClientSession` constructor BEFORE `initialize()`
- Tool discovery uses paginated `list_tools` cursor loop
- Transport: `streamable_http_client` from `mcp.client.streamable_http`

**SDK imports:**
```python
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
```

### proxy_server.py — ProxyServer

Uses the low-level `Server` API to expose tools to Claude Code via stdio.
The low-level API allows raw JSON schema pass-through without pydantic conversion
— critical for transparent proxying.

```python
class ProxyServer:
    def register_tools(self, tools: list[DiscoveredTool], upstream: UpstreamClient) -> None
    async def start(self) -> None
```

**Key details:**
- `@server.list_tools()` returns `list[types.Tool]` with raw `inputSchema` dicts
- `@server.call_tool()` forwards to `upstream.call_tool(name, args)` and returns content
- Transport: `mcp.server.stdio.stdio_server()`

**SDK imports:**
```python
from mcp.server.lowlevel import Server, NotificationOptions
from mcp.server.models import InitializationOptions
import mcp.server.stdio
```

**Critical:** Never write to stdout directly — it corrupts the MCP protocol
stream. All debug output goes to stderr.

### sampling.py — SamplingExecutor

The core complexity. Converts MCP `sampling/createMessage` requests into
`claude -p` subprocess invocations.

```python
class SamplingExecutor:
    def __init__(self, config: Config) -> None
    async def execute(self, params: CreateMessageRequestParams) -> CreateMessageResult
```

#### Subprocess invocation

```bash
claude -p <last_user_message> \
  --output-format stream-json \
  --verbose \
  --max-turns 1 \
  --no-session-persistence \
  --permission-mode bypassPermissions \
  --dangerously-skip-permissions
```

Add `--system-prompt <systemPrompt>` if `params.systemPrompt` is present.

#### Message handling

1. Extract last user message text from `params.messages` as the `-p` prompt arg
2. Write prior messages (all but last) to subprocess stdin as stream-json lines:
   ```json
   {"type":"user","message":{"role":"user","content":[{"type":"text","text":"..."}]}}
   {"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"..."}]}}
   ```
3. Close stdin after writing
4. Collect stdout, parse newline-delimited JSON objects
5. Find `type: "assistant"` message → extract `message.content`, `message.model`,
   `message.stop_reason`

#### stop_reason mapping

| claude stop_reason | MCP stopReason |
|---|---|
| `end_turn` | `endTurn` |
| `tool_use` | `toolUse` |
| `max_tokens` | `maxTokens` |
| `stop_sequence` | `stopSequence` |

#### Content mapping

MCP `CreateMessageResult.content` expects a single content block or array.
- Text responses: `TextContent(type="text", text="...")`
- Multiple text blocks: concatenate into single text block
- `tool_use` blocks (when `stopReason: "toolUse"`): serialize as JSON text so
  upstream can parse:
  ```json
  {"type": "text", "text": "{\"tool_use\":[{\"id\":\"...\",\"name\":\"...\",\"input\":{...}}]}"}
  ```

#### Error handling

- **Timeout:** `asyncio.wait_for` + `proc.terminate()`, fallback `proc.kill()`
  after 2s. Raise `McpError` with `INTERNAL_ERROR`.
- **Non-zero exit:** Raise `McpError` with stderr content as message.
- **claude not in PATH:** Raise `McpError` with clear installation guidance.
- **No assistant message in output:** Raise `McpError`.

#### Subprocess isolation

```python
proc = await asyncio.create_subprocess_exec(
    config.claude_path, *args,
    stdin=asyncio.subprocess.PIPE,   # Do NOT inherit parent stdio
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
)
```

### __main__.py — Startup sequence

```python
async def _run() -> None:
    config = load_config()
    sampling = SamplingExecutor(config)
    upstream = UpstreamClient(config)
    tools = await upstream.connect(sampling)

    proxy = ProxyServer(config)
    proxy.register_tools(tools, upstream)

    # Graceful shutdown on SIGINT/SIGTERM
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.ensure_future(shutdown()))

    await proxy.start()
```

## Dependencies

```toml
[project]
requires-python = ">=3.11"
dependencies = [
    "mcp>=1.9.0",
    "httpx>=0.28.0",
]
```

## Claude Code MCP config

Development:
```json
{
  "mcpServers": {
    "dot-ai": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/diixtra-mcp-sampling-proxy", "mcp-sampling-proxy"],
      "env": { "UPSTREAM_URL": "http://localhost:30106/mcp" }
    }
  }
}
```

Production (after `uv tool install`):
```json
{
  "mcpServers": {
    "dot-ai": {
      "command": "mcp-sampling-proxy",
      "env": { "UPSTREAM_URL": "http://localhost:30106/mcp" }
    }
  }
}
```

## Testing plan

### Unit tests (mock claude -p)
- Sampling handler correctly builds subprocess args
- Sampling handler parses stream-json output into CreateMessageResult
- stop_reason mapping is correct
- Timeout triggers SIGTERM
- Error cases return proper McpError

### Integration test (mock upstream)
- Spin up a mock MCP server that exposes a tool and sends sampling requests
- Verify tool discovery and registration
- Verify tool call forwarding
- Verify sampling round-trip

### Manual validation
- Configure in Claude Code MCP config pointing to dot-ai on `localhost:30106`
- Verify tools appear in Claude Code
- Call a simple tool (e.g. cluster info query)
- Call an agentic tool (remediate/operate) that triggers sampling
- Confirm sampling round-trips complete and tool returns result

## Build sequence

### Phase 1: Project skeleton
- [x] `pyproject.toml` with dependencies
- [x] `uv sync`
- [x] `src/mcp_sampling_proxy/types.py` with shared dataclasses
- [x] `src/mcp_sampling_proxy/config.py` with `load_config()`
- [x] Verify imports pass

### Phase 2: Sampling executor
- [x] `src/mcp_sampling_proxy/sampling.py` — SamplingExecutor class
- [ ] Verify `claude -p --output-format stream-json` output structure
- [x] Handle timeout, non-zero exit, missing binary
- [x] Map stop_reason and content correctly

### Phase 3: Upstream client
- [x] `src/mcp_sampling_proxy/upstream.py` — UpstreamClient class
- [x] Register sampling callback before initialize()
- [x] Paginated list_tools
- [x] call_tool pass-through

### Phase 4: Proxy server
- [x] `src/mcp_sampling_proxy/proxy_server.py` — ProxyServer class
- [x] Dynamic tool registration with raw JSON Schema (low-level Server API)
- [x] Stdio transport

### Phase 5: Integration wiring
- [x] `src/mcp_sampling_proxy/__main__.py` startup + signal handlers
- [x] All imports pass cleanly
- [ ] End-to-end test with dot-ai

### Phase 6: Hardening
- [x] Debug logging (stderr only, gated by config.debug)
- [ ] Upstream reconnection on transport errors
- [ ] README.md

---

## Companion: dot-ai deployment in diixtra-forge

**Issue:** [Diixtra/diixtra-forge#738](https://github.com/Diixtra/diixtra-forge/issues/738)

### Files to create

#### `apps/base/mcp-servers/dot-ai/rbac.yaml`

ServiceAccount + ClusterRole + ClusterRoleBinding. **Read-only** access:

| API Group | Resources | Verbs |
|-----------|-----------|-------|
| `""` (core) | pods, pods/log, services, endpoints, namespaces, configmaps, persistentvolumeclaims, serviceaccounts, nodes, events, persistentvolumes, replicationcontrollers | get, list, watch |
| `apps` | deployments, daemonsets, replicasets, statefulsets | get, list, watch |
| `batch` | jobs, cronjobs | get, list, watch |
| `networking.k8s.io` | ingresses, networkpolicies | get, list, watch |
| `rbac.authorization.k8s.io` | roles, rolebindings, clusterroles, clusterrolebindings | get, list, watch |
| `storage.k8s.io` | storageclasses | get, list, watch |
| `helm.toolkit.fluxcd.io` | helmreleases | get, list, watch |
| `kustomize.toolkit.fluxcd.io` | kustomizations | get, list, watch |
| `source.toolkit.fluxcd.io` | gitrepositories, helmrepositories | get, list, watch |

**No access to:** Secrets (prevents credential exfiltration)

#### `apps/base/mcp-servers/dot-ai/deployment.yaml`

- **Image:** `ghcr.io/vfarcic/dot-ai:1.7.0` (kubectl + helm baked in, ghcr.io passes registry policy)
- **Port:** 3456 (native HTTP transport)
- **Env:** `AI_PROVIDER=host`, `PORT=3456`, `HOST=0.0.0.0`
- **ServiceAccount:** `dot-ai`
- **Security context:** runAsNonRoot, runAsUser/Group 1000, fsGroup 1000, drop ALL, seccompProfile RuntimeDefault
- **Resources:** 100m–500m CPU, 256Mi–512Mi memory
- **Probes:** startup (httpGet /healthz, 150s grace), readiness (httpGet /healthz, 30s initial delay)

#### `apps/base/mcp-servers/dot-ai/service.yaml`

- **Type:** NodePort
- **Port:** 3456 → 3456
- **NodePort:** 30106

### Files to modify

| File | Change |
|------|--------|
| `apps/base/mcp-servers/kustomization.yaml` | Add `30106 - dot-ai` comment, add `dot-ai/rbac.yaml`, `dot-ai/deployment.yaml`, `dot-ai/service.yaml` |
| `clusters/dev/apps.yaml` | Add dot-ai Deployment to `healthChecks` |

### Kyverno compliance

| Policy | Status |
|--------|--------|
| `require-resource-limits` (enforce) | Satisfied — CPU + memory set |
| `pod-security-baseline` (enforce) | Satisfied — no privileged, no hostPath |
| `pod-security-restricted` (audit) | Satisfied — runAsNonRoot, drop ALL, seccomp |
| `require-standard-labels` (audit) | Satisfied — managed-by: flux |
| `restrict-image-registries` (audit) | Satisfied — ghcr.io is allowed |

### No vars.yaml changes needed

`AI_PROVIDER=host` means no API keys. All env vars are static.
