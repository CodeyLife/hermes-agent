"""Compatibility stub for tests that assert deterministic MCP recall avoids LLM summarization."""

async def async_call_llm(*args, **kwargs):
    raise RuntimeError("LLM summarization is not available in the MCP-only build")
