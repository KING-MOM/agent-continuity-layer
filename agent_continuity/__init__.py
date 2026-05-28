"""Python SDK for the agent-continuity substrate.

The SDK is intentionally thin: it shells out to the installed
`agent-continuity` CLI and uses the same M9/M13 operation surface as shell,
MCP, bundle, and bridge transports. It does not reimplement queue, decision,
or context semantics.
"""

from .substrate import CommandResult, Substrate, SubstrateCommandError, SubstrateError

__all__ = [
    "CommandResult",
    "Substrate",
    "SubstrateCommandError",
    "SubstrateError",
]
