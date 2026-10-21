---
title: API connector
section: implementation
updated: 2026-10-01
doc_id: implementation/api-connector
product: general
---
# API connector

The api-connector integration gives a module a live connection to your tool. Bookings, labels, tickets and status changes are written and read as they happen, with no files in between.

## How it works

Your tool offers an API with an access key. You create a key for MILO in your tool, with only the rights the module needs, and give it to us during setup. The module then reads and writes through the API. If the key is removed, the connection stops and nothing else changes in your tool.

## What you need

Your tool must offer an API with an access key. Setup takes one working day. In that day we connect the module, map the fields, and run the first requests through with you watching.

## Which modules support it

The api-connector is available for phone-intake, email-triage, ticket-creation and customer-status-updates. It is not offered for staff-assignment, which uses csv-export, or for document-data-entry, which uses csv-export or export-only-tool.

## Cost

The api-connector surcharge is 300 USD a month, paid once however many modules share the connection. The module's own base price and the setup fee of 900 USD per module still apply.

## Limits

The module can only do what the key allows. If the key cannot create bookings, phone-intake cannot book. We ask for the smallest set of rights that works, and we tell you which rights each module needs before you create the key.

## When to choose it

Choose api-connector when timing matters, for example a confirmation that should reach the customer within minutes, or when your staff work inside the tool all day and should see new tickets as they arrive. If your tool has no API, the csv-export integration does the same job with a delay.
