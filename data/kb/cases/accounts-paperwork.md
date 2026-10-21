---
title: Case: an accounts office stops re-typing forms
section: cases
updated: 2026-10-01
doc_id: cases/accounts-paperwork
product: document-data-entry
---
# Case: an accounts office stops re-typing forms

This is an anonymised, fictional case. The practice, its clients and its figures are invented.

## The situation

A small accounts practice received paper forms from its clients: expense sheets, mileage logs and signed engagement forms. A member of staff typed each form into the practice's system of record, line by line, and a second person checked a sample of the entries. The checking found wrong dates and transposed amounts often enough that the office did not trust the first pass, which meant every form was effectively handled twice.

## What the discovery found

The interview recorded the form types, the daily volume of forms, the minutes of typing per form and the minutes of checking. The error types were wrong and delayed. The practice's system could import CSV files, so csv-export was available at no surcharge. There were no urgent requests, and the personal data question was answered yes, because the forms carried client names and bank details.

## What was recommended

One module: document-data-entry, set up for the three agreed form types. Each form is scanned, the fields are extracted, and validation rules check them, for example that a date is a real date and that an amount has the right number of decimal places. Anything the module cannot read with confidence is flagged for a person. The estimate was indicative.

## What changed

The first pass was done by the module, and the staff member's time moved from typing to reviewing the flagged items. The entry log showed which scan produced which record, so a doubtful entry could be traced back to its page. The sample check continued, but on records the module had already validated.

## What it did not do

Handwriting outside the three agreed form types was flagged rather than read, so a client who sent a note on a napkin still created manual work. Signature verification was out of scope. The practice kept its own review step.

## Why it is a useful case

It shows the value of the flagged-items queue: the module did not need to be right every time, it needed to know when it was not sure.
