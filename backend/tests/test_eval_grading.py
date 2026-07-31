"""The eval harness grades the pipeline, and nothing grades the harness.

These are the tests that would have caught the bug they were written for: three
correct, fully-cited answers scored as refusals because a decline phrase
appeared somewhere after the substance. A grader that fails good answers moves
the headline number in the direction that blames the wrong stage, and it does it
silently - the run looks like a model regression.

The fixtures are real generation output from the 2026-07-31 Anthropic run, not
invented prose, because the failure was specifically about how a strong model
actually writes: it answers what the passages support and then names what they
do not.
"""

from __future__ import annotations

import pytest

from app.graph.state import Grade
from evals.questions import Expect, Question
from evals.run import grade_answer, is_refusal, looks_like_decline

PASS = str(Grade.PASS)
ABSTAIN = str(Grade.ABSTAIN)

# --- real answers from the 2026-07-31 run -----------------------------------

# Answered correctly, then scoped a reference to an external guide. Scored as a
# refusal on "not include".
C3_ANSWER = (
    "Based on langchain.md, the `sdk` property used for SDK reference generation "
    "supports the artifact `format` values typedoc, docfx, javadoc, sphinx and "
    "phpdoc [1]. For further details on generating artifacts and configuring "
    "sources, the document points to a separate \"SDK reference setup\" guide, "
    "though the contents of that guide are not included here [1]."
)

# The decline phrase *is the finding* - the question asked what each paper
# disclosed, and "no mention of figure generation" is the answer.
D3_ANSWER = (
    "**MathModDB paper** disclosed use of two generative models for language "
    "polishing, stylistic feedback and figure creation [1], whereas the LegalKG "
    "paper's tools were also used for paraphrasing and formatting, but with no "
    "mention of figure generation or structural feedback [2]."
)

# Answered, then added a completeness caveat about a different person.
D5_ANSWER = (
    "**Mr. Mayur Patel** appears most frequently, serving as Fund Manager across "
    "four separate 360 ONE schemes [1][4][3][6]. Mr. Milan Mody appears on two [3]. "
    "Note: The documents do not provide further biographical or tenure detail for "
    "Mr. Milan Mody beyond his name being listed on the respective fund pages, so "
    "a fuller capacity description is not available in the provided material."
)

# The abstain node's own text. No citations, refuses in the first breath.
ABSTAIN_TEXT = (
    "I could not find anything in your documents that answers this. I searched 4 "
    "document(s) and the closest matches scored below the relevance threshold, so "
    "I would be guessing."
)

# A model writing its own refusal, in its own words, with the marker in the
# second claim rather than the first.
MODEL_REFUSAL = (
    "I searched the documents you provided. They do not report an F1 score for "
    "the ontology comparison."
)


def _q(expect: Expect, *, must_include: tuple[str, ...] = ()) -> Question:
    return Question(
        id="T1",
        section="test",
        text="test question",
        expect=expect,
        docs=("doc",),
        must_include=must_include,
    )


class TestIsRefusal:
    """`is_refusal` gates answerable questions - it must be precise."""

    @pytest.mark.parametrize(
        ("label", "answer"),
        [("C3", C3_ANSWER), ("D3", D3_ANSWER), ("D5", D5_ANSWER)],
    )
    def test_a_cited_answer_with_a_trailing_caveat_is_not_a_refusal(
        self, label: str, answer: str
    ) -> None:
        assert is_refusal(answer, PASS) is False, label

    def test_the_abstain_text_is_a_refusal(self) -> None:
        assert is_refusal(ABSTAIN_TEXT, PASS) is True

    def test_an_abstain_grade_is_a_refusal_whatever_the_text_says(self) -> None:
        # The gate's decision is not overridable by prose.
        assert is_refusal(C3_ANSWER, ABSTAIN) is True

    def test_a_refusal_declaring_itself_in_the_second_claim_is_caught(self) -> None:
        assert is_refusal(MODEL_REFUSAL, PASS) is True

    def test_an_uncited_answer_with_a_late_caveat_is_not_a_refusal(self) -> None:
        # No citations, so the "a grounded answer cites" signal cannot fire and
        # the positional one has to carry it alone.
        answer = (
            "The Net AUM of the fund is 6,634.45 crore. The scheme is managed by "
            "Mr. Mayur Patel. The expense ratio is not stated in these pages."
        )
        assert is_refusal(answer, PASS) is False

    def test_an_empty_answer_is_a_refusal_only_by_grade(self) -> None:
        assert is_refusal("", ABSTAIN) is True
        assert is_refusal("", PASS) is False


# E2, from the same run, declining correctly - and scored as a hallucination,
# because the bold markers fall inside the phrase and split "not available in"
# into "not available** in".
E2_ANSWER = (
    "Based on the available data, the **June 2026 two-wheeler sales (%YoY)** "
    "figure is **not available** in the documents provided. [1]"
)


class TestLooksLikeDecline:
    """Unchanged, and still permissive - should-decline questions rely on it."""

    def test_a_hedge_anywhere_counts_as_declining(self) -> None:
        # The exact behaviour `is_refusal` deliberately does *not* copy.
        assert looks_like_decline(D5_ANSWER, PASS) is True

    def test_the_inflected_forms_are_still_matched(self) -> None:
        assert looks_like_decline(MODEL_REFUSAL, PASS) is True

    def test_emphasis_inside_the_phrase_does_not_hide_a_refusal(self) -> None:
        assert looks_like_decline(E2_ANSWER, PASS) is True

    @pytest.mark.parametrize(
        "answer",
        [
            "The score is `not stated` in the documents.",
            "The score is *not stated* in the documents.",
            "The score is __not stated__ in the documents.",
        ],
    )
    def test_every_emphasis_style_is_stripped(self, answer: str) -> None:
        assert looks_like_decline(answer, PASS) is True


class TestGradeAnswer:
    """The two expectations must use the two different tests."""

    def test_an_answerable_question_is_not_failed_by_a_trailing_caveat(self) -> None:
        passed, declined, missing = grade_answer(
            _q(Expect.ANSWER, must_include=("Mayur Patel",)), D5_ANSWER, PASS
        )
        assert (passed, declined, missing) == (True, False, [])

    def test_an_answerable_question_is_still_failed_by_a_real_refusal(self) -> None:
        passed, declined, _ = grade_answer(
            _q(Expect.ANSWER, must_include=("6,634.45",)), ABSTAIN_TEXT, ABSTAIN
        )
        assert passed is False
        assert declined is True

    def test_a_should_decline_question_still_passes_on_any_hedge(self) -> None:
        # A caveat-carrying answer to an unanswerable question is a correct
        # decline, and must keep passing under the permissive rule.
        passed, declined, _ = grade_answer(_q(Expect.DECLINE), MODEL_REFUSAL, PASS)
        assert (passed, declined) == (True, True)

    def test_a_should_decline_question_fails_on_a_confident_answer(self) -> None:
        passed, declined, _ = grade_answer(
            _q(Expect.DECLINE), "The F1 score is 0.87 [1].", PASS
        )
        assert (passed, declined) == (False, False)
