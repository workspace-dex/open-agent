## What is Open-Agent?

Open-Agent is a local AI agent that runs entirely on your machine. One Python file, a handful of dependencies, and an LLM running locally via llama.cpp.

It searches the web, runs terminal commands, reads and writes files, fetches RSS feeds, pulls full pages, and does multi-query parallel research. It keeps persistent conversation history across sessions. It loads deep behavioral instructions only when needed — simple queries pay zero overhead.

**Default context: 16,000 tokens.** Adjustable up to 64,000+ on a single 6 GB GPU.

Your data never leaves your machine.

![open-agent](https://github.com/user-attachments/assets/df878264-b594-4c5b-88a8-71bbdb6a3783)

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![llama.cpp](https://img.shields.io/badge/Inference-llama.cpp-orange.svg)](https://github.com/ggml-org/llama.cpp)
[![PydanticAI](https://img.shields.io/badge/Framework-PydanticAI-purple.svg)](https://ai.pydantic.dev)

**No cloud. No API bill. No rate limits.**

[Quick Start](#quick-start) · [Why This Exists](#why-this-exists) · [Tools](#built-in-tools) · [Model Guide](#model-guide) · [Philosophy](#philosophy)

</div>


## Why This Exists

Most "local AI agent" projects are built for people with 24 GB GPUs, Node.js experience, and a tolerance for 200 MB of dependencies. They're impressive demos. They're not tools you use every day on real hardware.

Open-Agent was built around a different constraint: **what is the minimum surface area you need to make a small model genuinely useful?**

The answer: a tight system prompt, real tools, smart context management, and the right model. Everything else is overhead.

The other thing local agents get wrong is context. They load skills, memories, behavioral instructions, and tool schemas before you've typed a word. On a 6 GB card, your context window is gone before the conversation starts.

Open-Agent uses lazy loading. The system prompt is ~60 words. Deeper instructions live in `SOUL.md` and only enter context when the query is complex enough to need them.


## How It Compares

| | Open-Agent | OpenClaw | Hermes Agent | PythonClaw |
|---|:---:|:---:|:---:|:---:|
| **Runs on 6 GB VRAM** | ✅ | ❌ | ❌ | ❌ |
| **Single-file core** | ✅ | ❌ | ❌ | ❌ |
| **Minimal default context** | ✅ | ❌ | ❌ | ❌ |
| **SOUL.md lazy loading** | ✅ | ✅ | ❌ | Partial |
| **Auto context compression** | ✅ | ✅ | ✅ | ✅ |
| **Built-in web search** | ✅ | ✅ | ✅ | ✅ |
| **No API key required** | ✅ | Partial | Partial | Partial |

OpenClaw and Hermes are powerful platforms with messaging gateways, multi-channel support, and large ecosystems. They're built for a full-time AI assistant running across Telegram, Discord, and WhatsApp. Open-Agent is built for people who want to run a capable agent in a terminal, on real hardware, right now.

---

## Quick Start

### Prerequisites

- Python 3.11+
- [llama.cpp](https://github.com/ggml-org/llama.cpp) built from source
- [SearxNG](https://docs.searxng.org/) running locally (for web search)
- A GGUF model (see [Model Guide](#model-guide))

### Install

```bash
git clone https://github.com/yourusername/open-agent.git
cd open-agent

# One command (uses uv if available, falls back to pip)
./install.sh

# Or manually
python3 -m venv venv
source venv/bin/activate
pip install pydantic-ai httpx feedparser rich
```

### Run

```bash
source venv/bin/activate
python agent.py
```

---

## Inference Server

### Download a model

```bash
# Recommended (fits in 6 GB VRAM with high context windows)
huggingface-cli download bartowski/gemma-4-E4B-it-GGUF \
  --include "gemma-4-E4B-it-Q4_K_M.gguf" \
  --local-dir ~/models
```

### Start llama.cpp

```bash
./build/bin/llama-server \
  -m ~/models/gemma-4-E4B-IT-Q4_K_M.gguf \
  --host 0.0.0.0 --port 8083 \
  --n-gpu-layers 99 \
  -c 55000 \
  --cache-type-k q4_0 \
  --cache-type-v q4_0 \
  -b 1028 -ub 218 \
  --flash-attn \
  --cont-batching \
  --top-k 40 --top-p 0.9 \
  --repeat-penalty 1.1 \
  --temp 0.5 \
  --threads $(nproc) \
  --threads-batch $(nproc)
```

**Why these flags:**

| Flag | Purpose |
|---|---|
| `-c 55000` | 55k context window |
| `--cache-type-k/v q4_0` | Compress KV cache FP16→4-bit, saving ~1.5 GB VRAM |
| `--flash-attn` | Memory-efficient attention for long contexts |
| `--cont-batching` | Process tokens as they arrive, not in batches |
| `-b 1028 -ub 218` | Tuned for 6 GB cards |

### Start SearxNG

```bash
docker compose up -d searxng
# or: docker run -d -p 8081:8080 -e BASE_URL="http://localhost:8081" searxng/searxng
```

---

## Built-in Tools

| Tool | What it does |
|---|---|
| `web_search` | Single SearxNG query |
| `smart_research` | 4 parallel queries merged |
| `fetch_page` | Extract full text from any URL |
| `read_rss_by_name` | 24 curated sources · categories: `tech`, `ai`, `security`, `engineering`, `startups` |
| `run_terminal` | Shell commands |
| `read_file` / `write_file` | Local file access |
| `load_soul` | Lazy-load `SOUL.md` for complex tasks |
| `search_obsidian` | Search your Obsidian vault |
| `read_obsidian_note` | Read a specific note |
| `write_obsidian_note` | Create or append to notes |

---

## Obsidian Integration

Set your vault path in `agent.py`:

```python
OBSIDIAN_VAULT_PATH = Path("/your/vault/path")
```

The agent can search notes, read them on demand, and write back. Ask it to "check my notes on X" or "log this idea to my inbox note."

---

## Adding Your Own Tools

Adding a tool is three lines:

```python
@agent.tool
async def my_tool(ctx: RunContext, argument: str) -> str:
    """One sentence describing when the LLM should call this."""
    return do_something(argument)
```

**Example: connect your TTS pipeline**

```python
@agent.tool
async def generate_audio(ctx: RunContext, text: str, voice: str = "default") -> str:
    """Generate speech audio from text using the local TTS model."""
    result = subprocess.run(
        ["python", "tts_script.py", "--text", text, "--voice", voice],
        capture_output=True, text=True,
    )
    return result.stdout or f"Generated: {voice}.wav"
```

Now you can say *"Generate dialogue for scene 3 in the gravel voice"* and the agent handles it. This is the core idea: your agent, connected to your actual workflow. The LLM routes. Your tools work.

---

## SOUL.md

The system prompt is ~60 words. Extended behavioral instructions — research protocols, tool selection logic, personality rules — live in `SOUL.md` and only enter context when needed.

**Auto-triggers:** `research`, `plan`, `compare`, `analyze`, `step by step`, `walk me through`, `help me build`, `pros and cons`, `investigate`, `outline`, `draft`

Simple queries never load SOUL.md. You pay for exactly what you use.

To change the agent's personality or research style, edit `SOUL.md`. No code needed.

---

## Slash Commands

| Command | Alias | |
|---|---|---|
| `/help` | `/h` | Show all commands |
| `/history` | `/hi` | Token count, context usage |
| `/compress` | | Force compress history |
| `/clear` | `/cl` | Wipe all history |
| `/soul` | | Print SOUL.md |
| `/model` | `/m` | Server and model config |
| `/sources` | | List RSS sources |
| `/save` | | Force save history |
| `/exit` | `/q` | Save and quit |

---

## Model Guide

### Recommended for 6 GB VRAM

| Model | VRAM | Speed | Notes |
|---|---|---|---|
| **Gemma-4-E4B-IT-Q4_K_M** ⭐ | ~2.5 GB | ~45 t/s | Best agentic discipline at 4B |
| **Qwen-3.5-9B-Q3_K_M** | ~4.5 GB | ~35 t/s | Excellent tool calling and reasoning, 264K context |
| **Llama 3.2 3B Q4_K_M** | ~2.0 GB | ~80 t/s | Fastest, for simple tasks |

### Qwen 3.5

Capable model, but thinking mode can cause excessive tool calls. If you try it, add `--chat-template-kwargs '{"enable_thinking":false}'` to your llama-server flags.

---

## Project Structure

```
open-agent/
├── agent.py            # The entire agent
├── SOUL.md             # Behavioral layer (lazy-loaded)
├── README.md
├── pyproject.toml
├── install.sh
├── docker-compose.yml  # SearxNG
├── .gitignore
└── LICENSE
```

---

## Philosophy

Open-Agent follows one rule: don't add a feature unless removing it makes the agent meaningfully worse.

Other agents compete on feature count. Open-Agent competes on signal-to-noise ratio.

The 6 GB VRAM constraint is not a limitation. It is the design target. Everything in the codebase exists to serve that target.

---

## Contributing

- New tools → add `@agent.tool` functions
- New RSS sources → add to `RSS_SOURCES` dict
- Behavioral changes → edit `SOUL.md`
- New slash commands → add to `handle_slash()`

Keep it minimal.

---

## License

MIT.
