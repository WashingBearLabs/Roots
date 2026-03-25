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

### US-001: Demo Server Infrastructure

**Description:** As a demo builder, I want a shared Python module that creates a FastAPI app from a Roots instance so that each demo doesn't reinvent the server setup.

**Implementation Hints:**
- Create `demo/_common/demo_server.py`:
  - `create_demo_app(roots: Roots, demo_name: str, static_dir: str, common_dir: str | None = None) -> FastAPI`:
    - Creates a FastAPI app
    - Stores `roots` on `app.state.roots`
    - Mounts Roots API routers (processes, runs, checkpoints, agents, graph, webhooks) under `/api/`
    - Mounts demo-specific static files at `/static/` from `static_dir`
    - Mounts shared assets at `/common/` from `common_dir` (defaults to `demo/_common/`)
    - Adds `GET /` that returns `FileResponse` for `{static_dir}/index.html`
    - Adds `GET /api/demo-info` returning `{"name": demo_name, "status": "ready"}`
  - `open_browser(port: int)`:
    - Spawns a thread that sleeps 1.5s then calls `webbrowser.open(f"http://localhost:{port}")`
  - `run_demo(roots: Roots, demo_name: str, static_dir: str, port: int = 8200)`:
    - Calls `create_demo_app`, calls `open_browser`, runs `uvicorn.run(app, host="127.0.0.1", port=port)`
- Create `demo/_common/__init__.py` (empty)
- Create `demo/__init__.py` (empty) — makes demo importable for run_all.py
- Add `.gitignore` in `demo/` ignoring `*.db`, `__pycache__/`, `.pyc`

**Acceptance Criteria:**
- [x] `demo/_common/demo_server.py` exists with all three functions
- [x] `create_demo_app` mounts Roots API routers and static files
- [x] Static files served at `/static/` (demo-specific) and `/common/` (shared)
- [x] `GET /` serves index.html
- [x] `open_browser` opens the URL after a delay
- [x] `demo/.gitignore` excludes db files and pycache
- [x] Tests verify the app factory creates a working FastAPI app

### US-002: Shared CSS and HTML Base Template

**Description:** As a demo builder, I want shared CSS and an HTML template so that all demos have a consistent, clean look without duplicating styles.

**Implementation Hints:**
- Create `demo/_common/styles.css`:
  - Dark theme: `--bg: #1a1a2e`, `--surface: #16213e`, `--text: #e0e0e0`, `--accent: #0f3460`, `--green: #4ecca3`, `--blue: #4a9ff5`, `--red: #e74c3c`, `--yellow: #f1c40f`, `--gray: #555`
  - CSS custom properties for all colors (easy theming)
  - Base layout: full-viewport flexbox with header, main (3-column or 2-column via CSS grid), footer
  - Card component: `.card` with surface background, subtle border, border-radius 8px, padding 1rem
  - Node status classes: `.node-pending` (gray), `.node-running` (blue + pulse animation), `.node-completed` (green), `.node-failed` (red), `.node-paused` (yellow)
  - Monospace font for code/data: `'JetBrains Mono', 'Fira Code', monospace`
  - Sans-serif for UI: `system-ui, -apple-system, sans-serif`
  - Button styles: `.btn-primary` (accent), `.btn-success` (green), `.btn-danger` (red), `.btn-outline` (border only)
  - Scrollable panels: `.panel-scroll` with max-height and overflow-y auto
  - Simple animations: `@keyframes pulse` for running nodes, `@keyframes fadeIn` for new elements
- Create `demo/_common/base.html`:
  - HTML5 template with `<meta charset="utf-8">`, viewport meta
  - Links to `/common/styles.css`
  - Script tags for `/common/graph-renderer.js`, `/common/state-viewer.js`, `/common/event-log.js`
  - Standard layout: `<header>` with demo name + process status badge, `<main>` with flexible content area, `<footer>` with "Powered by Roots" + link
  - This is a REFERENCE template — each demo copies and customizes it, it's not served directly

