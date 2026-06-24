import pytest

from leira.dispatcher.envelope import (
    ContractResult,
    load_and_validate,
    load_operation,
    validate_operation,
)

VALID_ENVELOPE = {
    "operation": {
        "id": "hello",
        "objective": "Build v0.1 operation envelope",
        "success_criteria": ["Tests pass"],
    },
    "metadata": {
        "assumptions": ["Python is installed"],
        "claims": ["The ledger exists"],
        "failure_distinguishability": {
            "stale_state": {"notes": ["Worker may hold stale state"]}
        },
    },
}


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_valid_envelope_passes():
    result = validate_operation(VALID_ENVELOPE)
    assert isinstance(result, ContractResult)
    assert result.success
    assert result.operation_id == "hello"


def test_missing_id_fails():
    doc = {
        "operation": {
            "objective": "x",
            "success_criteria": ["a"],
        }
    }
    result = validate_operation(doc)
    assert not result.success
    assert result.error_type == "MISSING_ID"


def test_missing_objective_fails():
    doc = {
        "operation": {
            "id": "hello",
            "success_criteria": ["a"],
        }
    }
    result = validate_operation(doc)
    assert not result.success
    assert result.error_type == "MISSING_OBJECTIVE"


def test_missing_success_criteria_fails():
    doc = {
        "operation": {
            "id": "hello",
            "objective": "x",
        }
    }
    result = validate_operation(doc)
    assert not result.success
    assert result.error_type == "MISSING_SUCCESS_CRITERIA"


def test_success_criteria_must_be_a_list():
    doc = {
        "operation": {
            "id": "hello",
            "objective": "x",
            "success_criteria": "not a list",
        }
    }
    result = validate_operation(doc)
    assert not result.success
    assert result.error_type == "INVALID_SUCCESS_CRITERIA"


def test_success_criteria_must_contain_only_strings():
    doc = {
        "operation": {
            "id": "hello",
            "objective": "x",
            "success_criteria": ["ok", 123],
        }
    }
    result = validate_operation(doc)
    assert not result.success
    assert result.error_type == "INVALID_SUCCESS_CRITERIA"


def test_malformed_yaml_fails(tmp_path):
    path = _write(tmp_path, "bad.yaml", "operation:\n  id: hello\n  objective: [unclosed\n")
    result = load_operation(path)
    assert not result.success
    assert result.error_type == "MALFORMED_YAML"

    full = load_and_validate(path)
    assert not full.success
    assert full.error_type == "MALFORMED_YAML"


def test_metadata_may_be_absent():
    doc = {
        "operation": {
            "id": "hello",
            "objective": "x",
            "success_criteria": ["a"],
        }
    }
    result = validate_operation(doc)
    assert result.success


def test_metadata_contents_are_not_interpreted():
    doc = {
        "operation": {
            "id": "hello",
            "objective": "x",
            "success_criteria": ["a"],
        },
        "metadata": {
            "claims": ["the moon is made of cheese"],
            "assumptions": [123, None, {"nonsense": True}],
            "anything_at_all": object,
        },
    }
    # The kernel must not choke on nonsensical/odd metadata content —
    # it is opaque payload, never semantically evaluated.
    result = validate_operation(doc)
    assert result.success
    assert result.operation["metadata"] == doc["metadata"]


def test_contract_result_is_returned_instead_of_exceptions(tmp_path):
    # Missing file
    missing = load_operation(tmp_path / "does_not_exist.yaml")
    assert isinstance(missing, ContractResult)
    assert not missing.success
    assert missing.error_type == "FILE_NOT_FOUND"

    # Malformed YAML
    bad_path = _write(tmp_path, "bad.yaml", "{not: valid: yaml: [")
    malformed = load_operation(bad_path)
    assert isinstance(malformed, ContractResult)
    assert not malformed.success

    # Non-mapping document
    list_path = _write(tmp_path, "list.yaml", "- a\n- b\n")
    not_mapping = load_operation(list_path)
    assert isinstance(not_mapping, ContractResult)
    assert not not_mapping.success
    assert not_mapping.error_type == "MALFORMED_YAML"

    # Structural failure on a validly-parsed-but-incomplete document
    incomplete = validate_operation({"operation": {"id": "x"}})
    assert isinstance(incomplete, ContractResult)
    assert not incomplete.success

    # Valid round trip through load_and_validate
    valid_path = _write(
        tmp_path,
        "good.yaml",
        "operation:\n  id: hello\n  objective: x\n  success_criteria:\n    - a\n",
    )
    ok = load_and_validate(valid_path)
    assert isinstance(ok, ContractResult)
    assert ok.success
    assert ok.operation_id == "hello"


def test_load_and_validate_on_example_op_yaml():
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    result = load_and_validate(repo_root / "op.yaml")
    assert result.success
    assert result.operation_id == "hello"
