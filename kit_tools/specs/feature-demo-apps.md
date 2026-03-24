<!-- Template Version: 2.0.0 -->
---
feature: demo-apps
status: active
session_ready: true
depends_on: []
vision_ref: "Demo applications showcasing the Roots framework"
type: feature
epic:
epic_seq:
epic_final:
created: 2026-03-24
updated: 2026-03-24
---

# Feature Spec: Demo Applications

## Overview

Five self-contained demo applications that showcase the Roots framework's capabilities through lightweight web UIs. Each demo lives in its own directory under `demo/`, includes a process YAML, local agent implementations, a FastAPI server, and a single-page HTML/JS frontend that consumes the headless graph API. No build tools, no npm, no React — just Python + vanilla HTML/JS served from a single `python run_demo.py`.

## Goals

- Demonstrate every major Roots capability through working, explorable examples
- Prove the headless graph API works for real UI consumption
- Provide copy-paste starting points for consumers building their own apps

## User Stories

### US-001: Shared Demo Infrastructure

**Description:** As a demo builder, I want shared HTML/CSS/JS components for graph rendering and process visualization so that each demo doesn't reinvent the UI layer.

**Implementation Hints:**
- Create `demo/_common/` directory with shared assets:
  - `graph-renderer.js` — Takes the Roots graph JSON structure (from `GET /runs/{id}/graph`) and renders it as an SVG with positioned nodes, colored by status, connected by edges. Use the `position` metadata from nodes. Nodes are rounded rectangles with label + type + status badge. Edges are SVG paths. Active node pulses. Completed nodes are green, running are blue, pending are gray, failed are red, paused are yellow.
  - `state-viewer.js` — Renders work item state as a collapsible JSON tree. Highlights keys that changed since last update.
  - `event-log.js` — Scrolling event log that shows events as they arrive. Each event shows timestamp, type (color-coded), node, and a brief description.
  - `styles.css` — Clean, minimal CSS. Dark background, light text, monospace for data. Card-based layout. Responsive. No framework — just CSS custom properties + flexbox/grid.
  - `base.html` — Template HTML that includes all shared assets via `<script>` and `<link>` tags. Provides a standard layout: header (demo name + process status), main area (graph + panels), footer (controls).
- Create `demo/_common/demo_server.py` — Shared Python helper:
  - `create_demo_app(roots: Roots, demo_name: str, static_dir: str) -> FastAPI` — Creates a FastAPI app that:
    - Mounts the Roots API routers (process, runs, checkpoints, graph)
    - Serves static files from the demo's `static/` directory
    - Serves shared assets from `demo/_common/`
    - Adds a `GET /` route that serves `index.html`
    - Adds a `GET /api/demo-info` route returning demo name, description, and instructions
  - `open_browser(port: int)` — Opens `http://localhost:{port}` in the default browser after a 1s delay
  - `run_demo(roots: Roots, demo_name: str, static_dir: str, port: int = 8200)` — One-liner to start the demo server and open browser
- The graph renderer should poll `GET /runs/{id}/graph` every 500ms to update the display (simple polling, no websockets)
- Load no external CDN dependencies — everything is self-contained in `_common/`

**Acceptance Criteria:**
- [ ] `demo/_common/` directory exists with all shared assets
- [ ] `graph-renderer.js` renders Roots graph JSON as SVG with node status colors
- [ ] `state-viewer.js` renders JSON state as collapsible tree
- [ ] `event-log.js` shows scrolling event log
- [ ] `styles.css` provides clean dark-theme layout
- [ ] `demo_server.py` creates a working FastAPI app from a Roots instance
- [ ] Static files served correctly (shared + demo-specific)
- [ ] Tests verify demo_server creates a working app

### US-002: Content Pipeline Demo

**Description:** As a new user, I want a content moderation pipeline demo so that I can see how agent pools and deterministic decisions work in a real scenario.

**Implementation Hints:**
- Create `demo/content-pipeline/`:
  - `process.yaml` — Content moderation pipeline:
    - `classify` (agent) — classifies content type (article/comment/image_caption)
    - `analyze` (agent_pool, parallel) — runs sentiment_analyzer + toxicity_scorer + spam_detector concurrently
    - `route` (decision, deterministic) — routes based on combined scores: approve (all clean), flag_review (borderline), reject (toxic/spam)
    - `approve` / `flag_review` / `reject` (end nodes with different statuses)
  - `agents.py` — Local agent callables that return realistic mock data. Each agent sleeps 0.5-1s to simulate work. Agents use the input text to produce deterministic but varied results (e.g., keyword matching for toxicity).
  - `static/index.html` — UI showing:
    - Text input area where user types or pastes content to moderate
    - "Submit" button that creates a run via API
    - Graph visualization showing the process flow with real-time status
    - Results panel showing agent outputs (scores, classifications)
    - Decision outcome with reasoning
  - `run_demo.py`:
    ```python
    async def main():
        async with Roots(storage=SqliteBackend(":memory:")) as roots:
            await roots.load_process("demo/content-pipeline/process.yaml")
            # register agents from agents.py
            run_demo(roots, "Content Pipeline", "demo/content-pipeline/static")
    ```
