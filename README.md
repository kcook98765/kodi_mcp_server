# kodi_mcp_server

Custom Python middle-layer server for remote Kodi integration.

## Purpose

This project runs on the OpenClaw host and acts as the control layer between:

1. local command-line wrappers used by an agent
2. the custom remote Kodi addon / bridge endpoints
3. Kodi JSON-RPC, repo serving, and addon deployment/update flows

The long-term goal is to provide a stable, structured backend that local CLI tools can call deterministically.

## Current Responsibilities

- expose server endpoints for Kodi-related operations
- adapt and normalize remote Kodi / bridge interactions
- provide addon orchestration helpers
- support repo serving / publish flows for Kodi addon packages
- provide a foundation for strict machine-friendly command wrappers

## Architectural Position

This project is **not** the final agent-facing interface.

The intended layering is:

- agent
- local CLI wrappers
- `kodi_mcp_server`
- remote Kodi addon / bridge + Kodi JSON-RPC
- Kodi runtime

## Current Status

This repository is now the canonical git-controlled codebase for the custom Kodi middle-layer server.

The current focus is to:
- stabilize configuration loading
- standardize structured responses
- clarify entrypoints and runtime modes
- define a clean contract for future CLI wrappers

## Scope Notes

This repo may include:
- server code
- transport/client layers
- repo/build/publish helpers
- scripts for development and validation

Agent workspace control files such as `AGENTS.md`, `BOOTSTRAP.md`, and related OpenClaw guidance files live outside this repo at the workspace root.

## Development Direction

Priority order:

1. stabilize and simplify server behavior
2. make outputs strict and predictable
3. define the CLI-facing contract
4. add thin local command wrappers
5. update agent docs to reference wrapper commands instead of generic MCP language
