---
title: CRM connector
section: implementation
updated: 2026-10-01
doc_id: implementation/crm-connector
product: general
---
# CRM connector

The crm-connector integration is a standard connector with field mapping for businesses that keep their customers in a CRM. It is offered for the ticket-creation module, so that a ticket created by MILO appears as a record in your CRM.

## How it works

During setup we map each ticket field to a field in your CRM: the customer, the address, the request type, the requested date and the status. Once mapped, every ticket the module creates is written to your CRM, and status changes flow back. Your CRM stays the system of record; the module writes to it, it does not replace it.

## What you need

Setup takes two working days. The first day is the field mapping and the connection; the second day is checking real tickets with you and adjusting the mapping where your CRM uses different values.

## Which modules support it

The crm-connector is offered for ticket-creation only. Other modules reach your CRM indirectly, through the ticket that ticket-creation writes.

## Cost

The crm-connector surcharge is 450 USD a month. The ticket-creation base price of 290 USD a month and the setup fee of 900 USD per module still apply.

## Limits

A connector can only map fields that exist on both sides. If your CRM has no field for a requested date, that value stays on the ticket and does not appear in the CRM. We list any unmapped fields at the end of setup so that you can decide whether to add them in your CRM.

## When to choose it

Choose crm-connector when your sales or office staff already work in the CRM and should see jobs there. If your CRM can export and import CSV files and the delay is acceptable, csv-export is a cheaper way to start, and you can move to the connector later.
