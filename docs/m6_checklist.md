# M6 manual checklist (verifier-manual items, SPEC section 16)

Phase 1a covers the shell. Phase 3 adds the recording and the wired items. Tick each item by hand.

## Keyboard walk-through

Start with `uv run flask --app milo.web.dev:create_dev_app run`, sign in as the seeded owner (see `src/milo/web/dev.py`).

- [ ] Tab from the page start: the first stop is "Skip to content" and it works.
- [ ] Sign-in, sign-up, reset and consent forms: every field and button is reachable in a logical order; Enter submits.
- [ ] Conversation: session panel, search, "New diagnosis", composer, upload label, Send, spec panel and Submit are all reachable.
- [ ] Reply details (`<details>`) opens and closes with Enter or Space.
- [ ] The Submit dialog opens from the keyboard, traps focus, closes with Escape and with Cancel.
- [ ] A visible focus ring shows on every control in light and dark.
- [ ] Below 900 px the spec panel moves under the conversation and nothing scrolls sideways.

## Contrast (WCAG AA, from the CSS tokens in `static/css/app.css`)

Compute each pair with any contrast checker, in light and in dark.

- [ ] Ink on canvas and on surface (4.5:1 or more).
- [ ] Muted text on canvas, surface and selected tint (4.5:1 or more). Note: the owner reference muted `#909090` is about 3.2:1 on white, so the shell uses `#5f5f66` (light) and `#a3a3ad` (dark) for text. Owner to confirm.
- [ ] Accent link text on surface and on tint.
- [ ] Button text on the ink button background.
- [ ] Danger text on surface.
- [ ] Status is a dot plus a text label, never colour alone (chips on `/spec` and the spec panel).

## Behaviour to check once wired (phase 3)

- [ ] Scope refusal appears as an assistant reply with its fixed text and the zero-cost check holds (D105).
- [ ] The scope test set is run against the live gate.
