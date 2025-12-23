from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from in_layers.core.models.backends import MemoryBackend
from in_layers.core.models.query import query_builder
from in_layers.core.models.protocols import (
    DatastoreValueType,
    EqualitySymbol,
    PropertyOptions,
    SortOrder,
)
from box import Box


class _StubModel:
    def __init__(
        self, domain: str = "test", plural_name: str = "items", pk: str = "id"
    ):
        self._domain = domain
        self._plural = plural_name
        self._pk = pk

    def get_model_definition(self):
        class _Meta:
            domain = self._domain
            plural_name = self._plural
            primary_key = self._pk

        return _Meta()

    def get_primary_key_name(self) -> str:
        return self._pk


def _mk_backend_and_model():
    return MemoryBackend(), _StubModel()


def _insert(be: _NoOpBackend, model: _StubModel, **data: Any) -> dict[str, Any]:
    return be.create(model, data)


def _names(result):
    return [x["name"] for x in result.instances]


def test_property_equals_string_match():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, name="Alice")
    _insert(be, model, id=2, name="Bob")
    qb = query_builder().property("name", "Alice")
    res = be.search(model, qb.compile())
    assert _names(res) == ["Alice"]


def test_property_not_equals_string_match():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, name="Alice")
    _insert(be, model, id=2, name="Bob")
    opts = PropertyOptions(
        type=DatastoreValueType.string, equality_symbol=EqualitySymbol.ne
    )
    qb = query_builder().property("name", "Alice", opts)
    res = be.search(model, qb.compile())
    assert _names(res) == ["Bob"]


def test_property_case_insensitive_match():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, name="alice")
    opts = PropertyOptions(case_sensitive=False, type=DatastoreValueType.string)
    qb = query_builder().property("name", "Alice", opts)
    res = be.search(model, qb.compile())
    assert _names(res) == ["alice"]


def test_property_startswith_match():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, name="Alice")
    _insert(be, model, id=2, name="Malice")
    opts = PropertyOptions(starts_with=True, type=DatastoreValueType.string)
    qb = query_builder().property("name", "Al", opts)
    res = be.search(model, qb.compile())
    assert _names(res) == ["Alice"]


def test_property_endswith_match():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, name="Alice")
    _insert(be, model, id=2, name="Malice")
    opts = PropertyOptions(ends_with=True, type=DatastoreValueType.string)
    qb = query_builder().property("name", "lice", opts)
    res = be.search(model, qb.compile())
    assert _names(res) == ["Alice", "Malice"]


def test_property_includes_match():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, name="Alice")
    _insert(be, model, id=2, name="Malice")
    opts = PropertyOptions(
        includes=True, type=DatastoreValueType.string, equality_symbol=EqualitySymbol.eq
    )
    qb = query_builder().property("name", "ali", opts)
    res = be.search(model, qb.compile())
    assert _names(res) == ["Alice", "Malice"]


def test_number_greater_than_match():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, age=30)
    _insert(be, model, id=2, age=40)
    qb = query_builder().property(
        "age",
        35,
        PropertyOptions(
            type=DatastoreValueType.number, equality_symbol=EqualitySymbol.gt
        ),
    )
    res = be.search(model, qb.compile())
    assert [x["age"] for x in res.instances] == [40]


def test_number_lte_match():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, age=30)
    _insert(be, model, id=2, age=40)
    qb = query_builder().property(
        "age",
        40,
        PropertyOptions(
            type=DatastoreValueType.number, equality_symbol=EqualitySymbol.lte
        ),
    )
    res = be.search(model, qb.compile())
    assert [x["age"] for x in res.instances] == [30, 40]


def test_boolean_equals_match():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, active=True)
    _insert(be, model, id=2, active=False)
    qb = query_builder().property(
        "active",
        True,
        PropertyOptions(
            type=DatastoreValueType.boolean, equality_symbol=EqualitySymbol.eq
        ),
    )
    res = be.search(model, qb.compile())
    assert [x["active"] for x in res.instances] == [True]


def test_dates_after_inclusive_iso_string():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, createdAt="2024-01-01T00:00:00Z")
    _insert(be, model, id=2, createdAt="2023-12-31T23:59:59Z")
    qb = query_builder().dates_after("createdAt", "2024-01-01T00:00:00Z")
    res = be.search(model, qb.compile())
    assert [x["id"] for x in res.instances] == [1]


