"""The numbers invariant.

A hallucinated figure must not be able to reach the PDF. The only path is
metrics -> results/<job>.json -> tables/<key>.tex -> \\input{}, and each arrow
is checked here.
"""

from __future__ import annotations

import json

import pytest

from galley.services import results


@pytest.fixture
def recorded(paper_repo):
    job = {
        "id": "j1",
        "code_sha": "deadbeef",
        "scheduler_id": "77",
        "note": "baseline",
        "state": "COMPLETED",
        "exit_code": 0,
    }
    results.record_result(paper_repo, job, {"eval": {"ce": 1.23456}, "train": {"steps": 1345}})
    job2 = {**job, "id": "j2", "note": "ablation", "code_sha": "cafe"}
    results.record_result(paper_repo, job2, {"eval": {"ce": 1.5}, "train": {"steps": 336}})
    return paper_repo


def test_a_result_carries_the_sha_that_produced_it(recorded) -> None:
    saved = json.loads((recorded / "results" / "j1.json").read_text())
    assert saved["code_sha"] == "deadbeef"
    assert saved["metrics"]["eval"]["ce"] == 1.23456


def test_the_table_is_built_from_the_results(recorded) -> None:
    path = results.write_result_table(recorded, "ablation", ["j1", "j2"])
    tex = path.read_text()
    assert "1.2346" in tex, "the format string in the spec is applied"
    assert "1345" in tex and "336" in tex
    assert "baseline" in tex and "ablation" in tex
    assert "\\label{tab:ablation}" in tex


def test_the_generated_table_says_it_is_generated(recorded) -> None:
    tex = results.write_result_table(recorded, "ablation", ["j1"]).read_text()
    assert "Do not edit by hand" in tex
    assert "jobs: j1" in tex


def test_a_table_cannot_be_built_from_a_job_with_no_results(recorded) -> None:
    with pytest.raises(results.ResultsError) as exc:
        results.write_result_table(recorded, "ablation", ["never-ran"])
    assert "no results/never-ran.json" in str(exc.value)


def test_a_table_needs_at_least_one_job(recorded) -> None:
    with pytest.raises(results.ResultsError):
        results.write_result_table(recorded, "ablation", [])


def test_a_missing_metric_is_a_dash_not_a_guess(paper_repo) -> None:
    results.record_result(paper_repo, {"id": "j3", "note": "partial"}, {"eval": {}})
    tex = results.write_result_table(paper_repo, "ablation", ["j3"]).read_text()
    assert "---" in tex
    assert "0.0000" not in tex, "a missing number must never render as a value"


def test_a_result_whose_job_galley_never_ran_is_flagged(paper_repo) -> None:
    (paper_repo / "results").mkdir(exist_ok=True)
    (paper_repo / "results" / "made_up.json").write_text('{"job_id": "phantom"}')
    results.record_result(paper_repo, {"id": "real"}, {})
    assert results.unbacked_results(paper_repo, {"real"}) == ["made_up.json"]