**Acceptance Criteria:**
- [x] `styles.css` exists with dark theme, all node status classes, card/button components
- [x] `base.html` exists as a reference template linking all shared assets
- [x] CSS uses custom properties for all colors (themeable)
- [x] Pulse animation defined for running nodes
- [x] No external CDN dependencies — fully self-contained
- [x] Looks clean and readable in a browser (manual visual check)

### US-003: Shared JS Components

**Description:** As a demo builder, I want shared JavaScript components for graph rendering, state viewing, and event logging so that each demo has consistent interactive elements.

**Implementation Hints:**
- Create `demo/_common/graph-renderer.js`:
  - `class GraphRenderer` — takes a container DOM element and renders Roots graph JSON as SVG
  - `render(graphData)` — clears container, draws SVG with:
    - Nodes as rounded rectangles (width 160, height 60) with label text + type subtitle + status badge
    - **Positions:** Read from `node.position` in graph JSON (`{x, y}`). If position is `{x:0, y:0}` (default), auto-layout: simple top-to-bottom flow, nodes spaced 100px vertically, parallel branches side by side
    - Edges as SVG `<line>` or `<path>` elements connecting node centers. Traversed edges are green, pending are gray dashed.
    - Node fill colors by status: pending=#555, running=#4a9ff5 (with CSS pulse), completed=#4ecca3, failed=#e74c3c, paused=#f1c40f, skipped=#333
    - Active node (status=running) gets a glow filter: `<filter id="glow">` with `feGaussianBlur`
  - Keep it simple: rectangular nodes, straight-line edges, no curved paths. This is demos, not a production graph editor.
  - `update(graphData)` — diff-updates existing SVG (update colors/status) without full re-render to avoid flicker
- Create `demo/_common/state-viewer.js`:
  - `class StateViewer` — takes a container element
  - `render(state, previousState)` — renders JSON as collapsible tree
  - Top-level keys are expandable sections. Values are syntax-highlighted (strings=green, numbers=blue, bools=yellow, null=gray)
  - Keys that changed since `previousState` are highlighted with a gold left-border
  - Use recursive DOM building — no library needed for a JSON tree
- Create `demo/_common/event-log.js`:
  - `class EventLog` — takes a container element
  - `addEvent(event)` — prepends a new event row (newest at top)
  - Each row: `[HH:MM:SS] <type-badge> <node-id> <description>`
  - Type badges color-coded: run.* = blue, node.* = green, agent.* = cyan, decision.* = purple, checkpoint.* = yellow, escalation.* = red
  - Auto-scroll to top on new event. Max 100 events displayed (remove oldest).
- Create `demo/_common/roots-client.js`:
  - `class RootsClient` — simple fetch wrapper for the Roots API
  - Methods: `createRun(processId, workItem)`, `getRun(runId)`, `getRunGraph(runId)`, `resolveCheckpoint(runId, decision, notes, redirectTo)`, `listRuns()`, `getProcessGraph(processId)`
  - `startPolling(runId, callback, intervalMs=500)` — polls `getRunGraph` at interval, calls callback with graph data. Slows to 3000ms when run status is completed/failed/paused/cancelled.
  - `stopPolling()` — clears the interval
  - All methods return promises (async fetch)

**Acceptance Criteria:**
- [x] `graph-renderer.js` renders graph JSON as SVG with status-colored nodes
- [x] Auto-layout works for nodes with default `{x:0, y:0}` positions
- [x] `update()` updates status colors without full re-render
- [x] `state-viewer.js` renders JSON tree with expandable sections
- [x] Changed keys highlighted when previous state provided
- [x] `event-log.js` shows scrolling events, color-coded by type
- [x] `roots-client.js` wraps all key API endpoints with polling support
- [x] Polling slows down when run is in terminal/paused state
- [x] No external dependencies — all vanilla JS

### US-004: Content Pipeline Demo

**Description:** As a new user, I want a content moderation pipeline demo so that I can see how agent pools and deterministic decisions work in a real scenario.

