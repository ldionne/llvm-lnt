# RUN: python %s
# END.

import sys
import unittest

from lnt.server.db.v5.schema import (
    CommitField,
    Metric,
    SchemaError,
    parse_schema,
)


class TestParseSchemaBasic(unittest.TestCase):

    def test_minimal_schema(self):
        schema = parse_schema({"name": "minimal"})
        self.assertEqual(schema.name, "minimal")
        self.assertEqual(schema.metrics, [])
        self.assertEqual(schema.commit_fields, [])

    def test_full_schema(self):
        data = {
            "name": "nts",
            "metrics": [
                {"name": "compile_time", "type": "Real", "display_name": "Compile Time",
                 "unit": "seconds", "unit_abbrev": "s"},
                {"name": "execution_time", "type": "real", "bigger_is_better": False},
                {"name": "compile_status", "type": "status"},
                {"name": "hash", "type": "Hash"},
            ],
            "commit_fields": [
                {"name": "git_sha", "searchable": True},
                {"name": "author", "searchable": True},
                {"name": "commit_message", "type": "text"},
                {"name": "commit_timestamp", "type": "datetime"},
                {"name": "priority", "type": "integer"},
            ],
        }
        schema = parse_schema(data)
        self.assertEqual(schema.name, "nts")

        self.assertEqual(len(schema.metrics), 4)
        self.assertEqual(schema.metrics[0].name, "compile_time")
        self.assertEqual(schema.metrics[0].type, "real")
        self.assertEqual(schema.metrics[0].display_name, "Compile Time")
        self.assertEqual(schema.metrics[0].unit, "seconds")
        self.assertEqual(schema.metrics[1].type, "real")
        self.assertEqual(schema.metrics[2].type, "status")
        self.assertEqual(schema.metrics[3].type, "hash")

        self.assertEqual(len(schema.commit_fields), 5)
        self.assertTrue(schema.commit_fields[0].searchable)
        self.assertEqual(schema.commit_fields[2].type, "text")
        self.assertEqual(schema.commit_fields[3].type, "datetime")
        self.assertEqual(schema.commit_fields[4].type, "integer")

    def test_searchable_commit_fields(self):
        data = {
            "name": "test",
            "commit_fields": [
                {"name": "a", "searchable": True},
                {"name": "b", "searchable": False},
                {"name": "c"},
            ],
        }
        schema = parse_schema(data)
        self.assertEqual(len(schema.searchable_commit_fields), 1)
        self.assertEqual(schema.searchable_commit_fields[0].name, "a")

    def test_searchable_fields_cached(self):
        """searchable_commit_fields should return the same list object on repeated access."""
        data = {
            "name": "test",
            "commit_fields": [{"name": "a", "searchable": True}],
        }
        schema = parse_schema(data)
        self.assertIs(schema.searchable_commit_fields, schema.searchable_commit_fields)

    def test_machine_fields_silently_ignored(self):
        """The machine_fields key, if present, should be silently ignored."""
        data = {
            "name": "test",
            "machine_fields": [
                {"name": "hardware", "searchable": True},
                {"name": "os"},
            ],
        }
        schema = parse_schema(data)
        self.assertEqual(schema.name, "test")
        # No machine_fields attribute on the schema
        self.assertFalse(hasattr(schema, "machine_fields"))

    def test_schema_without_machine_fields_parses(self):
        """Schema without machine_fields should parse normally."""
        data = {
            "name": "test",
            "metrics": [{"name": "time", "type": "real"}],
        }
        schema = parse_schema(data)
        self.assertEqual(schema.name, "test")


