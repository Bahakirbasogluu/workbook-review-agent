"""The review as a phase machine. Code owns every transition; the assistant only proposes."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from workbookreview import workbook
from workbookreview.assistant import Assistant
from workbookreview.models import (
    Answer,
    Document,
    EvidenceClass,
    Phase,
    Proposal,
    Question,
    QuestionSet,
    Review,
)

_BLANK = {"", "blank", "n/a", "na", "none", "-", "boş", "yok"}


class ReviewError(Exception):
    pass


class ReviewService:
    """intake -> customer_validation -> batches -> ready_to_write -> complete

    A phase changes only when the reviewer confirms. A confirmed answer is
    never proposed again or overwritten, and the workbook is written once, at
    the end, from confirmed answers only.
    """

    def __init__(self, questions: QuestionSet, template: str | Path, documents: list[Document], assistant: Assistant,
                 clock: Callable[[], datetime] | None = None):  # fmt: skip
        workbook.check_template(template)
        self.questions, self.template, self.documents, self.assistant = (
            questions,
            Path(template),
            documents,
            assistant,
        )
        self._now = clock or (lambda: datetime.now(timezone.utc))
        self._evidence_names = {d.name for d in documents if not d.reference}

    def start(self, review_id: str) -> Review:
        review = Review(
            id=review_id, config_version=self.questions.version
        )  # the review keeps the version it ran on
        review.customer = self.assistant.identify_customer(self.documents)
        review.phase = Phase.CUSTOMER_VALIDATION
        return review

    def confirm_customer(self, review: Review) -> Review:
        self._expect(review, Phase.CUSTOMER_VALIDATION)
        if not review.customer.consistent or not review.customer.name:
            raise ReviewError(
                f"the customer is not established: {review.customer.notes or 'no legal name found'}"
            )
        review.phase, review.current_batch = Phase.BATCHES, self.questions.batches()[0]
        return review

    def propose_batch(self, review: Review) -> list[Answer]:
        self._expect(review, Phase.BATCHES)
        asked = self.questions.in_batch(review.current_batch)
        if any(a.confirmed_at for a in (review.answers.get(q.id) for q in asked) if a):
            raise ReviewError(
                f"batch {review.current_batch} has confirmed answers and is not proposed again"
            )
        confirmed = {qid: a.final for qid, a in review.answers.items() if a.confirmed_at}
        proposal = self.assistant.propose_batch(asked, self.documents, review.customer, confirmed)
        by_id = {p.question_id: p for p in proposal.answers}
        for q in asked:
            p = by_id.get(q.id) or Proposal(
                question_id=q.id,
                evidence=EvidenceClass.NOT_FOUND,
                confidence="LOW",
                rationale="no proposal returned",
            )
            review.answers[q.id] = Answer(question_id=q.id, proposal=self._checked(p))
        return [review.answers[q.id] for q in asked]

    def _checked(self, p: Proposal) -> Proposal:
        """A proposal may name only evidence documents that exist; anything else is removed and noted."""
        unknown = [s for s in p.sources if s not in self._evidence_names]
        if unknown:
            p = p.model_copy(update={
                "sources": [s for s in p.sources if s in self._evidence_names],
                "rationale": f"{p.rationale} (removed sources that are not evidence documents: {', '.join(unknown)})".strip(),
            })  # fmt: skip
        if p.evidence in (EvidenceClass.SUPPORTED, EvidenceClass.DERIVED) and not p.sources:
            p = p.model_copy(
                update={"evidence": EvidenceClass.NOT_FOUND, "confidence": "LOW", "value": ""}
            )
        return p

    def confirm_batch(self, review: Review, corrections: dict[str, str] | None = None) -> Review:
        """The reviewer accepts the proposals of the current batch, with any corrections."""
        self._expect(review, Phase.BATCHES)
        asked = self.questions.in_batch(review.current_batch)
        if any(q.id not in review.answers for q in asked):
            raise ReviewError("the batch has not been proposed yet")
        corrections = corrections or {}
        if unknown := set(corrections) - {q.id for q in asked}:
            raise ReviewError(f"not in the current batch: {', '.join(sorted(unknown))}")
        finals = {
            q.id: self._validated(q, corrections.get(q.id, review.answers[q.id].proposal.value))
            for q in asked
        }
        for q in asked:  # nothing is stored unless the whole batch is valid
            answer = review.answers[q.id]
            answer.final, answer.confirmed_at = finals[q.id], self._now()
            answer.overridden = q.id in corrections and finals[q.id] != self._normal(
                answer.proposal.value
            )
        later = [b for b in self.questions.batches() if b > review.current_batch]
        review.current_batch = later[0] if later else None
        review.phase = Phase.BATCHES if later else Phase.READY_TO_WRITE
        return review

    def handle_message(self, review: Review, message: str) -> str:
        """Free text from the reviewer. Only an explicit confirmation moves the review on."""
        intent = self.assistant.classify_intent(message)
        if intent != "confirm":
            return intent
        if review.phase is Phase.CUSTOMER_VALIDATION:
            self.confirm_customer(review)
        elif review.phase is Phase.BATCHES:
            self.confirm_batch(review)
        return intent

    def finalize(self, review: Review, output: str | Path) -> Path:
        self._expect(review, Phase.READY_TO_WRITE)
        if review.config_version != self.questions.version:
            raise ReviewError("the question set changed since this review started")
        path = workbook.write_answers(
            self.template, output, self.questions, review.answers, review.id
        )
        review.phase, review.output_path = Phase.COMPLETE, str(path)
        return path

    @staticmethod
    def _normal(value: str | None) -> str:
        text = (value or "").strip()
        return "" if text.lower() in _BLANK else text

    def _validated(self, q: Question, raw: str | None) -> str:
        """A final answer must fit the cell it goes into."""
        value = self._normal(raw)
        if q.format == "yes_no":
            if value.lower() not in ("yes", "no"):
                raise ReviewError(f"{q.id}: a yes/no question needs Yes or No, not {value!r}")
            return value.capitalize()
        if not value:
            return ""
        if q.format == "dropdown":
            options = workbook.dropdown_options(self.template, q)
            if options is not None and value not in options:
                raise ReviewError(
                    f"{q.id}: {value!r} is not one of the workbook's options {options}"
                )
        if q.format == "date" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ReviewError(f"{q.id}: a date is written as YYYY-MM-DD")
        if q.format == "number" and not re.fullmatch(r"-?\d+(\.\d+)?", value):
            raise ReviewError(f"{q.id}: {value!r} is not a number")
        return value

    @staticmethod
    def _expect(review: Review, phase: Phase) -> None:
        if review.phase is not phase:
            raise ReviewError(f"the review is in phase {review.phase.value}, not {phase.value}")
