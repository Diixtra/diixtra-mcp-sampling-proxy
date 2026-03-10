"""Entrypoint — wires components, signal handling."""

from __future__ import annotations

import asyncio
import signal
import sys

from mcp_sampling_proxy.config import load_config
from mcp_sampling_proxy.proxy_server import ProxyServer
from mcp_sampling_proxy.sampling import SamplingExecutor
from mcp_sampling_proxy.upstream import UpstreamClient


async def _run() -> None:
    config = load_config()

    if config.debug:
        print("[main] starting mcp-sampling-proxy", file=sys.stderr)
        print(f"[main] upstream: {config.upstream_url}", file=sys.stderr)

    sampling = SamplingExecutor(config)
    upstream = UpstreamClient(config)

    try:
        tools = await upstream.connect(sampling)
    except Exception as e:
        print(f"Error: failed to connect to upstream: {e}", file=sys.stderr)
        sys.exit(1)

    if config.debug:
        print(f"[main] discovered {len(tools)} tools", file=sys.stderr)

    proxy = ProxyServer(config)
    proxy.register_tools(tools, upstream)

    # Graceful shutdown on signals
    loop = asyncio.get_running_loop()

    async def shutdown() -> None:
        if config.debug:
            print("[main] shutting down...", file=sys.stderr)
        await upstream.disconnect()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig, lambda: asyncio.ensure_future(shutdown())
        )

    await proxy.start()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
