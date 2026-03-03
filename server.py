#!/usr/bin/env python3
"""
PFC Agent Token Killer MCP Server — Customer Edition
=====================================================

Intelligent terminal output filter for Claude Code.
Reduces terminal output by 40-80% → keeps Claude's context fresh longer.

REQUIRES: PATK_API_KEY environment variable
GET YOUR KEY: https://api-production-44c2.up.railway.app

SETUP (Claude Code .mcp.json or settings):
  {
    "patk": {
      "command": "python",
      "args": ["/path/to/server_customer.py"],
      "env": {
        "PATK_API_KEY": "ptk_yourkey..."
      }
    }
  }

TOOLS:
  patk_safe_execute  → Run command + filter output in one step (RECOMMENDED)
  patk_filter_output → Filter already-captured terminal output
  patk_check_credits → Check remaining credits
  patk_status        → Show configuration and session stats

WHY PATK?
  Terminal outputs like npm install, docker build, pytest -v often produce
  100-200 lines of noise. PATK compresses them to 10-20 essential lines.
  This delays Claude's context summarization → better memory in long sessions.

SUB-AGENT USAGE:
  PATK works in sub-agents too (Task tool, Explore agents, parallel agents).
  Sub-agents have their own isolated context + separate API costs.
  Using patk_safe_execute in sub-agents saves THEIR tokens independently.
  → Add to Task tool prompts: "Use patk_safe_execute for all bash commands > 15 lines"
"""

import os
import sys
import asyncio
import httpx
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP


# ── Configuration ──────────────────────────────────────────────────────────────
PATK_API_KEY: str = os.environ.get("PATK_API_KEY", "")
PATK_API_URL: str = os.environ.get(
    "PATK_API_URL",
    "https://api-production-44c2.up.railway.app"
)
MAX_CHUNK_SIZE: int = 190_000  # Stay under Railway's 200KB limit; large inputs auto-chunked

if not PATK_API_KEY:
    print(
        "PATK ERROR: PATK_API_KEY not set.\n"
        "Get your key at: https://api-production-44c2.up.railway.app\n"
        "Set it in your MCP config: \"env\": {\"PATK_API_KEY\": \"ptk_...\"}",
        file=sys.stderr
    )
    # Don't exit — let MCP start but return errors from tools

# Session statistics
_session_calls: int = 0
_session_chars_saved: int = 0

# ── MCP Server ─────────────────────────────────────────────────────────────────
mcp = FastMCP("patk_mcp")


# ── Input Models ───────────────────────────────────────────────────────────────
class FilterInput(BaseModel):
    """Input model for terminal output filtering."""
    model_config = ConfigDict(
        str_strip_whitespace=False,
        validate_assignment=True,
        extra="forbid"
    )

    text: str = Field(
        ...,
        description=(
            "Raw terminal output to filter. "
            "Supports ANSI color codes (auto-removed). "
            "Large inputs (>190KB) are automatically split into chunks."
        ),
        min_length=1,
        max_length=10_000_000  # 10MB MCP limit; auto-chunked at 190KB per Railway call
    )
    max_lines: int = Field(
        default=50,
        description="Maximum output lines to keep (1–500). Default 50.",
        ge=1,
        le=500
    )


class SafeExecuteInput(BaseModel):
    """Input for running a shell command with automatic output filtering."""
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra="forbid"
    )

    command: str = Field(
        ...,
        description=(
            "Shell command to execute. "
            "Examples: 'npm install', 'pytest -v', 'docker build .'"
        ),
        min_length=1,
        max_length=1000
    )
    max_lines: int = Field(
        default=50,
        description="Maximum output lines to keep after filtering (1–500)",
        ge=1,
        le=500
    )
    timeout: int = Field(
        default=60,
        description="Command timeout in seconds (1–600)",
        ge=1,
        le=600
    )
    working_dir: Optional[str] = Field(
        default=None,
        description="Working directory for the command. Defaults to current directory."
    )


