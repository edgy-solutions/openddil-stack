"""The code must produce exactly the topics produces.yaml lists.

AST scan of the shipped code, src/asset_registry_service (tests excluded): the code the image runs.

Producer call sites: `.send(`, `.send_and_wait(`, `.produce(`. The one transport that receives the topic as a
parameter, `_HqProducerService.send` in registry_app.py, is the sink the caller feeds; it is skipped by name, so its
caller carries the topic. That caller (`hq_producer.send(output_topic, ...)` in make_edge_app) reads a function
parameter, so it carries `# produces: <manifest entry>`; the second half of this file holds that annotation to the
real config loader (Config.from_env) and to main.py's wiring.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PKG = ROOT / "asset-registry-service" / "src"
sys.path.insert(0, str(ROOT))
import produces_manifest as pm  # noqa: E402

MANIFEST = pm.load(ROOT / "produces.yaml")
METHODS = {"send", "send_and_wait", "produce"}
TRANSPORTS = {("asset_registry_service/registry_app.py", "send")}  # (file, function) taking topic as a parameter
ANNOTATION = re.compile(r"#\s*produces:\s*(.+?)\s*$")
ENV = {}  # fixture env: every code default applies


def _parents(tree):
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child.parent = node


def _fn(node):
    while node is not None and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        node = getattr(node, "parent", None)
    return node


def _env_template(call):
    """os.environ.get("X", "d") / os.getenv("X", "d") -> '${X:-d}', else None."""
    f = call.func
    ok = isinstance(f, ast.Attribute) and (
        (f.attr == "getenv" and isinstance(f.value, ast.Name) and f.value.id == "os")
        or (f.attr == "get" and isinstance(f.value, ast.Attribute) and f.value.attr == "environ"))
    if not ok or not call.args or not isinstance(call.args[0], ast.Constant):
        return None
    var = call.args[0].value
    if len(call.args) > 1 and isinstance(call.args[1], ast.Constant) and isinstance(call.args[1].value, str):
        return f"${{{var}:-{call.args[1].value}}}"
    return f"${{{var}}}"


def _scan():
    templates, bad, sites = set(), [], {}
    for path in sorted((PKG / "asset_registry_service").rglob("*.py")):
        rel = path.relative_to(PKG).as_posix()
        src = path.read_text(encoding="utf-8")
        lines = src.splitlines()
        tree = ast.parse(src)
        _parents(tree)
        consts = {}
        for n in tree.body:
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                if isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
                    consts[n.targets[0].id] = n.value.value
                elif isinstance(n.value, ast.Call) and _env_template(n.value):
                    consts[n.targets[0].id] = _env_template(n.value)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if not (isinstance(f, ast.Attribute) and f.attr in METHODS):
                continue
            fn = _fn(node)
            if fn is not None and (rel, fn.name) in TRANSPORTS:
                continue
            sites[rel] = sites.get(rel, 0) + 1
            span = lines[max(node.lineno - 2, 0):node.end_lineno]
            note = next((m.group(1).strip("\"'") for m in map(ANNOTATION.search, span) if m), None)
            if note:
                templates.add(note)
                continue
            kw = {k.arg: k.value for k in node.keywords}
            arg = kw.get("topic") or (node.args[0] if node.args else None)
            got = None
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                got = arg.value
            elif isinstance(arg, ast.Name):
                got = consts.get(arg.id)
            elif isinstance(arg, ast.Call):
                got = _env_template(arg)
            if got:
                templates.add(got)
            else:
                bad.append(f"{rel}:{node.lineno}")
    return templates, bad, sites


def test_code_produces_exactly_the_manifest():
    templates, bad, sites = _scan()
    assert sites, "no producer call sites found: the scan is not looking at the code"
    assert not bad, ("unresolvable producer call sites (annotate with '# produces: <entry>'): "
                     + ", ".join(bad))
    got = {pm.expand(t, ENV) for t in templates}
    declared = {pm.expand(t, ENV) for w in MANIFEST.values() for t in w["topics"] or []}
    msg = [f"manifest lists a topic the code does not produce: {t}" for t in sorted(declared - got)]
    msg += [f"code produces a topic the manifest does not list: {t}" for t in sorted(got - declared)]
    assert not msg, "\n".join(msg)


def test_manifest_is_exactly_the_asset_registry_image():
    assert set(MANIFEST) == {"asset-registry"}
    assert {w["image"] for w in MANIFEST.values()} == {"openddil/asset-registry-service"}


# ---- the annotated site against the real config loader -----------------------------------------------
# The send site reads the `output_topic` parameter of make_edge_app. main.py must pass cfg.output_topic, and
# Config.from_env (the image's path) must hold what the annotation says under a cleared env.
def test_annotation_equals_the_loaded_config_and_main_wiring(monkeypatch):
    import os
    for k in [k for k in os.environ if k.startswith(("ASSET_REGISTRY_", "PG"))]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("PGHOST", "fixture")  # store_dsn_from_env exits without a store; this is not a topic
    sys.path.insert(0, str(PKG))
    from asset_registry_service.config import Config
    held = Config.from_env().output_topic

    templates, _, _ = _scan()
    assert {pm.expand(t, ENV) for t in templates} == {held}, f"annotation vs Config.output_topic {held!r}"

    tree = ast.parse((PKG / "asset_registry_service" / "main.py").read_text(encoding="utf-8"))
    wired = [ast.unparse(k.value) for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", getattr(n.func, "attr", None)) == "make_edge_app"
             for k in n.keywords if k.arg == "output_topic"]
    assert wired == ["cfg.output_topic"], f"main.py no longer feeds make_edge_app from cfg.output_topic: {wired}"
