"""The one-shot flow's data side: read a user's data in any shape and map it onto a form's schema.

    read_table, sniff_text, profile, data_schema    formcast.data.table
    DataMapping, apply_mapping, records_from, ...   formcast.data.mapping
    derive_schema, map_table, extract_records       formcast.data.ai (one model call each)
"""

from formcast.data.ai import ExtractResult, MappingResult, SchemaResult, derive_schema, extract_records, map_table
from formcast.data.mapping import (
    DataMapping, Rule, Transform, apply_mapping, check_mapping, direct_mapping, records_from, target_paths,
)
from formcast.data.table import ColumnProfile, Table, data_schema, profile, read_table, sniff_text

__all__ = [
    "ColumnProfile", "DataMapping", "ExtractResult", "MappingResult", "Rule", "SchemaResult", "Table", "Transform",
    "apply_mapping", "check_mapping", "data_schema", "derive_schema", "direct_mapping", "extract_records",
    "map_table", "profile", "read_table", "records_from", "sniff_text", "target_paths",
]  # fmt: skip
