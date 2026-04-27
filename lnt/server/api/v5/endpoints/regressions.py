"""Regression endpoints for the v5 API.

GET    /api/v5/{ts}/regressions                     -- List
POST   /api/v5/{ts}/regressions                     -- Create
GET    /api/v5/{ts}/regressions/{uuid}              -- Detail
PATCH  /api/v5/{ts}/regressions/{uuid}              -- Update
DELETE /api/v5/{ts}/regressions/{uuid}              -- Delete
POST   /api/v5/{ts}/regressions/{uuid}/indicators   -- Add indicators (batch)
DELETE /api/v5/{ts}/regressions/{uuid}/indicators   -- Remove indicators (batch)
"""

from flask import g, jsonify, make_response
from flask.views import MethodView
from flask_smorest import Blueprint
from sqlalchemy.orm import joinedload, subqueryload

from ..auth import require_scope
from ..errors import abort_with_error, reject_unknown_params
from ..helpers import (
    dump_response,
    extract_param_filters,
    lookup_commit,
    lookup_regression,
    lookup_run_by_uuid,
    lookup_test,
    validate_metric_name,
)
from ..etag import add_etag_to_response
from ..pagination import (
    cursor_paginate,
    make_paginated_response,
)
from ..schemas.regressions import (
    IndicatorAddSchema,
    IndicatorRemoveSchema,
    IndicatorResponseSchema,
    PaginatedRegressionListSchema,
    RegressionCreateSchema,
    RegressionDetailSchema,
    RegressionListItemSchema,
    RegressionListQuerySchema,
    RegressionUpdateSchema,
    STATE_TO_DB,
    state_to_api,
    state_to_db,
)

_indicator_schema = IndicatorResponseSchema()
_regression_list_schema = RegressionListItemSchema()
_regression_detail_schema = RegressionDetailSchema()

