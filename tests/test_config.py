from __future__ import annotations

import pytest

from in_layers.core.libs import validate_config
from in_layers.core.protocols import CoreNamespace, LogFormat, LogLevelNames


def _base_config():
    return {
        "system_name": "test-system",
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
                    "services": {"create": lambda ctx: {}},
                    "features": {"create": lambda ctx: {}},
                }
            ],
        },
    }


def test_validate_config_happy():
    cfg = _base_config()
    validate_config(cfg)  # no exception


def test_validate_config_missing_name():
    cfg = _base_config()
    cfg[CoreNamespace.root.value]["apps"][0].pop("name")
    with pytest.raises(Exception):
        validate_config(cfg)