class TestSchemaValidation(unittest.TestCase):

    def test_missing_name(self):
        with self.assertRaises(SchemaError):
            parse_schema({})

    def test_empty_name(self):
        with self.assertRaises(SchemaError):
            parse_schema({"name": ""})

    def test_reserved_commit_field_id(self):
        data = {
            "name": "test",
            "commit_fields": [{"name": "id"}],
        }
        with self.assertRaises(SchemaError):
            parse_schema(data)

    def test_reserved_commit_field_commit(self):
        data = {
            "name": "test",
            "commit_fields": [{"name": "commit"}],
        }
        with self.assertRaises(SchemaError):
            parse_schema(data)

    def test_reserved_commit_field_ordinal(self):
        data = {
            "name": "test",
            "commit_fields": [{"name": "ordinal"}],
        }
        with self.assertRaises(SchemaError):
            parse_schema(data)

    def test_reserved_commit_field_tag(self):
        """'tag' is reserved and cannot be used as a commit_field name."""
        with self.assertRaises(SchemaError):
            parse_schema({
                "name": "test",
                "commit_fields": [{"name": "tag"}],
            })

    def test_unknown_commit_field_type(self):
        data = {
            "name": "test",
            "commit_fields": [{"name": "foo", "type": "blob"}],
        }
        with self.assertRaises(SchemaError):
            parse_schema(data)

    def test_unknown_metric_type(self):
        data = {
            "name": "test",
            "metrics": [{"name": "foo", "type": "complex"}],
        }
        with self.assertRaises(SchemaError):
            parse_schema(data)

    def test_duplicate_commit_field(self):
        data = {
            "name": "test",
            "commit_fields": [{"name": "a"}, {"name": "a"}],
        }
        with self.assertRaises(SchemaError):
            parse_schema(data)

    def test_duplicate_metric(self):
        data = {
            "name": "test",
            "metrics": [{"name": "a"}, {"name": "a"}],
        }
        with self.assertRaises(SchemaError):
            parse_schema(data)

    def test_commit_field_missing_name(self):
        data = {
            "name": "test",
            "commit_fields": [{"type": "text"}],
        }
        with self.assertRaises(SchemaError):
            parse_schema(data)

    def test_metric_missing_name(self):
        data = {
            "name": "test",
            "metrics": [{"type": "real"}],
        }
        with self.assertRaises(SchemaError):
            parse_schema(data)

    def test_default_commit_field_type(self):
        """Omitting type on commit_field should default to 'default'."""
        data = {
            "name": "test",
            "commit_fields": [{"name": "foo"}],
        }
        schema = parse_schema(data)
        self.assertEqual(schema.commit_fields[0].type, "default")

    def test_default_metric_type(self):
        """Omitting type on metric should default to 'real'."""
        data = {
            "name": "test",
            "metrics": [{"name": "foo"}],
        }
        schema = parse_schema(data)
        self.assertEqual(schema.metrics[0].type, "real")

    def test_bigger_is_better(self):
        data = {
            "name": "test",
            "metrics": [{"name": "score", "type": "real", "bigger_is_better": True}],
        }
        schema = parse_schema(data)
        self.assertTrue(schema.metrics[0].bigger_is_better)

    def test_reserved_metric_name_id(self):
        """Metric named 'id' should be rejected (reserved Sample column)."""
        data = {
            "name": "test",
            "metrics": [{"name": "id", "type": "real"}],
        }
        with self.assertRaises(SchemaError):
            parse_schema(data)

    def test_reserved_metric_name_run_id(self):
        """Metric named 'run_id' should be rejected (reserved Sample column)."""
        data = {
            "name": "test",
            "metrics": [{"name": "run_id", "type": "real"}],
        }
        with self.assertRaises(SchemaError):
            parse_schema(data)

    def test_reserved_metric_name_test_id(self):
        """Metric named 'test_id' should be rejected (reserved Sample column)."""
        data = {
            "name": "test",
            "metrics": [{"name": "test_id", "type": "real"}],
        }
        with self.assertRaises(SchemaError):
            parse_schema(data)


class TestDisplayFlag(unittest.TestCase):
    """Tests for the display:true commit_field validation (design D4)."""

    def test_single_display_true(self):
        data = {
            "name": "test",
            "commit_fields": [
                {"name": "sha", "display": True},
                {"name": "author"},
            ],
        }
        schema = parse_schema(data)
        self.assertTrue(schema.commit_fields[0].display)
        self.assertFalse(schema.commit_fields[1].display)

    def test_no_display_true(self):
        data = {
            "name": "test",
            "commit_fields": [
                {"name": "sha"},
                {"name": "author"},
            ],
        }
        schema = parse_schema(data)
        self.assertFalse(schema.commit_fields[0].display)
        self.assertFalse(schema.commit_fields[1].display)

    def test_multiple_display_true_rejected(self):
        data = {
            "name": "test",
            "commit_fields": [
                {"name": "sha", "display": True},
                {"name": "label", "display": True},
            ],
        }
        with self.assertRaises(SchemaError):
            parse_schema(data)

    def test_display_default_false(self):
        data = {
            "name": "test",
            "commit_fields": [{"name": "foo"}],
        }
        schema = parse_schema(data)
        self.assertFalse(schema.commit_fields[0].display)


class TestDataclassImmutability(unittest.TestCase):
    """Schema dataclasses should be frozen."""

    def test_commit_field_frozen(self):
        cf = CommitField(name="x")
        with self.assertRaises(AttributeError):
            cf.name = "y"

    def test_metric_frozen(self):
        m = Metric(name="x")
        with self.assertRaises(AttributeError):
            m.name = "y"


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], exit=True)
