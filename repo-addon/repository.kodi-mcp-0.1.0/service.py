#!/usr/bin/env python3
"""Kodi MCP Repository Service.

This stub file is required for Kodi to recognize the repository addon.
The actual repository metadata is served via HTTP endpoints.
"""


def main():
    print("Kodi MCP Repository Service initialized")
    # Repository addons run in the background and serve metadata
    # through Kodi's repository interface, not as standalone scripts


if __name__ == "__main__":
    main()
