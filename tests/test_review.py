import shutil
from pathlib import Path

import pytest
from openpyxl import load_workbook
from openpyxl.chart import BarChart, Reference

from workbookreview import EvidenceClass, Phase, ReviewError, ReviewService
from workbookreview.assistant import FieldAssistant, LLMAssistant, evidence_block
from workbookreview.cli import load_documents, load_questions, main
from workbookreview.models import BatchProposal, Customer, Document, Proposal
from workbookreview.workbook import WorkbookError, check_template, dropdown_options, write_answers

SAMPLES = Path(__file__).parent.parent / "samples"
TEMPLATE = SAMPLES / "template.xlsx"


@pytest.fixture
def questions():
    return load_questions(SAMPLES / "questions.json")


@pytest.fixture
def service(questions):
    return ReviewService(
        questions, TEMPLATE, load_documents(SAMPLES / "evidence"), FieldAssistant()
    )


def through_batches(service, corrections=None):
    review = service.confirm_customer(service.start("r1"))
    while review.current_batch is not None:
        batch = review.current_batch
        service.propose_batch(review)
        service.confirm_batch(review, (corrections or {}).get(batch, {}))
    return review


FIXES = {1: {"Q04": "12 Harbour Road, Rotterdam"}}


def test_the_review_walks_the_phases_only_on_confirmation(service):
    review = service.start("r1")
    assert (
        review.phase is Phase.CUSTOMER_VALIDATION
        and review.customer.name == "Northwind Supplies Ltd."
    )
    with pytest.raises(ReviewError, match="customer_validation"):
        service.propose_batch(review)
    service.confirm_customer(review)
    assert (review.phase, review.current_batch) == (Phase.BATCHES, 1)
    with pytest.raises(ReviewError, match="not been proposed"):
        service.confirm_batch(review)
    service.propose_batch(review)
    service.confirm_batch(review, FIXES[1])
    assert review.current_batch == 2 and all(
        review.answers[q].confirmed_at for q in ("Q01", "Q02", "Q03", "Q04")
    )


def test_each_proposal_carries_its_evidence_class_and_sources(service):
    review = service.confirm_customer(service.start("r1"))
    by_id = {a.question_id: a.proposal for a in service.propose_batch(review)}
    assert by_id["Q01"].evidence is EvidenceClass.SUPPORTED and by_id["Q01"].confidence == "HIGH"
    assert by_id["Q01"].sources == [
        "onboarding_form.txt",
        "ownership_chart.txt",
        "registry_extract.txt",
    ]
    conflict = by_id["Q04"]
    assert (
        conflict.evidence is EvidenceClass.CONFLICT
        and conflict.value == ""
        and "48 Canal Street" in conflict.rationale
    )


def test_a_derived_answer_says_what_it_was_derived_from(service):
    review = service.confirm_customer(service.start("r1"))
    service.propose_batch(review)
    service.confirm_batch(review, FIXES[1])
    q05 = service.propose_batch(review)[0].proposal
    assert (q05.value, q05.evidence) == (
        "Yes",
        EvidenceClass.DERIVED,
    ) and "2019-06-14" in q05.rationale


def test_reference_documents_are_not_evidence(service):
    review = through_batches(service, FIXES)
    assert review.answers["Q11"].proposal.sources == ["ownership_chart.txt"]
    assert "bank reference document" in evidence_block(service.documents)


def test_sources_that_are_not_evidence_documents_are_removed(questions):
    class Inventive(FieldAssistant):
        def propose_batch(self, asked, documents, customer, confirmed):
            return BatchProposal(answers=[
                Proposal(question_id="Q01", value="Northwind", evidence=EvidenceClass.SUPPORTED, confidence="HIGH", sources=["registry_extract.txt", "press_article.pdf"]),
                Proposal(question_id="Q02", value="HRB 1", evidence=EvidenceClass.SUPPORTED, confidence="HIGH", sources=["policy_annex.reference.txt"]),
            ])  # fmt: skip

    service = ReviewService(questions, TEMPLATE, load_documents(SAMPLES / "evidence"), Inventive())
    answers = {
        a.question_id: a.proposal
        for a in service.propose_batch(service.confirm_customer(service.start("r1")))
    }
    assert (
        answers["Q01"].sources == ["registry_extract.txt"]
        and "press_article.pdf" in answers["Q01"].rationale
    )
    assert (answers["Q02"].evidence, answers["Q02"].value) == (
        EvidenceClass.NOT_FOUND,
        "",
    )  # no evidence left to stand on
    assert answers["Q03"].evidence is EvidenceClass.NOT_FOUND  # the assistant skipped it