**Implementation Hints:**
- Create `demo/content-pipeline/`:
  - `process.yaml`:
    - `classify` (agent, output_key: classify_output) — classifies content type
    - `analyze` (agent_pool, parallel, output_key: analysis_output) — sentiment_analyzer + toxicity_scorer + spam_detector
    - `route` (decision, deterministic) — edges:
      - `approve`: condition `analysis_output.toxicity_score < 0.3 and analysis_output.spam_score < 0.3`
      - `flag_review`: condition `analysis_output.toxicity_score >= 0.3 and analysis_output.toxicity_score < 0.7`
      - `reject`: condition `analysis_output.toxicity_score >= 0.7 or analysis_output.spam_score >= 0.7`
    - `approve` (end, completed) / `flag_review` (end, completed) / `reject` (end, failed)
    - Hardcode node positions in metadata for clean top-to-bottom layout
  - `agents.py`:
    - `classify_content(input)` — keyword matching: returns `{"type": "article|comment|image_caption", "language": "en"}`
    - `analyze_sentiment(input)` — returns `{"sentiment": "positive|negative|neutral", "sentiment_score": 0.0-1.0}`
    - `score_toxicity(input)` — keyword list ("hate","kill","stupid",...) → returns `{"toxicity_score": 0.0-1.0, "flagged_words": [...]}`
    - `detect_spam(input)` — pattern matching (ALL CAPS, repeated chars, URLs) → returns `{"spam_score": 0.0-1.0, "indicators": [...]}`
    - Each agent sleeps `0.3-0.5s` (use `asyncio.sleep`) to simulate work
  - `static/index.html`:
    - Layout: graph panel (left 60%), results panel (right 40%)
    - Top bar: text input area (textarea) + "Submit" button
    - Quick-select sample buttons below input: "Clean article" / "Mild comment" / "Toxic post" / "Spam message" — each populates the textarea with preset text
    - On submit: `POST /api/runs` with `{"process_id": "content-pipeline", "work_item": {"text": "..."}}`
    - Store returned `run_id`, start polling with `RootsClient`
    - Graph panel: `GraphRenderer` shows process flow updating in real-time
    - Results panel: shows agent outputs as they appear in the state (poll state from graph response), then final decision outcome with badge (Approved/Flagged/Rejected)
  - `run_demo.py`: standard pattern — load process, register 4 agents, call `run_demo()`

**Acceptance Criteria:**
- [x] `python demo/content-pipeline/run_demo.py` starts server and opens browser
- [x] User can submit text and see the process execute in real-time
- [x] Agent pool parallel execution visible (3 agents run simultaneously)
- [x] Decision routing shows which path was taken
- [x] 4 sample texts produce different outcomes (approve/flag/reject)
- [x] Process completes in <3 seconds

### US-005: Research Assistant — Process, Agents, and Server

**Description:** As a demo builder, I want the research assistant's process definition, mock agents, and server setup so that the backend is complete and testable.

**Implementation Hints:**
- Create `demo/research-assistant/` directory
- `process.yaml`:
  - `topic_input` (checkpoint, prompt: "Enter a research topic to investigate")
  - `split` (fork) → 3 branches
  - `search_academic` (agent, output_key: academic_results)
  - `search_news` (agent, output_key: news_results)
  - `search_web` (agent, output_key: web_results)
  - `merge` (join, collect, collect_key: research_results)
  - `summarize` (agent, output_key: summary)
  - `quality_check` (decision, deterministic) — edges: `approve_publish` if `summary.source_count >= 3`, `insufficient` if `summary.source_count < 3`
  - `approve_publish` (checkpoint, prompt: "Review the summary and approve for publishing")
  - `publish` (end, completed) / `insufficient` (end, failed)
  - Hardcode node positions in metadata for clean layout
- `agents.py`:
  - `search_academic(input)` — returns `{"papers": [{"title": "...", "abstract": "...", "year": 2025},...]}`. 3-5 results based on topic keywords.
  - `search_news(input)` — returns `{"articles": [{"headline": "...", "source": "...", "date": "..."},...]}`. 3-5 results.
  - `search_web(input)` — returns `{"pages": [{"title": "...", "url": "...", "snippet": "..."},...]}`. 3-5 results.
  - `summarize(input)` — combines results into `{"summary_text": "...", "source_count": N, "key_findings": [...]}`
  - Each search agent sleeps 0.3-0.5s. Topic string seeds different results (hash-based selection from canned results).
