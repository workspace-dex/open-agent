#!/usr/bin/env bash
# Open-Agent installer
# Usage: ./install.sh

set -e

BOLD="\033[1m"
GREEN="\033[0;32m"
AMBER="\033[0;33m"
CYAN="\033[0;36m"
DIM="\033[2m"
RESET="\033[0m"

echo ""
echo -e "${CYAN}${BOLD}╔══════════════════════════════════╗${RESET}"
echo -e "${CYAN}${BOLD}║     Open-Agent  Installer        ║${RESET}"
echo -e "${CYAN}${BOLD}╚══════════════════════════════════╝${RESET}"
echo ""

# ── Python check ─────────────────────────────────────────────────────────
PYTHON=""
for candidate in python3.12 python3.11 python3; do
    if command -v "$candidate" &>/dev/null; then
        VER=$("$candidate" -c "import sys; print(sys.version_info[:2])" 2>/dev/null)
        if "$candidate" -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" 2>/dev/null; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo -e "${AMBER}✗ Python 3.11+ not found.${RESET}"
    echo -e "${DIM}  Install it from https://python.org or via your package manager.${RESET}"
    exit 1
fi

echo -e "${GREEN}✓ Python:${RESET} $($PYTHON --version)"

# ── uv (fast) or venv (fallback) ─────────────────────────────────────────
if command -v uv &>/dev/null; then
    echo -e "${GREEN}✓ Using uv for installation${RESET}"
    uv venv venv --python "$PYTHON" 2>/dev/null || uv venv venv
    source venv/bin/activate
    uv pip install pydantic-ai httpx feedparser rich
else
    echo -e "${DIM}  uv not found — using standard venv + pip${RESET}"
    echo -e "${DIM}  (Install uv for faster installs: curl -LsSf https://astral.sh/uv/install.sh | sh)${RESET}"
    $PYTHON -m venv venv
    source venv/bin/activate
    pip install --quiet --upgrade pip
    pip install --quiet pydantic-ai httpx feedparser rich
fi

echo -e "${GREEN}✓ Dependencies installed${RESET}"

# ── SOUL.md ───────────────────────────────────────────────────────────────
if [ ! -f "SOUL.md" ]; then
    echo -e "${AMBER}! SOUL.md not found — creating default${RESET}"
    cat > SOUL.md << 'SOUL'
# SOUL — Behavioral Core for Open-Agent

> Loaded on demand for complex tasks. Not in context by default.

## Grounding Rituals

Before answering any real-world, time-sensitive, or factual query:

1. **Time anchor** — `run_terminal("date && uname -r")` to establish current date/system.
2. **Freshness check** — Assume weights are stale for anything from the past 2 years. Search first.
3. **Confidence audit** — Rate confidence silently. Below 80%? Search.
4. **Source triangulation** — Confirm important claims across ≥2 distinct results.
5. **Staleness signal** — Flag results older than 6 months or run a follow-up search.

## Intelligence Layer

- **Prefer the web as your database** over your own weights for anything factual, current, or specific.
- Use `smart_research` for multi-angle topics; `web_search` for single lookups.
- After fetching: extract only what's relevant. Never dump raw content.
- For deep topics: fetch_page on 1–2 primary sources after web_search.

## Research Protocol (for plan/research/compare tasks)

1. Clarify scope if ambiguous.
2. Run `smart_research` with 3–4 queries covering different angles.
3. Fetch the 1–2 most promising URLs.
4. Synthesise: highlight consensus, flag contradictions, note uncertainties.
5. Deliver: summary → key findings → caveats.

## Personality

- **Direct.** No filler ("Certainly!", "Great question!").
- **Admit gaps.** Never hallucinate a source.
- **Compact.** One clear answer beats three vague ones.
- **Honest about confidence.** "I'm not certain — here's what I found:" is fine.
SOUL
    echo -e "${GREEN}✓ SOUL.md created${RESET}"
fi

# ── Summary ───────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}Installation complete.${RESET}"
echo ""
echo -e "${DIM}Next steps:${RESET}"
echo -e "  1. Start llama.cpp server on port 8083 (see README)"
echo -e "  2. Start SearxNG on port 8081  (docker compose up -d searxng)"
echo -e "  3. Run the agent:"
echo ""
echo -e "     ${CYAN}source venv/bin/activate${RESET}"
echo -e "     ${CYAN}python agent.py${RESET}"
echo ""
