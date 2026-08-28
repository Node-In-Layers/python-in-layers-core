from __future__ import annotations

from functools import reduce
from typing import Any

from box import Box

from ..libs import _merge, is_config, validate_config
from ..protocols import CommonContext, CoreNamespace, FeaturesContext

globals_name = CoreNamespace.globals.value


def _merge_domain_globals(acc: dict[str, Any], dep: dict[str, Any]) -> dict[str, Any]:
    """
    Deep-merge domain globals patches, but replace `config` wholesale.

    Secrets (and similar) return a fully rewritten config; deep-merging it would
    combine JSON secret objects with the old nil-secret placeholder dicts.
    """
    merged = _merge(acc, dep)
    if "config" in dep:
        merged["config"] = dep["config"]
    return merged


class GlobalsFeatures:
    def __init__(self, context: FeaturesContext):
        self.context = context

    def load_globals(self, environment_or_config: Any) -> CommonContext:
        services = self.context.services[globals_name]
        if not services:
            raise RuntimeError(f"Services for {globals_name} not found")
        config = (
            environment_or_config
            if is_config(environment_or_config)
            else services.load_config()
        )
        validate_config(config)
        common_globals: CommonContext = Box(
            config=config,
            root_logger=services.get_root_logger(),
            constants=services.get_constants(),
        )
        domains = getattr(config.in_layers_core, "domains", None) or []

        def _accumulate(acc: dict[str, Any], domain: Any) -> dict[str, Any]:
            dep = services.get_globals(common_globals, domain) or {}
            if not dep:
                return acc
            as_dict = dict(dep) if not isinstance(dep, dict) else dep
            return _merge_domain_globals(acc, as_dict)

        domain_globals = reduce(_accumulate, domains, {})
        if not domain_globals:
            return common_globals
        return Box(_merge_domain_globals(dict(common_globals), domain_globals))


def create(context: FeaturesContext) -> GlobalsFeatures:
    return GlobalsFeatures(context)
