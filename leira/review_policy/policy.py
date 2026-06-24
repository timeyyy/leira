"""Leira v1.11a review policy: explicit risk flags, not approval."""

from __future__ import annotations

from dataclasses import dataclass

LOW_REVIEWERS: tuple[str, ...] = ()
MEDIUM_REVIEWERS: tuple[str, ...] = ("Aura",)
HIGH_REVIEWERS: tuple[str, ...] = ("Aura", "Aether", "Claude")


@dataclass(frozen=True)
class ReviewPolicyInput:
    slice_id: str
    introduces_new_concept: bool
    changes_invariant: bool
    changes_authority_boundary: bool
    irreversible_or_hard_to_undo: bool
    ordering_uncertain: bool
    mechanical_implementation: bool
    already_agreed_concept: bool


@dataclass(frozen=True)
class ReviewPolicyDecision:
    review_required: bool
    risk_level: str
    recommended_reviewers: list[str]
    reason_codes: list[str]


def decide_review_policy(policy_input: ReviewPolicyInput) -> ReviewPolicyDecision:
    """Classify whether explicit slice metadata requires external feedback.

    This function does not inspect prompt text, call reviewers, approve
    anything, dispatch anything, or resolve disagreement. Tim remains the
    final cut authority outside this policy.
    """

    reason_codes = _reason_codes(policy_input)
    high = (
        policy_input.changes_invariant
        or policy_input.changes_authority_boundary
        or policy_input.irreversible_or_hard_to_undo
    )
    medium = policy_input.introduces_new_concept or policy_input.ordering_uncertain
    review_required = high or medium

    if high:
        risk_level = "high"
        reviewers = list(HIGH_REVIEWERS)
    elif medium:
        risk_level = "medium"
        reviewers = list(MEDIUM_REVIEWERS)
    else:
        risk_level = "low"
        reviewers = list(LOW_REVIEWERS)

    return ReviewPolicyDecision(
        review_required=review_required,
        risk_level=risk_level,
        recommended_reviewers=reviewers,
        reason_codes=reason_codes,
    )


def _reason_codes(policy_input: ReviewPolicyInput) -> list[str]:
    codes: list[str] = []
    if policy_input.mechanical_implementation:
        codes.append("mechanical_slice")
    if policy_input.already_agreed_concept:
        codes.append("already_agreed")
    if policy_input.introduces_new_concept:
        codes.append("new_concept")
    if policy_input.changes_invariant:
        codes.append("invariant_change")
    if policy_input.changes_authority_boundary:
        codes.append("authority_boundary_change")
    if policy_input.irreversible_or_hard_to_undo:
        codes.append("hard_to_undo")
    if policy_input.ordering_uncertain:
        codes.append("ordering_uncertain")
    return codes
