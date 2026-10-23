---
title: Export-only tools
section: implementation
updated: 2026-10-01
doc_id: implementation/export-only-tools
product: general
---
# Export-only tools

Some field-service and job tools can export data but cannot import it. This article explains what the export-only-tool integration does in that case, and what it cannot do.

## The problem

With an export-only tool, a module can read what is in your system, but it cannot write a new record back. Results must be re-keyed or pushed through a file drop. Many trades businesses are in this position: the tool was chosen years ago, the data is in it, and there is no API and no import.

## How the integration works

The module prepares its results in the form your tool expects. If your tool supports a file drop, a folder it watches for new files, the module places the file there. If not, the module prepares a clean, checked list for a member of staff to re-key, with every field already validated. Re-keying from a checked list is faster and makes fewer errors than re-keying from a paper sheet.

## Which modules support it

The export-only-tool integration is offered for document-data-entry. It pairs naturally with that module, because the paper job sheets the module reads are usually the ones that would have been re-keyed by hand.

## Cost

The export-only-tool surcharge is 250 USD a month. It covers the extra handling, the file-drop setup or the prepared re-keying list, and the checking that goes with it.

## What it does not fix

The integration does not add an import to a tool that has none. If your goal is to remove re-keying entirely, the honest answer is that an export-only tool makes that impossible, and the module can only make the re-keying shorter and safer. The discovery report says this plainly when it applies.

## When to choose it

Choose export-only-tool when you are not ready to change your job tool but want the paper-to-screen step to stop producing errors. When you later move to a tool with an API, the integration changes to api-connector and the surcharge changes with it. That applies to a module that offers api-connector; document-data-entry does not, so with a new tool that can import CSV files it moves to csv-export and the surcharge falls to 0 USD.