- Include 3-4 sample texts in the UI as quick-select buttons: one clean, one borderline, one toxic, one spam
- The UI auto-polls the run graph to show progress

**Acceptance Criteria:**
- [ ] `python demo/content-pipeline/run_demo.py` starts server and opens browser
- [ ] User can submit text and see the process execute in real-time
- [ ] Agent pool parallel execution visible (3 agents run simultaneously)
- [ ] Decision routing shows which path was taken and why
- [ ] Sample texts produce different outcomes (approve/flag/reject)
- [ ] Process completes in <3 seconds

### US-003: Research Assistant Demo

**Description:** As a new user, I want a research aggregator demo so that I can see fork/join parallel execution and checkpoint-based human approval.

**Implementation Hints:**
- Create `demo/research-assistant/`:
  - `process.yaml` — Research aggregation pipeline:
    - `topic_input` (checkpoint) — "Enter a research topic to investigate"
    - `split` (fork) — splits into 3 parallel research branches
    - Branch 1: `search_academic` (agent) — searches "academic papers"
    - Branch 2: `search_news` (agent) — searches "news articles"
    - Branch 3: `search_web` (agent) — searches "web sources"
    - `merge` (join, collect strategy, collect_key: "research_results")
    - `summarize` (agent) — combines collected results into a summary
    - `quality_check` (decision, deterministic) — checks if summary has enough sources
    - `approve_publish` (checkpoint) — human reviews summary before "publishing"
    - `publish` (end, completed) / `insufficient` (end, failed)
  - `agents.py` — Mock agents that return plausible research results based on the topic. Use the topic string to seed different results. `search_academic` returns {"papers": [...]}, `search_news` returns {"articles": [...]}, etc. `summarize` agent combines them into a prose summary.
  - `static/index.html` — UI showing:
    - Topic input with submit (resolves the first checkpoint)
    - Graph with fork/join branches clearly visible (3 parallel lanes)
    - Collected results panel showing what each branch found
    - Summary panel
    - Approval panel with approve/reject buttons (resolves second checkpoint)
  - `run_demo.py` — same pattern as content-pipeline

**Acceptance Criteria:**
- [ ] `python demo/research-assistant/run_demo.py` starts server and opens browser
- [ ] First checkpoint shows topic input UI, process pauses until submitted
- [ ] Fork/join branches execute in parallel with visual progress
- [ ] Collected results show all three sources merged
- [ ] Summary agent output displayed
- [ ] Second checkpoint shows approve/reject UI
- [ ] Approve leads to "published", reject leads to "insufficient"

### US-004: Incident Response Demo

**Description:** As a new user, I want a SOC incident triage demo so that I can see AI decisions, confidence thresholds, and escalation in action.

**Implementation Hints:**
- Create `demo/incident-response/`:
  - `process.yaml` — SOC incident response:
    - `ingest` (agent) — normalizes incident data
    - `enrich` (agent_pool, sequential) — enriches with threat intel + geo lookup
    - `triage` (decision, ai_bounded, confidence_threshold: 0.75) — AI evaluates severity, routes to response path. Edges: isolate_endpoint, reset_credentials, block_ip, escalate_to_analyst, close_benign
    - `respond` (agent) — executes the selected response action
    - `document` (emit) — emits a custom event with incident summary
    - `close` (end, completed)
  - `agents.py` — Mock agents. `ingest` normalizes JSON input. `enrich` agents add threat intel scores and geo data. `respond` logs the action taken.
  - `static/index.html` — UI showing:
    - Incident input panel (JSON editor or form with fields: source_ip, event_type, severity_hint)
    - Pre-built incident scenarios as buttons: "Brute force login", "Malware callback", "Data exfiltration", "False alarm"
    - Graph visualization with the AI decision node highlighted
    - **AI Decision panel** showing: the AI's recommendation, confidence score (as a gauge/bar), reasoning text, and which edge was selected
    - If confidence is below threshold: escalation panel explaining the escalation
    - Response action output
  - `run_demo.py`:
    - Accepts `--model` flag (default: mock mode with no API key needed)
    - **Mock mode** (default): Replace the AI decision with a deterministic mock that uses keyword matching on the incident data to select edges and generate fake confidence scores. This lets the demo run without any LLM API key.
    - **Live mode** (`--model gpt-4o-mini` or any LiteLLM string): Uses real LLM for the triage decision
    - Print clear instructions on startup: "Running in mock mode. Pass --model gpt-4o-mini to use a real LLM."

