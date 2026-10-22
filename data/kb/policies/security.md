---
title: Security policy
section: policies
updated: 2026-10-01
doc_id: policies/security
product: general
---
# Security policy

This policy explains how MILO keeps one owner's interview separate from another's, how it treats text that tries to give the assistant orders, and what it records.

## One account, one session

Access is by invitation only. An invitation is a one-time link that lets you set a password, and it stops working after it has been used or after it expires. Each owner account has exactly one session. Signing out saves the conversation; signing in again restores it. Several failed sign-in attempts in a row lock the account for a short time.

## Your identity comes from your sign-in

The assistant never takes your identity from the text of a conversation. Who you are is decided by the account you signed in with, and every record it reads or writes is looked up for that account only. A message that says to continue as a different owner, or that gives another owner's session or submission number, does not change who you are. The request is refused and an audit entry is written.

## Text is data, not instructions

Your messages, the documents you upload and the articles the assistant reads from the knowledge base are shown to the assistant as data. A line in a document that tells the assistant to change a status or a price is not followed, and neither is a line in an article that does the same. The assistant may quote it back to you as something it found, but it does not act on it. Statuses are set by the software, prices by the rate card, and neither can be changed by anything written in a message.

## Tools the assistant can use

The assistant can search the knowledge base, show your current facts, run the cost calculation, record a fact and start a withdrawal. Each of these is classed as read, write or destructive. A destructive action needs your approval. In a turn that looks like it contains an injected instruction, destructive actions are refused outright and price-related facts are not written.

## Input limits

A message may not be empty and may not be very long, and it may not contain control characters. An upload must be a PDF or a text file within the size limit. Messages that break these rules are rejected with a plain explanation, not processed in part.

## What is logged

Every turn writes a trace record: which steps ran, how long each took, which decisions the policy made, and how many tokens the model used. Your message text is logged as a hash and a length, not as text. Submissions, approvals, withdrawals, failed sign-ins, operator views and deletions go to an audit log.

## What we do not claim

This policy describes what the software does. It is not a certification and it does not promise that no attack can succeed. The build includes a set of attack tests and reports their results as measured.