- `run_demo.py`: load process, register 4 agents, call `run_demo()` from demo_server.py. Use same pattern as content-pipeline.

**Acceptance Criteria:**
- [x] `demo/research-assistant/process.yaml` parses and validates (including fork/join pairing)
- [x] All 4 agents registered and callable
- [x] `run_demo.py` starts server successfully
- [x] Process can be created and executed via API (test with curl or httpx)
- [x] Fork/join executes correctly with collected results

### US-006: Research Assistant — Frontend

**Description:** As a new user, I want the research assistant's web UI so that I can interact with checkpoints and see fork/join execution visually.

**Implementation Hints:**
- Create `demo/research-assistant/static/index.html`:
  - Include shared assets: `/common/styles.css`, `/common/graph-renderer.js`, `/common/state-viewer.js`, `/common/event-log.js`, `/common/roots-client.js`
  - Layout: graph panel (top 50%), content panels (bottom 50% — results left, summary right)
  - **Initial state:** Topic input field + "Start Research" button. On click: `POST /api/runs` with `{"process_id": "research-assistant", "work_item": {"topic": inputValue}}`. Store `run_id`. The process starts paused at the first checkpoint.
  - **Checkpoint resolution:** After run creation, auto-resolve the first checkpoint: `POST /api/runs/{id}/checkpoint` with `{"decision": "approve"}`. Then start polling.
  - **Fork/join visualization:** GraphRenderer shows 3 parallel branches updating as agents complete.
  - **Results display:** After join completes, show collected research in a tabbed panel (Academic / News / Web) — parse from `work_item_state.research_results` in the graph response.
  - **Summary display:** After summarize agent, show summary text.
  - **Second checkpoint:** When process pauses again, show "Approve" / "Reject" buttons. On click: resolve checkpoint with the chosen decision.
  - **Completion:** Show final status badge (Published / Insufficient).
  - Quick-select topic buttons: "AI Safety", "Climate Change", "Quantum Computing"

**Acceptance Criteria:**
- [x] `python demo/research-assistant/run_demo.py` starts server and opens browser
- [x] Topic input creates run and resolves first checkpoint
- [x] Fork/join branches visible executing in parallel
- [x] Collected results show all three sources in tabs
- [x] Summary displayed after summarize agent
- [x] Second checkpoint shows approve/reject buttons
- [x] Approve → completed, Reject → failed

### US-007: Incident Response — Backend

**Description:** As a demo builder, I want the incident response process definition, mock agents, mock LLM decision, and server setup so that the backend is complete and testable.

**Implementation Hints:**
- Create `demo/incident-response/`:
  - `process.yaml`:
    - `ingest` (agent, output_key: normalized_incident)
    - `enrich` (agent_pool, sequential, output_key: enriched_data) — threat_intel_lookup + geo_lookup
    - `triage` (decision, ai_bounded, confidence_threshold: 0.75, model: configured at runtime) — edges: isolate_endpoint / reset_credentials / block_ip / escalate_to_analyst / close_benign
    - `respond` (agent, output_key: response_result)
    - `document` (emit, event_type: "incident.response_complete", payload_keys: ["normalized_incident", "enriched_data", "response_result"])
    - `close` (end, completed)
    - Plus a separate path from triage escalation → `escalation_review` (checkpoint) → `respond`
  - `agents.py`:
    - `ingest_incident(input)` — normalizes raw incident data, returns `{"source_ip": "...", "event_type": "...", "severity": "...", "timestamp": "..."}`
    - `threat_intel_lookup(input)` — returns `{"threat_score": 0.0-1.0, "known_iocs": [...], "threat_category": "..."}`
    - `geo_lookup(input)` — returns `{"country": "...", "city": "...", "is_vpn": bool, "is_tor": bool}`
    - `execute_response(input)` — returns `{"action_taken": "...", "success": true, "details": "..."}`
  - `mock_decision.py` — A mock LLM callable that implements `LLMCompletionFunc`:
    - Keyword matching: "brute force" → reset_credentials (0.92), "malware" → isolate_endpoint (0.88), "exfiltration" → block_ip (0.85), "port scan" → close_benign (0.45 — below threshold, triggers escalation), default → escalate_to_analyst (0.6)
    - Returns `LLMResponse` with a ToolCall containing the decision JSON
  - `run_demo.py`:
    - Default: mock mode using `mock_decision.py` as `llm_callable`
    - `--model MODEL --base-url URL --api-key KEY`: live mode using `LLMConfig` + `openai_chat_completion`
    - Print on startup: `"Running in mock mode (no API key needed). Use --model gpt-4o-mini --api-key YOUR_KEY for live AI."`
  - `static/index.html`: Placeholder page (same pattern as research-assistant placeholder) that shows demo name and "Frontend coming in US-008"

