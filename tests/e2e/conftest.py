"""Boot the real app (uvicorn, real HTTP) on a free port for browser tests."""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import replace

import httpx
import pytest
import uvicorn

from app.config import Settings
from app.main import create_app


@pytest.fixture
def live_server():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = create_app(replace(Settings(), tool_timeout_seconds=0.5), persist_traces=False)
    server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            httpx.get(f"{url}/health")
            break
        except httpx.TransportError:
            time.sleep(0.1)
    yield url
    server.should_exit = True
    thread.join(timeout=5)
