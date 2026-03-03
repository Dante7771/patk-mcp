# PATK — PFC Agent Token Killer MCP Server

> **Compress noisy terminal output before it fills Claude's context.**
> npm install, docker build, pytest — filtered down to what actually matters.

---

## Why PATK?

Long coding sessions fill Claude's context with terminal noise.
Each `npm install` dumps 150–200 lines of deprecation warnings and progress bars.
That's **~10,000 tokens of pure noise per session** — eating into your context window.

**PATK filters that noise before Claude ever sees it.**

### Real benchmark (5-command session, Haiku API):

| Command | Lines Before | Lines After | Reduction |
|---------|-------------|-------------|-----------|
| `npm install` | 52 | 6 | **80%** |
| `pip install -r requirements.txt` | 42 | 10 | **77%** |
| `docker build .` | 35 | 15 | **57%** |
| `pytest -v` | 30 | 21 | **30%** |
| **Session total** | **8,380 tokens** | **3,829 tokens** | **54% ↓** |

> Real Haiku API benchmark. No spin — actual measured values.

**What this means:**
- Context fills up **2× slower** in long sessions
- Claude's summarization is delayed → **longer memory of early session details**
- At CI/CD scale: meaningful API cost reduction

---

## Works in Sub-Agents Too

Sub-agents (Task tool, Explore agents, parallel agents) have their **own isolated context**
and their **own separate API costs**. PATK installed once works everywhere:

- Main agent context: protected
- Sub-agent context: also protected (independently)
- n8n pipelines: filter between nodes to halve accumulated data

> Add to your Task tool prompts:
> `"Use patk_safe_execute for all bash commands producing > 15 lines output"`

---

## Installation

### 1. Get your free API key

Register at **[pfc-token-killer.com](https://pfc-token-killer.com)** — 200 free credits, no payment.

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Add to Claude Code MCP config

**Claude Code (`~/.claude.json` or project `.mcp.json`):**

```json
{
  "mcpServers": {
    "patk": {
      "command": "python",
      "args": ["/path/to/server.py"],
      "env": {
        "PATK_API_KEY": "ptk_your_key_here"
      }
    }
  }
}
```

Or via CLI:
```bash
PATK_API_KEY=ptk_your_key_here claude mcp add patk python /path/to/server.py
```

**That's it.** No configuration. No extra dependencies beyond `requirements.txt`.

---

## Tools

| Tool | When to use |
|------|-------------|
| `patk_safe_execute` | **PRIMARY** — Run command + filter in one step. Raw output never enters Claude's context. |
| `patk_filter_output` | Filter already-captured terminal output (Bash tool fallback) |
| `patk_check_credits` | Check remaining API credits |
| `patk_status` | Show configuration and session statistics |

### Example: `patk_safe_execute`

```
patk_safe_execute(command="npm install", max_lines=50)

→ npm install  [✅ Exit 0]
  ──────────────────────────────────────────────────
  added 847 packages in 12.3s
  [10× npm warn deprecated — run 'npm audit fix']
  // ✂ 80% reduction — 52→6 lines
  ---
  ⚡ PATK API  |  80% Reduktion  |  52 → 6 Zeilen  |  ~1,200 Tokens gespart
```

---

## Filter Pipeline

1. **ANSI removal** — Strip colors, cursor codes
2. **Progress bar detection** — Remove `[=====>  ] 47%`, `━━━━━━ 100%`, download bars
3. **Pattern condensation** — Group similar lines: `[10× npm warn deprecated]`, `[15× pip Collecting/Downloading]`
4. **Duplicate compression** — Identical lines → single summary
5. **Timestamp clusters** — 20 timestamp log lines → 1 summary
6. **Entropy scoring** — Remove low-information lines; always keep errors/warnings

---

## Compatibility

Works with any MCP-compatible client:

- **Claude Code** (primary target)
- **Cursor**
- **Cline**
- **Windsurf**
- Any client supporting the MCP protocol

---

## Pricing

| Plan | Credits | Price |
|------|---------|-------|
| **Starter** | 200 | Free |
| Hobby | 2,000 | $5 one-time |
| Pro | 10,000 | $15 one-time |
| Enterprise | 50,000 | $49 one-time / seat |

1 credit = 1 filter call. Credits never expire. No subscriptions.

> **Early Bird:** Pre-register now for +50% bonus credits on paid plans at launch.

---

## Get your key

→ **[pfc-token-killer.com](https://pfc-token-killer.com)**

---

*Built by ForgeBuddy & Dante · 2026*
