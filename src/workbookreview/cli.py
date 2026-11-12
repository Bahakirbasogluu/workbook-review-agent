from __future__ import annotations

import argparse
import json
from pathlib import Path

from openpyxl import load_workbook

from workbookreview.assistant import FieldAssistant
from workbookreview.models import Document, QuestionSet
from workbookreview.service import ReviewService


def load_documents(folder: str | Path) -> list[Document]:
    return [
        Document(name=p.name, text=p.read_text(encoding="utf-8"), reference=".reference." in p.name)
        for p in sorted(Path(folder).glob("*.txt"))
    ]


def load_questions(path: str | Path) -> QuestionSet:
    return QuestionSet(**json.loads(Path(path).read_text(encoding="utf-8")))


def _assistant(backend: str):
    if backend == "fields":
        return FieldAssistant()
    from workbookreview.assistant import AzureOpenAIModel, LLMAssistant

    return LLMAssistant(AzureOpenAIModel())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="workbookreview",
        description="Runs the sample review with scripted reviewer decisions.",
    )
    parser.add_argument("--backend", choices=("fields", "azure"), default="fields")
    parser.add_argument("--samples", type=Path, default=Path("samples"))
    parser.add_argument("--out", type=Path, default=Path("out/review.xlsx"))
    args = parser.parse_args(argv)

    questions = load_questions(args.samples / "questions.json")
    service = ReviewService(
        questions,
        args.samples / "template.xlsx",
        load_documents(args.samples / "evidence"),
        _assistant(args.backend),
    )
    review = service.start("demo-001")
    print(
        f"customer: {review.customer.name} ({review.customer.registration_number}), consistent: {review.customer.consistent}"
    )
    service.confirm_customer(review)

    # What the relationship manager decides after reading each batch.
    corrections = {1: {"Q04": "12 Harbour Road, Rotterdam"}, 2: {}, 3: {"Q11": "Medium"}}
    while review.current_batch is not None:
        batch = review.current_batch
        print(f"\nbatch {batch}")
        for answer in service.propose_batch(review):
            p = answer.proposal
            print(
                f"  {p.question_id}  {p.evidence.value:<10} {p.confidence:<6} {p.value or '-':<34} {', '.join(p.sources)}"
            )
        for qid, value in corrections[batch].items():
            print(f"  reviewer sets {qid} to: {value}")
        service.confirm_batch(review, corrections[batch])
        print("  reviewer confirms the batch")

    path = service.finalize(review, args.out)
    sheets = load_workbook(path).sheetnames
    overridden = [a.question_id for a in review.answers.values() if a.overridden]
    print(
        f"\nwritten: {path}  (sheets: {', '.join(sheets)}; overridden by the reviewer: {', '.join(overridden)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