**Acceptance Criteria:**
- [ ] `python demo/incident-response/run_demo.py` works with NO API key (mock mode)
- [ ] `python demo/incident-response/run_demo.py --model gpt-4o-mini` uses real LLM
- [ ] Pre-built scenarios produce different routing decisions
- [ ] AI decision panel shows confidence score and reasoning
- [ ] Low-confidence scenario triggers escalation (confidence below 0.75)
- [ ] Emit node fires a visible custom event in the event log
- [ ] Mock mode confidence scores are deterministic and predictable

### US-005: API Explorer Demo

**Description:** As a new user, I want an API explorer demo so that I can understand all available HTTP endpoints and interact with them directly.

**Implementation Hints:**
- Create `demo/api-explorer/`:
  - `process.yaml` — A simple 3-node process (agent → decision → end) pre-loaded for experimentation
  - `agents.py` — Simple echo agent that returns its input with a timestamp
  - `static/index.html` — UI showing:
    - Left panel: API endpoint catalog grouped by category (Processes, Runs, Checkpoints, Agents, Webhooks, Graph). Each endpoint shows method, path, description.
    - Center panel: Request builder (select endpoint, fill params, edit JSON body, send)
    - Right panel: Response viewer (status code, headers, formatted JSON body)
    - Bottom panel: Live event log (register a webhook on startup that posts to a local receiver endpoint)
    - Pre-built "recipes" as quick-action buttons: "Create a run", "Check run status", "Get run graph", "List agents", "Register webhook"
  - `run_demo.py`:
    - Starts the Roots API server with the pre-loaded process
    - Registers a webhook that posts to a local `/api/webhook-receiver` endpoint on the same server
    - The webhook receiver stores events in memory and exposes them via `GET /api/received-events`
    - Opens browser
  - The API explorer is essentially a lightweight Postman/Swagger UI tailored to Roots

**Acceptance Criteria:**
- [ ] `python demo/api-explorer/run_demo.py` starts server and opens browser
- [ ] All API endpoint categories visible and documented
- [ ] Request builder can send requests to any endpoint
- [ ] Response viewer shows formatted JSON responses
- [ ] Pre-built recipes work correctly
- [ ] Webhook events appear in the live event log
- [ ] Process is pre-loaded and ready for experimentation

### US-006: Node Explorer Demo (Interactive Tutorial)

**Description:** As a new user, I want an interactive node explorer so that I can learn what each node type does by stepping through a process that uses all of them.

**Implementation Hints:**
- Create `demo/node-explorer/`:
  - `process.yaml` — "Greatest hits" process designed to demonstrate every node type in sequence:
    1. `welcome` (checkpoint) — "Welcome! Press Continue to start the tour." Teaches checkpoints.
    2. `classify` (agent) — Classifies a sample work item. Teaches single agent nodes and output_key.
    3. `validate` (agent_pool, parallel) — Runs 3 validators in parallel. Teaches agent pools and execution modes.
    4. `quality_gate` (decision, deterministic) — Routes based on validation scores. Teaches deterministic decisions.
    5. `deep_analysis` (fork) — Splits into 2 branches. Teaches fork nodes.
    6. Branch A: `analyze_content` (agent) / Branch B: `analyze_metadata` (agent)
    7. `combine` (join, merge_all) — Merges branch results. Teaches join nodes and merge strategies.
    8. `notify` (emit, event_type: "tour.analysis_complete") — Fires custom event. Teaches emit nodes.
    9. `complete` (end, completed) — Process complete. Teaches end nodes.
    - Also include a retry demonstration: one agent configured with `retry: {max_attempts: 2}` that fails on first call, succeeds on second (agent tracks call count in a closure). Teaches retry behavior.
  - `agents.py` — Each agent is annotated with docstrings explaining what it demonstrates. The "failing" agent for retry is clearly labeled.
  - `static/index.html` — UI showing:
    - **Graph panel** (top): Full process graph with all nodes visible. Current node highlighted with a glow effect. Completed nodes have checkmarks. Lines connecting nodes show traversal status.
    - **Tutorial panel** (right): For each node the execution reaches, shows:
      - Node type name and icon
      - "What this node does" — 2-3 sentence explanation
      - "Configuration" — The actual YAML config for this node (syntax highlighted)
      - "What happened" — What the node produced (output, decision, event, etc.)
      - "Try it yourself" — A code snippet showing how to define this node type in your own process
    - **State panel** (bottom-left): Work item state, updated after each node. Changed keys highlighted in gold.
    - **Event log** (bottom-right): Events stream, color-coded by type.
    - **Controls**:
      - "Step" button — Advances one node (resolves checkpoint or triggers next tick)
      - "Auto-play" toggle — Runs through automatically with 2s pause between nodes
      - "Reset" button — Starts the process over
    - The process starts paused at the first checkpoint. The user clicks "Continue" to begin the tour.
  - `run_demo.py`:
    - Starts in step-through mode by default
    - Uses a custom execution approach: instead of `execute_run` (which runs to completion), the demo's JS calls a custom `/api/step` endpoint that runs a single tick
    - Add a `POST /api/step` endpoint to the demo server that calls `ProcessRunner.tick()` once and returns the updated graph
    - Add a `POST /api/reset` endpoint that cancels the current run and starts a new one
    - Add a `GET /api/tutorial/{node_id}` endpoint that returns the tutorial content for a specific node type (loaded from a `tutorial_content.json` file)
  - `tutorial_content.json` — Structured tutorial data for each node:
    ```json
    {
      "checkpoint": {
        "title": "Checkpoint Node",
        "icon": "pause-circle",
        "what": "Pauses execution for human review. The process waits until someone approves, rejects, or redirects.",
        "when": "Use checkpoints for quality gates, approvals, or any point where a human should verify before continuing.",
        "config_example": "type: checkpoint\nconfig:\n  prompt: \"Review and approve\"",
        "tips": ["Checkpoints create a record in storage", "Resolve via API: POST /runs/{id}/checkpoint", "Escalations also surface as checkpoints"]
      }
    }
    ```