# ── Shared Utilities ────────────────────────────────────────────────────────────
def _check_api_key() -> Optional[str]:
    """Returns error string if API key is missing, None if OK."""
    if not PATK_API_KEY:
        return (
            "❌ PATK_API_KEY not configured.\n"
            "Add to your MCP server env: PATK_API_KEY=ptk_yourkey\n"
            "Get your key at: https://api-production-44c2.up.railway.app"
        )
    return None


async def _call_api(text: str, max_lines: int) -> dict:
    """Call PATK Railway API to filter text."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{PATK_API_URL}/token-killer",
            headers={
                "X-API-Key": PATK_API_KEY,
                "Content-Type": "application/json",
            },
            json={"text": text, "max_lines": max_lines},
        )
        response.raise_for_status()
        return response.json()


def _format_stats(
    original_chars: int,
    filtered_chars: int,
    reduction_pct: float,
    original_lines: int,
    filtered_lines: int,
    credits_remaining: Optional[int] = None
) -> str:
    """Format savings summary for response footer."""
    tokens_saved = (original_chars - filtered_chars) // 4
    dollar_saved = tokens_saved * 0.000003

    parts = [
        f"✂️  {reduction_pct}% Reduktion",
        f"{original_lines} → {filtered_lines} Zeilen",
        f"~{tokens_saved:,} Tokens gespart ≈ ${dollar_saved:.4f}",
    ]
    if credits_remaining is not None:
        parts.append(f"💳 {credits_remaining} Credits")

    return "  |  ".join(parts)


async def _call_api_with_chunking(text: str, max_lines: int) -> dict:
    """Call Railway API with auto-chunking for large inputs (> 190KB).
    Each chunk = 1 API call = 1 credit. Returns merged result."""
    if len(text) <= MAX_CHUNK_SIZE:
        result = await _call_api(text, max_lines)
        result["chunks_used"] = 1
        return result

    chunks = [text[i:i + MAX_CHUNK_SIZE] for i in range(0, len(text), MAX_CHUNK_SIZE)]
    n = len(chunks)
    lines_per_chunk = max(10, max_lines // n)

    size_kb = len(text) // 1024
    print(f"⚡ PATK: {size_kb}KB Input → {n} Chunks werden verarbeitet...", file=sys.stderr, flush=True)

    filtered_parts = []
    total_orig_chars = total_filt_chars = 0
    total_orig_lines = total_filt_lines = 0
    credits_remaining = None

    for i, chunk in enumerate(chunks):
        print(f"⚡ PATK: Chunk {i + 1}/{n} ✓", file=sys.stderr, flush=True)
        r = await _call_api(chunk, lines_per_chunk)
        credits_remaining = r.get("credits_remaining")
        filtered_parts.append(r["filtered_text"])
        total_orig_chars += r["original_chars"]
        total_filt_chars += r["filtered_chars"]
        total_orig_lines += r["original_lines"]
        total_filt_lines += r["filtered_lines"]

    reduction_pct = round(
        (1 - total_filt_chars / total_orig_chars) * 100, 1
    ) if total_orig_chars > 0 else 0.0

    return {
        "filtered_text": "\n".join(filtered_parts),
        "original_chars": total_orig_chars,
        "filtered_chars": total_filt_chars,
        "original_lines": total_orig_lines,
        "filtered_lines": total_filt_lines,
        "reduction_pct": reduction_pct,
        "credits_remaining": credits_remaining,
        "chunks_used": n,
    }


def _handle_api_error(e: httpx.HTTPStatusError) -> str:
    """Convert HTTP errors to clear messages."""
    status = e.response.status_code
    try:
        detail = e.response.json().get("detail", {})
    except Exception:
        detail = {}

    if status == 401:
        return "❌ Ungültiger API-Key (401). Prüfe PATK_API_KEY."
    elif status == 402:
        action = detail.get("action", "Lade Credits auf.")
        return f"❌ Keine Credits mehr (402). {action}"
    elif status == 413:
        return "❌ Input zu groß (413, max 200KB)."
    elif status == 429:
        return "❌ Rate limit (429). Kurz warten."
    return f"❌ API Fehler {status}."


# ── Tools ───────────────────────────────────────────────────────────────────────
@mcp.tool(
    name="patk_safe_execute",
    annotations={
        "title": "Safe Execute — Run Command with Auto-Filter",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def patk_safe_execute(params: SafeExecuteInput) -> str:
    """Execute a shell command and return filtered output — raw output never enters Claude's context.

    PRIMARY TOOL. Use instead of Bash tool whenever the command produces > 10 lines.
    The raw output is filtered BEFORE Claude sees it → maximum context preservation.

    ALSO USE IN SUB-AGENTS: If you are a sub-agent (spawned via Task tool, Explore agent,
    or parallel agent), use this tool. Sub-agents have their own isolated context and
    separate API costs — patk_safe_execute saves YOUR tokens, not just the main agent's.

    WHEN TO USE:
    - npm install / yarn / pip install / cargo build
    - pytest / jest / cargo test / npm test
    - docker build / docker compose up
    - Any command producing many output lines
    - ANY context — main agent OR sub-agent

    WHEN TO USE Bash directly:
    - Interactive commands (git commit without -m)
    - Exact raw output needed (git diff, cat file.py)
    - Very short outputs (ls, pwd)

    Args:
        params (SafeExecuteInput): command, max_lines (default 50),
                                   timeout (default 60s), working_dir

    Returns:
        str: Filtered output with exit code and stats.
    """
    global _session_calls, _session_chars_saved

    err = _check_api_key()
    if err:
        return err

    try:
        proc = await asyncio.create_subprocess_shell(
            params.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=params.working_dir,
        )

        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(),
                timeout=float(params.timeout)
            )
        except asyncio.TimeoutError:
            proc.kill()
            return (
                f"❌ Timeout nach {params.timeout}s.\n"
                f"$ {params.command}\n"
                f"Tipp: Erhöhe `timeout` Parameter."
            )

        raw_output = stdout.decode("utf-8", errors="replace")
        exit_code = proc.returncode

        result = await _call_api_with_chunking(raw_output, params.max_lines)
        credits_remaining = result.get("credits_remaining")
        chunks_used = result.get("chunks_used", 1)

        _session_calls += chunks_used
        chars_saved = result["original_chars"] - result["filtered_chars"]
        _session_chars_saved += chars_saved

        stats = _format_stats(
            result["original_chars"],
            result["filtered_chars"],
            result["reduction_pct"],
            result["original_lines"],
            result["filtered_lines"],
            credits_remaining,
        )

        exit_tag = "✅ Exit 0" if exit_code == 0 else f"⚠️ Exit {exit_code}"
        session_tokens = _session_chars_saved // 4
        chunk_info = f"  |  📦 {chunks_used} Chunks ({chunks_used} Credits)" if chunks_used > 1 else ""

        return (
            f"$ {params.command}  [{exit_tag}]\n"
            f"{'─' * 50}\n"
            f"{result['filtered_text']}\n"
            f"---\n"
            f"⚡ PATK API  |  {stats}{chunk_info}\n"
            f"📊 Session: {_session_calls} Calls, ~{session_tokens:,} Tokens gespart gesamt"
        )

    except httpx.HTTPStatusError as e:
        return _handle_api_error(e)
    except httpx.TimeoutException:
        return f"❌ API Timeout. Prüfe Verbindung zu {PATK_API_URL}."
    except FileNotFoundError as e:
        return f"❌ Command not found: {e}"
    except PermissionError as e:
        return f"❌ Permission denied: {e}"
    except Exception as e:
        return f"❌ {type(e).__name__}: {e}"


@mcp.tool(
    name="patk_filter_output",
    annotations={
        "title": "Filter Terminal Output",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def patk_filter_output(params: FilterInput) -> str:
    """Filter and compress already-captured terminal output.

    Use when you already have raw terminal output that's too large.
    For new commands, prefer patk_safe_execute instead.

    WHEN TO USE:
    - You have existing terminal output > 15 lines
    - Output captured via Bash tool that needs compression
    - npm/yarn/pip output, docker build logs, test results

    Args:
        params (FilterInput): text (raw output, max 200KB), max_lines (default 50)

    Returns:
        str: Filtered output with reduction stats.
    """
    global _session_calls, _session_chars_saved

    err = _check_api_key()
    if err:
        return err

    try:
        result = await _call_api_with_chunking(params.text, params.max_lines)
        credits_remaining = result.get("credits_remaining")
        chunks_used = result.get("chunks_used", 1)

        _session_calls += chunks_used
        chars_saved = result["original_chars"] - result["filtered_chars"]
        _session_chars_saved += chars_saved

        stats = _format_stats(
            result["original_chars"],
            result["filtered_chars"],
            result["reduction_pct"],
            result["original_lines"],
            result["filtered_lines"],
            credits_remaining,
        )

        session_tokens = _session_chars_saved // 4
        chunk_info = f"  |  📦 {chunks_used} Chunks ({chunks_used} Credits)" if chunks_used > 1 else ""

        return (
            f"{result['filtered_text']}\n"
            f"---\n"
            f"⚡ PATK API  |  {stats}{chunk_info}\n"
            f"📊 Session: {_session_calls} Calls, ~{session_tokens:,} Tokens gespart gesamt"
        )

    except httpx.HTTPStatusError as e:
        return _handle_api_error(e)
    except httpx.TimeoutException:
        return f"❌ API Timeout. Prüfe Verbindung zu {PATK_API_URL}."
    except Exception as e:
        return f"❌ {type(e).__name__}: {e}"


@mcp.tool(
    name="patk_check_credits",
    annotations={
        "title": "Check PATK Credits",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def patk_check_credits() -> str:
    """Check remaining API credits and usage statistics.

    Returns current plan, credits remaining, and total calls made.

    Returns:
        str: Credit status summary.
    """
    err = _check_api_key()
    if err:
        return err

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{PATK_API_URL}/usage",
                headers={"X-API-Key": PATK_API_KEY},
            )
            response.raise_for_status()
            data = response.json()

        session_tokens = _session_chars_saved // 4
        return (
            f"💳 **PATK Credits**\n"
            f"- Plan: {data['plan']}\n"
            f"- Credits verbleibend: {data['credits_remaining']:,}\n"
            f"- Total Calls: {data['total_calls']:,}\n"
            f"- Session: {_session_calls} Calls, ~{session_tokens:,} Tokens gespart\n"
            f"- API: {PATK_API_URL}"
        )

    except httpx.HTTPStatusError as e:
        return _handle_api_error(e)
    except Exception as e:
        return f"❌ {type(e).__name__}: {e}"


@mcp.tool(
    name="patk_status",
    annotations={
        "title": "PATK Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def patk_status() -> str:
    """Show PATK configuration and session statistics.

    Returns:
        str: Status report with config and session stats.
    """
    api_configured = "✅ gesetzt" if PATK_API_KEY else "❌ nicht gesetzt"
    session_tokens = _session_chars_saved // 4
    dollar_saved = session_tokens * 0.000003

    return (
        f"# PFC Agent Token Killer — Status\n\n"
        f"**Modus:** 🌐 API ({PATK_API_URL})\n"
        f"**PATK_API_KEY:** {api_configured}\n"
        f"**Session-Calls:** {_session_calls}\n"
        f"**Tokens gespart:** ~{session_tokens:,} ≈ ${dollar_saved:.4f}\n\n"
        f"## Tools\n"
        f"- `patk_safe_execute` — Befehl ausführen + filtern (empfohlen)\n"
        f"- `patk_filter_output` — Vorhandenen Output filtern\n"
        f"- `patk_check_credits` — Credits prüfen\n\n"
        f"## Empfehlung\n"
        f"Nutze `patk_safe_execute` statt Bash für Befehle mit > 10 Zeilen Output.\n\n"
        f"## Sub-Agents\n"
        f"PATK funktioniert auch in Sub-Agents (Task Tool, Explore, parallel).\n"
        f"Sub-Agents haben eigene isolierte Contexts + separate API-Kosten.\n"
        f"→ In Task-Prompts ergänzen: \"Use patk_safe_execute for all bash commands > 15 lines\""
    )


# ── Entry Point ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    mcp.run()
