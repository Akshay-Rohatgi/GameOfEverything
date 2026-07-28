"""Tests for retry escalation logic — detecting stuck loops and escalating."""

import pytest

from goe.retry.diagnostician import Diagnosis, DiagnosisCategory


def test_escalation_logic_same_category_twice():
    """Test that diagnosis_history tracks categories and escalation works."""
    # Simulate the retry loop logic
    diagnosis_history = []

    # First diagnosis: implementation_bug
    diagnosis = Diagnosis(
        category=DiagnosisCategory.implementation_bug,
        description="First failure",
        evidence="",
    )

    # Check escalation logic (should NOT escalate on first occurrence)
    should_escalate = (
        len(diagnosis_history) >= 1
        and diagnosis_history[-1] == diagnosis.category
        and diagnosis.category != DiagnosisCategory.design_flaw
    )
    assert not should_escalate
    diagnosis_history.append(diagnosis.category)

    # Second diagnosis: implementation_bug again (stuck loop)
    diagnosis2 = Diagnosis(
        category=DiagnosisCategory.implementation_bug,
        description="Second failure, same category",
        evidence="",
    )

    # Check escalation logic (SHOULD escalate now)
    should_escalate = (
        len(diagnosis_history) >= 1
        and diagnosis_history[-1] == diagnosis2.category
        and diagnosis2.category != DiagnosisCategory.design_flaw
    )
    assert should_escalate

    # Simulate escalation
    if should_escalate:
        diagnosis2 = Diagnosis(
            category=DiagnosisCategory.design_flaw,
            description=f"Escalated from {diagnosis_history[-1].value} after 2 consecutive failures",
            evidence=diagnosis2.evidence,
        )

    assert diagnosis2.category == DiagnosisCategory.design_flaw
    diagnosis_history.append(diagnosis2.category)

    # Verify history
    assert diagnosis_history == [
        DiagnosisCategory.implementation_bug,
        DiagnosisCategory.design_flaw,
    ]


def test_no_escalation_when_categories_differ():
    """Test that escalation does NOT happen if categories alternate."""
    diagnosis_history = []

    # First: procedure_bug
    diagnosis1 = Diagnosis(
        category=DiagnosisCategory.procedure_bug,
        description="Wrong payload",
        evidence="",
    )
    should_escalate = (
        len(diagnosis_history) >= 1
        and diagnosis_history[-1] == diagnosis1.category
        and diagnosis1.category != DiagnosisCategory.design_flaw
    )
    assert not should_escalate
    diagnosis_history.append(diagnosis1.category)

    # Second: implementation_bug (different category)
    diagnosis2 = Diagnosis(
        category=DiagnosisCategory.implementation_bug,
        description="Wrong query",
        evidence="",
    )
    should_escalate = (
        len(diagnosis_history) >= 1
        and diagnosis_history[-1] == diagnosis2.category
        and diagnosis2.category != DiagnosisCategory.design_flaw
    )
    assert not should_escalate  # Should NOT escalate because categories differ
    diagnosis_history.append(diagnosis2.category)

    # Third: procedure_bug again (but not consecutive with first)
    diagnosis3 = Diagnosis(
        category=DiagnosisCategory.procedure_bug,
        description="Another payload issue",
        evidence="",
    )
    should_escalate = (
        len(diagnosis_history) >= 1
        and diagnosis_history[-1] == diagnosis3.category
        and diagnosis3.category != DiagnosisCategory.design_flaw
    )
    assert not should_escalate  # Last was implementation_bug, not procedure_bug
    diagnosis_history.append(diagnosis3.category)

    # Verify no escalation happened
    assert diagnosis_history == [
        DiagnosisCategory.procedure_bug,
        DiagnosisCategory.implementation_bug,
        DiagnosisCategory.procedure_bug,
    ]
    assert DiagnosisCategory.design_flaw not in diagnosis_history


def test_no_escalation_from_design_flaw():
    """Test that design_flaw does not trigger escalation (already top level)."""
    diagnosis_history = [DiagnosisCategory.design_flaw]

    # Another design_flaw (should NOT trigger escalation logic)
    diagnosis = Diagnosis(
        category=DiagnosisCategory.design_flaw,
        description="Still broken",
        evidence="",
    )

    should_escalate = (
        len(diagnosis_history) >= 1
        and diagnosis_history[-1] == diagnosis.category
        and diagnosis.category != DiagnosisCategory.design_flaw
    )
    assert not should_escalate  # design_flaw excluded from escalation
    diagnosis_history.append(diagnosis.category)

    assert diagnosis_history == [
        DiagnosisCategory.design_flaw,
        DiagnosisCategory.design_flaw,
    ]


def test_procedure_bug_escalation():
    """Test that 2 consecutive procedure_bug diagnoses escalate to design_flaw."""
    diagnosis_history = []

    # First procedure_bug
    diagnosis1 = Diagnosis(
        category=DiagnosisCategory.procedure_bug,
        description="Wrong assertion",
        evidence="",
    )
    diagnosis_history.append(diagnosis1.category)

    # Second procedure_bug (stuck loop)
    diagnosis2 = Diagnosis(
        category=DiagnosisCategory.procedure_bug,
        description="Still wrong assertion",
        evidence="",
    )

    should_escalate = (
        len(diagnosis_history) >= 1
        and diagnosis_history[-1] == diagnosis2.category
        and diagnosis2.category != DiagnosisCategory.design_flaw
    )
    assert should_escalate

    # Simulate escalation
    if should_escalate:
        diagnosis2 = Diagnosis(
            category=DiagnosisCategory.design_flaw,
            description=f"Escalated from {diagnosis_history[-1].value}",
            evidence="",
        )

    assert diagnosis2.category == DiagnosisCategory.design_flaw
    diagnosis_history.append(diagnosis2.category)

    assert diagnosis_history == [
        DiagnosisCategory.procedure_bug,
        DiagnosisCategory.design_flaw,
    ]
