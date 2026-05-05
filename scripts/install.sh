#!/usr/bin/env bash
# Open-Agent installer — makes `opagent` available as a shell command.
# Usage: ./install.sh

set -e

BOLD="\033[1m"; GREEN="\033[0;32m"; AMBER="\033[0;33m"
CYAN="\033[0;36m"; DIM="\033[2m"; RED="\033[0;31m"; RESET="\033[0m"

echo ""
echo -e "${CYAN}${BOLD}"
echo "  ╔══════════════════════════════════════╗"
echo "  ║       Open-Agent  Installer          ║"
echo "  ║  Run capable AI on any laptop.       ║"
echo "  ╚══════════════════════════════════════╝"
echo -e "${RESET}"

# ── Python ────────────────────────────────────────────────────────────────
PYTHON=""
for candidate in python3.12 python3.11 python3; do
    if command -v "$candidate" &>/dev/null; then
        if "$candidate" -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" 2>/dev/null; then
            PYTHON="$candidate"; break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo -e "${RED}✗ Python 3.11+ required.${RESET}"
    echo -e "${DIM}  Install: https://python.org or via your distro's package manager.${RESET}"
    exit 1
fi
echo -e "${GREEN}✓ Python:${RESET} $($PYTHON --version)"

# ── Install with uv (preferred) or pip ───────────────────────────────────
if command -v uv &>/dev/null; then
    echo -e "${GREEN}✓ uv detected — fast install${RESET}"
    uv pip install -e . --quiet
    INSTALL_METHOD="uv"
else
    echo -e "${DIM}  uv not found — using pip (slower).${RESET}"
    echo -e "${DIM}  Install uv for 10x faster installs: curl -LsSf https://astral.sh/uv/install.sh | sh${RESET}"
    $PYTHON -m pip install -e . --quiet
    INSTALL_METHOD="pip"
fi

echo -e "${GREEN}✓ open-agent installed${RESET}"

# ── Verify the opagent command is available ───────────────────────────────
if command -v opagent &>/dev/null; then
    echo -e "${GREEN}✓ 'opagent' command is ready${RESET}"
else
    # pip installed to a PATH-excluded directory — tell the user
    PIPX_BIN=$(python3 -m site --user-base 2>/dev/null)/bin
    echo -e "${AMBER}⚠ 'opagent' not found in PATH.${RESET}"
    echo -e "${DIM}  Add this to your ~/.bashrc or ~/.zshrc:${RESET}"
    echo -e "     ${CYAN}export PATH=\"\$PATH:$PIPX_BIN\"${RESET}"
    echo -e "${DIM}  Then restart your terminal, or run: source ~/.bashrc${RESET}"
    # Try to add it automatically
    SHELL_RC="$HOME/.bashrc"
    [ -f "$HOME/.zshrc" ] && SHELL_RC="$HOME/.zshrc"
    if grep -q "$PIPX_BIN" "$SHELL_RC" 2>/dev/null; then
        echo -e "${DIM}  (Already in $SHELL_RC — just restart terminal)${RESET}"
    else
        echo "" >> "$SHELL_RC"
        echo "# Open-Agent" >> "$SHELL_RC"
        echo "export PATH=\"\$PATH:$PIPX_BIN\"" >> "$SHELL_RC"
        echo -e "${GREEN}✓ Added to $SHELL_RC — restart terminal to use 'opagent'${RESET}"
    fi
fi

# ── SOUL.md ───────────────────────────────────────────────────────────────
if [ ! -f "SOUL.md" ]; then
    echo -e "${AMBER}! SOUL.md not found — creating default${RESET}"
    cat > SOUL.md << 'SOUL'
# SOUL — Behavioral Core for Open-Agent
> Loaded on demand for complex tasks. Not in context by default.

## Grounding Rituals
1. **Time anchor** — `run_terminal("date && uname -r")` before time-sensitive queries.
2. **Freshness check** — Assume weights are stale for anything from the past 2 years. Search first.
3. **Confidence audit** — Below 80% confidence? Search before answering.
4. **Source triangulation** — Confirm important claims across ≥2 distinct results.

## Intelligence Layer
- Prefer the web as your database over your own weights for factual/current topics.
- Use `smart_research` for multi-angle topics; `web_search` for single lookups.
- After fetching: extract only what's relevant. Never dump raw content.

## Research Protocol (plan / research / compare tasks)
1. Clarify scope if ambiguous.
2. Run `smart_research` with 3–4 queries covering different angles.
3. Fetch the 1–2 most promising URLs with `fetch_page`.
4. Synthesise: highlight consensus, flag contradictions, note uncertainties.
5. Deliver: summary → key findings → caveats.

## Personality
- **Direct.** No filler phrases ("Certainly!", "Great question!").
- **Admit gaps.** Never hallucinate a source URL or date.
- **Compact.** One clear answer beats three vague ones.
SOUL
    echo -e "${GREEN}✓ SOUL.md created${RESET}"
fi

# ── Done ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}Installation complete!${RESET}"
echo ""
echo -e "  ${DIM}Quick start:${RESET}"
echo ""
echo -e "  1. Start llama.cpp server:"
echo -e "     ${CYAN}./build/bin/llama-server -m ~/models/gemma-4-E4B-IT-Q4_K_M.gguf \\${RESET}"
echo -e "     ${CYAN}  --host 0.0.0.0 --port 8083 --n-gpu-layers 99 -c 55000 \\${RESET}"
echo -e "     ${CYAN}  --cache-type-k q4_0 --cache-type-v q4_0 --flash-attn${RESET}"
echo ""
echo -e "  2. Start SearxNG (web search):"
echo -e "     ${CYAN}docker compose up -d searxng${RESET}"
echo ""
echo -e "  3. Run the agent:"
echo -e "     ${CYAN}opagent${RESET}"
echo ""
echo -e "  ${DIM}On first run, a setup wizard will guide you through configuration.${RESET}"
echo ""
