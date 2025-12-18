from __future__ import annotations

import logging
from types import SimpleNamespace
from pydantic import BaseModel
from box import Box
from in_layers.core import CrossLayerProps
from in_layers.core.layers.features import create as create_features
from in_layers.core.entries import load_system, SystemProps
from in_layers.core.models.libs import model
from in_layers.core.models.query import query_builder
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


def test_layers_load_models_and_can_be_used_in_services():
    class MyFeature:
        def __init__(self, ctx):
            self._ctx = ctx

        def echo(self, x: Mapping[str, Any]):
            return "F:" + x["x"]

    @model(domain="mydomain", plural_name="MyModels")
    class MyModel(BaseModel):
        id: str
        name: str

    class MyServices:
        def __init__(self, ctx):
            self._ctx = ctx

        def return_a_model_instance(self):
            print("in here")
            print(self._ctx.models.mydomain.get_models())
            return self._ctx.models.mydomain.get_models().MyModels.instance(
                id="123", name="John Doe"
            )

    class MyDomain(Domain):
        name = "mydomain"
        features = SimpleNamespace(create=MyFeature)
        services = SimpleNamespace(create=MyServices)
        models = SimpleNamespace(MyModel=MyModel)

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
    res = sys.services.mydomain.return_a_model_instance()
    assert res.get.id() == "123"
    assert res.get.name() == "John Doe"


def test_layers_uses_custom_model_backend():
    class MyFeature:
        def __init__(self, ctx):
            self._ctx = ctx

        def echo(self, x: Mapping[str, Any]):
            return "F:" + x["x"]

    @model(domain="mydomain", plural_name="MyModels")
    class MyModel(BaseModel):
        id: str
        name: str

    class MyServices:
        def __init__(self, ctx):
            self._ctx = ctx

    class AnotherServices:
        def __init__(self, ctx):
            self._ctx = ctx

        def get_model_backend(self, model_definition):
            return Box(
                create=lambda model, data: Box(id="987", name="John Doe"),
                retrieve=lambda model, id: Box(id="123", name="John Doe"),
                update=lambda model, id, data: Box(id="123", name="John Doe"),
                delete=lambda model, id: None,
                search=lambda model, query: Box(instances=[], page=None),
            )

    class AnotherDomain(Domain):
        name = "anotherdomain"
        services = SimpleNamespace(create=AnotherServices)

    class MyDomain(Domain):
        name = "mydomain"
        features = SimpleNamespace(create=MyFeature)
        services = SimpleNamespace(create=MyServices)
        models = SimpleNamespace(MyModel=MyModel)

    config = Box(
        system_name="test",
        environment="test",
        in_layers_core=Box(
            logging=Box(
                log_level=LogLevelNames.info,
                log_format=LogFormat.simple,
            ),
            layer_order=["services", "features"],
            domains=[AnotherDomain, MyDomain],
            models=Box(
                model_backend="anotherdomain",
                model_services_cruds=True,
                model_features_cruds=True,
            ),
        ),
    )

    sys = load_system(SystemProps(environment="test", config=config))
    res = sys.services.mydomain.cruds.MyModels.create(id="123", name="John Doe")
    assert res.id == "987"
    assert res.name == "John Doe"


def test_layers_puts_cruds_in_features():
    class MyFeature:
        def __init__(self, ctx):
            self._ctx = ctx

        def echo(self, x: Mapping[str, Any]):
            return "F:" + x["x"]

    @model(domain="mydomain", plural_name="MyModels")
    class MyModel(BaseModel):
        id: str
        name: str

    class MyServices:
        def __init__(self, ctx):
            self._ctx = ctx

    class MyDomain(Domain):
        name = "mydomain"
        features = SimpleNamespace(create=MyFeature)
        services = SimpleNamespace(create=MyServices)
        models = SimpleNamespace(MyModel=MyModel)

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
            models=Box(
                model_services_cruds=True,
                model_features_cruds=True,
            ),
        ),
    )

    sys = load_system(SystemProps(environment="test", config=config))
    try:
        sys.features.mydomain.cruds.MyModels.create(id="123", name="John Doe")
        sys.features.mydomain.cruds.MyModels.retrieve("123")
        sys.features.mydomain.cruds.MyModels.update("123", name="Jane Doe")
        sys.features.mydomain.cruds.MyModels.delete("123")
        sys.features.mydomain.cruds.MyModels.search(
            query=query_builder().property("name", "John Doe").compile()
        )
        sys.features.mydomain.cruds.MyModels.bulk_insert(
            [{"name": "John Doe"}, {"name": "Jane Doe"}]
        )
        sys.features.mydomain.cruds.MyModels.bulk_delete(["123", "456"])
    except Exception as e:
        print(e)
        assert False
