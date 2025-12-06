from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from box import Box

from ..protocols import CommonContext, GlobalsServices, GlobalsServicesProps
from .logging import standard_logger


def create(props: GlobalsServicesProps) -> GlobalsServices:
    environment = props["environment"]
    working_directory = props["working_directory"]
    runtime_id = props.get("runtime_id")

    def get_root_logger():
        return standard_logger()

    def load_config():
        raise RuntimeError(
            f"Config auto-discovery not implemented for Python; pass config explicitly for environment {environment}"
        )

    def get_constants():
        return {
            "runtime_id": runtime_id or uuid.uuid4().hex,
            "working_directory": working_directory,
            "environment": environment,
        }

    async def get_globals(common_globals: CommonContext, app: Mapping[str, Any]):
      if 'globals' in app:
        return app.globals.create(common_globals)
      return {}

    return Box(
        {
            "load_config": load_config,
            "get_constants": get_constants,
            "get_root_logger": get_root_logger,
            "get_globals": get_globals,
        }
    )
