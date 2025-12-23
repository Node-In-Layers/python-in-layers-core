from __future__ import annotations

import logging
from types import SimpleNamespace
from pydantic import BaseModel
from box import Box
from in_layers.core import CrossLayerProps
from in_layers.core.layers.features import create as create_features
from in_layers.core.layers.features import _call_with_optional_cross
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


# Tests for _call_with_optional_cross


def test_call_with_optional_cross_none_cross_props():
    """When cross_layer_props is None, should just pass through without injection."""

    def func(a, b):
        return (a, b)

    result = _call_with_optional_cross(func, [1, 2], {}, None)
    assert result == (1, 2)


def test_call_with_optional_cross_with_cross_kw_param():
    """When function has cross_layer_props keyword param, should inject via keyword."""

    def func(a, b, cross_layer_props=None):
        return (a, b, cross_layer_props)

    cross_props = Box(logging=Box(ids=[]))
    result = _call_with_optional_cross(func, [1, 2], {}, cross_props)
    assert result == (1, 2, cross_props)


def test_call_with_optional_cross_with_cross_kw_param_already_in_kwargs():
    """When cross_layer_props is already in kwargs, should not overwrite."""

    def func(a, b, cross_layer_props=None):
        return (a, b, cross_layer_props)

    existing_cross = Box(existing=True)
    cross_props = Box(logging=Box(ids=[]))
    result = _call_with_optional_cross(
        func, [1, 2], {"cross_layer_props": existing_cross}, cross_props
    )
    assert result == (1, 2, existing_cross)


def test_call_with_optional_cross_with_var_positional():
    """When function has *args, should not inject positionally."""

    def func(a, *args):
        return (a, args)

    cross_props = Box(logging=Box(ids=[]))
    result = _call_with_optional_cross(func, [1], {}, cross_props)
    # Should not inject cross_props, just pass through
    assert result == (1, ())


def test_call_with_optional_cross_positional_injection_when_room():
    """When there's room for positional injection, should inject."""

    def func(a, b):
        return (a, b)

    cross_props = Box(logging=Box(ids=[]))
    result = _call_with_optional_cross(func, [1], {}, cross_props)
    # Should inject cross_props as second positional arg
    assert result == (1, cross_props)


def test_call_with_optional_cross_positional_injection_conflict():
    """When next positional param is already in kwargs, should skip injection."""

    # Simulate the bug scenario: function with 2 params, args_no_cross has 1, next param in kwargs
    def func(a, request):
        return (a, request)

    cross_props = Box(logging=Box(ids=[]))
    request_obj = Box(data="test")
    # args_no_cross has 'a', next param 'request' is in kwargs
    # len(args_no_cross) + 1 = 2, len(explicit) = 2, so we'd try to inject
    # But 'request' is in kwargs, so we should skip injection
    result = _call_with_optional_cross(
        func, ["a_value"], {"request": request_obj}, cross_props
    )
    # Should not inject cross_props positionally to avoid conflict with 'request' in kwargs
    assert result == ("a_value", request_obj)


def test_call_with_optional_cross_positional_injection_no_room():
    """When there's no room for positional injection, should not inject."""

    def func(a, b, c):
        return (a, b, c)

    cross_props = Box(logging=Box(ids=[]))
    result = _call_with_optional_cross(func, [1, 2, 3], {}, cross_props)
    # Should not inject, all positions filled
    assert result == (1, 2, 3)


def test_call_with_optional_cross_method_with_request_in_kwargs():
    """Test the specific bug scenario: method with request in kwargs."""

    class TestClass:
        def xyz(self, request):
            return (self, request)

    obj = TestClass()
    cross_props = Box(logging=Box(ids=[]))
    request_obj = Box(data="test")
    # args_no_cross is empty (self is bound), request is in kwargs
    result = _call_with_optional_cross(
        obj.xyz, [], {"request": request_obj}, cross_props
    )
    # Should not inject cross_props to avoid "multiple values for argument 'request'"
    assert result == (obj, request_obj)


def test_call_with_optional_cross_with_partial_args():
    """When some args are provided positionally and next param in kwargs."""

    def func(a, b, c):
        return (a, b, c)

    cross_props = Box(logging=Box(ids=[]))
    # a is positional, b is in kwargs, c would be next for cross_props
    result = _call_with_optional_cross(func, [1], {"b": 2, "c": 3}, cross_props)
    # Should not inject cross_props since c is already in kwargs
    assert result == (1, 2, 3)


def test_call_with_optional_cross_with_cross_kw_variants():
    """Test different cross param name variants."""

    def func1(a, crossLayer=None):
        return (a, crossLayer)

    def func2(a, cross_layer=None):
        return (a, cross_layer)

    def func3(a, crossLayerProps=None):
        return (a, crossLayerProps)

    cross_props = Box(logging=Box(ids=[]))

    result1 = _call_with_optional_cross(func1, [1], {}, cross_props)
    assert result1 == (1, cross_props)

    result2 = _call_with_optional_cross(func2, [1], {}, cross_props)
    assert result2 == (1, cross_props)

    result3 = _call_with_optional_cross(func3, [1], {}, cross_props)
    assert result3 == (1, cross_props)


def test_call_with_optional_cross_with_keyword_only_params():
    """When function has keyword-only params, should handle correctly."""

    def func(a, *, b):
        return (a, b)

    cross_props = Box(logging=Box(ids=[]))
    result = _call_with_optional_cross(func, [1], {"b": 2}, cross_props)
    # Should not inject positionally (no room), just pass through
    assert result == (1, 2)


def test_call_with_optional_cross_trim_surplus_args():
    """Test that surplus args are trimmed correctly."""

    def func(a):
        return a

    cross_props = Box(logging=Box(ids=[]))
    # Provide more args than function accepts
    # _trim_surplus_args_for_params trims from the beginning, keeping the last N args
    # So [1, 2, 3] with 1 param becomes [3]
    result = _call_with_optional_cross(func, [1, 2, 3], {}, cross_props)
    # Should trim to just what's needed (keeps the last arg)
    assert result == 3