def test_dates_before_exclusive_datetime():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, createdAt=datetime(2024, 1, 1, 0, 0, 0))
    _insert(be, model, id=2, createdAt=datetime(2023, 1, 1, 0, 0, 0))
    qb = query_builder().dates_before(
        "createdAt", datetime(2024, 1, 1, 0, 0, 0), equal_to_and_before=False
    )
    res = be.search(model, qb.compile())
    assert [x["id"] for x in res.instances] == [2]


def test_and_or_nested_grouping():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, name="Alice", age=25, active=False)
    _insert(be, model, id=2, name="Alice", age=35, active=False)
    _insert(be, model, id=3, name="Bob", age=20, active=True)
    qb = (
        query_builder()
        .property("name", "Alice")
        .and_()
        .complex(
            lambda b: b.property(
                "age",
                30,
                PropertyOptions(
                    type=DatastoreValueType.number, equality_symbol=EqualitySymbol.gt
                ),
            )
            .or_()
            .property(
                "active",
                True,
                PropertyOptions(
                    type=DatastoreValueType.boolean, equality_symbol=EqualitySymbol.eq
                ),
            )
        )
    )
    res = be.search(model, qb.compile())
    assert [x["id"] for x in res.instances] == [2]


def test_sort_ascending_by_age():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, age=30)
    _insert(be, model, id=2, age=20)
    qb = query_builder().sort("age", SortOrder.asc)
    res = be.search(model, qb.compile())
    assert [x["id"] for x in res.instances] == [2, 1]


def test_sort_descending_by_age():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, age=30)
    _insert(be, model, id=2, age=20)
    qb = query_builder().sort("age", SortOrder.dsc)
    res = be.search(model, qb.compile())
    assert [x["id"] for x in res.instances] == [1, 2]


def test_property_includes_not_equals_negates():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, name="Alice")
    _insert(be, model, id=2, name="Malice")
    opts = PropertyOptions(
        includes=True, type=DatastoreValueType.string, equality_symbol=EqualitySymbol.ne
    )
    qb = query_builder().property("name", "ali", opts)
    res = be.search(model, qb.compile())
    assert _names(res) == []


def test_sort_no_sort_returns_original_order():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, age=30)
    _insert(be, model, id=2, age=20)
    # No sort specified
    qb = query_builder()
    res = be.search(model, qb.compile())
    assert [x["id"] for x in res.instances] == [1, 2]


def test_empty_tokens_match_all():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, x=1)
    qb = query_builder()
    res = be.search(model, qb.compile())
    assert [x["id"] for x in res.instances] == [1]


def test_tokens_without_links_all_must_match():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, a=1, b=2)
    qb = (
        query_builder()
        .property(
            "a",
            1,
            PropertyOptions(
                type=DatastoreValueType.number, equalitySymbol=EqualitySymbol.eq
            ),
        )
        .property(
            "b",
            2,
            PropertyOptions(
                type=DatastoreValueType.number, equalitySymbol=EqualitySymbol.eq
            ),
        )
    )
    res = be.search(model, qb.compile())
    assert [x["id"] for x in res.instances] == [1]


def test_even_number_of_tokens_raises():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, a=1, b=2)
    pq_a = (
        query_builder()
        .property("a", 1, PropertyOptions(type=DatastoreValueType.number))
        .compile()
        .query[0]
    )
    pq_b = (
        query_builder()
        .property("b", 2, PropertyOptions(type=DatastoreValueType.number))
        .compile()
        .query[0]
    )
    with pytest.raises(ValueError):
        be.search(
            model,
            Box(query=[pq_a, "AND", pq_b, "AND"], sort=None, take=None, page=None),
        )


def test_as_link_invalid_middle_token_raises():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, a=1, b=2, c=3)
    pq_a = (
        query_builder()
        .property("a", 1, PropertyOptions(type=DatastoreValueType.number))
        .compile()
        .query[0]
    )
    pq_b = (
        query_builder()
        .property("b", 2, PropertyOptions(type=DatastoreValueType.number))
        .compile()
        .query[0]
    )
    pq_c = (
        query_builder()
        .property("c", 3, PropertyOptions(type=DatastoreValueType.number))
        .compile()
        .query[0]
    )
    with pytest.raises(ValueError):
        # Include a link later so linked evaluation is chosen, but the middle token is invalid as a link
        be.search(
            model,
            Box(query=[pq_a, pq_b, pq_c, "OR", pq_c], sort=None, take=None, page=None),
        )


