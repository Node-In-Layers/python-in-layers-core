from __future__ import annotations

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

        def ping(self, x, cross_layer_props=None):
            return "l1:" + x

    class L1Features:
        def __init__(self, ctx):
            self._ctx = ctx

        def callPing(self, x, cross_layer_props=None):
            return self._ctx.services.layer1.ping(x, cross_layer_props)

    class L2Services:
        def __init__(self, ctx):
            self._ctx = ctx

        def ping(self, x, cross_layer_props=None):
            return "l2:" + x

    class L2Features:
        def __init__(self, ctx):
            self._ctx = ctx

        def callPing(self, x, cross_layer_props=None):
            return self._ctx.services.layer2.ping(x, cross_layer_props)

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
    system = load_system(SystemProps(environment="test", config=_config_multi()))

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


def test_cross_layer_function_ids_accumulated_across_calls():
    class L1Services:
        def __init__(self, ctx):
            self._ctx = ctx

        def ping(self, x, cross_layer_props=None):
            return ("l1:" + x, cross_layer_props)

    class L2Services:
        def __init__(self, ctx):
            self._ctx = ctx

        def ping(self, x, cross_layer_props=None):
            return ("l2:" + x, cross_layer_props)

    class L1Features:
        def __init__(self, ctx):
            self._ctx = ctx

        def stack(self, x, cross_layer_props=None):
            # Call into L1 services twice, passing cross along and accumulating ids
            a, cross1 = self._ctx.services.layer1.ping(x, cross_layer_props)
            return a, cross1

    class Domain1(Domain):
        name = "layer1"
        services = SimpleNamespace(create=lambda ctx: L1Services(ctx))
        features = SimpleNamespace(create=lambda ctx: L1Features(ctx))

    class Domain2(Domain):
        name = "layer2"
        services = SimpleNamespace(create=lambda ctx: L2Services(ctx))
        features = SimpleNamespace(create=lambda ctx: lambda _: None)

    config = Box(
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

    system = load_system(SystemProps(environment="test", config=config))
    out, cross = system.features.layer1.stack("x")
    assert out == "l1:x"
    # Ensure we have an id object for each function call in the stack:
    # - features.layer1.stack
    # - services.layer2.ping
    # - services.layer1.ping
    ids = cross["logging"]["ids"]
    num_function_call_ids = sum(
        1 for obj in ids if isinstance(obj, dict) and "function_call_id" in obj
    )
    assert num_function_call_ids == 2


def test_cross_layer_props_flow_across_domains_and_back():
    class D1Services:
        def __init__(self, ctx):
            self._ctx = ctx

        def ping(self, x, cross_layer_props=None):
            return ("l1:" + x, cross_layer_props)

    class D2Services:
        def __init__(self, ctx):
            self._ctx = ctx

        def callOther(self, x, cross_layer_props=None):
            # Pass cross along to domain1 services
            return self._ctx.services.layer1.ping(x, cross_layer_props)

    class D2Features:
        def __init__(self, ctx):
            self._ctx = ctx

        def chain(self, x, cross_layer_props=None):
            # Pass cross from feature -> services.layer2 -> services.layer1
            res, cross = self._ctx.services.layer2.callOther(x, cross_layer_props)
            # Return cross received from deepest call
            return res, cross

    class Domain1(Domain):
        name = "layer1"
        services = SimpleNamespace(create=lambda ctx: D1Services(ctx))
        features = SimpleNamespace(create=lambda ctx: lambda _: None)

    class Domain2(Domain):
        name = "layer2"
        services = SimpleNamespace(create=lambda ctx: D2Services(ctx))
        features = SimpleNamespace(create=lambda ctx: D2Features(ctx))

    config = Box(
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

    system = load_system(SystemProps(environment="test", config=config))
    out, cross = system.features.layer2.chain("x")
    assert out == "l1:x"
    # Expect exactly one function_call_id for each wrapped function in the chain:
    # - features.layer2.chain
    # - services.layer2.callOther
    # - services.layer1.ping
    ids = cross["logging"]["ids"]
    num_function_call_ids = sum(
        1 for obj in ids if isinstance(obj, dict) and "function_call_id" in obj
    )
    assert num_function_call_ids == 3
