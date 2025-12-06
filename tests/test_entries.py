from __future__ import annotations

import asyncio

from in_layers.core.entries import load_system, SystemProps
from in_layers.core.protocols import CoreNamespace, LogFormat, LogLevelNames


def _config():
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
                            "ping": lambda x, cross=None: "pong:" + x
                        }
                    },
                    "features": {
                        "create": lambda ctx: {
                            "callPing": lambda x, cross=None: ctx["services"]["demo"][
                                "ping"
                            ](x, cross)
                        }
                    },
                }
            ],
        },
    }


def test_load_system_end_to_end():
    async def run():
        sys = await load_system(SystemProps(environment="test", config=_config()))
        assert sys.services.demo.ping("x")[0:5] == "pong:"
        assert sys.features.demo.callPing("x")[0:5] == "pong:"

    asyncio.run(run())
