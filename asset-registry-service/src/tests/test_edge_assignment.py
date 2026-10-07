"""edge_assignment.load_yaml_no_duplicate_keys: refuse silent last-wins on
a repeated mapping key.

Mirrors openddil-projector/src/tests/test_edge_assignment.py's coverage of
the same loader (the two repos carry independent copies of
_NoDuplicateKeysLoader / load_yaml_no_duplicate_keys -- see edge_assignment.py's
module docstring). These tests exercise this repo's copy directly, plus
main.py's _load_edge_assignment wiring, which degrades to the permissive
fallback strategy (logged error, not a crash) on a bad file -- unlike
projector, where a malformed config is fatal at startup.
"""
from __future__ import annotations

import logging

import pytest

from asset_registry_service.edge_assignment import load_yaml_no_duplicate_keys
from asset_registry_service.main import _load_edge_assignment
from asset_registry_service import edge_assignment as ea


def test_load_yaml_no_duplicate_keys_accepts_unique_keys():
    result = load_yaml_no_duplicate_keys("a: 1\nb: 2\n")
    assert result == {"a": 1, "b": 2}


def test_load_yaml_no_duplicate_keys_rejects_top_level_duplicate():
    with pytest.raises(ValueError, match="duplicate YAML key 'a'"):
        load_yaml_no_duplicate_keys("a: 1\na: 2\n")


def test_load_yaml_no_duplicate_keys_rejects_nested_duplicate():
    text = (
        "strategy: static\n"
        "static_map:\n"
        "  ASSET_A: {edge_id: edge-1, region_id: region-1}\n"
        "  ASSET_A: {edge_id: edge-2, region_id: region-2}\n"
    )
    with pytest.raises(ValueError, match="duplicate YAML key 'ASSET_A'"):
        load_yaml_no_duplicate_keys(text)


def test_load_yaml_no_duplicate_keys_names_both_values():
    with pytest.raises(ValueError) as exc:
        load_yaml_no_duplicate_keys("a: 1\na: 2\n")
    msg = str(exc.value)
    assert "first value=1" in msg
    assert "second value=2" in msg


def test_load_edge_assignment_falls_back_on_duplicate_key(tmp_path, caplog):
    """main.py's _load_edge_assignment catches the loader's ValueError and
    degrades to the permissive unspecified-only fallback instead of
    crashing -- this is the documented, pre-existing behavior difference
    from projector's load_config (fatal there). Assert the fallback is
    installed and the failure is logged, not silently swallowed."""
    bad = tmp_path / "edge-assignment.yaml"
    bad.write_text(
        "strategy: static\n"
        "static_map:\n"
        "  ASSET_A: {edge_id: edge-1, region_id: region-1}\n"
        "  ASSET_A: {edge_id: edge-2, region_id: region-2}\n"
    )
    with caplog.at_level(logging.ERROR, logger="asset_registry_service.main"):
        _load_edge_assignment(str(bad))
    assert any("duplicate YAML key" in rec.message for rec in caplog.records)
    assignment = ea.resolve_for("ANY-ASSET", None, None, "test")
    assert assignment.edge_id == "edge-unspecified"
    assert assignment.region_id == "region-unspecified"
