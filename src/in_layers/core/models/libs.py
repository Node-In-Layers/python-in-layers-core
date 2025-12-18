import inspect
import re
import unicodedata
from typing import Any

from box import Box
from pydantic import BaseModel

from .protocols import InLayersModel, ModelDefinition, ModelServices

_NON_ALNUM_RE = re.compile(r"[^a-z0-9_]+")
_UNDERSCORE_RE = re.compile(r"_+")

_META_KEY = "__in_layers_model__"


def get_model_definition(model_cls: Any) -> ModelDefinition:
    attr = getattr(model_cls, _META_KEY, None)
    if attr is None:
        raise ValueError(f"Model {model_cls} has no meta data")
    return Box(attr)


def create_model_services(models: list[InLayersModel]) -> ModelServices:
    """
    Creates a

    :param self: Description
    :param models: Description
    :type models: list[InLayersModel]
    :return: Description
    :rtype: dict[str, InLayersModel]
    """
    models = {}
    for model in models:
        meta = model.get_model_definition()
        models[meta.plural_name] = model
    return models


def is_model_class(obj: Any) -> bool:
    return (
        inspect.isclass(obj) and issubclass(obj, BaseModel) and hasattr(obj, _META_KEY)
    )


def model(*, domain: str, plural_name: str, primary_key: str = "id"):
    """
    Wrapper to turn a Pydantic model into a model capable of being used in the Simple Models system.

    :param domain: The domain this model belongs to.
    :type domain: str
    :param plural_name: The plural name of the model.
    :type plural_name: str
    """

    def decorator(cls):
        validate_plural_name(plural_name)
        existing = getattr(cls, _META_KEY, {})
        merged = {
            **existing,
            "domain": domain,
            "plural_name": plural_name,
            "primary_key": primary_key,
        }
        cls.__in_layers_model__ = merged
        return cls

    return decorator


def validate_plural_name(name: str) -> None:
    """
    Validates that the provided plural name is valid according to our rules.

    :param name: The plural name to validate.
    :type name: str
    :raises ValueError: If the name is invalid.
    """
    if not name:
        raise ValueError("Plural name cannot be empty.")
    if not re.match(r"^[A-Z][a-zA-Z0-9_]*$", name):
        raise ValueError(
            "Plural name must start with an uppercase letter and contain only alphanumeric characters and underscores."
        )


def _ascii_lower(text: str) -> str:
    """Convert text to lowercase ASCII, dropping non-ASCII characters."""
    norm = unicodedata.normalize("NFKD", text)
    ascii_bytes = norm.encode("ascii", "ignore")
    return ascii_bytes.decode("ascii").lower()


def normalize_identifier(value: str, *, max_length: int = 63) -> str:
    """
    Normalize an arbitrary string to a DB-friendly identifier:
      - ASCII only, lowercase
      - Replace non-alphanumeric with underscore
      - Collapse multiple underscores; trim leading/trailing underscores
      - Ensure it doesn't start with a digit
      - Enforce max_length with a stable hash suffix when necessary

    Default max_length 63 works for common SQL systems and is fine for Mongo collections.
    """
    if not value:
        raise ValueError("Cannot normalize empty string as identifier")
    if value[0].isdigit():
        raise ValueError(
            "Cannot normalize string that starts with a digit as identifier"
        )
    base = _ascii_lower(value)
    base = _NON_ALNUM_RE.sub("_", base)
    base = _UNDERSCORE_RE.sub("_", base).strip("_")

    if len(base) > max_length:
        raise ValueError(
            "Resulting identifier is too long. Max size is {max_length} characters."
        )

    return base
