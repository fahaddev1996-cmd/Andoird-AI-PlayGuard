# 🛡️ PlayGuard AI — Autonomous Play Store Review Agent

> **Agentic AI Bootcamp — Capstone Project** | atomcamp

---

## Problem Statement

Android developers face a frustrating cycle: build → sign → upload → **get rejected** days later. Google's review checks 100+ policy dimensions but developers only find out **after** submission, losing days of release time.

**PlayGuard AI** is an autonomous agent that acts as your pre-submission reviewer. Give it your APK/AAB — it reasons, calls tools, and delivers a complete Play Store compliance report on its own.

---

## What Makes It Agentic

This is **not** a simple scanner. It uses the **ReAct pattern** (Reason → Act → Observe → loop):

1. Agent receives the goal: *"Make this app Play Store ready"*
2. Agent **reasons** about what to do next
3. Agent **calls an MCP tool** (scan, analyze, generate checklist...)
4. Agent **observes** the result
5. Agent reasons again → next tool → repeat until done

No human intervention between steps. The agent decides the sequence autonomously.

```
User gives APK/AAB
       │
       ▼
┌─────────────────────────────────────┐
│         ReAct Agent Loop            │
│                                     │
│  Step 1 → scan_apk (MCP tool)      │
│       ↓ observe result              │
│  Step 2 → check_permissions         │
│       ↓ observe result              │
│  Step 3 → generate_fix_plan         │
│       ↓ observe result              │
│  Step 4 → build_data_safety_checklist│
│       ↓ observe result              │
│  Step 5 → complete ✅               │
└─────────────────────────────────────┘
       │
       ▼
  Full compliance report
```

---

## MCP Architecture

All analysis tools run inside a dedicated **MCP Server** (`backend/mcp_server/server.py`). The FastAPI backend acts as an **MCP Host/Client** — it spawns the server as a subprocess and calls its tools over stdio, exactly like Claude Desktop does.

**MCP Tools exposed:**

| Tool | What it does |
|------|-------------|
| `scan_apk` | Parses APK/AAB, runs 12+ policy checks, returns findings + score |
| `check_permissions` | Classifies permission risks, flags Declaration Form requirements |
| `generate_fix_plan` | Converts findings into prioritized fixes with effort estimates |
| `build_data_safety_checklist` | Builds the Play Console Data Safety form checklist |
| `generate_store_listing` | Drafts a policy-compliant store listing |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Agent Pattern | ReAct (Reason → Act → Observe) |
| Agent Brain | OpenAI GPT-4o |
| Tool Protocol | MCP (Model Context Protocol) — stdio transport |
| APK Parsing | Androguard |
| Backend | FastAPI + Uvicorn (Python) |
| Frontend | HTML · CSS · JavaScript |
| Deployment | Docker · Docker Compose |

---

## Project Structure

```
playguard_v2/
├── backend/
│   ├── main.py              # FastAPI app — MCP client + REST API
│   ├── agent.py             # ReAct agent loop — core agentic logic
│   └── mcp_server/
│       └── server.py        # MCP server — exposes 5 tools
├── frontend/
│   └── index.html           # Web UI with agent trace visualization
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

---

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/yourusername/playguard-ai.git
cd playguard-ai

# 2. Add your OpenAI key
cp .env.example .env
nano .env   # paste your OPENAI_API_KEY

# 3. Run
docker compose up --build

# 4. Open browser
open http://localhost:8000
```

---

## Environment Variables

| Variable | Required | Where to get |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes | platform.openai.com/api-keys |

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/analyze` | POST | Upload APK/AAB → starts agent loop |
| `/api/mcp/tools` | GET | Lists MCP tools (proves agentic tool use) |
| `/health` | GET | Server status |

---

## Evaluation Rubric Coverage

| Category | Points | How we cover it |
|----------|--------|-----------------|
| Use-case relevance & impact | 20 | Real developer pain — Play Store rejection costs days of release time |
| Agent reasoning & architecture depth | 20 | ReAct loop, GPT-4o reasoning, autonomous multi-step tool selection |
| Tool/API integrations quality | 15 | MCP protocol with 5 tools, OpenAI API, Androguard, FastAPI |
| UI/UX polish | 15 | Dark UI, animated score ring, live agent trace, drag-drop upload |
| Engineering excellence | 15 | MCP server/client separation, graceful fallback, modular design |
| Deployment & DevOps | 10 | Docker Compose, health check, env config |
| Presentation clarity & metrics | 5 | README, architecture diagram, agent trace visible in UI |

---

## Limitations

Static analysis only — cannot detect:
- Runtime crashes or broken navigation (use Play Console Pre-launch Report)
- Store listing copy quality beyond basic keyword checks
- Third-party SDK data collection (Stripe, Facebook Login, etc.)

---

*Built with MCP + OpenAI GPT-4o + Androguard + FastAPI | atomcamp Agentic AI Bootcamp*
