"""Run parameter endpoints for the v5 API.

GET    /api/v5/{ts}/run-parameters              -- List known parameter keys
GET    /api/v5/{ts}/run-parameters/{key}/values -- List known values for a key
"""

from flask import g, jsonify
from flask.views import MethodView
from flask_smorest import Blueprint

from ..auth import require_scope
from ..errors import reject_unknown_params
from ..pagination import make_paginated_response
from ..schemas.run_parameters import (
    PaginatedParameterKeyResponseSchema,
    PaginatedParameterValueResponseSchema,
    ParameterKeyListQuerySchema,
    ParameterValueListQuerySchema,
)

blp = Blueprint(
    'Run Parameters',
    __name__,
    url_prefix='/api/v5/<testsuite>',
    description='Discover run parameter keys and values for autocomplete',
)


@blp.route('/run-parameters')
class ParameterKeyList(MethodView):
    """List known parameter keys."""

    @require_scope('read')
    @blp.arguments(ParameterKeyListQuerySchema, location="query")
    @blp.response(200, PaginatedParameterKeyResponseSchema)
    def get(self, query_args, testsuite):
        """List known parameter keys (paginated, searchable)."""
        reject_unknown_params({'search', 'limit', 'offset'})
        ts = g.ts
        session = g.db_session

        search = query_args.get('search')
        limit = query_args['limit']
        offset = query_args['offset']

        keys = ts.list_parameter_keys(
            session, search=search, limit=limit, offset=offset)

        items = [{'key': k.key} for k in keys]
        # Use None for next cursor (offset-based pagination)
        return jsonify(make_paginated_response(items, None))


@blp.route('/run-parameters/<string:key_name>/values')
class ParameterValueList(MethodView):
    """List known values for a parameter key."""

    @require_scope('read')
    @blp.arguments(ParameterValueListQuerySchema, location="query")
    @blp.response(200, PaginatedParameterValueResponseSchema)
    def get(self, query_args, testsuite, key_name):
        """List known values for a parameter key (paginated, searchable)."""
        reject_unknown_params({'search', 'limit', 'offset'})
        ts = g.ts
        session = g.db_session

        search = query_args.get('search')
        limit = query_args['limit']
        offset = query_args['offset']

        values = ts.list_parameter_values(
            session, key_name, search=search, limit=limit, offset=offset)

        items = [{'value': v.value} for v in values]
        return jsonify(make_paginated_response(items, None))
