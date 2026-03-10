"""sampling/createMessage → claude -p subprocess invocation."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import TYPE_CHECKING

import mcp.types as mcp_types
from mcp.shared.exceptions import McpError

if TYPE_CHECKING:
    from mcp_sampling_proxy.config import Config


def _raise(code: int, message: str) -> None:
    raise McpError(mcp_types.ErrorData(code=code, message=message))

# claude stop_reason → MCP stopReason
_STOP_REASON_MAP: dict[str, str] = {
    "end_turn": "endTurn",
    "tool_use": "toolUse",
    "max_tokens": "maxTokens",
    "stop_sequence": "stopSequence",
}


def _debug(config: Config, msg: str) -> None:
    if config.debug:
        print(f"[sampling] {msg}", file=sys.stderr)


class SamplingExecutor:
    def __init__(self, config: Config) -> None:
        self._config = config

    async def execute(
        self, params: mcp_types.CreateMessageRequestParams
    ) -> mcp_types.CreateMessageResult:
        """Execute a sampling request by delegating to claude -p."""
        config = self._config

        # Extract last user message as the -p prompt argument
        if not params.messages:
            _raise(mcp_types.INTERNAL_ERROR, "No messages in sampling request")

        last_msg = params.messages[-1]
        if isinstance(last_msg.content, str):
            prompt_text = last_msg.content
        elif isinstance(last_msg.content, mcp_types.TextContent):
            prompt_text = last_msg.content.text
        else:
            # Content is a list of content blocks
            prompt_text = " ".join(
                block.text
                for block in last_msg.content
                if isinstance(block, mcp_types.TextContent)
            )

        # Build claude -p args
        args = [
            "-p",
            prompt_text,
            "--output-format",
            "stream-json",
            "--verbose",
            "--max-turns",
            "1",
            "--no-session-persistence",
            "--permission-mode",
            "bypassPermissions",
            "--dangerously-skip-permissions",
        ]

        if params.systemPrompt:
            args.extend(["--system-prompt", params.systemPrompt])

        _debug(config, f"spawning: {config.claude_path} {' '.join(args[:6])}...")

        # Build prior messages as stream-json lines for stdin
        prior_lines: list[str] = []
        for msg in params.messages[:-1]:
            if isinstance(msg.content, str):
                content_blocks = [{"type": "text", "text": msg.content}]
            elif isinstance(msg.content, mcp_types.TextContent):
                content_blocks = [{"type": "text", "text": msg.content.text}]
            else:
                content_blocks = [
                    {"type": "text", "text": block.text}
                    for block in msg.content
                    if isinstance(block, mcp_types.TextContent)
                ]

            line = json.dumps(
                {
                    "type": msg.role,
                    "message": {
                        "role": msg.role,
                        "content": content_blocks,
                    },
                }
            )
            prior_lines.append(line)

        stdin_data = ("\n".join(prior_lines) + "\n").encode() if prior_lines else b""

        try:
            proc = await asyncio.create_subprocess_exec(
                config.claude_path,
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            _raise(
                mcp_types.INTERNAL_ERROR,
                f"claude binary not found at '{config.claude_path}'. "
                "Install Claude Code: https://docs.anthropic.com/en/docs/claude-code",
            )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=stdin_data),
                timeout=config.sampling_timeout_s,
            )
        except asyncio.TimeoutError:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                proc.kill()
            _raise(
                mcp_types.INTERNAL_ERROR,
                f"claude -p timed out after {config.sampling_timeout_s}s",
            )

        if proc.returncode != 0:
            stderr_text = stderr_bytes.decode(errors="replace").strip()
            _raise(
                mcp_types.INTERNAL_ERROR,
                f"claude -p exited with code {proc.returncode}: {stderr_text}",
            )

        # Parse stream-json output: newline-delimited JSON objects
        stdout_text = stdout_bytes.decode(errors="replace")
        assistant_message = None

        for line in stdout_text.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            if obj.get("type") == "assistant":
                assistant_message = obj.get("message", {})
                break

        if not assistant_message:
            _debug(config, f"claude output: {stdout_text[:500]}")
            _raise(
                mcp_types.INTERNAL_ERROR,
                "No assistant message found in claude -p output",
            )

        # Extract content
        raw_content = assistant_message.get("content", [])
        text_parts: list[str] = []
        tool_use_blocks: list[dict] = []

        for block in raw_content:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_use_blocks.append(block)

        # Map stop_reason
        raw_stop = assistant_message.get("stop_reason", "end_turn")
        stop_reason = _STOP_REASON_MAP.get(raw_stop, "endTurn")

        # Build content: if tool_use blocks exist, serialize them as JSON text
        if tool_use_blocks and stop_reason == "toolUse":
            content = mcp_types.TextContent(
                type="text",
                text=json.dumps({"tool_use": tool_use_blocks}),
            )
        else:
            content = mcp_types.TextContent(
                type="text",
                text=" ".join(text_parts) if text_parts else "",
            )

        model = assistant_message.get("model", "unknown")
        _debug(config, f"sampling complete: model={model}, stop={stop_reason}")

        return mcp_types.CreateMessageResult(
            role="assistant",
            content=content,
            model=model,
            stopReason=stop_reason,
        )
