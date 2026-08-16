"""LlamaServerDirectorProvider: managed server lifecycle (AGENT.md §37)."""

from __future__ import annotations

import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from aidirector.ai.providers.director import LlamaServerDirectorProvider
from aidirector.ai.schemas import Message
from aidirector.config import ModelEndpointConfig
from aidirector.director.schemas import StoryPlan
from aidirector.errors import ProviderError

# A stand-in "llama-server": answers /health and /v1/chat/completions.
FAKE_SERVER = r"""
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        ok = self.path == "/health"
        self.send_response(200 if ok else 404); self.end_headers()
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = json.dumps({"choices": [{"message": {"content": json.dumps({
            "concept": "quiet trip", "tone": "calm", "pace": "slow",
            "story_arc": ["arrival", "ending"],
        })}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
"""


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def make_cfg(port: int, **extra) -> ModelEndpointConfig:
    return ModelEndpointConfig(
        provider="llama-server",
        model="Qwen/Qwen3-8B-GGUF:Q4_K_M",
        context_length=4096,
        extra={
            "binary": sys.executable,
            "args": ["-c", FAKE_SERVER, str(port)],
            "port": port,
            "startup_timeout": 30,
            **extra,
        },
    )


async def test_spawns_waits_and_stops():
    port = free_port()
    provider = LlamaServerDirectorProvider(make_cfg(port))
    assert not provider.gpu_resident

    await provider.load()
    assert provider._process is not None
    assert provider._process.poll() is None, "server process running"

    story = await provider.generate_structured(
        [Message(role="user", content="plan")], StoryPlan
    )
    assert story.concept == "quiet trip"

    process = provider._process
    await provider.unload()
    assert process.poll() is not None, "owned server stopped on unload"


async def test_reuses_external_server():
    port = free_port()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): ...
        def do_GET(self):
            self.send_response(200 if self.path == "/health" else 404)
            self.end_headers()

    server = HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = LlamaServerDirectorProvider(make_cfg(port))
        await provider.load()
        assert provider._process is None, "external server reused, not spawned"
        await provider.unload()
        # external server must still be alive
        import httpx

        response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=5)
        assert response.status_code == 200
    finally:
        server.shutdown()


async def test_missing_binary_errors():
    port = free_port()
    cfg = ModelEndpointConfig(
        provider="llama-server", model="x.gguf",
        extra={"binary": "/no/such/llama-server", "port": port},
    )
    provider = LlamaServerDirectorProvider(cfg)
    with pytest.raises(ProviderError, match="binary not found"):
        await provider.load()


def test_command_building():
    cfg = ModelEndpointConfig(
        provider="llama-server", model="Qwen/Qwen3-8B-GGUF:Q4_K_M",
        context_length=8192,
        extra={"port": 9999, "gpu_layers": 50, "extra_args": ["--mlock"]},
    )
    command = LlamaServerDirectorProvider(cfg)._build_command()
    assert command[0] == "llama-server"
    assert "-hf" in command and "Qwen/Qwen3-8B-GGUF:Q4_K_M" in command
    assert command[command.index("-c") + 1] == "8192"
    assert command[command.index("-ngl") + 1] == "50"
    assert command[-1] == "--mlock"

    local = ModelEndpointConfig(
        provider="llama-server", model="/models/qwen3.gguf", extra={"port": 9999},
    )
    command = LlamaServerDirectorProvider(local)._build_command()
    assert command[1] == "-m" and command[2] == "/models/qwen3.gguf"


# A stand-in multimodal server: /health + /v1/chat/completions -> VisionAnalysis.
FAKE_VISION_SERVER = r"""
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(200 if self.path == "/health" else 404); self.end_headers()
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = json.dumps({"choices": [{"message": {"content": json.dumps({
            "description": "a train arrives at the station",
            "mood": ["calm"], "camera_motion": "static",
        })}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
"""


async def test_llama_server_vision_provider(tmp_path):
    from aidirector.ai.providers.vision import LlamaServerVisionProvider
    from aidirector.ai.schemas import ImageInput, VisionContext

    port = free_port()
    cfg = ModelEndpointConfig(
        provider="llama-server", model="Qwen3.8-27B-GGUF:UD-IQ3_XXS",
        extra={"binary": sys.executable,
               "args": ["-c", FAKE_VISION_SERVER, str(port)],
               "port": port, "startup_timeout": 30},
    )
    provider = LlamaServerVisionProvider(cfg)
    assert not provider.gpu_resident, "server process owns the VRAM"

    frame = tmp_path / "frame.jpg"
    frame.write_bytes(bytes.fromhex("ffd8ffdb") + b"\x00" * 16)

    await provider.load()
    process = provider._server._process
    assert process is not None and process.poll() is None

    analysis = await provider.analyze_segment(
        [ImageInput(path=frame)], VisionContext(asset_name="clip.mp4")
    )
    assert analysis.description == "a train arrives at the station"
    assert analysis.mood == ["calm"]

    await provider.unload()
    assert process.poll() is not None, "owned server stopped on unload"
