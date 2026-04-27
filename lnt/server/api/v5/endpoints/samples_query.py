"""Unified sample query endpoint for the v5 API.

POST /api/v5/{ts}/samples
  Body (JSON): {params, test, metric, commit, run, sort, limit, cursor,
                submitted_before, submitted_after}

Returns cursor-paginated sample data points.  When ``metric`` is specified,
only that metric's value is returned per item.  When omitted, all non-null
metric values are returned as a ``metrics`` dict.
"""

from flask import g, jsonify
from flask.views import MethodView
from flask_smorest import Blueprint

from ..auth import require_scope
from ..errors import abort_with_error
from ..helpers import (
    format_utc,
    lookup_commit,
    lookup_run_by_uuid,
    parse_datetime,
    validate_metric_name,
)
from ..pagination import (
    build_cursor_filter,
    decode_multi_cursor,
    encode_multi_cursor,
    extract_cursor_values,
    make_paginated_response,
    parse_sort,
)
from ..schemas.samples_query import (
    SamplesQueryRequestSchema,
    SamplesQueryResponseSchema,
)

blp = Blueprint(
    'Samples',
    __name__,
    url_prefix='/api/v5/<testsuite>',
    description='Unified sample query endpoint',
)

_DEFAULT_LIMIT = 5000
_MAX_LIMIT = 10000

_ALLOWED_SORT_FIELDS = {'ordinal', 'submitted_at', 'test'}


def _resolve_sort_column(ts, field_name):
    """Map a sort field name to its SQLAlchemy column."""
    if field_name == 'ordinal':
        return ts.Commit.ordinal
    elif field_name == 'submitted_at':
        return ts.Run.submitted_at
    elif field_name == 'test':
        return ts.Test.name
    elif field_name == 'id':
        return ts.Sample.id
    raise ValueError("Unknown sort field: %s" % field_name)


def _build_query(session, ts, metric_name, metric_col, params, test_ids,
                 sort_spec, cursor_values, commit, run,
                 submitted_after, submitted_before, limit):
    """Build and execute the sample query.

    Uses column projection (not ORM objects) for performance.
    Returns (items, has_next, last_row_data).
    """
    # Build the SELECT columns.  Always include the base columns.
    select_cols = [
        ts.Test.name.label('test_name'),
        ts.Run.uuid.label('run_uuid'),
        ts.Commit.commit,
        ts.Commit.ordinal,
        ts.Commit.tag,
        ts.Run.submitted_at,
        ts.Sample.id.label('sample_id'),
    ]

    # When metric is specified, include only that column.
    # When omitted, include all metric columns.
    if metric_col is not None:
        select_cols.append(metric_col.label('metric_value'))
    else:
        for m in ts.schema.metrics:
            select_cols.append(getattr(ts.Sample, m.name).label(m.name))

    q = (
        session.query(*select_cols)
        .select_from(ts.Sample)
        .join(ts.Run, ts.Sample.run_id == ts.Run.id)
        .join(ts.Commit, ts.Run.commit_id == ts.Commit.id)
        .join(ts.Test, ts.Sample.test_id == ts.Test.id)
    )

    # When metric is specified, filter to non-null values.
    if metric_col is not None:
        q = q.filter(metric_col.isnot(None))

    # Params filter (JSONB containment on Run.run_parameters).
    if params:
        q = q.filter(ts._build_params_filter(ts.Run, params))

    # Test filter (OR across test names).
    if test_ids is not None:
        if len(test_ids) == 1:
            q = q.filter(ts.Sample.test_id == test_ids[0])
        else:
            q = q.filter(ts.Sample.test_id.in_(test_ids))

    # Commit filter.
    if commit is not None:
        q = q.filter(ts.Run.commit_id == commit.id)

    # Run filter.
    if run is not None:
        q = q.filter(ts.Sample.run_id == run.id)

    # Time range filters.
    if submitted_after is not None:
        q = q.filter(ts.Run.submitted_at > submitted_after)
    if submitted_before is not None:
        q = q.filter(ts.Run.submitted_at < submitted_before)

    # When sorting by ordinal, exclude NULL ordinals.
    sort_fields = {fn for fn, _ in sort_spec}
    if 'ordinal' in sort_fields:
        q = q.filter(ts.Commit.ordinal.isnot(None))

    # Cursor filter.
    if cursor_values is not None:
        try:
            q = q.filter(build_cursor_filter(
                sort_spec, cursor_values,
                lambda fn: _resolve_sort_column(ts, fn)))
        except (ValueError, TypeError):
            abort_with_error(400, "Invalid pagination cursor")

    # Ordering.
    for field_name, ascending in sort_spec:
        col = _resolve_sort_column(ts, field_name)
        q = q.order_by(col.asc() if ascending else col.desc())

    # Fetch limit + 1 to detect next page.
    rows = q.limit(limit + 1).all()

    has_next = len(rows) > limit
    rows = rows[:limit]

    items = []
    for row in rows:
        item = {
            'test': row.test_name,
            'run_uuid': row.run_uuid,
            'commit': row.commit,
            'ordinal': row.ordinal,
            'tag': row.tag,
            'submitted_at': format_utc(row.submitted_at),
        }

        if metric_col is not None:
            item[metric_name] = row.metric_value
        else:
            metrics = {}
            for m in ts.schema.metrics:
                val = getattr(row, m.name, None)
                if val is not None:
                    metrics[m.name] = val
            item['metrics'] = metrics

        items.append(item)

    last_row_data = None
    if rows:
        r = rows[-1]
        last_row_data = {
            'ordinal': r.ordinal,
            'submitted_at': format_utc(r.submitted_at),
            'test': r.test_name,
            'id': r.sample_id,
        }

    return items, has_next, last_row_data


