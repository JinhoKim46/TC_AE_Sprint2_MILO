---
title: CSV export and import
section: implementation
updated: 2026-10-01
doc_id: implementation/csv-export
product: general
---
# CSV export and import

The csv-export integration is the simplest way to connect a module to the tool you already use. It needs no live connection, no access key and no change to your tool. This article explains how it works and when it is the right choice.

## How it works

Your tool exports a CSV file, for example the list of bookings or jobs for the day. The module reads that file, does its work, and writes a CSV file back. Your tool imports that file. The exchange runs on a schedule you choose, such as every hour or at the end of the day. There is no live connection between the two systems.

## What you need

Your tool must be able to export and import CSV files. Most spreadsheets, booking widgets and small job tools can. During setup we agree the column names on both sides so that the files always line up.

## Which modules support it

Every module in the catalogue works with csv-export: phone-intake, email-triage, ticket-creation, staff-assignment, document-data-entry and customer-status-updates. For staff-assignment it is the only integration offered.

## Cost

The csv-export integration carries no monthly surcharge. Its line on the rate card is 0 USD. The module's own base price and the setup fee still apply.

## Limits

Because the exchange runs on a schedule, a change in your tool is not seen by the module until the next export. If you need a customer status update to go out within minutes of a change, the api-connector is the better choice. CSV also carries no permissions: anyone who can read the file can read every row, so the file location must be a folder only your staff can see.

## When to choose it

Choose csv-export when your tool has no API, when you want to try a module without touching your tool, or when a delay of up to the export interval does not matter. Many owners start here and move to api-connector later.
