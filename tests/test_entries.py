from __future__ import annotations

import asyncio
from types import SimpleNamespace
from box import Box

from in_layers.core.entries import load_system, SystemProps
from in_layers.core.protocols import (
    CoreNamespace,
    LogFormat,
    LogLevelNames,
    CoreConfig,
    CoreLoggingConfig,
    Domain,
)
from in_layers.core.protocols import Config


def _config():
    class DemoServices:
        def __init__(self, ctx):
            self._ctx = ctx

        def ping(self, x, cross=None):
            return ("pong:" + x, cross)

    class DemoFeatures:
        def __init__(self, ctx):
            self._ctx = ctx

        def callPing(self, x, cross=None):
            return self._ctx.services.demo.ping(x, cross)

    class DemoDomain(Domain):
        name = "demo"
        services = SimpleNamespace(create=lambda ctx: DemoServices(ctx))
        features = SimpleNamespace(create=lambda ctx: DemoFeatures(ctx))

    return Box(
        system_name="test",
        environment="test",
        in_layers_core=Box(
            logging=Box(
                log_level=LogLevelNames.info,
                log_format=LogFormat.simple,
            ),
            layer_order=["services", "features"],
            domains=[DemoDomain],
        ),
    )


def test_load_system_end_to_end():
    async def run():
        sys = await load_system(SystemProps(environment="test", config=_config()))
        ping = sys.services.demo.ping("x")[0]
        call_ping = sys.features.demo.callPing("y")[0]
        assert ping[0:5] == "pong:"
        assert call_ping[0:6] == "pong:y"

    asyncio.run(run())
