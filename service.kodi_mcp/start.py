#!/usr/bin/env python3
"""Service entry point for service.kodi_mcp."""

import logging

logger = logging.getLogger("kodi_mcp")

def start():
    logger.info("service.kodi_mcp starting")

if __name__ == "__main__":
    start()
