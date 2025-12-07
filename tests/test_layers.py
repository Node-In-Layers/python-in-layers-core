from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

from box import Box
from in_layers.core.layers.features import create as create_features
from in_layers.core.entries import load_system, SystemProps
from in_layers.core.protocols import (
    CoreConfig,
    CoreLoggingConfig,
    LogFormat,
    LogLevelNames,
    Domain,
    Config,
)


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

    class DemoDomain(Domain):
        name = "demo"
        services = SimpleNamespace(create=services_create)
        features = SimpleNamespace(create=features_create)

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


def _config_2():
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

    class DemoDomain(Domain):
        name = "demo"
        services = SimpleNamespace(create=services_create)
        features = SimpleNamespace(create=features_create)

    def custom_logger():
        pass

    return Box(
        system_name="test",
        environment="test",
        in_layers_core=Box(
            logging=Box(
                log_level=LogLevelNames.info,
                log_format=LogFormat.simple,
                custom_logger=custom_logger,
            ),
            layer_order=["services", "features"],
            domains=[DemoDomain],
        ),
    )


def test_deep_context_copy():
    class RootLogger:
        def get_logger(self, ctx):
            return Box(
                get_app_logger=lambda app_name: Box(
                    get_layer_logger=lambda layer: Box(
                        get_ids=lambda: [],
                        _log_wrap=lambda function_name, func: func,
                        trace=lambda message, data_or_error=None, options=None: None,
                        debug=lambda message, data_or_error=None, options=None: None,
                        info=lambda message, data_or_error=None, options=None: None,
                        warn=lambda message, data_or_error=None, options=None: None,
                        error=lambda message, data_or_error=None, options=None: None,
                    )
                ),
                trace=lambda message, data_or_error=None, options=None: None,
                debug=lambda message, data_or_error=None, options=None: None,
                info=lambda message, data_or_error=None, options=None: None,
                warn=lambda message, data_or_error=None, options=None: None,
                error=lambda message, data_or_error=None, options=None: None,
            )

    features = create_features(
        context=Box(
            services=Box(
                in_layers_core_layers=Box(load_layer=lambda app, layer, context: True)
            ),
            config=_config_2(),
            root_logger=RootLogger(),
        )
    )

    async def run():
        instance = await features.load_layers()
        return instance

    asyncio.run(run())


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
