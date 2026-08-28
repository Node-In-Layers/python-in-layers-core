from __future__ import annotations

from typing import Any

import pytest
from box import Box
from pydantic import BaseModel

from in_layers.core.libs import (
    annotated_function,
    annotation_function_props,
    combine_cross_layer_props,
    create_cross_layer_props,
    cross_layer_props_with_logging_overrides,
    cross_layer_props_with_otel_baggage,
    get_otel_baggage_from_cross_layer_props,
)
from in_layers.core.protocols import AnnotatedFunctionProps


def test_create_cross_layer_props_strips_one_hop_overrides():
    logger = Box(get_ids=lambda: [{"runtime_id": "rid-1"}, {"function_call_id": "f-1"}])
    cross_layer_props = cross_layer_props_with_logging_overrides(
        {"omit_data": True},
        {"logging": {"ids": [{"request_id": "req-1"}]}},
    )

    actual = create_cross_layer_props(logger, cross_layer_props)

    assert actual.logging.ids == [
        {"request_id": "req-1"},
        {"runtime_id": "rid-1"},
        {"function_call_id": "f-1"},
    ]
    assert "overrides" not in actual.logging


def test_combine_cross_layer_props_replaces_otel_when_forwarding_baggage():
    base = Box(
        {
            "logging": {
                "ids": [{"runtime_id": "rid-1"}],
                "otel": {"baggage": {"tenant": "a"}},
            }
        }
    )
    incoming = Box(
        {
            "logging": {
                "ids": [{"request_id": "req-1"}],
                "otel": {"baggage": {"tenant": "b", "locale": "en"}},
            }
        }
    )

    actual = combine_cross_layer_props(
        base,
        incoming,
        {"forward_baggage": True},
    )

    assert actual.logging.ids == [{"runtime_id": "rid-1"}, {"request_id": "req-1"}]
    assert actual.logging.otel.baggage == {"tenant": "b", "locale": "en"}


def test_cross_layer_props_with_otel_baggage_round_trips():
    input_props = {"logging": {"ids": [{"runtime_id": "rid-1"}]}}

    actual = cross_layer_props_with_otel_baggage(
        {"tenant": "north", "request": "abc"},
        input_props,
    )
    extracted = get_otel_baggage_from_cross_layer_props(actual)

    assert actual.logging.ids == [{"runtime_id": "rid-1"}]
    assert extracted == {"tenant": "north", "request": "abc"}


def test_annotated_function_exposes_metadata_and_validates_input():
    props = annotation_function_props(
        AnnotatedFunctionProps(
            function_name="greet",
            domain="demo",
            args_schema=dict[str, str],
            returns_schema=dict[str, str],
            description="Greets a user",
        )
    )

    def implementation(args: dict[str, str], cross_layer_props=None):  # noqa: ARG001
        return {"message": f"hello {args['name']}"}

    actual = annotated_function(props, implementation)
    expected = {"message": "hello mike"}

    assert actual({"name": "mike"}) == expected
    assert actual.function_name == "greet"
    assert actual.domain == "demo"
    assert actual.description == "Greets a user"
    assert "args" in actual.schema
    with pytest.raises(Exception):
        actual({"name": 123})  # type: ignore[arg-type]


def test_annotated_function_supports_pydantic_models():
    class GreetingArgs(BaseModel):
        name: str

    class GreetingResult(BaseModel):
        message: str

    props = annotation_function_props(
        AnnotatedFunctionProps(
            function_name="greet_model",
            domain="demo",
            args_schema=GreetingArgs,
            returns_schema=GreetingResult,
            description="Greets with models",
        )
    )

    def implementation(args: GreetingArgs, cross_layer_props=None):  # noqa: ARG001
        assert isinstance(args, GreetingArgs)
        return {"message": f"hello {args.name}"}

    actual = annotated_function(props, implementation)
    result = actual({"name": "mike"})

    assert isinstance(result, GreetingResult)
    assert result.message == "hello mike"
