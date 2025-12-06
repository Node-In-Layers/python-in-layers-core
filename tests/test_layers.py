from __future__ import annotations

import asyncio

from in_layers.core.entries import load_system
from in_layers.core.protocols import CoreNamespace, LogFormat, LogLevelNames


def _config():
    def services_create(ctx):
        return {
            "echo": lambda x, cross=None: ("S:" + x, cross),
        }

    def features_create(ctx):
        def call_echo(x, cross=None):
            # ensure crossLayerProps auto-injected when omitted
            res, passed = ctx["services"]["demo"]["echo"](x, cross)
            return ("F:" + res, passed)

        return {"callEcho": call_echo}

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
                    "services": {
                        "create": lambda ctx: {
                            "echo": lambda x, cross=None: ("S:" + x, cross)
                        }
                    },
                    "features": {
                        "create": lambda ctx: {
                            "callEcho": lambda x, cross=None: ctx["services"]["demo"][
                                "echo"
                            ](x, cross)
                        }
                    },
                }
            ],
        },
    }


def test_load_layers_wraps_and_passes_crosslayer():
    async def run():
        sys = await load_system({"environment": "test", "config": _config()})
        print(sys)
        res = sys.features.demo.callEcho("X")
        assert res[0] == "S:X"
        # ensure crosslayer exists (ids present)
        assert (
            isinstance(res[1], dict)
            and "logging" in res[1]
            and "ids" in res[1]["logging"]
        )

    asyncio.run(run())
