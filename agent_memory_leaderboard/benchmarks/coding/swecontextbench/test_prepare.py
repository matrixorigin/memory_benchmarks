"""Ensure targets and solution material cannot enter endpoint memory."""

import pytest

from benchmarks.coding.swecontextbench.prepare import build


def task(identifier, repo="owner/repo"):
    return {"instance_id": identifier, "repo": repo, "problem_statement": "issue " + identifier,
            "created_at": "2020-01-01", "patch": "SECRET_PATCH", "test_patch": "SECRET_TEST"}


def test_cross_repo_links_and_no_solution_leakage():
    result = build([task("past", "old/repo"), task("target")], [task("target")],
                   [{"related_instance_id": "target", "experience_instance_id": "past"}])
    assert len(result) == 1
    history = result[0]["conversation"]
    assert history["session_1"][0]["dia_id"] == "past"
    assert "SECRET" not in str(result)
    assert result[0]["qa"][0]["evidence"] == ["past"]
    assert len([key for key in history if not key.endswith("_date_time")]) == 1


def test_missing_link_is_error():
    with pytest.raises(ValueError, match="Missing linked experience"):
        build([task("past")], [task("target")], [])