@blp.route('/samples')
class SamplesQueryView(MethodView):
    """Query sample data points."""

    @require_scope('read')
    @blp.arguments(SamplesQueryRequestSchema, location="json")
    @blp.response(200, SamplesQueryResponseSchema)
    def post(self, query_args, testsuite):
        """Query sample data points (unified endpoint).

        Returns cursor-paginated sample data.  When ``metric`` is specified,
        only that metric's value appears per item.  When omitted, all
        non-null metric values are returned as a ``metrics`` dict.
        """
        ts = g.ts
        session = g.db_session

        # -- Params filter --
        params = query_args.get('params')

        # -- Test filter --
        test_names = query_args.get('test')
        test_ids = None
        if test_names:
            test_ids = []
            for tn in test_names:
                test = ts.get_test(session, name=tn)
                if test is not None:
                    test_ids.append(test.id)
            if not test_ids:
                return jsonify(make_paginated_response([], None))

        # -- Metric --
        metric_name = query_args.get('metric')
        metric_col = None
        if metric_name:
            validate_metric_name(ts, metric_name)
            metric_col = getattr(ts.Sample, metric_name)

        # -- Commit filter --
        commit_str = query_args.get('commit')
        commit = None
        if commit_str:
            commit = lookup_commit(session, ts, commit_str)

        # -- Run filter --
        run_uuid_str = query_args.get('run')
        run = None
        if run_uuid_str:
            run = lookup_run_by_uuid(session, ts, run_uuid_str)

        # -- Sort --
        sort_str = query_args.get('sort')
        sort_spec = parse_sort(sort_str, _ALLOWED_SORT_FIELDS)
        if sort_spec is None:
            abort_with_error(
                400, "Invalid sort parameter. Allowed fields: %s. "
                     "Use - prefix for descending."
                     % ', '.join(sorted(_ALLOWED_SORT_FIELDS)))

        # -- Time range --
        submitted_after_str = query_args.get('submitted_after')
        submitted_before_str = query_args.get('submitted_before')

        submitted_after = None
        if submitted_after_str:
            submitted_after = parse_datetime(submitted_after_str)
            if submitted_after is None:
                abort_with_error(
                    400, "Invalid submitted_after format, expected ISO 8601")

        submitted_before = None
        if submitted_before_str:
            submitted_before = parse_datetime(submitted_before_str)
            if submitted_before is None:
                abort_with_error(
                    400, "Invalid submitted_before format, expected ISO 8601")

        # -- Pagination --
        limit = query_args['limit']
        limit = max(1, min(limit, _MAX_LIMIT))

        cursor_str = query_args.get('cursor')
        cursor_values = None
        if cursor_str:
            cursor_values = decode_multi_cursor(cursor_str, len(sort_spec))
            if cursor_values is None:
                abort_with_error(400, "Invalid pagination cursor")

        # -- Execute --
        items, has_next, last_row_data = _build_query(
            session, ts, metric_name, metric_col, params, test_ids,
            sort_spec, cursor_values, commit, run,
            submitted_after, submitted_before, limit)

        # -- Build cursor and response --
        next_cursor = None
        if has_next and last_row_data:
            cursor_vals = extract_cursor_values(sort_spec, last_row_data)
            next_cursor = encode_multi_cursor(cursor_vals)

        return jsonify(make_paginated_response(items, next_cursor))
