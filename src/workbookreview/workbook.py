"""Reading constraints from the workbook and writing confirmed answers into a copy of it."""

from __future__ import annotations

import shutil
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import range_boundaries
from openpyxl.utils.cell import column_index_from_string, coordinate_from_string

from workbookreview.models import Answer, Question, QuestionSet


class WorkbookError(Exception):
    pass


def check_template(path: str | Path) -> None:
    """Workbooks with macros, charts or pivot tables are refused: writing through them is not safe."""
    path = Path(path)
    if path.suffix.lower() != ".xlsx":
        raise WorkbookError(f"{path.name}: only .xlsx workbooks without macros are accepted")
    workbook = load_workbook(path)
    for sheet in workbook.worksheets:
        if sheet._charts or sheet._pivots:
            raise WorkbookError(f"{path.name}: sheet {sheet.title} contains charts or pivot tables")


def dropdown_options(path: str | Path, question: Question) -> list[str] | None:
    """The list a cell's own data validation allows, or None if the cell has no list validation."""
    sheet = load_workbook(path)[question.sheet]
    for validation in sheet.data_validations.dataValidation:
        if validation.type == "list" and question.cell in validation.sqref:
            formula = validation.formula1 or ""
            if formula.startswith('"'):
                return [option.strip() for option in formula.strip('"').split(",")]
    return None


def _in_range(sheet: str, cell: str, protected: str) -> bool:
    name, _, cells = protected.partition("!")
    if name != sheet:
        return False
    min_col, min_row, max_col, max_row = range_boundaries(cells)
    column, row = coordinate_from_string(cell)
    return min_col <= column_index_from_string(column) <= max_col and min_row <= row <= max_row


def protected_targets(questions: QuestionSet) -> list[str]:
    return [
        q.id
        for q in questions.questions
        if any(_in_range(q.sheet, q.cell, p) for p in questions.protected)
    ]


def _snapshot(path: str | Path) -> dict[tuple[str, str], object]:
    return {
        (s.title, c.coordinate): c.value
        for s in load_workbook(path).worksheets
        for row in s.iter_rows()
        for c in row
        if c.value is not None
    }


def write_answers(
    template: str | Path,
    output: str | Path,
    questions: QuestionSet,
    answers: dict[str, Answer],
    review_id: str,
) -> Path:
    """Copies the template, writes only the target cells, and verifies nothing else changed.

    The whole workbook is read before and after the write. If any cell other
    than a target differs, the output is deleted and the write is rejected.
    """
    template, output = Path(template), Path(output)
    check_template(template)
    if blocked := protected_targets(questions):
        raise WorkbookError(f"target cells of {', '.join(blocked)} lie inside a protected range")
    missing = [
        q.id
        for q in questions.questions
        if q.id not in answers or answers[q.id].confirmed_at is None
    ]
    if missing:
        raise WorkbookError(f"not confirmed: {', '.join(missing)}")

    before = _snapshot(template)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template, output)
    workbook = load_workbook(output)
    targets = {}
    for q in questions.questions:
        value = answers[q.id].final or None
        if q.format == "number" and value is not None:
            value = float(value) if "." in value else int(value)
        workbook[q.sheet][q.cell] = value
        targets[(q.sheet, q.cell)] = value
    workbook.save(output)

    after = _snapshot(output)
    changed = {
        key for key in before.keys() | after.keys() if before.get(key) != after.get(key)
    } - targets.keys()
    if changed:
        output.unlink()
        raise WorkbookError(f"cells outside the targets changed: {sorted(changed)[:5]}")

    audit = workbook.create_sheet("Review audit")  # always the last sheet
    audit.append(["Review", review_id, "Question set", questions.version])
    audit.append(
        [
            "Question",
            "Cell",
            "Final answer",
            "Proposed",
            "Evidence",
            "Confidence",
            "Sources",
            "Overridden",
            "Confirmed at",
            "Rationale",
        ]
    )
    for q in questions.questions:
        a = answers[q.id]
        audit.append([q.id, f"{q.sheet}!{q.cell}", a.final, a.proposal.value, a.proposal.evidence.value, a.proposal.confidence,
                      ", ".join(a.proposal.sources), "yes" if a.overridden else "no", a.confirmed_at.isoformat(), a.proposal.rationale])  # fmt: skip
    workbook.save(output)
    return output
