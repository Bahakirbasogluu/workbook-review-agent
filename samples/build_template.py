"""Builds samples/template.xlsx, the workbook the reviews are written into."""

import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.worksheet.datavalidation import DataValidation

HERE = Path(__file__).parent
questions = json.loads((HERE / "questions.json").read_text(encoding="utf-8"))["questions"]

workbook = Workbook()
review = workbook.active
review.title = "Review"
review["A1"], review["B1"], review["C1"] = "No.", "Question", "Answer"
for cell in ("A1", "B1", "C1"):
    review[cell].font = Font(bold=True)
for q in questions:
    row = q["cell"][1:]
    review[f"A{row}"], review[f"B{row}"] = q["id"], q["text"]
review.column_dimensions["B"].width, review.column_dimensions["C"].width = 52, 40

yes_no = DataValidation(type="list", formula1='"Yes,No"', allow_blank=False)
risk = DataValidation(type="list", formula1='"Low,Medium,High"', allow_blank=True)
review.add_data_validation(yes_no)
review.add_data_validation(risk)
for q in questions:
    if q["format"] == "yes_no":
        yes_no.add(q["cell"])
    if q["format"] == "dropdown":
        risk.add(q["cell"])

summary = workbook.create_sheet("Summary")
summary["A1"], summary["B1"] = "Answers filled", "=COUNTA(Review!C3:C16)"
summary["A2"], summary["B2"] = "Risk rating", "=Review!C15"
workbook.save(HERE / "template.xlsx")
