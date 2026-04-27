"""Cursor-based and offset pagination utilities for the v5 API.

Cursor-based pagination encodes the last-seen primary key as base64.
Forward-only in v1 (``previous`` is always null).

Response envelope:
    {"items": [...], "cursor": {"next": "...", "previous": null}}

Multi-column cursor utilities
-----------------------------
:func:`parse_sort`, :func:`encode_multi_cursor`, :func:`decode_multi_cursor`,
:func:`build_cursor_filter`, :func:`extract_cursor_values`, and
:func:`coerce_cursor_value` support cursor-based pagination across multiple
sort columns with mixed ASC/DESC ordering.
"""

import base64
import datetime
import json

from sqlalchemy import and_, or_


def encode_cursor(value):
    """Encode an integer ID into a base64 cursor string."""
    return base64.urlsafe_b64encode(str(value).encode('utf-8')).decode('ascii')


def decode_cursor(cursor_str):
    """Decode a base64 cursor string back into an integer ID.

    Returns None if the cursor is malformed.
    """
    if not cursor_str:
        return None
    try:
        decoded = base64.urlsafe_b64decode(cursor_str.encode('ascii'))
        return int(decoded.decode('utf-8'))
    except (ValueError, TypeError, UnicodeDecodeError):
        return None


def cursor_paginate(query, id_column, cursor_str=None, limit=25,
                    descending=False):
    """Apply cursor-based pagination to a SQLAlchemy query.

    Parameters
    ----------
    query : sqlalchemy.orm.Query
        The base query to paginate.  Callers should **not** apply their
        own ``.order_by()`` for the paginated column -- this function
        handles ordering.
    id_column : sqlalchemy.Column
        The column used for ordering and cursor position (usually `Model.id`).
    cursor_str : str or None
        The cursor from the previous response (``cursor.next``).
    limit : int
        Maximum number of items to return.
    descending : bool
        When *True*, order by ``id_column DESC`` and page forward with
        ``id_column < last_id``.  Default is ascending order.

    Returns
    -------
    (items, next_cursor) : (list, str or None)
        The page of results and the cursor for the next page (or None if
        there are no more results).
    """
    limit = min(max(limit, 1), 10000)

    if cursor_str:
        last_id = decode_cursor(cursor_str)
        if last_id is None:
            from flask import abort
            abort(400, description="Invalid pagination cursor")
        if descending:
            query = query.filter(id_column < last_id)
        else:
            query = query.filter(id_column > last_id)

    if descending:
        query = query.order_by(id_column.desc())
    else:
        query = query.order_by(id_column.asc())

    # Fetch one extra to detect if there is a next page.
    items = query.limit(limit + 1).all()

    if len(items) > limit:
        items = items[:limit]
        next_cursor = encode_cursor(getattr(items[-1], id_column.key))
    else:
        next_cursor = None

    return items, next_cursor


def make_paginated_response(items, next_cursor, total=None):
    """Build the standard paginated response envelope.

    Parameters
    ----------
    items : list
        The serialized items for this page.
    next_cursor : str or None
        Cursor string for the next page.
    total : int or None
        Total count (included for offset-based pagination).

    Returns
    -------
    dict
    """
    result = {
        'items': items,
        'cursor': {
            'next': next_cursor,
            'previous': None,  # Forward-only in v1
        },
    }
    if total is not None:
        result['total'] = total
    return result


# =========================================================================
# Multi-column cursor pagination utilities
# =========================================================================

def parse_sort(sort_str, allowed_fields):
    """Parse a comma-separated sort string into (field_name, ascending) pairs.

    An internal ``id`` tiebreaker is always appended to guarantee
    deterministic cursor pagination.  When *sort_str* is empty the result
    is ``[("id", True)]`` -- an arbitrary but stable ordering.

    Returns a list of (field_name, ascending) tuples, or None on error.
    """
    if not sort_str:
        return [('id', True)]

    result = []
    seen = set()
    for part in sort_str.split(','):
        part = part.strip()
        if not part:
            continue
        if part.startswith('-'):
            ascending = False
            field_name = part[1:]
        else:
            ascending = True
            field_name = part
        if field_name not in allowed_fields:
            return None
        if field_name in seen:
            continue
        seen.add(field_name)
        result.append((field_name, ascending))

    if not result:
        return None

    if 'id' not in seen:
        result.append(('id', True))

    return result


def encode_multi_cursor(values):
    """Encode a list of cursor values into an opaque string.

    Values are JSON-encoded then base64-wrapped.
    """
    payload = json.dumps(values, separators=(',', ':'))
    return base64.urlsafe_b64encode(payload.encode('utf-8')).decode('ascii')


def decode_multi_cursor(cursor_str, num_fields):
    """Decode a multi-column cursor string back into a list of values.

    Returns None if the cursor is malformed.
    """
    if not cursor_str:
        return None
    try:
        decoded = base64.urlsafe_b64decode(
            cursor_str.encode('ascii')).decode('utf-8')
        parts = json.loads(decoded)
        if not isinstance(parts, list) or len(parts) != num_fields:
            return None
        return parts
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def build_cursor_filter(sort_spec, cursor_values, resolve_column_fn):
    """Build a SQLAlchemy filter for multi-column cursor pagination.

    *sort_spec* is a list of (field_name, ascending) tuples.
    *cursor_values* is a list of values aligned with *sort_spec*.
    *resolve_column_fn* maps field_name -> SQLAlchemy column.

    For mixed ASC/DESC sort orders, expands to an OR chain::

        (col1 > v1)
        OR (col1 = v1 AND col2 < v2)  -- if col2 is DESC
        OR (col1 = v1 AND col2 = v2 AND col3 > v3)
        ...
    """
    conditions = []
    for i in range(len(sort_spec)):
        field_name, ascending = sort_spec[i]
        col = resolve_column_fn(field_name)
        cursor_val = coerce_cursor_value(field_name, cursor_values[i])

        # All prefix columns must be equal
        prefix_conditions = []
        for j in range(i):
            pf_name, _ = sort_spec[j]
            pf_col = resolve_column_fn(pf_name)
            pf_val = coerce_cursor_value(pf_name, cursor_values[j])
            prefix_conditions.append(pf_col == pf_val)

        # The i-th column uses > (ASC) or < (DESC)
        if ascending:
            cmp = col > cursor_val
        else:
            cmp = col < cursor_val

        if prefix_conditions:
            conditions.append(and_(*prefix_conditions, cmp))
        else:
            conditions.append(cmp)

    return or_(*conditions)


def coerce_cursor_value(field_name, value):
    """Coerce a cursor value to the appropriate Python type.

    With JSON-encoded cursors, values are already the right type
    in most cases.  This handles edge cases and type enforcement.
    """
    if field_name == 'ordinal':
        return int(value)
    elif field_name == 'test':
        return str(value) if value is not None else ''
    elif field_name == 'submitted_at':
        if value is None:
            return value
        if isinstance(value, str):
            dt = datetime.datetime.fromisoformat(
                value.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt
        return value  # already a datetime
    elif field_name == 'id':
        return int(value)
    return value


def extract_cursor_values(sort_spec, row_data):
    """Extract cursor values from a result row for encoding.

    *row_data* is a dict with keys aligned to the sort field names.
    """
    values = []
    for field_name, _ in sort_spec:
        values.append(row_data.get(field_name))
    return values