@pytest.mark.parametrize(
    ("batch", "correction", "message"),
    [
        (1, {"Q04": ""}, None),  # a free question may stay empty
        (1, {"Q03": "14.06.2019"}, "YYYY-MM-DD"),
        (2, {"Q08": "maybe"}, "Yes or No"),
        (2, {"Q06": "about 80"}, "not a number"),
        (3, {"Q11": "Moderate"}, "workbook's options"),
        (2, {"Q01": "Other"}, "not in the current batch"),
    ],
)
def test_final_answers_must_fit_their_cells(service, batch, correction, message):
    review = service.confirm_customer(service.start("r1"))
    for earlier in range(1, batch):
        service.propose_batch(review)
        service.confirm_batch(review, FIXES.get(earlier, {}))
    service.propose_batch(review)
    if message is None:
        assert service.confirm_batch(review, correction).answers["Q04"].final == ""
        return
    with pytest.raises(ReviewError, match=message):
        service.confirm_batch(review, correction)
    assert review.current_batch == batch and not any(
        review.answers[q.id].confirmed_at for q in service.questions.in_batch(batch)
    )


def test_dropdown_options_come_from_the_workbook(questions):
    q11 = next(q for q in questions.questions if q.id == "Q11")
    assert dropdown_options(TEMPLATE, q11) == ["Low", "Medium", "High"]


def test_overrides_are_recorded_and_blank_words_are_normalised(service):
    review = through_batches(
        service,
        {
            1: {"Q04": "12 Harbour Road, Rotterdam", "Q01": "Northwind Supplies Ltd."},
            3: {"Q09": "N/A"},
        },
    )
    assert review.answers["Q04"].overridden and not review.answers["Q01"].overridden
    assert review.answers["Q09"].final == "" and review.answers["Q09"].overridden


def test_confirmed_answers_are_not_proposed_again(service):
    review = service.confirm_customer(service.start("r1"))
    service.propose_batch(review)
    service.confirm_batch(review, FIXES[1])
    review.current_batch = 1
    with pytest.raises(ReviewError, match="not proposed again"):
        service.propose_batch(review)


def test_only_an_explicit_confirmation_moves_the_review_on(service):
    review = service.start("r1")
    assert (
        service.handle_message(review, "why is the address missing?") == "ask"
        and review.phase is Phase.CUSTOMER_VALIDATION
    )
    assert (
        service.handle_message(review, "hmm") == "unclear"
        and review.phase is Phase.CUSTOMER_VALIDATION
    )
    assert (
        service.handle_message(review, "Confirmed.") == "confirm" and review.phase is Phase.BATCHES
    )


def test_an_inconsistent_customer_stops_the_review(questions):
    documents = [
        *load_documents(SAMPLES / "evidence"),
        Document(name="other.txt", text="Legal name: Northwind Traders BV"),
    ]
    service = ReviewService(questions, TEMPLATE, documents, FieldAssistant())
    review = service.start("r1")
    assert not review.customer.consistent
    with pytest.raises(ReviewError, match="not established"):
        service.confirm_customer(review)


def test_the_workbook_is_written_once_and_only_in_the_target_cells(service, tmp_path):
    review = through_batches(service, {**FIXES, 3: {"Q11": "Medium"}})
    assert review.phase is Phase.READY_TO_WRITE
    out = service.finalize(review, tmp_path / "review.xlsx")
    book = load_workbook(out)
    assert book.sheetnames == ["Review", "Summary", "Review audit"]
    sheet = book["Review"]
    assert (sheet["C3"].value, sheet["C9"].value, sheet["C15"].value, sheet["C16"].value) == (
        "Northwind Supplies Ltd.",
        85,
        "Medium",
        2400000,
    )
    assert (
        sheet["B3"].value == "Legal name of the customer"
        and book["Summary"]["B1"].value == "=COUNTA(Review!C3:C16)"
    )
    audit = list(book["Review audit"].iter_rows(values_only=True))
    assert audit[0][:4] == ("Review", "r1", "Question set", "2026.03-1") and len(audit) == 14
    q11 = next(row for row in audit if row[0] == "Q11")
    assert (q11[2], q11[3], q11[7]) == ("Medium", "Low", "yes")
    assert load_workbook(TEMPLATE)["Review"]["C3"].value is None  # the template itself is untouched
    with pytest.raises(ReviewError, match="complete"):
        service.finalize(review, tmp_path / "again.xlsx")


