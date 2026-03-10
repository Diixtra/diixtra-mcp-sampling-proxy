"""CLI args + env var parsing."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    upstream_url: str
    claude_path: str = "claude"
    sampling_timeout_s: int = 120
    debug: bool = False


def load_config() -> Config:
    parser = argparse.ArgumentParser(description="MCP sampling proxy")
    parser.add_argument("--upstream-url", help="URL of upstream MCP server")
    parser.add_argument("--claude-path", help="Path to claude binary")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    upstream_url = args.upstream_url or os.environ.get("UPSTREAM_URL")
    if not upstream_url:
        print("Error: --upstream-url or UPSTREAM_URL is required", file=sys.stderr)
        sys.exit(1)

    claude_path = args.claude_path or os.environ.get("CLAUDE_PATH", "claude")
    sampling_timeout_s = int(os.environ.get("SAMPLING_TIMEOUT_S", "120"))
    debug = args.debug or os.environ.get("DEBUG", "").strip() in ("1", "true")

    return Config(
        upstream_url=upstream_url,
        claude_path=claude_path,
        sampling_timeout_s=sampling_timeout_s,
        debug=debug,
    )
