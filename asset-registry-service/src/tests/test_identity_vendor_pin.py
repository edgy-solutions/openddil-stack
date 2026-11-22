"""The vendored identity library equals the contracts commit its PIN names."""
from __future__ import annotations

import hashlib
from pathlib import Path

VENDOR = (Path(__file__).resolve().parents[1] / "asset_registry_service" / "vendor" / "openddil_identity")


def test_vendored_identity_matches_its_pin():
    lines = (VENDOR / "PIN").read_text().splitlines()
    assert "openddil-contracts" in lines[0] and "3dd7b16" in lines[0]
    listed = []
    for line in lines[1:]:
        name, sha = line.split()
        listed.append(name)
        assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == sha, name
    # every vendored module is pinned, and nothing else is there
    on_disk = sorted(p.name for p in VENDOR.glob("*.py"))
    assert sorted(listed) == on_disk
    assert "ids.py" in listed and "declaration.py" in listed


def test_vendored_library_imports_and_names_its_generator():
    from asset_registry_service.vendor import openddil_identity as lib

    assert lib.GENERATOR == "openddil_identity.ids.mint_identity"
    assert lib.GENERATOR_VERSION