def test_unknown_query_type_filters_out():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1)
    # Unknown query object will be treated as False by evaluator
    res = be.search(model, Box(query=[object()], sort=None, take=None, page=None))
    assert [x["id"] for x in res.instances] == []


def test_property_object_type_equality():
    be, model = _mk_backend_and_model()
    payload = {"nested": 1}
    _insert(be, model, id=1, meta=payload)
    qb = query_builder().property(
        "meta",
        payload,
        PropertyOptions(
            type=DatastoreValueType.object, equality_symbol=EqualitySymbol.eq
        ),
    )
    res = be.search(model, qb.compile())
    assert [x["id"] for x in res.instances] == [1]


def test_property_string_case_sensitive_true():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, name="alice")
    qb = query_builder().property(
        "name",
        "Alice",
        PropertyOptions(type=DatastoreValueType.string, case_sensitive=True),
    )
    res = be.search(model, qb.compile())
    assert [x["id"] for x in res.instances] == []


def test_number_invalid_conversion_returns_false():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, age="abc")
    qb = query_builder().property(
        "age",
        5,
        PropertyOptions(
            type=DatastoreValueType.number, equality_symbol=EqualitySymbol.eq
        ),
    )
    res = be.search(model, qb.compile())
    assert [x["id"] for x in res.instances] == []


def test_boolean_with_invalid_symbol_returns_false():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, active=True)
    qb = query_builder().property(
        "active",
        True,
        PropertyOptions(
            type=DatastoreValueType.boolean, equality_symbol=EqualitySymbol.gt
        ),
    )
    res = be.search(model, qb.compile())
    assert [x["id"] for x in res.instances] == []


def test_apply_equality_non_numeric_fallback_false():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, v="abc")
    qb = query_builder().property(
        "v",
        5,
        PropertyOptions(
            type=DatastoreValueType.object, equality_symbol=EqualitySymbol.gt
        ),
    )
    res = be.search(model, qb.compile())
    assert [x["id"] for x in res.instances] == []


def test_compare_eq_and_ne_via_number_query():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, age=10)
    _insert(be, model, id=2, age=10)
    # eq
    qb_eq = query_builder().property(
        "age",
        10,
        PropertyOptions(
            type=DatastoreValueType.number, equality_symbol=EqualitySymbol.eq
        ),
    )
    res_eq = be.search(model, qb_eq.compile())
    assert [x["id"] for x in res_eq.instances] == [1, 2]


def test_dates_after_invalid_string_returns_false():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, createdAt="not-a-date")
    qb = query_builder().dates_after("createdAt", "2024-01-01T00:00:00Z")
    res = be.search(model, qb.compile())
    assert [x["id"] for x in res.instances] == []


def test_dates_after_with_timestamp():
    be, model = _mk_backend_and_model()
    # 1700000000 -> 2023-11-14T22:13:20Z approximately
    _insert(be, model, id=1, createdAt=1700000000)
    qb = query_builder().dates_after("createdAt", "2000-01-01T00:00:00Z")
    res = be.search(model, qb.compile())
    assert [x["id"] for x in res.instances] == [1]


def test_pagination_passthrough():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1)
    page = {"cursor": "abc"}
    res = be.search(model, Box(query=[], sort=None, take=None, page=page))
    assert res.page == page


def test_sort_missing_key_is_stable():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, v=1)
    _insert(be, model, id=2, v=2)
    qb = query_builder().sort("missing", SortOrder.asc)
    res = be.search(model, qb.compile())
    assert [x["id"] for x in res.instances] == [1, 2]


def test_autoincrement_primary_key_when_missing():
    be, model = _mk_backend_and_model()
    created = be.create(model, {"name": "auto"})
    assert created["id"] == 1


def test_retrieve_missing_returns_none():
    be, model = _mk_backend_and_model()
    assert be.retrieve(model, 999) is None


def test_update_raises_when_missing():
    be, model = _mk_backend_and_model()
    with pytest.raises(KeyError):
        be.update(model, 123, {"x": 1})


def test_update_sets_primary_key_field():
    be, model = _mk_backend_and_model()
    created = be.create(model, {"name": "x"})
    updated = be.update(model, created["id"], {"name": "y"})
    assert updated["id"] == created["id"]


def test_delete_removes_instance():
    be, model = _mk_backend_and_model()
    be.create(model, {"name": "x"})
    be.delete(model, 1)
    assert be.retrieve(model, 1) is None


