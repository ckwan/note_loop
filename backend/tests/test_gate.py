import json

from evals import gate


def run_gate(*args):
    return gate.main(list(args))


def test_the_gate_passes_on_the_current_code(capsys):
    assert run_gate() == 0
    assert "11 of 11 checks held" in capsys.readouterr().out


def test_the_gate_fails_when_the_rules_break(monkeypatch, capsys):
    # Pretend a change stopped the rules from recognizing any diagnosis or drug.
    monkeypatch.setattr("app.guardrails.DIAGNOSIS_TERMS", ())
    monkeypatch.setattr("app.guardrails.MEDICATION_TERMS", ())
    assert run_gate() == 1
    out = capsys.readouterr().out
    assert "FAIL  review.catch_combined" in out


def test_a_threshold_that_cannot_be_met_fails_the_gate(tmp_path, capsys):
    path = tmp_path / "t.json"
    path.write_text(json.dumps({"min": {"fix.resolves": 0.99}}))
    assert run_gate("--thresholds", str(path)) == 1
    assert "FAIL  fix.resolves" in capsys.readouterr().out


def test_the_gate_can_check_a_saved_run(tmp_path, capsys):
    summary = {
        "draft_checks": {"grounded": [10, 12]},
        "fix_checks": {},
        "catch_general": [0, 0], "catch_specialists": [0, 0], "catch_combined": [0, 0],
        "false_alarms_general": [0, 0], "false_alarms_specialists": [0, 0], "false_alarms_combined": [0, 0],
    }
    results = tmp_path / "run.json"
    results.write_text(json.dumps({"summary": summary}))
    limits = tmp_path / "t.json"
    limits.write_text(json.dumps({"min": {"draft.grounded": 0.8}}))
    assert run_gate("--results", str(results), "--thresholds", str(limits)) == 0
    limits.write_text(json.dumps({"min": {"draft.grounded": 0.8, "review.catch_combined": 0.5}}))
    # Nothing was scored for review catches, so that threshold cannot be met.
    assert run_gate("--results", str(results), "--thresholds", str(limits)) == 1
    assert "n/a" in capsys.readouterr().out


def test_unknown_metric_names_are_an_error(tmp_path):
    import pytest

    path = tmp_path / "t.json"
    path.write_text(json.dumps({"min": {"draft.typo": 1.0}}))
    with pytest.raises(SystemExit):
        run_gate("--thresholds", str(path))