**Acceptance Criteria:**
- [ ] `python demo/node-explorer/run_demo.py` starts server and opens browser
- [ ] Process graph shows all 8+ nodes with clear visual layout
- [ ] Step-through execution advances one node at a time
- [ ] Tutorial panel updates with node-specific content at each step
- [ ] Each node type's explanation includes what, when, config example, and tips
- [ ] State panel shows accumulated state changes with highlights
- [ ] Event log shows events in real-time
- [ ] Auto-play mode works with configurable speed
- [ ] Reset restarts the process from the beginning
- [ ] Retry behavior is visible (agent fails, retries, succeeds)

### US-007: Demo Landing Page

**Description:** As a new user, I want a landing page at `demo/` that lists all available demos with descriptions so that I can pick which one to explore.

**Implementation Hints:**
- Create `demo/index/`:
  - `static/index.html` — Landing page showing:
    - Roots logo/name + tagline ("AI-native process orchestration")
    - Card grid with all 5 demos, each showing: name, 1-line description, what Roots features it demonstrates, a "Launch" button
    - Each Launch button links to `http://localhost:{port}` for that demo
    - Footer with links: GitHub repo, API docs (FastAPI auto-generated), architecture overview
  - `run_all.py` — Starts ALL demo servers on consecutive ports (8201-8205) and opens the landing page:
    ```
    Content Pipeline:   http://localhost:8201
    Research Assistant:  http://localhost:8202
    Incident Response:   http://localhost:8203
    API Explorer:        http://localhost:8204
    Node Explorer:       http://localhost:8205
    Landing Page:        http://localhost:8200
    ```
  - Each individual demo can still be run standalone via its own `run_demo.py`
- Also create `demo/README.md` documenting the demo suite

**Acceptance Criteria:**
- [ ] `python demo/run_all.py` starts all demos and opens landing page
- [ ] Landing page lists all 5 demos with descriptions
- [ ] Each demo card links to the correct port
- [ ] Individual demos still work standalone
- [ ] `demo/README.md` documents how to run

## Out of Scope

- Production-quality UI design (demos should be clean but not polished)
- React, Vue, or any JS framework (vanilla JS + HTML only)
- npm, webpack, or any build tooling
- Authentication on demo servers
- Persistent storage (all demos use in-memory SQLite)
- Mobile responsiveness (desktop-first is fine)

## Technical Considerations

- All demos use `Roots(storage=SqliteBackend(":memory:"))` — fresh state on every restart
- The graph renderer JS needs to handle the specific JSON structure from `GET /runs/{id}/graph`
- Graph node positions: either hardcode in the YAML metadata or compute a simple top-to-bottom layout in JS
- Use `webbrowser.open()` for auto-opening browser
- Each demo's FastAPI app mounts the Roots API + its own static files + the shared `_common/` assets
- The node-explorer's step-through mode needs a custom endpoint that doesn't exist in the base Roots API — this is a demo-specific addition
- Keep agent sleep times short (0.3-0.5s) so demos feel snappy
- All agents return deterministic results for reproducibility (no randomness unless seeded)

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
