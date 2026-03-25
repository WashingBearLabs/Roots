#!/usr/bin/env python3
"""Launch all Roots demo servers and open the landing page.

Usage:
    python demo/run_all.py
"""

from __future__ import annotations

import signal
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent

DEMOS = [
    ("Content Pipeline", "content-pipeline", 8201),
    ("Research Assistant", "research-assistant", 8202),
    ("Incident Response", "incident-response", 8203),
    ("API Explorer", "api-explorer", 8204),
    ("Node Explorer", "node-explorer", 8205),
]

LANDING_PORT = 8200


def start_demo(directory: str) -> subprocess.Popen[bytes]:
    """Start a single demo server as a subprocess."""
    demo_path = DEMO_DIR / directory / "run_demo.py"
    return subprocess.Popen(
        [sys.executable, str(demo_path)],
        cwd=str(DEMO_DIR / directory),
    )


def start_landing_page() -> subprocess.Popen[bytes]:
    """Start the landing page server via uvicorn."""
    static_dir = str(DEMO_DIR / "index" / "static")
    # Inline a tiny ASGI app that serves the landing page
    code = (
        "import uvicorn\n"
        "from fastapi import FastAPI\n"
        "from fastapi.responses import FileResponse\n"
        "from fastapi.staticfiles import StaticFiles\n"
        "from pathlib import Path\n"
        f"STATIC = {static_dir!r}\n"
        "app = FastAPI(title='Roots Demo Hub')\n"
        "@app.get('/')\n"
        "async def index(): return FileResponse(Path(STATIC) / 'index.html')\n"
        "app.mount('/static', StaticFiles(directory=STATIC), name='static')\n"
        f"uvicorn.run(app, host='127.0.0.1', port={LANDING_PORT})\n"
    )
    return subprocess.Popen([sys.executable, "-c", code])


def print_port_table() -> None:
    """Print a summary of all running demo servers."""
    print("\n" + "=" * 50)
    print("  Roots Demo Hub")
    print("=" * 50)
    print(f"  {'Demo':<22} {'Port':<8} {'URL'}")
    print("-" * 50)
    print(f"  {'Landing Page':<22} {LANDING_PORT:<8} http://localhost:{LANDING_PORT}")
    for name, _, port in DEMOS:
        print(f"  {name:<22} {port:<8} http://localhost:{port}")
    print("=" * 50)
    print("  Press Ctrl+C to stop all servers")
    print("=" * 50 + "\n")


def main() -> None:
    processes: list[subprocess.Popen[bytes]] = []

    # Start all demo servers
    for _, directory, _port in DEMOS:
        proc = start_demo(directory)
        processes.append(proc)

    # Start landing page
    landing_proc = start_landing_page()
    processes.append(landing_proc)

    print_port_table()

    # Open browser after a short delay
    def _open_browser() -> None:
        time.sleep(2.0)
        webbrowser.open(f"http://localhost:{LANDING_PORT}")

    threading.Thread(target=_open_browser, daemon=True).start()

    # Handle Ctrl+C: terminate all subprocesses
    def shutdown(_signum: int, _frame: object) -> None:
        print("\nShutting down all demo servers...")
        for proc in processes:
            proc.terminate()
        for proc in processes:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        print("All servers stopped.")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Wait for any process to exit (keeps main alive)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown(signal.SIGINT, None)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
