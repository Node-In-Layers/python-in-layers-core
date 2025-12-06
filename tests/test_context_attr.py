from __future__ import annotations

import asyncio

from in_layers.core.entries import load_system
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
                        "create": lambda context: {
                            "echo": lambda x, cross=None: ("S:" + x, cross)
                        }
                    },
                    # Use dot-notation in the feature create closure to call into services
                    "features": {
                        "create": lambda context: {
                            "viaDots": (
                                lambda x, cross=None: context.services.demo.echo(
                                    x, cross
                                )
                            ),
                            "viaDicts": (
                                lambda x, cross=None: context["services"]["demo"][
                                    "echo"
                                ](x, cross)
                            ),
                        }
                    },
                }
            ],
        },
    }


def test_context_supports_dot_notation_in_features_context():
    async def run():
        sys = await load_system({"environment": "test", "config": _config()})
        a = sys.features.demo.viaDots("X")
        b = sys.features.demo.viaDicts("Y")
        assert a[0] == "S:X"
        assert b[0] == "S:Y"

    asyncio.run(run())
