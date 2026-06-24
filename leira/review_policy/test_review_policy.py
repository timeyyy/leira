from dataclasses import asdict
from pathlib import Path

from leira.review_policy.policy import ReviewPolicyInput, decide_review_policy


def _mechanical_input(**overrides):
    values = {
        "slice_id": "slice",
        "introduces_new_concept": False,
        "changes_invariant": False,
        "changes_authority_boundary": False,
        "irreversible_or_hard_to_undo": False,
        "ordering_uncertain": False,
        "mechanical_implementation": True,
        "already_agreed_concept": True,
    }
    values.update(overrides)
    return ReviewPolicyInput(**values)


def test_low_risk_mechanical_slice_does_not_require_review():
    decision = decide_review_policy(_mechanical_input())
    assert not decision.review_required
    assert decision.risk_level == "low"
    assert decision.recommended_reviewers == []
    assert decision.reason_codes == ["mechanical_slice", "already_agreed"]


def test_new_concept_requires_review():
    decision = decide_review_policy(_mechanical_input(introduces_new_concept=True))
    assert decision.review_required
    assert decision.risk_level == "medium"
    assert "new_concept" in decision.reason_codes


def test_invariant_change_requires_review():
    decision = decide_review_policy(_mechanical_input(changes_invariant=True))
    assert decision.review_required
    assert decision.risk_level == "high"
    assert "invariant_change" in decision.reason_codes


def test_authority_boundary_change_requires_review():
    decision = decide_review_policy(_mechanical_input(changes_authority_boundary=True))
    assert decision.review_required
    assert decision.risk_level == "high"
    assert "authority_boundary_change" in decision.reason_codes


def test_hard_to_undo_change_requires_review():
    decision = decide_review_policy(_mechanical_input(irreversible_or_hard_to_undo=True))
    assert decision.review_required
    assert decision.risk_level == "high"
    assert "hard_to_undo" in decision.reason_codes


def test_ordering_uncertainty_requires_review():
    decision = decide_review_policy(_mechanical_input(ordering_uncertain=True))
    assert decision.review_required
    assert decision.risk_level == "medium"
    assert "ordering_uncertain" in decision.reason_codes


def test_high_risk_slices_recommend_aura_aether_and_claude():
    decision = decide_review_policy(_mechanical_input(changes_invariant=True))
    assert decision.recommended_reviewers == ["Aura", "Aether", "Claude"]


def test_medium_risk_slices_recommend_aura():
    decision = decide_review_policy(_mechanical_input(introduces_new_concept=True))
    assert decision.recommended_reviewers == ["Aura"]


def test_decisions_are_deterministic():
    policy_input = _mechanical_input(
        introduces_new_concept=True,
        ordering_uncertain=True,
    )
    first = decide_review_policy(policy_input)
    second = decide_review_policy(policy_input)
    assert asdict(first) == asdict(second)


def test_no_reviewer_calls_are_made():
    decision = decide_review_policy(_mechanical_input(changes_invariant=True))
    assert decision.recommended_reviewers == ["Aura", "Aether", "Claude"]
    assert all(isinstance(name, str) for name in decision.recommended_reviewers)


def test_no_approval_dispatch_proposal_or_mind_code_is_added():
    root = Path(__file__).resolve().parents[2]
    assert not (root / "leira/review_policy/approval.py").exists()
    assert not (root / "leira/review_policy/dispatch.py").exists()
    assert not (root / "leira/review_policy/proposal.py").exists()
    assert not (root / "leira/review_policy/mind.py").exists()
