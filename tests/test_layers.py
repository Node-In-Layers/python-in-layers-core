from __future__ import annotations

import logging
from types import SimpleNamespace

from box import Box
from in_layers.core import CrossLayerProps
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

    instance = features.load_layers()


def test_feature_calls_service_and_returns_expected_value():
    sys = load_system(SystemProps(environment="test", config=_config()))
    res = sys.features.demo.callEcho("X")
    assert res[0] == "F:S:X"


def test_crosslayer_ids_present_in_result():
    sys = load_system(SystemProps(environment="test", config=_config()))
    res = sys.features.demo.callEcho("X")
    assert isinstance(res[1], dict)
    assert "logging" in res[1]
    assert "ids" in res[1]["logging"]
    ids = res[1]["logging"]["ids"]
    assert any(isinstance(obj, dict) and "function_call_id" in obj for obj in ids)


def test_wrapper_logs_emitted(caplog):
    with caplog.at_level(logging.INFO):
        sys = load_system(SystemProps(environment="test", config=_config()))
        _ = sys.features.demo.callEcho("X")
        joined = " ".join(caplog.messages)
        assert ("Executing features function" in joined) or (
            "Executed features function" in joined
        )


def test_feature_callable_exposed():
    sys = load_system(SystemProps(environment="test", config=_config()))
    assert callable(sys.features.demo.callEcho)


def test_pydantic_model_args_with_no_cross_layer_props():
    class MyFeature:
        def __init__(self, ctx):
            self._ctx = ctx

        def echo(self, x: Mapping[str, Any]):
            return "F:" + x["x"]

    class MyDomain(Domain):
        name = "my"
        features = SimpleNamespace(create=MyFeature)

    config = Box(
        system_name="test",
        environment="test",
        in_layers_core=Box(
            logging=Box(
                log_level=LogLevelNames.info,
                log_format=LogFormat.simple,
            ),
            layer_order=["services", "features"],
            domains=[MyDomain],
        ),
    )

    sys = load_system(SystemProps(environment="test", config=config))
    res = sys.features.my.echo({"x": "X"})
    assert res == "F:X"


def test_pydantic_model_args_with_cross_layer_props_as_optional():
    class MyFeature:
        def __init__(self, ctx):
            self._ctx = ctx

        def echo(
            self, x: Mapping[str, Any], cross_layer_props: CrossLayerProps | None = None
        ):
            return ("F:" + x["x"], cross_layer_props)

    class MyDomain(Domain):
        name = "my"
        features = SimpleNamespace(create=MyFeature)

    config = Box(
        system_name="test",
        environment="test",
        in_layers_core=Box(
            logging=Box(
                log_level=LogLevelNames.info,
                log_format=LogFormat.simple,
            ),
            layer_order=["services", "features"],
            domains=[MyDomain],
        ),
    )

    sys = load_system(SystemProps(environment="test", config=config))
    res = sys.features.my.echo({"x": "X"})
    assert res[0] == "F:X"


def test_pydantic_model_args_with_cross_layer_props_as_required():
    class MyFeature:
        def __init__(self, ctx):
            self._ctx = ctx

        def echo(self, x: Mapping[str, Any], cross_layer_props: CrossLayerProps):
            return ("F:" + x["x"], cross_layer_props)

    class MyDomain(Domain):
        name = "my"
        features = SimpleNamespace(create=MyFeature)

    config = Box(
        system_name="test",
        environment="test",
        in_layers_core=Box(
            logging=Box(
                log_level=LogLevelNames.info,
                log_format=LogFormat.simple,
            ),
            layer_order=["services", "features"],
            domains=[MyDomain],
        ),
    )

    sys = load_system(SystemProps(environment="test", config=config))
    res = sys.features.my.echo({"x": "X"}, Box(logging=Box(ids=[{"id1": "123"}])))
    assert res[0] == "F:X"
    assert any(x.id1 == "123" for x in res[1].logging.ids)
