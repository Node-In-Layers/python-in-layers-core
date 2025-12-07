from __future__ import annotations

import importlib.util
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..libs import validate_config
from ..protocols import CommonContext, Config, GlobalsServicesProps
from .logging import standard_logger


class GlobalsServices:
    def __init__(self, props: GlobalsServicesProps):
        self.props = props

    def get_root_logger(self):
        return standard_logger()

    def _import_module_from_file(self, module_name: str, path: Path):
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load module {module_name} from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _load_config(self):
        environment = self.props.environment
        base_dir = Path(self.props.working_directory)
        config_file = base_dir / f"config_{environment}.py"

        if not config_file.exists():
            raise FileNotFoundError(
                f"Config file not found for environment '{environment}': {config_file}"
            )

        module_name = f"config_{environment}"
        module = self._import_module_from_file(module_name, config_file)

        if not hasattr(module, "get_config"):
            raise AttributeError(f"Module {module_name} does not define get_config()")

        get_config: Callable[[], Config] = module.get_config
        config = get_config()
        validate_config(config)
        return config

    def load_config(self):
        return self._load_config()

    def get_constants(self):
        return {
            "runtime_id": self.props.runtime_id or uuid.uuid4().hex,
            "working_directory": self.props.working_directory,
            "environment": self.props.environment,
        }

    async def get_globals(self, common_globals: CommonContext, app: Mapping[str, Any]):
        if "globals" in app:
            return app.globals.create(common_globals)
        return {}


def create(props: GlobalsServicesProps) -> GlobalsServices:
    return GlobalsServices(props)
