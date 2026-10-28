"""One definition of supported required conditions for preview and search."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.agents.query_match import Assessment


def required_fulfilled(assessment: Assessment, required_count: int) -> bool:
    """A supported approximate match is admitted without calling it confirmed."""
    return (
        not assessment.scope_conflict
        and not assessment.domain_reason
        and len(assessment.matches) >= required_count
        and all(match.supported for match in assessment.matches[:required_count])
    )
