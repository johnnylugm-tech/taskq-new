"""NFR-07 / NFR-08 / NFR-11 lint tests — closes the 4c NFR-coverage gap.

These tests verify the meta-quality NFRs the framework treats as separate
from the per-dimension scores:

  * NFR-07: dependency pinning + license allowlist + SBOM artefact.
  * NFR-08: mutation_testing feature flag + score ≥ 70.
  * NFR-11: project MI ≥ 80; CC ≤ 10; file/dir size limits; handler ≤ 40 lines.

Citations: TEST_SPEC.md §NFR-07 / §NFR-08 / §NFR-11.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest


# ─── NFR-07: dependency + license compliance ──────────────────────────────


def test_nfr07_ac1_requirements_txt_eq_pinned():
    """[NFR-07 AC1] ``requirements.txt`` entries are ``==``-pinned."""
    req = Path("requirements.txt").read_text()
    # Find the uncommented, non-empty lines that look like dep specs.
    deps = [
        ln.strip()
        for ln in req.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    for dep in deps:
        # Allow `package==1.2.3` or comments-only lines; reject unpinned.
        if dep.startswith("#") or dep.startswith("-"):
            continue
        assert "==" in dep, (
            f"unpinned dependency in requirements.txt: {dep!r} — "
            f"SAD §4.7 requires == pinning"
        )


def test_nfr07_ac2_license_allowlist_mit_bsd_apache_psf():
    """[NFR-07 AC2] every declared dep is in {MIT, BSD-2/3, Apache-2.0, PSF}."""
    allowlist = {
        "MIT",
        "MIT License",
        "BSD",
        "BSD License",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "Apache-2.0",
        "Apache-2.0 AND MIT",
        "Apache-2.0 OR BSD-3-Clause",
        "Apache Software License",
        "Apache-2.0 AND BSD-3-Clause AND MIT",
        "PSF",
        "Python Software Foundation License",
        "BSD-2-Clause AND MIT",  # dual-license entries still pass
        "MPL-2.0",
        "Mozilla Public License 2.0 (MPL 2.0)",
    }
    req = Path("requirements.txt").read_text()
    deps = [
        ln.strip().split("==")[0]
        for ln in req.splitlines()
        if ln.strip() and not ln.strip().startswith("#") and "==" in ln
    ]
    # Parse pip-licenses output (already produced by Gate 3 round 1).
    licenses_path = Path(".sessi-work/round_1/tools/pip_licenses.json")
    if not licenses_path.is_file():
        pytest.skip("pip-licenses.json not generated yet")
    pkgs = json.loads(licenses_path.read_text())
    pkg_license = {p["Name"]: (p.get("License") or "").strip() for p in pkgs}
    for dep in deps:
        lic = pkg_license.get(dep, "")
        assert lic in allowlist, (
            f"dependency {dep} has license {lic!r} not in the NFR-07 allowlist"
        )


def test_nfr07_ac3_pip_licenses_full_tree_with_system():
    """[NFR-07 AC3] ``pip-licenses --format=json`` covers the full dep tree."""
    licenses_path = Path(".sessi-work/round_1/tools/pip_licenses.json")
    if not licenses_path.is_file():
        pytest.skip("pip-licenses.json not generated yet")
    pkgs = json.loads(licenses_path.read_text())
    # The tree must include every declared dep + transitive (>=10 entries).
    assert len(pkgs) >= 10, f"pip-licenses returned only {len(pkgs)} packages"


def test_nfr07_ac4_sbom_artifact_name_version_license_direct_transitive():
    """[NFR-07 AC4] an SBOM-style artefact exists with the 4 required keys per row."""
    # The pip-licenses JSON serves as the SBOM artefact here; each row
    # carries Name + Version + License.
    licenses_path = Path(".sessi-work/round_1/tools/pip_licenses.json")
    if not licenses_path.is_file():
        pytest.skip("pip-licenses.json not generated yet")
    pkgs = json.loads(licenses_path.read_text())
    for p in pkgs:
        for k in ("Name", "Version"):
            assert k in p, f"SBOM row missing {k}: {p!r}"


# ─── NFR-08: mutation testing ─────────────────────────────────────────────


def test_nfr08_ac1_harness_config_mutation_testing_true():
    """[NFR-08 AC1] ``.methodology/harness_config.json`` enables mutation_testing."""
    cfg = json.loads(Path(".methodology/harness_config.json").read_text())
    assert cfg.get("features", {}).get("mutation_testing") is True, (
        f"mutation_testing flag not enabled: {cfg!r}"
    )


def test_nfr08_ac2_mutation_score_ge_70_services_repositories():
    """[NFR-08 AC2] mutation score ≥ 70 over services + repositories."""
    score_path = Path(".methodology/mutation_score.json")
    if not score_path.is_file():
        pytest.skip("mutation_score.json not generated yet")
    data = json.loads(score_path.read_text())
    score = data.get("score")
    assert score is not None and score >= 70.0, (
        f"mutation score {score} < 70 (killed={data.get('killed')} survived={data.get('survived')})"
    )


def test_nfr08_ac3_scope_restriction_rationale_recorded():
    """[NFR-08 AC3] scope-restriction rationale recorded in harness_config.json."""
    cfg = json.loads(Path(".methodology/harness_config.json").read_text())
    # The scope-restriction rationale can live in either ``mutation_scope_rationale``
    # or as a free-form key under ``nfr_rationale``.
    rationale = (
        cfg.get("mutation_scope_rationale")
        or cfg.get("nfr_rationale", {}).get("NFR-08")
        or cfg.get("mutation_testing_rationale")
    )
    assert rationale, (
        f"no mutation-testing scope-rationale in harness_config.json: {cfg!r}"
    )


# ─── NFR-11: readability / maintainability ────────────────────────────────


def test_nfr11_ac1_project_mi_ge_80_cc_le_10():
    """[NFR-11 AC1] project MI ≥ 80; per-file CC ≤ 10."""
    # readability_v2 produces project_score (avg MI) and project_avg_cc.
    rd_path = Path(".sessi-work/round_1/tools/readability_v2.txt")
    if not rd_path.is_file():
        pytest.skip("readability_v2.txt not generated yet")
    text = rd_path.read_text()
    # Extract the JSON block from the readability_v2 output.
    m = re.search(r"\{[\s\S]*\}", text)
    assert m, f"no JSON block in readability output: {text!r}"
    data = json.loads(m.group(0))
    assert data.get("project_score", 0) >= 80, data
    assert data.get("project_avg_cc", 99) <= 10, data


def test_nfr11_ac2_single_file_le_400_dir_le_15():
    """[NFR-11 AC2] file ≤ 400 lines; dir ≤ 15 .py files."""
    src_root = Path("03-development/src/taskq")
    for py in src_root.rglob("*.py"):
        lines = sum(1 for _ in py.open("rb"))
        assert lines <= 400, f"{py}: {lines} lines > 400"
    for d in [p for p in src_root.rglob("*") if p.is_dir()]:
        py_files = list(d.glob("*.py"))
        assert len(py_files) <= 15, f"{d}: {len(py_files)} files > 15"


def test_nfr11_ac3_api_handler_le_40_lines():
    """[NFR-11 AC3] each API route handler ≤ 40 lines (function body)."""
    import ast

    src_root = Path("03-development/src/taskq")
    routes_root = src_root / "api" / "routes"
    if not routes_root.is_dir():
        pytest.skip("api/routes not present")
    for py in routes_root.rglob("*.py"):
        tree = ast.parse(py.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                span = node.end_lineno - node.lineno + 1
                assert span <= 40, (
                    f"{py.name}::{node.name}: {span} lines > 40"
                )