**Acceptance Criteria:**
- [x] `demo/incident-response/process.yaml` parses and validates
- [x] All 4 agents registered and callable
- [x] mock_decision.py implements LLMCompletionFunc with keyword-based routing
- [x] `run_demo.py` starts server in mock mode by default
- [x] `--model` flag switches to live LLM mode

### US-008: Incident Response — Frontend

**Description:** As a new user, I want the incident response web UI so that I can see AI decisions, confidence thresholds, and escalation in action.

**Implementation Hints:**
- Replace `demo/incident-response/static/index.html` with full UI:
  - Layout: incident input (top), graph (middle-left), AI decision panel (middle-right), event log (bottom)
  - Pre-built scenario buttons: "Brute Force Login" / "Malware Callback" / "Data Exfiltration" / "Port Scan (Low Confidence)"
  - AI decision panel: shows selected edge, confidence as a progress bar (color: green >0.75, yellow 0.5-0.75, red <0.5), reasoning text
  - If escalated: panel changes to escalation view with "Escalation triggered — confidence {score} below threshold 0.75" + resolution buttons
  - Emit node: custom event visible in event log with incident summary

**Acceptance Criteria:**
- [ ] Incident input panel with form fields and scenario buttons
- [ ] AI decision panel shows confidence bar and reasoning
- [ ] "Port Scan" scenario triggers escalation display
- [ ] Emit node fires visible custom event in event log
- [ ] Pre-built scenarios produce different routing decisions

### US-009: API Explorer — Backend

**Description:** As a demo builder, I want the API explorer's process definition, echo agent, and server setup with webhook receiver routes so that the backend is complete and testable.

**Implementation Hints:**
- Create `demo/api-explorer/`:
  - `process.yaml` — Simple 3-node process: `echo_input` (agent) → `check` (decision, deterministic: always passes) → `done` (end). Pre-loaded for experimentation.
  - `agents.py` — `echo_agent(input)`: returns `{"echo": input["work_item_state"], "timestamp": "..."}`
  - `run_demo.py`:
    - Load process, register echo agent
    - Add custom routes:
      - `POST /api/webhook-receiver` — stores received webhooks in `app.state.received_events` list
      - `GET /api/received-events` — returns the list (for the event log panel)
    - On startup: register a webhook via storage pointing to `http://localhost:{port}/api/webhook-receiver` with events `["*"]`
    - run_demo()
  - `static/index.html`: Placeholder page that shows demo name and "Frontend coming in US-010"

**Acceptance Criteria:**
- [ ] `demo/api-explorer/process.yaml` parses and validates
- [ ] Echo agent registered
- [ ] Webhook receiver endpoint stores events
- [ ] `GET /api/received-events` returns stored events
- [ ] `run_demo.py` starts server with pre-loaded process

### US-010: API Explorer — Frontend

**Description:** As a new user, I want the API explorer web UI so that I can understand all available HTTP endpoints and interact with them directly.

**Implementation Hints:**
- Replace `demo/api-explorer/static/index.html` with full UI:
  - 3-panel layout: endpoint catalog (left 25%), request builder (center 50%), response viewer (right 25%)
  - **Endpoint catalog:** Collapsible groups: Processes, Runs, Checkpoints, Agents, Webhooks, Graph. Each endpoint: colored method badge (GET=green, POST=blue, PUT=yellow, DELETE=red) + path + 1-line description. Click to load into request builder.
  - **Request builder:** Method dropdown, path with editable params (e.g., `{run_id}` shows an input field), JSON body editor (textarea with syntax highlighting via wrapping in `<pre>` and coloring), "Send" button
  - **Response viewer:** Status code (color-coded: 2xx=green, 4xx=yellow, 5xx=red), response time in ms, formatted JSON body (collapsible tree using StateViewer)
  - **Bottom bar:** Pre-built recipe buttons: "Create Run", "Get Run Graph", "List Agents", "Register Webhook". Each populates the request builder with the correct method/path/body.
  - Endpoint data: hardcode the full endpoint list in a JS const (method, path, description, sample body). ~30 endpoints.