def test_nothing_is_written_before_every_batch_is_confirmed(service, tmp_path, questions):
    review = service.confirm_customer(service.start("r1"))
    with pytest.raises(ReviewError, match="batches"):
        service.finalize(review, tmp_path / "early.xlsx")
    with pytest.raises(WorkbookError, match="not confirmed"):
        write_answers(TEMPLATE, tmp_path / "early.xlsx", questions, review.answers, "r1")
    assert not (tmp_path / "early.xlsx").exists()


def test_a_target_inside_a_protected_range_is_refused_before_writing(service, tmp_path, questions):
    review = through_batches(service, FIXES)
    questions.questions[0].cell = "B3"  # the question label column
    with pytest.raises(WorkbookError, match="protected range"):
        write_answers(TEMPLATE, tmp_path / "x.xlsx", questions, review.answers, "r1")
    assert not (tmp_path / "x.xlsx").exists()


def test_a_write_that_touches_other_cells_is_rejected_and_removed(
    service, tmp_path, questions, monkeypatch
):
    review = through_batches(service, FIXES)
    real_copy = shutil.copyfile

    def tampering_copy(src, dst):
        real_copy(src, dst)
        book = load_workbook(dst)
        book["Summary"]["B2"] = "edited"
        book.save(dst)

    monkeypatch.setattr("workbookreview.workbook.shutil.copyfile", tampering_copy)
    with pytest.raises(WorkbookError, match="outside the targets"):
        write_answers(TEMPLATE, tmp_path / "x.xlsx", questions, review.answers, "r1")
    assert not (tmp_path / "x.xlsx").exists()


def test_workbooks_with_macros_or_charts_are_refused(tmp_path):
    with pytest.raises(WorkbookError, match="without macros"):
        check_template(tmp_path / "book.xlsm")
    book = load_workbook(TEMPLATE)
    chart = BarChart()
    chart.add_data(Reference(book["Review"], min_col=1, min_row=1, max_row=3))
    book["Summary"].add_chart(chart, "D5")
    book.save(tmp_path / "charted.xlsx")
    with pytest.raises(WorkbookError, match="charts or pivot tables"):
        check_template(tmp_path / "charted.xlsx")


def test_a_changed_question_set_blocks_the_write(service, tmp_path):
    review = through_batches(service, FIXES)
    service.questions.version = "2026.04-1"
    with pytest.raises(ReviewError, match="question set changed"):
        service.finalize(review, tmp_path / "x.xlsx")


def test_nothing_in_the_code_assumes_the_number_of_questions_or_batches(questions, tmp_path):
    questions.questions = [q for q in questions.questions if q.batch != 2 and q.id != "Q12"]
    service = ReviewService(
        questions, TEMPLATE, load_documents(SAMPLES / "evidence"), FieldAssistant()
    )
    review = through_batches(service, FIXES)
    assert len(review.answers) == 7 and service.finalize(review, tmp_path / "small.xlsx").exists()


def test_llm_assistant_sends_questions_confirmed_answers_and_marked_evidence():
    class Model:
        def parse(self, system, user, schema):
            self.system, self.user = system, user
            return schema(answers=[]) if schema is BatchProposal else schema(intent="confirm")

    model, assistant = Model(), None
    assistant = LLMAssistant(model)
    documents = load_documents(SAMPLES / "evidence")
    questions = load_questions(SAMPLES / "questions.json").in_batch(2)
    assistant.propose_batch(
        questions,
        documents,
        Customer(name="Northwind Supplies Ltd.", registration_number="HRB 482196"),
        {"Q01": "Northwind Supplies Ltd."},
    )
    assert (
        "- Q05 (yes_no): Has the company existed" in model.user
        and "- Q01: Northwind Supplies Ltd." in model.user
    )
    assert (
        "policy_annex.reference.txt (bank reference document" in model.user
        and "CONFLICT when documents disagree" in model.system
    )
    assert assistant.classify_intent("ok") == "confirm"


def test_cli_runs_the_sample_review(tmp_path, capsys):
    assert main(["--samples", str(SAMPLES), "--out", str(tmp_path / "out.xlsx")]) == 0
    out = capsys.readouterr().out
    assert "Q04  CONFLICT" in out and "overridden by the reviewer: Q04, Q11" in out
