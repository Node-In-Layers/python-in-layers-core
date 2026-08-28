from __future__ import annotations

from types import SimpleNamespace

from box import Box

from in_layers.core.entries import SystemProps, load_system
from in_layers.core.protocols import (
    Domain,
    LogFormat,
    LogLevelNames,
)


def test_domain_globals_merge_patches_config():
    class DemoServices:
        def __init__(self, ctx):
            self._ctx = ctx

        def read_patched(self, cross=None):
            return self._ctx.config.patched_value

    class DemoDomain(Domain):
        name = "demo"
        services = SimpleNamespace(create=lambda ctx: DemoServices(ctx))
        globals = SimpleNamespace(
            create=lambda common: {
                "config": Box(dict(common.config) | {"patched_value": "from-globals"})
            }
        )

    config = Box(
        system_name="test",
        environment="test",
        in_layers_core=Box(
            logging=Box(
                log_level=LogLevelNames.info,
                log_format=LogFormat.simple,
            ),
            layer_order=["services"],
            domains=[DemoDomain],
        ),
        patched_value="original",
    )

    system = load_system(SystemProps(environment="test", config=config))
    assert system.services.demo.read_patched() == "from-globals"


def test_domain_globals_config_replaces_nested_object_not_deep_merge():
    class DemoServices:
        def __init__(self, ctx):
            self._ctx = ctx

        def read_secret(self):
            return self._ctx.config.nested.secret

    class DemoDomain(Domain):
        name = "demo"
        services = SimpleNamespace(create=lambda ctx: DemoServices(ctx))
        globals = SimpleNamespace(
            create=lambda common: {
                "config": Box(
                    {
                        **dict(common.config),
                        "nested": {"secret": {"ok": True}},
                    }
                )
            }
        )

    config = Box(
        system_name="test",
        environment="test",
        in_layers_core=Box(
            logging=Box(
                log_level=LogLevelNames.info,
                log_format=LogFormat.simple,
            ),
            layer_order=["services"],
            domains=[DemoDomain],
        ),
        nested=Box(
            secret={
                "type": "nil-secret",
                "key": "/x",
            }
        ),
    )

    system = load_system(SystemProps(environment="test", config=config))
    assert system.services.demo.read_secret() == {"ok": True}


def test_domain_without_globals_is_noop():
    class DemoServices:
        def __init__(self, ctx):
            self._ctx = ctx

        def ping(self):
            return "ok"

    class DemoDomain(Domain):
        name = "demo"
        services = SimpleNamespace(create=lambda ctx: DemoServices(ctx))

    config = Box(
        system_name="test",
        environment="test",
        in_layers_core=Box(
            logging=Box(
                log_level=LogLevelNames.info,
                log_format=LogFormat.simple,
            ),
            layer_order=["services"],
            domains=[DemoDomain],
        ),
    )

    system = load_system(SystemProps(environment="test", config=config))
    assert system.services.demo.ping() == "ok"
