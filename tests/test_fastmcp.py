"""
Special tests for testing FastMCP integration.
"""

from types import SimpleNamespace
from box import Box
from in_layers.core.entries import load_system, SystemProps
from in_layers.core.protocols import (
    CoreConfig,
    CoreLoggingConfig,
    LogFormat,
    LogLevelNames,
    Domain,
)
from in_layers.core.protocols import Config
from fastmcp import FastMCP


def _config():
    class FastMCPFeatures:
        def __init__(self, ctx):
            self._ctx = ctx

        def callPing(self, x):
            return "pong:" + x

    class FastMCPDomain(Domain):
        name = "fastmcp"
        features = SimpleNamespace(create=FastMCPFeatures)

    return Box(
        system_name="test",
        environment="test",
        in_layers_core=Box(
            logging=Box(
                log_level=LogLevelNames.info,
                log_format=LogFormat.simple,
            ),
            layer_order=["services", "features"],
            domains=[FastMCPDomain],
        ),
    )


def test_fastmcp_can_create_mcp_tool_for_feature():
    config = _config()
    system = load_system(SystemProps(environment="test", config=config))
    fastmcp = FastMCP()
    fastmcp.tool(system.features.fastmcp.callPing)
