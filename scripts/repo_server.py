#!/usr/bin/env python3
"""
Kodi Repo Server

Serves the authoritative project-root Kodi dev repository over HTTP so Kodi can
install/update addons without changing externally visible URLs or paths.

Usage:
    python repo_server.py

Served content source of truth:
    <project>/repo/

Legacy note:
    `server/repo*` is not authoritative and must not be used by this script.
"""

import http.server
import socketserver
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kodi_mcp_server.paths import AUTHORITATIVE_REPO_ROOT

REPO_ROOT = AUTHORITATIVE_REPO_ROOT
PORT = 8001

class KodiRepoHandler(http.server.SimpleHTTPRequestHandler):
    """Custom handler that serves from repo root."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=REPO_ROOT, **kwargs)
    
    def log_message(self, format, *args):
        """Log to stderr for easier debugging."""
        print(f"[repo-server] {args[0]}", file=sys.stderr)

class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

def main():
    with ReusableTCPServer(("", PORT), KodiRepoHandler) as httpd:
        print(f"Kodi repo server running at http://localhost:{PORT}/")
        print(f"Serving from: {REPO_ROOT}")
        print(f"Repo structure:")
        print(f"  - http://localhost:{PORT}/dev-repo/addons.xml")
        print(f"  - http://localhost:{PORT}/dev-repo/addons.xml.md5")
        print(f"  - http://localhost:{PORT}/dev-repo/zips/")
        print("\nPress Ctrl+C to stop")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down repo server...")

if __name__ == "__main__":
    main()