**Acceptance Criteria:**
- [ ] All endpoint categories visible with method badges
- [ ] Clicking endpoint loads into request builder
- [ ] Request builder sends requests and shows responses
- [ ] Pre-built recipes populate correct data
- [ ] Webhook events visible in event log

### US-011: Node Explorer — Process and Custom Endpoints

**Description:** As a demo builder, I want the node explorer's process definition, agents, and custom API endpoints so that step-through execution works.

**Implementation Hints:**
- Create `demo/node-explorer/`:
  - `process.yaml` — "Greatest hits" tour of every node type:
    1. `welcome` (checkpoint, prompt: "Welcome to the Roots Node Explorer! Click Continue to start the tour.")
    2. `classify` (agent, output_key: classify_output) — classifies sample work item
    3. `validate` (agent_pool, parallel, output_key: validation_output) — 3 validators: format_checker, schema_validator, content_analyzer
    4. `quality_gate` (decision, deterministic) — edges: `deep_analysis` if `validation_output.overall_score >= 0.7`, `needs_fix` (end, failed) otherwise
    5. `deep_analysis` (fork) → 2 branches
    6. Branch A: `analyze_content` (agent, output_key: content_analysis, retry: {max_attempts: 2}) — fails first call, succeeds second (demonstrates retry)
    7. Branch B: `analyze_metadata` (agent, output_key: metadata_analysis)
    8. `combine` (join, merge_all)
    9. `notify` (emit, event_type: "tour.analysis_complete", payload_keys: ["content_analysis", "metadata_analysis"])
    10. `complete` (end, completed)
    - Hardcode positions for clean visual layout (2 columns for fork branches)
  - `agents.py`:
    - Each agent has a descriptive docstring: `"""DEMO: Demonstrates the 'agent' node type. Output is written to state['classify_output']."""`
    - `classify_item(input)` — returns `{"category": "document", "confidence": 0.95}`
    - `check_format(input)`, `validate_schema(input)`, `analyze_content_quality(input)` — three validators returning scores, merged into `{"overall_score": 0.85, ...}`
    - `analyze_content_deep(input)` — tracks call count via closure. First call raises `Exception("Simulated transient failure")`. Second call returns `{"depth": "thorough", "findings": [...]}`. This demonstrates retry.
    - `analyze_metadata_deep(input)` — returns `{"metadata_score": 0.9, "fields_checked": 12}`
    - All agents sleep 0.3s
  - Add to `demo_server.py` (or create `demo/node-explorer/server_extensions.py`):
    - `POST /api/step` — Takes `{"run_id": "..."}`. Creates a `ProcessRunner` directly (import from `roots.core.orchestrator`), calls `tick()` once, returns the updated graph JSON. If the run is paused at a checkpoint, this auto-resolves with approve first, then ticks.
    - `POST /api/reset` — Takes `{"process_id": "..."}`. Creates a new run with a default work_item, returns `{"run_id": "..."}`.
    - `GET /api/tutorial/{node_type}` — Returns tutorial content from `tutorial_content.json`
    - These are added as extra routes on the demo's FastAPI app, not on the shared `demo_server.py`
  - `tutorial_content.json` — Tutorial data for all node types plus retry:
    ```json
    {
      "checkpoint": {"title": "Checkpoint", "what": "Pauses execution for human review...", "when": "Quality gates, approvals...", "config": "type: checkpoint\nconfig:\n  prompt: \"Review...\"", "tips": ["Creates a record in storage", "Resolve via POST /runs/{id}/checkpoint"]},
      "agent": {"title": "Agent", "what": "Executes a single registered agent...", ...},
      "agent_pool": {"title": "Agent Pool", "what": "Executes multiple agents...", ...},
      "decision": {"title": "Decision", "what": "Evaluates conditions and routes...", ...},
      "fork": {"title": "Fork", "what": "Splits execution into parallel branches...", ...},
      "join": {"title": "Join", "what": "Waits for all branches and merges results...", ...},
      "emit": {"title": "Emit", "what": "Fires a custom event to the event system...", ...},
      "end": {"title": "End", "what": "Marks the process as completed or failed...", ...},
      "retry": {"title": "Retry (Node Feature)", "what": "Automatically retries failed agent nodes...", ...}
    }
    ```

