from __future__ import annotations

import pytest
from box import Box

from in_layers.core.libs import validate_config
from in_layers.core.protocols import (
    CoreConfig,
    CoreLoggingConfig,
    LogFormat,
    LogLevelNames,
    Config,
)
from in_layers.core.protocols import Domain, Config, CoreNamespace


def _base_config():
    return Box(
        system_name="test-system",
        environment="test",
        in_layers_core=Box(
            logging=Box(
                log_level=LogLevelNames.info,
                log_format=LogFormat.simple,
            ),
            layer_order=["services", "features"],
            domains=[],
        ),
    )


def test_validate_config_happy():
    cfg = _base_config()
    validate_config(cfg)  # no exception
