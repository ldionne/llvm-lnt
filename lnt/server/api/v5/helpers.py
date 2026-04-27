"""Shared helper functions for v5 API endpoints."""

import datetime

import marshmallow as ma
from flask import request as flask_request

from .errors import abort_with_error
from .schemas.runs import RunResponseSchema

_run_schema = RunResponseSchema()


def parse_datetime(value):
    """Parse an ISO datetime string. Returns a timezone-aware UTC datetime
    or None.

    Two differences from ``datetime.fromisoformat``:

    1. Accepts ``Z`` as a timezone suffix (mapped to ``+00:00``).
    2. Always returns a **timezone-aware UTC** datetime.  Bare datetime
       strings (no timezone suffix) are assumed to be UTC.
    """
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is not None:
            dt = dt.astimezone(datetime.timezone.utc)
        else:
            # Bare datetime assumed UTC
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def escape_like(pattern):
    """Escape SQL LIKE wildcards in user-supplied patterns."""
    return pattern.replace('\\', '\\\\').replace('%', r'\%').replace('_', r'\_')


def validate_metric_name(ts, field_name):
    """Validate that *field_name* is a known metric for this test suite.

    Aborts with 400 if the metric is not found.  Returns *field_name*
    unchanged on success.
    """
    if field_name not in ts._metric_names:
        abort_with_error(400, "Unknown metric name '%s'" % field_name)
    return field_name


def get_metric_def(ts, metric_name):
    """Validate *metric_name* and return its schema Metric definition.

    Aborts with 400 if the metric is not found.
    """
    validate_metric_name(ts, metric_name)
    for m in ts.schema.metrics:
        if m.name == metric_name:
            return m
    # Unreachable if validate_metric_name passed
    abort_with_error(400, "Unknown metric name '%s'" % metric_name)


# ---------------------------------------------------------------------------
# Entity lookup helpers (abort with 404 if not found)
# ---------------------------------------------------------------------------

def lookup_run_by_uuid(session, ts, run_uuid):
    """Look up a Run by UUID. Aborts with 404 if not found."""
    run = ts.get_run(session, uuid=run_uuid)
    if run is None:
        abort_with_error(404, "Run '%s' not found" % run_uuid)
    return run


def lookup_commit(session, ts, commit_id):
    """Look up a Commit by its identity string (e.g. git SHA).

    Aborts with 404 if not found.
    """
    commit_obj = ts.get_commit(session, commit=commit_id)
    if commit_obj is None:
        abort_with_error(404, "Commit '%s' not found" % commit_id)
    return commit_obj


def lookup_test(session, ts, test_name):
    """Look up a Test by name. Aborts with 404 if not found."""
    test = ts.get_test(session, name=test_name)
    if test is None:
        abort_with_error(404, "Test '%s' not found" % test_name)
    return test


def lookup_regression(session, ts, regression_uuid):
    """Look up a Regression by UUID. Aborts with 404 if not found."""
    regression = ts.get_regression(session, uuid=regression_uuid)
    if regression is None:
        abort_with_error(404, "Regression '%s' not found" % regression_uuid)
    return regression


def lookup_profile(session, ts, profile_uuid, *, load_data=False):
    """Look up a Profile by UUID. Aborts with 404 if not found.

    When *load_data* is True, eagerly loads the deferred ``data`` column
    and joins the ``test`` and ``run`` relations.
    """
    profile = ts.get_profile(session, uuid=profile_uuid, load_data=load_data)
    if profile is None:
        abort_with_error(404, "Profile '%s' not found" % profile_uuid)
    return profile


# ---------------------------------------------------------------------------
# Run parameter extraction
# ---------------------------------------------------------------------------

def extract_param_filters(request=None):
    """Extract ``param.*`` query parameters from the request.

    Returns a dict of ``{key: value_or_list}`` suitable for passing to
    ``V5TestSuiteDB._build_params_filter``.  Multiple values for the
    same key are collected into a list (OR semantics); single values
    remain as plain strings.

    Uses ``request.args.getlist()`` for multi-value support.
    """
    if request is None:
        request = flask_request
    params = {}
    for full_key in request.args:
        if full_key.startswith('param.'):
            key = full_key[len('param.'):]
            if not key:
                continue
            values = request.args.getlist(full_key)
            if len(values) == 1:
                params[key] = values[0]
            else:
                params[key] = values
    return params


def build_api_params_filter(ts, params):
    """Build a SQLAlchemy filter from extracted param dict.

    Wrapper around the DB-layer ``_build_params_filter`` for API use.
    Returns None if *params* is empty.
    """
    if not params:
        return None
    return ts._build_params_filter(ts.Run, params)


# ---------------------------------------------------------------------------
# Response validation
# ---------------------------------------------------------------------------

def dump_response(schema, data):
    """Validate and serialize *data* through a marshmallow schema.

    Catches serializer-schema drift at dev time:

    - Extra keys in *data* not declared in the schema raise ValueError
      (dump() would silently discard them).
    - Missing required fields are caught by validate().
    """
    schema_fields = set(schema.dump_fields.keys())
    extra = set(data.keys()) - schema_fields
    if extra:
        raise ValueError(
            f"Serializer produced keys not in "
            f"{type(schema).__name__}: {extra}")
    result = schema.dump(data)
    errors = schema.validate(result)
    if errors:
        raise ma.ValidationError(errors)
    return result


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def format_utc(dt):
    """Format a UTC datetime as an ISO 8601 string with Z suffix.

    Returns None if *dt* is None.  Naive datetimes are assumed to be
    UTC and tagged accordingly.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')


def serialize_run(run, ts):
    """Serialize a Run model instance for API responses.

    Returns a validated dict with uuid, commit, submitted_at,
    and run_parameters.
    """
    data = {
        'uuid': run.uuid,
        'commit': run.commit_obj.commit if run.commit_obj else None,
        'submitted_at': format_utc(run.submitted_at),
        'run_parameters': dict(run.run_parameters) if run.run_parameters else {},
    }
    return dump_response(_run_schema, data)
