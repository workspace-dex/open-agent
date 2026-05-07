<div align="center">
<br/>

<pre>
┌─────────────────────────────────────────────────────┐
│  ░▒▓ █▀█ █▀█ █▀▀ █▄ █ ▄▄ ▄▀█ █▀▀ █▀▀ █▄ █ ▀█▀ ▓▒░   │
│  ░▒▓ █▄█ █▀▀ ██▄ █ ▀█    █▀█ █▄█ ██▄ █ ▀█  █  ▓▒░   │
└─────────────────────────────────────────────────────┘
</pre>

### `local-first · privacy-friendly · intelligence-driven`

<br/>

[![License: MIT](https://img.shields.io/badge/License-MIT-00ff88.svg?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-00cfff.svg?style=flat-square)](https://python.org)
[![llama.cpp](https://img.shields.io/badge/Inference-llama.cpp-ff6e00.svg?style=flat-square)](https://github.com/ggml-org/llama.cpp)
[![PydanticAI](https://img.shields.io/badge/Framework-PydanticAI-bf00ff.svg?style=flat-square)](https://ai.pydantic.dev)
[![Architecture](https://img.shields.io/badge/Architecture-Skill--Driven-ff0077.svg?style=flat-square)](#-the-skill-driven-ecosystem)

</div>

<div align="center">

> *"If removing a feature doesn't make the agent worse — it doesn't belong."*

</div>


## ◈ What is open-agent?

`open-agent` is a professional-grade autonomous agent framework that turns your local machine into a **private, high-performance intelligence hub**. 

No cloud. No data leakage. No compromise.

<div align="center">
  <img width="100%" alt="open-agent banner" src="https://github.com/user-attachments/assets/158d0850-b836-409f-9ace-f24c46c422f8" />

</div>
<br/>

Built on **PydanticAI** + **llama.cpp**.

It combines structured reasoning, tool orchestration, and a markdown-driven skill system into a cohesive agentic runtime — optimized for consumer hardware down to **6 GB VRAM**.

## ⚡ Quick Start

```bash
# Activate your environment and launch
source venv/bin/activate
python open-agent.py
```

That's it. The agent is live.

## ◈ Model Guide — The Intelligence Engine

Autonomous agent behavior demands a model capable of sustained reasoning and precise tool orchestration. Below is the recommended configuration.

### ✦ Recommended: Qwen 3.6 35B · Start with Q3 Quant

*Current gold standard for local reasoning and tool use.*

```bash
./build/bin/llama-server \
-m /home/dex/models/Qwen3.6-35B-A3B-UD-IQ3_XXS.gguf \
--host 0.0.0.0 \
--port 8083 \
--n-cpu-moe 25 \
-c 27000 \
--n-gpu-layers 99 \
--override-tensor 'blk\.(2[0-9]|3[0-9]|4[0-6])\.ffn_(gate_up|down)_exps\.weight=CPU' \
-b 1442 \
-ub 512 \
--cache-type-k q4_0 \
--cache-type-v q4_0 \
--flash-attn on \
--cont-batching \
--jinja \
--reasoning off \
--top-k 20 \
--top-p 0.8 \
--temp 0.7 \
--repeat-penalty 1.05 \
--presence-penalty 1.5 \
--cache-prompt
```
*Optimizations Note:*

1. Use **--reasoning** auto for preserve thinking and coding

2. Adjust **--n-cpu-moe 25**. Reduce this to allocate more GPU space.


<div align="center">
  
| Flag | Why It Matters |
|:---|:---|
| `--override-tensor` (MoE CPU offload) | Runs massive mixture-of-experts models on consumer GPUs |
| `--cache-type-k/v q4_0` | Halves VRAM usage with negligible quality loss |
| `--flash-attn on` | Efficient long-context handling — critical for agentic loops |
| `--cont-batching` | Non-blocking streaming of thoughts and outputs |
| `--reasoning auto` | Activates chain-of-thought for complex multi-step tasks |

</div>

## ◈ Core Capabilities

### ◆ Built-in Tool Suite

<div align="center">

| Tool | Function |
|:---|:---|
| `⬡ web_search` | Real-time retrieval via SearxNG |
| `⬡ smart_research` | Deep research via 4 parallel multi-angle queries |
| `⬡ fetch_page` | Full-text extraction and cleaning of any URL |
| `⬡ read_rss_by_name` | Access to 24 curated tech, AI, and security feeds |
| `⬡ run_terminal` | Local shell execution for system automation |
| `⬡ read / write_file` | Secure direct filesystem interaction |
| `⬡ search_obsidian` | Deep semantic search of your local knowledge vault |
| `⬡ write_obsidian_note` | Dynamic creation and updating of your knowledge base |
| `⬡ load_soul` | Invokes the advanced reasoning layer for complex tasks |

</div>

### ◆ The SOUL.md System — Lazy-Loading Reasoning

The agent's core reasoning layer is **not loaded by default**. `SOUL.md` is invoked only when the agent encounters high-complexity tasks. This keeps the base prompt minimal (~60 tokens) while providing maximum cognitive depth on demand.

```
Base prompt  →  ~60 tokens  →  Fast, lightweight responses
SOUL loaded  →  Full depth  →  Complex reasoning & planning
```

## ◈ The Skill-Driven Ecosystem

`open-agent` is designed to **grow**. Intelligence is defined by Markdown files, not hard-coded logic.

### ◆ How Skills Work

```
task identified
      │
      ▼
skill file loaded (.md)
      │
      ▼
agent adopts specialized persona + workflow
      │
      ▼
task executed
```

**Plug-and-Play Intelligence** — Drop a `coding.md` for a coder. Drop `research.md` for a researcher. No Python required.

**Community-Driven Growth** — We are building a **Skill Gallery**: a global, decentralized library of shareable intelligence modules.

**Infinite Extensibility** — Expand capabilities without touching the core codebase. Ever.

## ◈ Why open-agent?

<div align="center">

| Feature | `open-agent` | Cloud Agent Frameworks |
|:---|:---:|:---:|
| Privacy | 🛡️ **Local-First** | ☁️ Cloud-Dependent |
| Data Leakage | ✦ **Zero Risk** | ⚠️ High Risk |
| Context Depth | ✦ **Deep & Local** | ✗ Minimal / Fragmented |
| VRAM Requirement | ✦ **Optimized · 6 GB+** | ✗ High / Unoptimized |
| Extensibility | ✦ **Markdown Skills** | ✗ Code-Heavy |

</div>

## ◈ Project Structure

```
open-agent/
├── open-agent.py          ◆ Main entry point
├── SOUL.md                ◆ Core reasoning & behavioral logic
├── pydantic-ai-skills/    ◆ The Skill Library
│   ├── coding.md              Specialized coding workflows
│   ├── research.md            Specialized research workflows
│   └── ...
├── install.sh             ◆ Automated setup
├── docker-compose.yml     ◆ Containerized deployment
├── pyproject.toml         ◆ Project dependencies
└── README.md              ◆ This document
```

## ◈ Contributing

We welcome contributors who push the boundaries of local AI.

- **Add a Tool** — Implement a new capability via `@agent.tool`
- **Add a Skill** — Create a new `.md` file in `skills/`
- **Modify Behavior** — Refine the reasoning logic in `SOUL.md`
- **Share a Skill** — Submit to the community Skill Gallery

## ◈ Important Note
- **To run open-agent Windows users will have to remove anything related to "import termios" from /open_agent/_impl.py


<div align="center">
  
<pre>
╔═════════════════════════════════════════════════════╗
║  MIT License · github.com/workspace-dex/open-agent  ║
╚═════════════════════════════════════════════════════╝
</pre>

*Built for the community. Runs on your machine. Owned by you.*

</div>
