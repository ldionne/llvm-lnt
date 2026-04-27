"""Dashboard configuration endpoints for the v5 API.

GET    /api/v5/{ts}/dashboard           -- Get dashboard card configuration
PUT    /api/v5/{ts}/dashboard           -- Replace dashboard card configuration
"""

from flask import g, jsonify
from flask.views import MethodView
from flask_smorest import Blueprint

from ..auth import require_scope
from ..errors import reject_unknown_params
from ..schemas.dashboard import (
    DashboardPutRequestSchema,
    DashboardResponseSchema,
)

blp = Blueprint(
    'Dashboard',
    __name__,
    url_prefix='/api/v5/<testsuite>',
    description='Dashboard card configuration',
)


@blp.route('/dashboard')
class Dashboard(MethodView):
    """Get and replace dashboard card configuration."""

    @require_scope('read')
    @blp.response(200, DashboardResponseSchema)
    def get(self, testsuite):
        """Get current dashboard card configuration."""
        reject_unknown_params(set())
        ts = g.ts
        session = g.db_session

        cards = ts.get_dashboard_cards(session)
        items = []
        for card in cards:
            items.append({
                'position': card.position,
                'params': dict(card.params) if card.params else {},
                'metric': card.metric,
                'last_n': card.last_n,
            })
        return jsonify({'cards': items})

    @require_scope('manage')
    @blp.arguments(DashboardPutRequestSchema)
    @blp.response(200, DashboardResponseSchema)
    def put(self, body, testsuite):
        """Replace the full dashboard card configuration."""
        reject_unknown_params(set())
        ts = g.ts
        session = g.db_session

        card_data = body['cards']
        created = ts.set_dashboard_cards(session, card_data)
        session.flush()

        items = []
        for card in created:
            items.append({
                'position': card.position,
                'params': dict(card.params) if card.params else {},
                'metric': card.metric,
                'last_n': card.last_n,
            })
        return jsonify({'cards': items})
