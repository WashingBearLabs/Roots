# Roots Demos

Interactive demos showcasing Roots framework capabilities.

## Quick Start

Run all demos at once:

```bash
python demo/run_all.py
```

This starts all five demo servers and opens the landing page at `http://localhost:8200`.
Press `Ctrl+C` to stop all servers.

## Individual Demos

Each demo can be run standalone from the repo root:

```bash
python demo/content-pipeline/run_demo.py
python demo/research-assistant/run_demo.py
python demo/incident-response/run_demo.py
python demo/api-explorer/run_demo.py
python demo/node-explorer/run_demo.py
```

## Port Assignments

| Demo               | Port | URL                        |
|--------------------|------|----------------------------|
| Landing Page       | 8200 | http://localhost:8200      |
| Content Pipeline   | 8201 | http://localhost:8201      |
| Research Assistant  | 8202 | http://localhost:8202      |
| Incident Response  | 8203 | http://localhost:8203      |
| API Explorer       | 8204 | http://localhost:8204      |
| Node Explorer      | 8205 | http://localhost:8205      |

## Demo Descriptions

**Content Pipeline** — Agent pools and deterministic decisions in action. Shows how `agent`, `agent_pool`, `decision`, and `end` nodes work together to process content.

**Research Assistant** — Fork/join parallelism and human checkpoints. Demonstrates `fork`, `join`, `checkpoint`, and `decision` nodes for parallel research workflows.

**Incident Response** — AI-powered decisions with confidence thresholds. Features `ai_decision`, escalation, `retry`, and `emit` nodes for incident triage. Supports mock mode (default) or live LLM mode with `--model` and `--api-key` flags.

**API Explorer** — Interactive interface for every Roots API endpoint. Covers the full HTTP API and webhooks with a three-panel layout for request building, response viewing, and event monitoring.

**Node Explorer** — Interactive tutorial that walks through all 8 node types step by step. Includes a graph view, tutorial panel, state viewer, and event log with auto-play controls.
