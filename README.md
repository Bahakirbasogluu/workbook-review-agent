# workbookreview

[![CI](https://github.com/Bahakirbasogluu/workbook-review-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/Bahakirbasogluu/workbook-review-agent/actions/workflows/ci.yml)

An agent that pre-fills a review workbook from evidence documents, batch by batch, while a person confirms every answer. When the last batch is confirmed, the answers are written into the organisation's own Excel workbook in one guarded pass.

The design comes from a KYC onboarding assistant I built for a bank, where relationship managers used to complete a long eligibility workbook by hand from the documents a client sends. The code here is written from scratch with an invented customer, question set and workbook; it contains no client code or data.

## How a review runs

```
intake ──> customer validation ──> batch 1 ──> batch 2 ──> ... ──> ready to write ──> complete
              │                       │
   agent: who is this about?   agent: proposes each answer with
   reviewer: confirms          evidence class, confidence, sources
                               reviewer: confirms or corrects
```

The workflow is a phase machine owned by code. The agent has three bounded tasks: identify the customer, propose the answers of a batch, and classify what the reviewer typed. Deciding that the review moves on is not one of them; only an explicit confirmation does that.

## Design decisions

- **Every proposal says how well it is supported.** Each answer carries an evidence class (`SUPPORTED`: a document states it; `DERIVED`: it follows from stated facts; `CONFLICT`: documents disagree; `NOT_FOUND`), a confidence, the source documents and a one-line rationale. A conflict is left empty for the reviewer to decide, with both values shown.
- **Sources are checked against the documents that exist.** A proposal may name only evidence documents of this review. Other names are removed and noted, and an answer left without a source is downgraded to `NOT_FOUND`. Internal reference documents explain the questions but never count as evidence about the customer.
- **A final answer must fit its cell.** Yes/no questions cannot be blank, dropdown answers must be one of the options in the workbook's own data validation, dates and numbers must parse. A batch is confirmed as a whole or not at all.
- **Confirmed answers are final.** They are passed to later batches as context, never proposed again and never overwritten. A correction by the reviewer is recorded as an override next to what the agent proposed.
- **One write, into a copy, verified.** The template is copied and only the target cells are written. Targets inside a protected range are refused up front. The whole workbook is read before and after; if any other cell differs, the output is deleted and the write is rejected. Formulas, labels and validations stay as the organisation built them.
- **The audit travels with the file.** The last sheet lists every question with the final answer, the proposal, its evidence class, sources, whether the reviewer overrode it, and when it was confirmed.
- **Questions are configuration.** The question set (batches, target cells, formats, protected ranges) is a versioned JSON file. A review keeps the version it started on and refuses to write if the set changed underneath it. Nothing in the code assumes how many questions or batches there are.
- **Unsafe workbooks are refused.** Workbooks with macros, charts or pivot tables are rejected at the start rather than written through.

## Quick start

```bash
pip install -e ".[dev]"
workbookreview
pytest
```

`workbookreview` runs the sample review with scripted reviewer decisions:

```
customer: Northwind Supplies Ltd. (HRB 482196), consistent: True

batch 1
  Q01  SUPPORTED  HIGH   Northwind Supplies Ltd.            onboarding_form.txt, ownership_chart.txt, registry_extract.txt
  Q02  SUPPORTED  HIGH   HRB 482196                         onboarding_form.txt, registry_extract.txt
  Q03  SUPPORTED  MEDIUM 2019-06-14                         registry_extract.txt
  Q04  CONFLICT   LOW    -                                  onboarding_form.txt, registry_extract.txt
  reviewer sets Q04 to: 12 Harbour Road, Rotterdam
  reviewer confirms the batch

batch 2
  Q05  DERIVED    MEDIUM Yes                                registry_extract.txt
  Q06  SUPPORTED  MEDIUM 85                                 onboarding_form.txt
  ...

batch 3
  ...
  Q11  SUPPORTED  MEDIUM Low                                ownership_chart.txt
  reviewer sets Q11 to: Medium
  reviewer confirms the batch

written: out/review.xlsx  (sheets: Review, Summary, Review audit; overridden by the reviewer: Q04, Q11)
```

The two documents disagree on the registered address, so the agent proposes nothing for Q04 and shows both. Q05 is derived from the incorporation date. For Q11 the reviewer overrides the proposal with another option from the workbook's dropdown; the audit sheet keeps both values.

## Using a model

The sample runs on `FieldAssistant`, a deterministic assistant for documents written as `Label: value` lines. `LLMAssistant` performs the same three tasks on a structured-output model, for documents in any form:

```bash
pip install -e ".[azure]"
workbookreview --backend azure
```

Any model that can fill a Pydantic schema works: implement `parse(system, user, schema)`.

## Tests

25 tests. They cover the phase transitions, evidence classes and sources, removal of sources that are not evidence, validation of final answers against the workbook's own constraints, overrides, the guarded write (target cells only, protected ranges, a tampered copy being rejected, formulas preserved), refusal of workbooks with macros or charts, a changed question set, and a smaller question set to show nothing is hard-coded.

## Layout

```
src/workbookreview/
  models.py      Question, QuestionSet, Proposal, Answer, Review, phases
  assistant.py   the three tasks: deterministic and model-based
  service.py     the phase machine, validation, confirmation
  workbook.py    template checks, dropdown options, guarded write, audit sheet
  cli.py         the sample review
samples/         question set, evidence documents, template workbook
tests/
```

## License

MIT