**Acceptance Criteria:**
- [ ] `process.yaml` uses all 8 node types plus retry
- [ ] Agents produce deterministic, educational outputs
- [ ] Retry agent fails first call, succeeds second
- [ ] `POST /api/step` advances one tick and returns graph
- [ ] `POST /api/reset` creates fresh run
- [ ] `GET /api/tutorial/{node_type}` returns tutorial content
- [ ] `tutorial_content.json` has entries for all 8 types + retry
- [ ] Tests verify step/reset endpoints work

### US-012: Node Explorer — Tutorial Panel UI

**Description:** As a new user, I want the tutorial panel to explain each node type as I step through the process.

**Implementation Hints:**
- In `demo/node-explorer/static/index.html`:
  - **Tutorial panel** (right side, 35% width):
    - Header: node type icon (emoji or unicode symbol) + title (e.g., "Agent Node")
    - Section: **"What this does"** — 2-3 sentence explanation
    - Section: **"When to use it"** — brief guidance
    - Section: **"Configuration"** — the YAML config shown in a `<pre>` block with basic syntax highlighting (keys in cyan, strings in green, numbers in blue)
    - Section: **"What happened"** — dynamically populated after the node executes. Shows the node's output from the state, or the decision taken, or the event emitted. Formatted as JSON.
    - Section: **"Try it yourself"** — copyable YAML snippet showing how to add this node type to a process
    - The panel updates when the current node changes (detected via polling — compare `run_status.current_node_id` with previous)
    - Load tutorial content from `/api/tutorial/{node_type}` when the current node changes
    - For the retry demonstration: show a special "Retry" section when the node has retry config, explaining what happened (attempt 1 failed, attempt 2 succeeded) pulled from the event log

**Acceptance Criteria:**
- [ ] Tutorial panel shows content for the current node
- [ ] All sections populated: what, when, config, what happened, try it yourself
- [ ] YAML config has basic syntax highlighting
- [ ] "What happened" updates after node executes with actual output
- [ ] Retry node shows attempt history
- [ ] Panel transitions smoothly when node changes

### US-013: Node Explorer — Interactive Controls and Graph

**Description:** As a new user, I want step-through controls and a live graph so that I can explore the process at my own pace.

**Implementation Hints:**
- In `demo/node-explorer/static/index.html`:
  - **Graph panel** (top-left, 65% width, 50% height):
    - Uses `GraphRenderer` from shared components
    - All nodes visible from the start (full process graph)
    - Current node highlighted with glow effect
    - Completed nodes show checkmark icon
    - Edge colors: traversed=green, pending=gray dashed
  - **State panel** (bottom-left):
    - Uses `StateViewer` from shared components
    - Shows full work_item_state
    - After each step, highlights the new/changed key
  - **Event log** (bottom-right):
    - Uses `EventLog` from shared components
    - Shows all events as they fire during stepping
  - **Control bar** (fixed bottom):
    - "Step" button (primary) — calls `POST /api/step`, updates graph + tutorial + state + events
    - "Auto-play" toggle — when on, calls step every 2 seconds automatically. Speed slider (1s - 5s).
    - "Reset" button (outline) — calls `POST /api/reset`, clears all panels, restarts from welcome checkpoint
    - Step counter: "Step 3 of ~10"
  - **Polling flow:**
    - NOT using continuous polling like other demos. Instead: click Step → call API → update all panels from response. This gives precise control.
    - Auto-play mode: `setInterval` that clicks Step programmatically
  - **Initial state:** Process created but not started. Graph shows all nodes as pending. Tutorial panel shows "Welcome" intro. Click Step to resolve the first checkpoint and begin.

