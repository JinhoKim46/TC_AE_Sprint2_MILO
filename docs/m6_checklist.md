# M6 manual checklist (verifier-manual items, SPEC section 16)

Phase 1a covered the shell; phase 3 wires it. The automated DOM checks are in
`evals/results/m6/m6_dom_checks.json` (`make record`) and `tests/test_web.py`. Tick each item below by hand.

## Setup

`uv run flask --app milo.web.dev:create_dev_app run` starts the wired app over the in-memory store with the
scripted demo model (no model call, no database). Seeded accounts: `owner@example.test` and
`operator@example.test`, password in `src/milo/web/dev.py`. The three demo messages are `DEMO_MESSAGES` in the
same file; after them the diagnosis is `assessment_ready` and Submit opens.

## Keyboard walk-through

- [ ] Tab from the page start: the first stop is "Skip to content" and it works.
- [ ] Sign-in, sign-up, reset and consent forms: every field and button is reachable in a logical order; Enter submits.
- [ ] Conversation: session panel, search, "New diagnosis", archive buttons, Rename, composer, upload label, Send,
      spec panel and Submit are all reachable. Ctrl+Enter sends from the composer.
- [ ] Reply details (`<details>`) opens and closes with Enter or Space.
- [ ] The approval banner takes focus when it appears; "Keep as is" and "Approve" work from the keyboard.
- [ ] The Submit dialog opens from the keyboard, traps focus, closes with Escape and with Cancel.
- [ ] The Withdraw button on a report asks for confirmation.
- [ ] A visible focus ring shows on every control in light and dark.
- [ ] Below 900 px the spec panel moves under the conversation and nothing scrolls sideways.

## Contrast (WCAG AA, from the CSS tokens in `static/css/app.css`)

Compute each pair with any contrast checker, in light and in dark.

- [ ] Ink on canvas and on surface (4.5:1 or more).
- [ ] Muted text on canvas, surface and selected tint (4.5:1 or more). The owner reference muted `#909090` is
      about 3.2:1 on white, so text uses `#5f5f66` (light) and `#a3a3ad` (dark). Owner to confirm.
- [ ] Accent link text on surface and on tint.
- [ ] Button text on the ink button background.
- [ ] Danger text on surface: light `#c53a3a` is 5.2:1 on white (the owner's `#D64545` was 4.38:1, verify
      m6-shell F4); dark `#ff7a7a`. `tests/test_web.py` asserts the light value.
- [ ] Status is a dot plus a text label, never colour alone (chips on `/spec` and the spec panel; "missing" has a
      hollow dot).

## Behaviour, wired (phase 3)

- [ ] A message shows the reply, and the drawer lists progress, sources, tool calls with arguments and results,
      and the model cost; the estimate is labelled separately in the panel.
- [ ] Facts appear in the panel with status and the verbatim quote while the turn runs.
- [ ] A second message to the same diagnosis while a reply is being written gives the "still being written"
      toast (409); a message to another diagnosis meanwhile gives the "wait" toast (429).
- [ ] Upload of a `.txt` and of a text PDF starts a turn; a file over 5 MB is refused.
- [ ] Sign out, sign in again: the conversation and the panel are restored (the drawer of earlier replies says
      details are shown for replies given in this visit).
- [ ] Scope refusal appears as an assistant reply with its fixed text and the zero-cost check holds (D105; needs
      M5G on the branch).
- [ ] The scope test set is run against the live gate (M5G).
