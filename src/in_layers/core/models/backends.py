from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from box import Box

from .protocols import (
    AND,
    OR,
    BackendProtocol,
    BooleanQuery,
    DatastoreValueType,
    DatesAfterQuery,
    DatesBeforeQuery,
    EqualitySymbol,
    ModelDefinition,
    PropertyOptions,
    PropertyQuery,
    Query,
    QueryTokens,
    SortOrder,
    SortStatement,
)


class MemoryBackend(BackendProtocol):
    @staticmethod
    def create_unique_connection_string() -> str:
        return f"memory://{uuid4().hex}"

    def __init__(self) -> None:
        # Storage layout:
        #   key -> f"{domain}_{plural_name}"
        #   value -> { primary_key_value -> data_mapping }
        self.__buckets: dict[str, dict[Any, dict[str, Any]]] = {}
        # Auto-increment sequences per bucket (when PK not provided)
        self.__sequences: dict[str, int] = {}

    def __bucket_key(self, model) -> str:
        meta = model.get_model_definition()
        return f"{meta.domain}_{meta.plural_name}"

    def __get_bucket(self, model) -> dict[Any, dict[str, Any]]:
        key = self.__bucket_key(model)
        if key not in self.__buckets:
            self.__buckets[key] = {}
            self.__sequences[key] = 0
        return self.__buckets[key]

    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def create(self, model, data):  # type: ignore[override]
        bucket = self.__get_bucket(model)
        payload = dict(data)

        pk_name = model.get_primary_key_name()
        pk_value = payload.get(pk_name)
        if pk_value is None:
            # allocate a simple auto-increment integer id
            bucket_key = self.__bucket_key(model)
            self.__sequences[bucket_key] = self.__sequences.get(bucket_key, 0) + 1
            pk_value = self.__sequences[bucket_key]
            payload[pk_name] = pk_value

        bucket[pk_value] = payload
        return dict(payload)

    def retrieve(self, model, id):  # type: ignore[override]
        bucket = self.__get_bucket(model)
        record = bucket.get(id)
        if record is None:
            return None
        return dict(record)

    def update(self, model, id, data):  # type: ignore[override]
        bucket = self.__get_bucket(model)
        existing = bucket.get(id)
        if existing is None:
            raise KeyError(f"Instance with id {id!r} not found")

        updated = dict(existing)
        updated.update(dict(data))
        # Ensure primary key field remains consistent with the id argument
        pk_name = model.get_primary_key_name()
        updated[pk_name] = id

        bucket[id] = updated
        return dict(updated)

    def delete(self, model, id):  # type: ignore[override]
        bucket = self.__get_bucket(model)
        bucket.pop(id, None)

    def search(self, model, query):
        bucket = self.__get_bucket(model)
        records = list(bucket.values())
        filtered = [r for r in records if _matches_query_tokens(r, query.query)]
        sorted_records = _apply_sort(filtered, query.sort)
        limited = _apply_take(sorted_records, query.take)
        return Box(instances=[dict(x) for x in limited], page=query.page)

    def dispose(self) -> None:
        self.__buckets.clear()
        self.__sequences.clear()


class DefaultModelFactory:
    """
    Core fallback model factory. Returns a no-op backend.
    Domains can provide their own factory services to supply real backends.
    """

    def __init__(self, context: Any):
        self.__context = context

    def get_model_backend(self, model_definition: ModelDefinition):  # noqa: ARG002
        return MemoryBackend()


def create(context: Any) -> DefaultModelFactory:
    return DefaultModelFactory(context)


# ================
# Search utilities
# ================


def _apply_sort(records: list[dict[str, Any]], sort_stmt: SortStatement | None):
    if not sort_stmt:
        return records
    key = sort_stmt.key
    reverse = sort_stmt.order == SortOrder.dsc

    def _safe_key(r: dict[str, Any]):
        v = r.get(key)
        return (v is None, str(v))

    return sorted(records, key=_safe_key, reverse=reverse)


def _apply_take(records: list[dict[str, Any]], take_value: int | None):
    if not take_value:
        return records
    return records[: int(take_value)]


def _matches_query_tokens(record: dict[str, Any], tokens: list[QueryTokens]) -> bool:
    if not tokens:
        return True
    if _has_links(tokens):
        return _evaluate_linked_tokens(record, tokens)
    return all(_evaluate_token(record, t) for t in tokens)


def _has_links(tokens: list[QueryTokens]) -> bool:
    return any(t == "AND" or t == "OR" for t in tokens)  # noqa: PLR1714


def _evaluate_token(record: dict[str, Any], token: QueryTokens) -> bool:
    if isinstance(token, list):
        return _matches_query_tokens(record, token)
    if (
        token == AND or token == OR  # noqa: PLR1714
    ):  # pragma: no cover - structure validated elsewhere
        return True
    return _evaluate_query(record, token)


def _evaluate_linked_tokens(record: dict[str, Any], tokens: list[QueryTokens]) -> bool:
    threes = _threeitize(tokens)
    if not threes:
        # Single element with link tokens present shouldn't happen, fallback to AND across items
        return all(_evaluate_token(record, t) for t in tokens)
    # Evaluate left-to-right using provided links
    result: bool | None = None
    for a, link, b in threes:
        left = _evaluate_token(record, a)
        right = _evaluate_token(record, b)
        current = _combine_link(left, link, right)
        result = current if result is None else _combine_link(result, link, current)
    return bool(result)


