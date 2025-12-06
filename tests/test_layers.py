from __future__ import annotations

import asyncio
import logging

from in_layers.core.entries import load_system, SystemProps
from box import Box
from in_layers.core.protocols import CoreNamespace, LogFormat, LogLevelNames


def _config():
    class DemoServices:
        def __init__(self, ctx):
            self._ctx = ctx

        def echo(self, x, cross_layer_props=None):
            return ("S:" + x, cross_layer_props)

    class DemoFeatures:
        def __init__(self, ctx):
            self._ctx = ctx

        def callEcho(self, x, cross_layer_props=None):
            res, passed = self._ctx.services.demo.echo(x, cross_layer_props)
            return ("F:" + res, passed)

    def services_create(ctx):
        return DemoServices(ctx)

    def features_create(ctx):
        return DemoFeatures(ctx)

    return {
        "system_name": "test",
        "environment": "test",
        CoreNamespace.root.value: {
            "logging": {
                "log_level": LogLevelNames.info,
                "log_format": LogFormat.simple,
            },
            "layer_order": ["services", "features"],
            "apps": [
                {
                    "name": "demo",
                    "services": {"create": services_create},
                    "features": {"create": features_create},
                }
            ],
        },
    }


def test_feature_calls_service_and_returns_expected_value():
    async def run():
        sys = await load_system(SystemProps(environment="test", config=_config()))
        res = sys.features.demo.callEcho("X")
        assert res[0] == "F:S:X"

    asyncio.run(run())


def test_crosslayer_ids_present_in_result():
    async def run():
        sys = await load_system(SystemProps(environment="test", config=_config()))
        res = sys.features.demo.callEcho("X")
        assert isinstance(res[1], dict)
        assert "logging" in res[1]
        assert "ids" in res[1]["logging"]
        ids = res[1]["logging"]["ids"]
        assert any(isinstance(obj, dict) and "function_call_id" in obj for obj in ids)

    asyncio.run(run())


def test_wrapper_logs_emitted(caplog):
    async def run():
        with caplog.at_level(logging.INFO):
            sys = await load_system(SystemProps(environment="test", config=_config()))
            _ = sys.features.demo.callEcho("X")
            joined = " ".join(caplog.messages)
            assert ("Executing features function" in joined) or (
                "Executed features function" in joined
            )

    asyncio.run(run())


def test_feature_callable_exposed():
    async def run():
        sys = await load_system(SystemProps(environment="test", config=_config()))
        assert callable(sys.features.demo.callEcho)

    asyncio.run(run())
