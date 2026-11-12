from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Phase(str, Enum):
    INTAKE = "intake"
    CUSTOMER_VALIDATION = "customer_validation"
    BATCHES = "batches"
    READY_TO_WRITE = "ready_to_write"
    COMPLETE = "complete"


class EvidenceClass(str, Enum):
    SUPPORTED = "SUPPORTED"  # stated in a document
    DERIVED = "DERIVED"  # follows from stated facts
    CONFLICT = "CONFLICT"  # documents disagree
    NOT_FOUND = "NOT_FOUND"


class Question(BaseModel):
    id: str
    batch: int
    order: int
    text: str
    sheet: str
    cell: str
    format: Literal["free", "yes_no", "dropdown", "date", "number"]
    hint: str = ""


class QuestionSet(BaseModel):
    version: str
    protected: list[str] = Field(
        default_factory=list, description="Ranges such as 'Sheet!A1:B20' that must not change"
    )
    questions: list[Question]

    def batches(self) -> list[int]:
        return sorted({q.batch for q in self.questions})

    def in_batch(self, batch: int) -> list[Question]:
        return sorted((q for q in self.questions if q.batch == batch), key=lambda q: q.order)


class Document(BaseModel):
    name: str
    text: str
    reference: bool = Field(
        False, description="An internal reference document, not evidence about the customer"
    )


class Customer(BaseModel):
    name: str | None = None
    registration_number: str | None = None
    consistent: bool = Field(
        True, description="False when the documents disagree about who the customer is"
    )
    notes: str = ""


class Proposal(BaseModel):
    question_id: str
    value: str = ""
    evidence: EvidenceClass
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    sources: list[str] = Field(
        default_factory=list, description="Names of the documents the answer comes from"
    )
    rationale: str = ""


class BatchProposal(BaseModel):
    answers: list[Proposal]


class Answer(BaseModel):
    question_id: str
    proposal: Proposal
    final: str | None = None
    overridden: bool = False
    confirmed_at: datetime | None = None


class Review(BaseModel):
    id: str
    config_version: str
    phase: Phase = Phase.INTAKE
    customer: Customer | None = None
    current_batch: int | None = None
    answers: dict[str, Answer] = Field(default_factory=dict)
    output_path: str | None = None