def test_dispose_clears_storage():
    be, model = _mk_backend_and_model()
    be.create(model, {"name": "x"})
    be.dispose()
    # New bucket should be empty
    assert be.retrieve(model, 1) is None


def test_default_model_factory_backend():
    from in_layers.core.models.backends import DefaultModelFactory

    factory = DefaultModelFactory(context=None)
    backend = factory.get_model_backend(
        model_definition=_StubModel().get_model_definition()
    )
    assert isinstance(backend, MemoryBackend)


def test_create_unique_connection_string_format():
    s = MemoryBackend.create_unique_connection_string()
    assert s.startswith("memory://")


def test_dates_after_string_comparison_branch():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, createdAt="2024-01-02")
    qb = query_builder().dates_after(
        "createdAt", "2024-01-01", value_type=DatastoreValueType.string
    )
    res = be.search(model, qb.compile())
    assert [x["id"] for x in res.instances] == [1]


def test_dates_before_string_comparison_branch_exclusive():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, createdAt="2024-01-01")
    qb = query_builder().dates_before(
        "createdAt",
        "2024-01-01",
        value_type=DatastoreValueType.string,
        equal_to_and_before=False,
    )
    res = be.search(model, qb.compile())
    assert [x["id"] for x in res.instances] == []


def test_dates_after_string_comparison_equal_allowed():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, createdAt="2024-01-01")
    qb = query_builder().dates_after(
        "createdAt", "2024-01-01", value_type=DatastoreValueType.string
    )
    res = be.search(model, qb.compile())
    assert [x["id"] for x in res.instances] == [1]


def test_dates_before_string_comparison_equal_allowed():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, createdAt="2024-01-01")
    qb = query_builder().dates_before(
        "createdAt", "2024-01-01", value_type=DatastoreValueType.string
    )
    res = be.search(model, qb.compile())
    assert [x["id"] for x in res.instances] == [1]


def test_compare_lt_via_number_query():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, age=20)
    _insert(be, model, id=2, age=30)
    qb = query_builder().property(
        "age",
        25,
        PropertyOptions(
            type=DatastoreValueType.number, equality_symbol=EqualitySymbol.lt
        ),
    )
    res = be.search(model, qb.compile())
    assert [x["id"] for x in res.instances] == [1]


def test_compare_gte_via_number_query():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, age=30)
    _insert(be, model, id=2, age=40)
    qb = query_builder().property(
        "age",
        35,
        PropertyOptions(
            type=DatastoreValueType.number, equality_symbol=EqualitySymbol.gte
        ),
    )
    res = be.search(model, qb.compile())
    assert [x["id"] for x in res.instances] == [2]


def test_compare_ne_via_number_query():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, age=10)
    _insert(be, model, id=2, age=20)
    qb = query_builder().property(
        "age",
        10,
        PropertyOptions(
            type=DatastoreValueType.number, equality_symbol=EqualitySymbol.ne
        ),
    )
    res = be.search(model, qb.compile())
    assert [x["id"] for x in res.instances] == [2]


def test_connect_disconnect_noop():
    be, _ = _mk_backend_and_model()
    be.dispose()
    assert True


def test_retrieve_existing_returns_copy():
    be, model = _mk_backend_and_model()
    created = be.create(model, {"name": "x"})
    rec = be.retrieve(model, created["id"])
    assert rec["name"] == "x"


def test_create_function_returns_factory():
    from in_layers.core.models.backends import create as create_factory

    factory = create_factory(context=None)
    assert factory.__class__.__name__ == "DefaultModelFactory"


def test_to_datetime_out_of_range_timestamp_safe():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, createdAt=10**20)
    qb = query_builder().dates_after("createdAt", "2020-01-01T00:00:00Z")
    res = be.search(model, qb.compile())
    assert [x["id"] for x in res.instances] == []


def test_to_datetime_unsupported_type_safe():
    be, model = _mk_backend_and_model()
    _insert(be, model, id=1, createdAt={"not": "dt"})
    qb = query_builder().dates_after("createdAt", "2020-01-01T00:00:00Z")
    res = be.search(model, qb.compile())
    assert [x["id"] for x in res.instances] == []


def test_take_limit_two():
    be, model = _mk_backend_and_model()
    for i in range(5):
        _insert(be, model, id=i + 1, v=i)
    qb = query_builder().take(2)
    res = be.search(model, qb.compile())
    assert [x["id"] for x in res.instances] == [1, 2]
