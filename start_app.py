#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""One-click launcher for the Music Catalog Manager.

This script:
  1) finds a free localhost port (default preference: 8000)
  2) starts the FastAPI backend (uvicorn) as a child process
  3) waits until the server is reachable
  4) opens the default web browser to /setup

It is intended to be invoked by the provided OS launcher scripts.

Notes:
- The server stops when this process stops (close the terminal window).
- If port 8000 is occupied, it will automatically pick another free port.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import webbrowser


def _is_port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        try:
            return s.connect_ex((host, port)) != 0
        except OSError:
            return False


def _find_free_port(host: str, preferred: int, max_tries: int = 50) -> int:
    if preferred <= 0 or preferred > 65535:
        preferred = 8000
    port = preferred
    for _ in range(max_tries):
        if _is_port_free(host, port):
            return port
        port += 1
        if port > 65535:
            port = 1024
    raise RuntimeError("No free TCP port found on localhost.")


def _wait_until_up(url: str, timeout_sec: float = 10.0) -> bool:
    # Avoid external deps (requests). Use a raw socket connect loop.
    # We only need to know the server is listening.
    # url example: http://127.0.0.1:8000/setup
    try:
        host_port = url.split("//", 1)[1].split("/", 1)[0]
        host, port_s = host_port.split(":", 1)
        port = int(port_s)
    except Exception:
        return False

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            if s.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.1)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Start Music Catalog Manager and open the browser.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000, help="Preferred port (auto-fallback if occupied)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the browser")
    parser.add_argument("--reload", action="store_true", help="Enable uvicorn auto-reload (developer mode)")
    args = parser.parse_args()

    # Ensure working directory is project root (where this file lives).
    here = os.path.dirname(os.path.abspath(__file__))
    os.chdir(here)

    port = _find_free_port(args.host, args.port)
    url = f"http://{args.host}:{port}/setup"

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        args.host,
        "--port",
        str(port),
    ]
    if args.reload:
        cmd.append("--reload")

    print("\n=== Catcity Music Asset Manager ===")
    print(f"Starting server on: {args.host}:{port}")
    print("Close this window to stop the server.")

    # Start backend
    proc = subprocess.Popen(cmd, stdout=None, stderr=None)

    # Wait until listening, then open browser
    if not args.no_browser:
        if _wait_until_up(url, timeout_sec=12.0):
            try:
                webbrowser.open(url)
                print(f"Opened browser: {url}")
            except Exception:
                print(f"Server is running. Please open: {url}")
        else:
            print(f"Server may still be starting. Please open: {url}")

    # Block until server exits
    try:
        return proc.wait()
    except KeyboardInterrupt:
        try:
            proc.terminate()
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