blp = Blueprint(
    'Regressions',
    __name__,
    url_prefix='/api/v5/<testsuite>',
    description='Triage performance regressions: create, update, delete, and manage indicators',
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _serialize_indicator(ri):
    """Serialize a RegressionIndicator into the API response dict."""
    return dump_response(_indicator_schema, {
        'uuid': ri.uuid,
        'run_uuid': ri.run.uuid if ri.run else None,
        'test': ri.test.name if ri.test else None,
        'metric': ri.metric,
    })


def _serialize_regression_base(regression):
    """Shared fields for both list and detail regression responses."""
    return {
        'uuid': regression.uuid,
        'title': regression.title,
        'bug': regression.bug,
        'state': state_to_api(regression.state),
        'commit': (regression.commit_obj.commit
                   if regression.commit_obj else None),
    }


def _serialize_regression_list(regression):
    """Serialize a Regression for the list endpoint.

    Requires the regression to have indicators eagerly loaded (or
    accessible) for computing run_count and test_count.
    """
    runs = set()
    tests = set()
    for ri in regression.indicators:
        runs.add(ri.run_id)
        tests.add(ri.test_id)

    result = _serialize_regression_base(regression)
    result['run_count'] = len(runs)
    result['test_count'] = len(tests)
    return dump_response(_regression_list_schema, result)


def _serialize_regression_detail(regression):
    """Serialize a Regression for the detail endpoint (with indicators)."""
    result = _serialize_regression_base(regression)
    result['notes'] = regression.notes
    result['indicators'] = [
        _serialize_indicator(ri) for ri in regression.indicators
    ]
    return dump_response(_regression_detail_schema, result)


def _validate_state(state_str):
    """Validate and convert a state string to its DB integer.

    Aborts with 400 if invalid.
    """
    db_state = state_to_db(state_str)
    if db_state is None:
        abort_with_error(
            400,
            "Invalid state '%s'. Valid states: %s"
            % (state_str, ', '.join(sorted(STATE_TO_DB.keys()))))
    return db_state


def _resolve_indicators(session, ts, indicator_dicts):
    """Resolve indicator input dicts to DB-ready dicts.

    Each input dict has {run_uuid, test, metric} (names/UUIDs).  This
    function looks up each entity and returns a list of dicts with
    {run_id, test_id, metric}.

    Aborts with 404 if any run or test is not found, 400 if metric is
    unknown.
    """
    resolved = []
    run_cache = {}
    test_cache = {}
    for ind in indicator_dicts:
        r_uuid = ind['run_uuid']
        if r_uuid not in run_cache:
            run_cache[r_uuid] = lookup_run_by_uuid(session, ts, r_uuid)
        t_name = ind['test']
        if t_name not in test_cache:
            test_cache[t_name] = lookup_test(session, ts, t_name)
        validate_metric_name(ts, ind['metric'])
        resolved.append({
            'run_id': run_cache[r_uuid].id,
            'test_id': test_cache[t_name].id,
            'metric': ind['metric'],
        })
    return resolved


def _eager_load_regression(session, ts, regression_uuid):
    """Look up a regression by UUID with eager-loaded relationships.

    Loads indicators with their run (and run's commit) and test
    relationships for serialization. Aborts with 404 if not found.
    """
    reg = (
        session.query(ts.Regression)
        .populate_existing()
        .options(
            joinedload(ts.Regression.commit_obj),
            subqueryload(ts.Regression.indicators)
            .joinedload(ts.RegressionIndicator.run)
            .joinedload(ts.Run.commit_obj),
            subqueryload(ts.Regression.indicators)
            .joinedload(ts.RegressionIndicator.test),
        )
        .filter(ts.Regression.uuid == regression_uuid)
        .first()
    )
    if reg is None:
        abort_with_error(404, "Regression '%s' not found" % regression_uuid)
    return reg


# ---------------------------------------------------------------------------
# Regression List / Create
# ---------------------------------------------------------------------------

@blp.route('/regressions')
class RegressionList(MethodView):
    """List and create regressions."""

    @require_scope('read')
    @blp.arguments(RegressionListQuerySchema, location="query")
    @blp.response(200, PaginatedRegressionListSchema)
    def get(self, query_args, testsuite):
        """List regressions (cursor-paginated, filterable)."""
        reject_unknown_params(
            {'state', 'test', 'metric', 'commit',
             'cursor', 'limit'})
        ts = g.ts
        session = g.db_session

        query = session.query(ts.Regression).options(
            joinedload(ts.Regression.commit_obj),
            subqueryload(ts.Regression.indicators),
        )

        # -- State filter --
        state_values = query_args['state']
        if state_values:
            db_states = [_validate_state(sv) for sv in state_values]
            query = query.filter(ts.Regression.state.in_(db_states))

        # -- Commit filter --
        commit_value = query_args.get('commit')
        if commit_value:
            commit_obj = lookup_commit(session, ts, commit_value)
            query = query.filter(
                ts.Regression.commit_id == commit_obj.id)

        # -- param.* filter (through indicator -> run -> run_parameters) --
        param_filters = extract_param_filters()
        if param_filters:
            indicator_run_subq = (
                session.query(ts.RegressionIndicator.regression_id)
                .join(ts.Run, ts.RegressionIndicator.run_id == ts.Run.id)
                .filter(
                    ts.RegressionIndicator.regression_id == ts.Regression.id,
                    ts._build_params_filter(ts.Run, param_filters),
                )
                .correlate(ts.Regression)
            )
            query = query.filter(indicator_run_subq.exists())

        # -- Test / metric filters (via indicator JOIN) --
        test_name = query_args.get('test')
        metric_name = query_args.get('metric')

        test = None
        if test_name:
            test = lookup_test(session, ts, test_name)
        if metric_name:
            validate_metric_name(ts, metric_name)

        if test or metric_name:
            query = query.join(
                ts.RegressionIndicator,
                ts.RegressionIndicator.regression_id == ts.Regression.id
            )

        if test:
            query = query.filter(
                ts.RegressionIndicator.test_id == test.id)
        if metric_name:
            query = query.filter(
                ts.RegressionIndicator.metric == metric_name)

        if test or metric_name:
            query = query.distinct()

        cursor_str = query_args.get('cursor')
        limit = query_args['limit']
        items, next_cursor = cursor_paginate(
            query, ts.Regression.id, cursor_str, limit)

        serialized = [_serialize_regression_list(r) for r in items]
        return jsonify(make_paginated_response(serialized, next_cursor))

    @require_scope('triage')
    @blp.arguments(RegressionCreateSchema)
    @blp.response(201, RegressionDetailSchema)
    def post(self, body, testsuite):
        """Create a new regression."""
        ts = g.ts
        session = g.db_session

        state_str = body.get('state') or 'detected'
        db_state = _validate_state(state_str)

        # Resolve commit by value (required)
        commit_value = body['commit']
        commit_obj = lookup_commit(session, ts, commit_value)

        # Resolve indicators (optional)
        indicator_dicts = body.get('indicators') or []
        resolved = _resolve_indicators(session, ts, indicator_dicts)

        title = body.get('title') or None
        bug = body.get('bug')
        notes = body.get('notes')

        try:
            regression = ts.create_regression(
                session, title, resolved,
                bug=bug, notes=notes, commit=commit_obj, state=db_state)
        except ValueError as e:
            abort_with_error(409, str(e))

        # Reload with eager-loaded relationships for serialization
        regression = _eager_load_regression(
            session, ts, regression.uuid)

        result = _serialize_regression_detail(regression)
        resp = jsonify(result)
        resp.status_code = 201
        return resp


# ---------------------------------------------------------------------------
# Regression Detail / Update / Delete
# ---------------------------------------------------------------------------

@blp.route('/regressions/<string:regression_uuid>')
class RegressionDetail(MethodView):
    """Regression detail, update, and delete."""

    @require_scope('read')
    @blp.response(200, RegressionDetailSchema)
    def get(self, testsuite, regression_uuid):
        """Get regression detail with embedded indicators."""
        reject_unknown_params(set())
        ts = g.ts
        session = g.db_session
        regression = _eager_load_regression(session, ts, regression_uuid)
        data = _serialize_regression_detail(regression)
        return add_etag_to_response(jsonify(data), data)

    @require_scope('triage')
    @blp.arguments(RegressionUpdateSchema)
    @blp.response(200, RegressionDetailSchema)
    def patch(self, body, testsuite, regression_uuid):
        """Update regression title, bug, notes, state, and/or commit."""
        ts = g.ts
        session = g.db_session
        regression = lookup_regression(session, ts, regression_uuid)

        # Fields present in body are updated; None values clear the field.
        # Fields absent from body are left unchanged.
        kwargs = {}

        if 'title' in body:
            kwargs['title'] = body['title']

        if 'bug' in body:
            kwargs['bug'] = body['bug']

        if 'notes' in body:
            kwargs['notes'] = body['notes']

        if 'state' in body:
            kwargs['state'] = _validate_state(body['state'])

        if 'commit' in body:
            commit_value = body['commit']
            kwargs['commit'] = lookup_commit(session, ts, commit_value)

        try:
            ts.update_regression(session, regression, **kwargs)
        except ValueError as e:
            abort_with_error(409, str(e))

        # Reload for serialization (relationships may have changed)
        regression = _eager_load_regression(session, ts, regression_uuid)
        return jsonify(_serialize_regression_detail(regression))

    @require_scope('triage')
    @blp.response(204)
    def delete(self, testsuite, regression_uuid):
        """Delete a regression and its indicators."""
        ts = g.ts
        session = g.db_session
        regression = lookup_regression(session, ts, regression_uuid)
        ts.delete_regression(session, regression.id)
        return make_response('', 204)


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

@blp.route('/regressions/<string:regression_uuid>/indicators')
class RegressionIndicators(MethodView):
    """Add and remove indicators for a regression (batch operations)."""

    @require_scope('triage')
    @blp.arguments(IndicatorAddSchema)
    @blp.response(200, RegressionDetailSchema)
    def post(self, body, testsuite, regression_uuid):
        """Add indicators to a regression (batch).

        Duplicates (same regression+run+test+metric) are silently
        ignored.  Each indicator's run must belong to the regression's
        commit (409 if not).
        """
        ts = g.ts
        session = g.db_session
        regression = lookup_regression(session, ts, regression_uuid)

        indicator_dicts = body['indicators']
        resolved = _resolve_indicators(session, ts, indicator_dicts)

        try:
            ts.add_regression_indicators_batch(session, regression, resolved)
        except ValueError as e:
            abort_with_error(409, str(e))

        # Reload and return full detail
        regression = _eager_load_regression(
            session, ts, regression_uuid)
        return jsonify(_serialize_regression_detail(regression))

    @require_scope('triage')
    @blp.arguments(IndicatorRemoveSchema)
    @blp.response(200, RegressionDetailSchema)
    def delete(self, body, testsuite, regression_uuid):
        """Remove indicators from a regression (batch, by UUID).

        Unknown UUIDs are silently ignored.
        """
        ts = g.ts
        session = g.db_session
        regression = lookup_regression(session, ts, regression_uuid)

        session.query(ts.RegressionIndicator).filter(
            ts.RegressionIndicator.regression_id == regression.id,
            ts.RegressionIndicator.uuid.in_(body['indicator_uuids']),
        ).delete(synchronize_session='fetch')
        session.flush()

        # Reload and return full detail
        regression = _eager_load_regression(
            session, ts, regression_uuid)
        return jsonify(_serialize_regression_detail(regression))
