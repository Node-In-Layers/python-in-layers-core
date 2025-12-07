from __future__ import annotations

import asyncio
from types import SimpleNamespace
from box import Box

from in_layers.core.entries import load_system, SystemProps
from in_layers.core.protocols import (
    LogFormat,
    LogLevelNames,
    Domain,
)


def _config_multi():
    class L1Services:
        def __init__(self, ctx):
            self._ctx = ctx

        def ping(self, x, cross=None):
            return "l1:" + x

    class L1Features:
        def __init__(self, ctx):
            self._ctx = ctx

        def callPing(self, x, cross=None):
            return self._ctx.services.layer1.ping(x, cross)

    class L2Services:
        def __init__(self, ctx):
            self._ctx = ctx

        def ping(self, x, cross=None):
            return "l2:" + x

    class L2Features:
        def __init__(self, ctx):
            self._ctx = ctx

        def callPing(self, x, cross=None):
            return self._ctx.services.layer2.ping(x, cross)

    class Domain1(Domain):
        name = "layer1"
        services = SimpleNamespace(create=lambda ctx: L1Services(ctx))
        features = SimpleNamespace(create=lambda ctx: L1Features(ctx))

    class Domain2(Domain):
        name = "layer2"
        services = SimpleNamespace(create=lambda ctx: L2Services(ctx))
        features = SimpleNamespace(create=lambda ctx: L2Features(ctx))

    return Box(
        system_name="test",
        environment="test",
        in_layers_core=Box(
            logging=Box(
                log_level=LogLevelNames.info,
                log_format=LogFormat.simple,
            ),
            layer_order=["services", "features"],
            domains=[Domain1, Domain2],
        ),
    )


def test_load_system_exposes_all_domains_layers():
    async def run():
        system = await load_system(
            SystemProps(environment="test", config=_config_multi())
        )

        # Existence checks for both domains on both layers (clear, explicit)
        features = system.features
        services = system.services
        assert (
            "layer1" in features
        ), f"Expected features to include 'layer1'. Keys: {list(features.keys())}"
        assert (
            "layer2" in features
        ), f"Expected features to include 'layer2'. Keys: {list(features.keys())}"
        assert (
            "layer1" in services
        ), f"Expected services to include 'layer1'. Keys: {list(services.keys())}"
        assert (
            "layer2" in services
        ), f"Expected services to include 'layer2'. Keys: {list(services.keys())}"

        # Sanity checks: methods from both domains are callable and independent
        assert system.services.layer1.ping("x") == "l1:x"
        assert system.services.layer2.ping("y") == "l2:y"
        assert system.features.layer1.callPing("a") == "l1:a"
        assert system.features.layer2.callPing("b") == "l2:b"

    asyncio.run(run())