**Acceptance Criteria:**
- [ ] Graph shows all nodes from the start with correct layout
- [ ] Step button advances one node and updates all panels
- [ ] Auto-play mode steps automatically at configurable speed
- [ ] Reset clears everything and restarts
- [ ] State panel shows accumulated state with change highlights
- [ ] Event log captures all events during execution
- [ ] Step counter tracks progress
- [ ] Process starts paused at welcome checkpoint

### US-014: Demo Landing Page

**Description:** As a new user, I want a landing page that lists all demos and lets me launch any of them.

**Implementation Hints:**
- Create `demo/index/static/index.html`:
  - Hero section: "Roots" in large text + "AI-native process orchestration framework" subtitle
  - Card grid (3 columns on desktop, 1 on narrow): one card per demo:
    - **Content Pipeline** — "See agent pools and deterministic decisions in action" — Features: agent, agent_pool, decision, end
    - **Research Assistant** — "Explore fork/join parallelism and human checkpoints" — Features: fork, join, checkpoint, decision
    - **Incident Response** — "Watch AI-powered decisions with confidence thresholds" — Features: ai_decision, escalation, retry, emit
    - **API Explorer** — "Interact with every Roots API endpoint" — Features: full HTTP API, webhooks
    - **Node Explorer** — "Interactive tutorial — learn every node type step by step" — Features: all 8 node types, step-through
  - Each card: colored accent bar (top), demo name, description, feature tags as pills, "Launch" button linking to `http://localhost:{port}`
  - Footer: "Built with Roots v0.1.0" + link to GitHub
  - Simple, clean — same dark theme as other demos
- Create `demo/run_all.py`:
  - Starts each demo server in a subprocess on consecutive ports:
    - Content Pipeline: 8201
    - Research Assistant: 8202
    - Incident Response: 8203
    - API Explorer: 8204
    - Node Explorer: 8205
  - Starts landing page server on 8200 (simple `uvicorn` serving the index)
  - Opens browser to `http://localhost:8200`
  - Prints port table to console
  - Handles Ctrl+C: terminates all subprocesses gracefully
- Create `demo/README.md`:
  - Quick start: `python demo/run_all.py`
  - Individual demo instructions
  - Port assignments
  - Screenshot descriptions (no actual screenshots needed)

**Acceptance Criteria:**
- [ ] `python demo/run_all.py` starts all demos and opens landing page
- [ ] Landing page shows all 5 demos with descriptions and feature tags
- [ ] Each card links to correct port
- [ ] Individual demos still work standalone via their own `run_demo.py`
- [ ] Ctrl+C stops all demo servers cleanly
- [ ] `demo/README.md` documents everything

## Out of Scope

- Production-quality UI design (clean but not polished)
- React, Vue, or any JS framework (vanilla JS + HTML only)
- npm, webpack, or any build tooling
- Authentication on demo servers
- Persistent storage (all demos use in-memory SQLite)
- Mobile responsiveness (desktop-first)
- Curved edge paths or advanced graph layout algorithms

## Technical Considerations

- All demos use `Roots(storage=SqliteBackend(":memory:"))` — fresh state on every restart
- Graph node positions: hardcoded in YAML metadata for each demo process. The JS auto-layout is a fallback only.
- The node-explorer's step mode uses on-demand requests (not continuous polling). Other demos poll at 500ms during runs, 3s when idle.
- The `POST /api/step` endpoint in node-explorer creates a `ProcessRunner` directly — this bypasses the `Orchestrator` class's polling loop. It's demo-specific, not a pattern for production use.
- Keep agent sleep times at 0.3-0.5s so demos feel snappy but execution is visible
- All agents return deterministic results (no randomness) for reproducibility
- The shared `roots-client.js` is the JS counterpart to the Python `Roots` class — it should feel natural to use
- Each demo's `index.html` includes shared assets via `<script src="/common/...">` and `<link href="/common/styles.css">`

## Related Documentation

- Architecture: [CODE_ARCH.md](../arch/CODE_ARCH.md)
