"""Marshmallow schemas for the POST /samples query endpoint in the v5 API."""

import marshmallow as ma

from . import BaseSchema
from .common import CursorSchema


class SamplesQueryRequestSchema(BaseSchema):
    """JSON body for POST /samples."""

    class Meta:
        ordered = True
        unknown = ma.RAISE

    params = ma.fields.Dict(
        keys=ma.fields.String(),
        values=ma.fields.String(),
        load_default=None,
        metadata={'description': 'Run parameter filters (key -> value, AND across keys)'},
    )
    test = ma.fields.List(
        ma.fields.String(),
        load_default=None,
        metadata={'description': 'Filter by test name(s) (disjunction)'},
    )
    metric = ma.fields.String(
        load_default=None,
        metadata={'description': 'When specified, only this metric is returned per sample'},
    )
    commit = ma.fields.String(
        load_default=None,
        metadata={'description': 'Filter by exact commit string'},
    )
    run = ma.fields.String(
        load_default=None,
        metadata={'description': 'Filter by run UUID'},
    )
    sort = ma.fields.String(
        load_default=None,
        metadata={
            'description': 'Comma-separated sort fields: ordinal, submitted_at, '
            'test (prefix with - for descending). Default: by Sample.id.',
        },
    )
    limit = ma.fields.Integer(
        load_default=5000,
        metadata={'description': 'Maximum results per page (default 5000, max 10000)'},
    )
    cursor = ma.fields.String(
        load_default=None,
        metadata={'description': 'Pagination cursor from a previous response'},
    )
    submitted_before = ma.fields.String(
        load_default=None,
        metadata={'description': 'ISO datetime upper bound on Run.submitted_at'},
    )
    submitted_after = ma.fields.String(
        load_default=None,
        metadata={'description': 'ISO datetime lower bound on Run.submitted_at'},
    )


class SamplesQueryResponseSchema(BaseSchema):
    """Response schema for POST /api/v5/{ts}/samples."""
    items = ma.fields.List(
        ma.fields.Dict(),
        required=True,
        metadata={'description': 'Sample data points'},
    )
    cursor = ma.fields.Nested(CursorSchema)
