"""Marshmallow schemas for dashboard endpoints."""

import marshmallow as ma

from . import BaseSchema


# ---------------------------------------------------------------------------
# Response / request schemas
# ---------------------------------------------------------------------------

class DashboardCardSchema(BaseSchema):
    """A single dashboard card."""
    position = ma.fields.Integer(
        required=True,
        metadata={'description': 'Card position (0-indexed)'},
    )
    params = ma.fields.Dict(
        keys=ma.fields.String(),
        values=ma.fields.Raw(),
        load_default={},
        metadata={
            'description': 'Run parameter query for this card',
            'example': {'compiler': 'clang-21'},
        },
    )
    metric = ma.fields.String(
        required=True,
        metadata={'description': 'Metric name for this card'},
    )
    last_n = ma.fields.Integer(
        load_default=500,
        metadata={'description': 'Number of recent commits to include'},
    )


class DashboardResponseSchema(BaseSchema):
    """Response for GET /dashboard."""
    cards = ma.fields.List(
        ma.fields.Nested(DashboardCardSchema),
        required=True,
        metadata={'description': 'Dashboard card configuration'},
    )


class DashboardPutRequestSchema(BaseSchema):
    """Request body for PUT /dashboard."""
    cards = ma.fields.List(
        ma.fields.Nested(DashboardCardSchema),
        required=True,
        metadata={'description': 'New dashboard card configuration (replaces all)'},
    )