def _combine_link(a: bool, link: BooleanQuery, b: bool) -> bool:
    if link == "AND":
        return a and b
    return a or b


def _threeitize(
    data: list[QueryTokens],
) -> list[tuple[QueryTokens, BooleanQuery, QueryTokens]]:
    if len(data) in (0, 1):
        return []
    if len(data) % 2 == 0:
        raise ValueError("Must be an odd number of 3 or greater.")
    three = (data[0], _as_link(data[1]), data[2])
    rest = data[2:]
    more = _threeitize(rest)
    return [three, *more]


def _as_link(value: QueryTokens) -> BooleanQuery:
    if value in {"AND", "OR"}:
        return value
    raise ValueError("Must have AND/OR between statements")


def _evaluate_query(record: dict[str, Any], q: Query) -> bool:
    if isinstance(q, PropertyQuery):
        return _match_property(record, q)
    if isinstance(q, DatesBeforeQuery):
        return _match_dates_before(record, q)
    if isinstance(q, DatesAfterQuery):
        return _match_dates_after(record, q)
    return False


def _match_property(record: dict[str, Any], q: PropertyQuery) -> bool:
    actual = record.get(q.key, None)
    opts = q.options
    if q.value_type == DatastoreValueType.string:
        return _match_string(actual, q.value, q.equality_symbol, opts)
    if q.value_type == DatastoreValueType.number:
        return _match_number(actual, q.value, q.equality_symbol)
    if q.value_type == DatastoreValueType.boolean:
        return _match_boolean(actual, q.value, q.equality_symbol)
    # Fallback to equality for other types
    return _apply_equality(actual, q.value, q.equality_symbol)


def _match_string(
    actual: Any, expected: Any, symbol: EqualitySymbol, opts: PropertyOptions
) -> bool:
    a = _to_str(actual)
    e = _to_str(expected)
    # Default to case-insensitive matching unless explicitly set
    case_sensitive = opts.case_sensitive if opts.case_sensitive is not None else False
    if not case_sensitive:
        a = a.lower() if a is not None else a
        e = e.lower() if e is not None else e
    # Pattern options
    if opts.starts_with:
        return _apply_symbol_bool(
            a.startswith(e) if a is not None and e is not None else False, symbol
        )
    if opts.ends_with:
        return _apply_symbol_bool(
            a.endswith(e) if a is not None and e is not None else False, symbol
        )
    if opts.includes:
        return _apply_symbol_bool(
            (e in a) if a is not None and e is not None else False, symbol
        )
    # Default equals / not equals
    return _apply_equality(a, e, symbol)


def _apply_symbol_bool(condition: bool, symbol: EqualitySymbol) -> bool:
    if symbol == EqualitySymbol.ne:
        return not condition
    return condition


def _match_number(actual: Any, expected: Any, symbol: EqualitySymbol) -> bool:
    try:
        a = float(actual)
        e = float(expected)
    except (TypeError, ValueError):
        return False
    return _compare(a, e, symbol)


def _match_boolean(actual: Any, expected: Any, symbol: EqualitySymbol) -> bool:
    if symbol not in (EqualitySymbol.eq, EqualitySymbol.ne):
        return False
    a = bool(actual) if actual is not None else None
    e = bool(expected) if expected is not None else None
    return _apply_equality(a, e, symbol)


def _apply_equality(a: Any, b: Any, symbol: EqualitySymbol) -> bool:
    if symbol == EqualitySymbol.eq:
        return a == b
    if symbol == EqualitySymbol.ne:
        return a != b
    # For non-equality symbols, attempt numeric comparison when possible
    try:
        return _compare(float(a), float(b), symbol)
    except (TypeError, ValueError):
        return False


def _compare(a: float, b: float, symbol: EqualitySymbol) -> bool:  # noqa: PLR0911
    if symbol == EqualitySymbol.lt:
        return a < b
    if symbol == EqualitySymbol.lte:
        return a <= b
    if symbol == EqualitySymbol.gt:
        return a > b
    if symbol == EqualitySymbol.gte:
        return a >= b
    if symbol == EqualitySymbol.eq:
        return a == b
    if symbol == EqualitySymbol.ne:
        return a != b
    return False


def _match_dates_after(record: dict[str, Any], q: DatesAfterQuery) -> bool:
    actual = record.get(q.key, None)
    if q.value_type == DatastoreValueType.date:
        a = _to_datetime(actual)
        b = _to_datetime(q.date)
        if a is None or b is None:
            return False
        if q.options.equal_to_and_after:
            return a >= b
        return a > b
    # String comparison
    a_str = _to_str(actual)
    b_str = _to_str(q.date)
    if a_str is None or b_str is None:
        return False
    if q.options.equal_to_and_after:
        return a_str >= b_str
    return a_str > b_str


def _match_dates_before(record: dict[str, Any], q: DatesBeforeQuery) -> bool:
    actual = record.get(q.key, None)
    if q.value_type == DatastoreValueType.date:
        a = _to_datetime(actual)
        b = _to_datetime(q.date)
        if a is None or b is None:
            return False
        if q.options.equal_to_and_before:
            return a <= b
        return a < b
    a_str = _to_str(actual)
    b_str = _to_str(q.date)
    if a_str is None or b_str is None:
        return False
    if q.options.equal_to_and_before:
        return a_str <= b_str
    return a_str < b_str


def _to_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _to_datetime(value: Any):  # noqa: PLR0911

    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except Exception:
            return None
    if isinstance(value, str):
        try:
            # ISO-8601
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None
    return None
