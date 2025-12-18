from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any, cast

from .protocols import (
    AllowableEqualitySymbols,
    BooleanQuery,
    DatastoreValueType,
    DatesAfterQuery,
    DatesBeforeQuery,
    EqualitySymbol,
    ModelSearch,
    PropertyOptions,
    PropertyQuery,
    QueryTokens,
    SortOrder,
    SortStatement,
)


def _objectize(key: str, value: Any) -> dict[str, Any]:
    return {key: value} if value is not None else {}


def is_link_token(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    lower = value.lower()
    return lower in {"and", "or"}


def is_property_based_query(value: Any) -> bool:
    return isinstance(value, (PropertyQuery, DatesBeforeQuery, DatesAfterQuery))


def and_() -> BooleanQuery:
    return cast(BooleanQuery, "AND")


def or_() -> BooleanQuery:
    return cast(BooleanQuery, "OR")


def _is_date(obj: Any) -> bool:
    return isinstance(obj, datetime)


def dates_after(
    key: str,
    js_date: datetime | str,
    value_type: DatastoreValueType = DatastoreValueType.date,
    equal_to_and_after: bool = True,
) -> DatesAfterQuery:
    iso_date = js_date.isoformat() if _is_date(js_date) else cast(str, js_date)
    return DatesAfterQuery(
        type="datesAfter",
        key=key,
        date=iso_date,
        value_type=value_type,
        options=cast(Any, {"equal_to_and_after": bool(equal_to_and_after)}),
    )


def dates_before(
    key: str,
    js_date: datetime | str,
    value_type: DatastoreValueType = DatastoreValueType.date,
    equal_to_and_before: bool = True,
) -> DatesBeforeQuery:
    iso_date = js_date.isoformat() if _is_date(js_date) else cast(str, js_date)
    return DatesBeforeQuery(
        type="datesBefore",
        key=key,
        date=iso_date,
        value_type=value_type,
        options=cast(Any, {"equal_to_and_before": bool(equal_to_and_before)}),
    )


def pagination(value: Any) -> Any:
    return value


def sort(key: str, order: SortOrder = SortOrder.asc) -> SortStatement:
    if order not in (SortOrder.asc, SortOrder.dsc):
        raise ValueError("Sort must be either asc or dsc")
    return SortStatement(key=key, order=order)


def take(max_count: int | str) -> int:
    try:
        parsed = int(max_count)
    except (TypeError, ValueError):
        raise ValueError(f'Number "{max_count}" is not integerable') from None
    return parsed


def property(
    key: str,
    value: Any,
    options: PropertyOptions | None = None,
) -> PropertyQuery:
    options = options or PropertyOptions()
    equality_symbol = options.equality_symbol or EqualitySymbol.eq
    value_type = options.type or DatastoreValueType.string
    if equality_symbol not in AllowableEqualitySymbols:
        raise ValueError(f"{equality_symbol} is not a valid symbol")
    if value_type == DatastoreValueType.string and equality_symbol not in (
        EqualitySymbol.eq,
        EqualitySymbol.ne,
    ):
        raise ValueError("Cannot use a non = symbol for a string type")
    # Build options dictionary with only set values
    opts = PropertyOptions(
        case_sensitive=options.case_sensitive,
        starts_with=options.starts_with,
        ends_with=options.ends_with,
        includes=options.includes,
        # Preserve provided explicit fields; equality/type are captured above
    )
    return PropertyQuery(
        type="property",
        key=key,
        value=value,
        value_type=value_type,
        equality_symbol=equality_symbol,
        options=opts,
    )


def text_query(
    key: str,
    value: str | None,
    *,
    case_sensitive: bool | None = None,
    starts_with: bool | None = None,
    ends_with: bool | None = None,
    includes: bool | None = None,
) -> PropertyQuery:
    return property(
        key=key,
        value=value,
        options=PropertyOptions(
            case_sensitive=case_sensitive,
            starts_with=starts_with,
            ends_with=ends_with,
            includes=includes,
            type=DatastoreValueType.string,
            equality_symbol=None,
        ),
    )


def number_query(
    key: str,
    value: int | float | str | None,
    equality_symbol: EqualitySymbol = EqualitySymbol.eq,
) -> PropertyQuery:
    return property(
        key=key,
        value=value,
        options=PropertyOptions(
            type=DatastoreValueType.number,
            equality_symbol=equality_symbol,
        ),
    )


def boolean_query(key: str, value: bool | None) -> PropertyQuery:
    return property(
        key=key,
        value=value,
        options=PropertyOptions(
            type=DatastoreValueType.boolean,
            equality_symbol=EqualitySymbol.eq,
        ),
    )


def threeitize(data: Sequence[QueryTokens]) -> list[list[QueryTokens]]:
    if len(data) in (0, 1):
        return []
    if len(data) % 2 == 0:
        raise ValueError("Must be an odd number of 3 or greater.")
    three = list(data[:3])
    rest = data[2:]
    more = threeitize(rest)
    return [three, *more]


def _validate_array_or_query(obj: QueryTokens) -> None:
    if isinstance(obj, list):
        _validate_token_structure(obj)
        return
    if is_property_based_query(obj):
        return
    raise ValueError("Order of link tokens and queries invalid")


def _validate_token_types(token: QueryTokens) -> None:
    if isinstance(token, list):
        for inner in token:
            _validate_token_types(inner)
        return
    if is_property_based_query(token):
        return
    if is_link_token(token):
        return
    raise ValueError(f"Unknown token type {token}")


def _validate_token_structure(tokens: list[QueryTokens]) -> None:
    if not tokens:
        return
    first = tokens[0]
    if first in {"AND", "OR"}:
        raise ValueError("Cannot have AND or OR at the very start.")
    last = tokens[-1]
    if last in {"AND", "OR"}:
        raise ValueError("Cannot have AND or OR at the very end.")
    if all((t not in {"AND", "OR"}) for t in tokens):
        for t in tokens:
            _validate_array_or_query(t)
        return
    total_links = [t for t in tokens if t in {"AND", "OR"}]
    non_links = [t for t in tokens if t not in {"AND", "OR"}]
    if len(total_links) != len(non_links) - 1:
        raise ValueError("Must separate each statement with an AND or OR")
    threes = list(reversed(threeitize(tokens)))
    for a, link, b in threes:
        if link not in {"AND", "OR"}:
            if is_property_based_query(link):  # type: ignore[arg-type]
                raise ValueError("Must have AND/OR between property queries")
            raise ValueError("Must have AND/OR between nested queries")
        _validate_array_or_query(a)
        _validate_array_or_query(b)


def validate_model_search(search: ModelSearch) -> None:
    if not isinstance(search.query, list):
        raise ValueError("Query must be an array")
    if len(search.query) < 1:
        return
    _validate_token_types(search.query)
    _validate_token_structure(search.query)


class QueryBuilder:
    """
    Immutable-style builder for OrmSearch.
    """

    def __init__(self, data: ModelSearch | None = None):
        self._data = data or ModelSearch(query=[], take=None, sort=None, page=None)

    def _with(self, **kwargs: Any) -> QueryBuilder:
        new_data = replace(self._data, **kwargs)
        return QueryBuilder(new_data)

    def _append(self, token: QueryTokens) -> QueryBuilder:
        new_query = [*self._data.query, token]
        return self._with(query=new_query)

    # Query parts
    def property(
        self, key: str, value: Any, options: PropertyOptions | None = None
    ) -> QueryBuilder:
        return self._append(property(key, value, options))

    def dates_after(
        self,
        key: str,
        js_date: datetime | str,
        *,
        value_type: DatastoreValueType = DatastoreValueType.date,
        equal_to_and_after: bool = True,
    ) -> QueryBuilder:
        return self._append(
            dates_after(
                key=key,
                js_date=js_date,
                value_type=value_type,
                equal_to_and_after=equal_to_and_after,
            )
        )

    def dates_before(
        self,
        key: str,
        js_date: datetime | str,
        *,
        value_type: DatastoreValueType = DatastoreValueType.date,
        equal_to_and_before: bool = True,
    ) -> QueryBuilder:
        return self._append(
            dates_before(
                key=key,
                js_date=js_date,
                value_type=value_type,
                equal_to_and_before=equal_to_and_before,
            )
        )

    def complex(self, sub_builder_func: Callable[[QueryBuilder], Any]) -> QueryBuilder:
        sub_builder = QueryBuilder()
        result = sub_builder_func(sub_builder)
        if isinstance(result, QueryBuilder):
            compiled = result.compile()
            sub_tokens = compiled.query
        elif isinstance(result, ModelSearch):
            sub_tokens = result.query
        elif isinstance(result, dict) and "query" in result:
            sub_tokens = result["query"]
        else:
            raise TypeError("SubBuilder must return a QueryBuilder or ModelSearch-like")
        return self._append(cast(QueryTokens, list(sub_tokens)))

    # Linkers and non-query fields
    def and_(self) -> QueryBuilder:
        return self._append(and_())

    def or_(self) -> QueryBuilder:
        return self._append(or_())

    def take(self, count: int | str) -> QueryBuilder:
        return self._with(take=take(count))

    def sort(self, key: str, order: SortOrder = SortOrder.asc) -> QueryBuilder:
        return self._with(sort=sort(key, order))

    def pagination(self, value: Any) -> QueryBuilder:
        return self._with(page=pagination(value))

    def compile(self) -> ModelSearch:
        # Return a snapshot ModelSearch
        return ModelSearch(
            query=list(self._data.query),
            take=self._data.take,
            sort=self._data.sort,
            page=self._data.page,
        )


def query_builder() -> QueryBuilder:
    return QueryBuilder()
