from __future__ import annotations

import asyncio
import logging

from in_layers.core.entries import load_system
from in_layers.core.protocols import CoreNamespace, LogFormat, LogLevelNames


def _config():
    class FirstLayer:
        def __init__(self, ctx):
            self._ctx = ctx

        def f(self, x, cross=None):
            return ("1:" + x, cross)

    class SecondLayer:
        def __init__(self, ctx):
            self._ctx = ctx

        def g(self, x, cross=None):
            return ("2:" + x, cross)

    class DemoFeatures:
        def __init__(self, ctx):
            self._ctx = ctx

        def callBoth(self, x, cross=None):
            a, _ = self._ctx.first_layer.demo.f(x, cross)
            b, _ = self._ctx.second_layer.demo.g(x, cross)
            return (a + b, cross)

    def first_create(ctx):
        return FirstLayer(ctx)

    def second_create(ctx):
        return SecondLayer(ctx)

    def features_create(ctx):
        return DemoFeatures(ctx)

    return {
        "system_name": "test",
        "environment": "test",
        CoreNamespace.root.value: {
            "logging": {"log_level": LogLevelNames.info, "log_format": LogFormat.simple},
            # composite layer with two sub-layers 'first' and 'second'
            "layer_order": ["services", ["first_layer", "second_layer"], "features"],
            "apps": [
                {
                  "name": "demo",
                  "first_layer": {"create": first_create},
                  "second_layer": {"create": second_create},
                  "features": {"create": features_create}
                }
            ],
        },
    }


def test_composite_layers_output_correct():
    async def run():
        sys = await load_system({"environment": "test", "config": _config()})
        out, _ = sys.features.demo.callBoth("X")
        assert out == "1:X2:X"

    asyncio.run(run())


def test_composite_layers_crosslayer_ids_present():
    async def run():
        sys = await load_system({"environment": "test", "config": _config()})
        _, cross = sys.features.demo.callBoth("X")
        assert isinstance(cross, dict)
        assert "logging" in cross
        assert "ids" in cross["logging"]

    asyncio.run(run())

