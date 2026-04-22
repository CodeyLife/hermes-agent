# Hermes MCP-only

This repository has been trimmed to the MCP server surface only.

## Kept
- `mcp_serve.py` MCP stdio server
- MCP-related tools under `tools/`
- Bundled workflow skills under `my_skills/`
- Minimal support modules (`hermes_constants.py`, `hermes_state.py`, `agent/skill_*`)
- MCP regression tests in `tests/test_mcp_serve.py`

## Run
- Installed entrypoint: `hermes mcp serve`
- Direct Python: `python -c "from mcp_serve import run_mcp_server; run_mcp_server()"`
- Windows helper: `mcp-serve.bat`

## Verify
```bash
scripts/run_tests.sh tests/test_mcp_serve.py
```
