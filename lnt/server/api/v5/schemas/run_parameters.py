"""Marshmallow schemas for run parameter endpoints."""

import marshmallow as ma

from . import BaseSchema
from .common import BaseQuerySchema, OffsetPaginationQuerySchema, PaginatedResponseSchema


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class ParameterKeySchema(BaseSchema):
    """A single parameter key."""
    key = ma.fields.String(
        required=True,
        metadata={'description': 'Parameter key name'},
    )


class ParameterValueSchema(BaseSchema):
    """A single parameter value."""
    value = ma.fields.String(
        required=True,
        metadata={'description': 'Parameter value'},
    )


class PaginatedParameterKeyResponseSchema(PaginatedResponseSchema):
    """Paginated list of parameter keys."""
    items = ma.fields.List(ma.fields.Nested(ParameterKeySchema))


class PaginatedParameterValueResponseSchema(PaginatedResponseSchema):
    """Paginated list of parameter values."""
    items = ma.fields.List(ma.fields.Nested(ParameterValueSchema))


# ---------------------------------------------------------------------------
# Query parameter schemas
# ---------------------------------------------------------------------------

class ParameterKeyListQuerySchema(OffsetPaginationQuerySchema):
    """Query parameters for GET /run-parameters."""
    search = ma.fields.String(
        load_default=None,
        metadata={'description': 'Prefix search on parameter key names'},
    )


class ParameterValueListQuerySchema(OffsetPaginationQuerySchema):
    """Query parameters for GET /run-parameters/{key}/values."""
    search = ma.fields.String(
        load_default=None,
        metadata={'description': 'Prefix search on parameter values'},
    )
