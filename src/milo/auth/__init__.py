"""Accounts and sessions through Supabase Auth (SPEC section 23, D88, D92, D136).

Our code stores no passwords. Sign-up, sign-in, confirmation, reset and refresh go through the project's Auth API
with the publishable key; session tokens are verified locally against the JWKS. Nothing here is imported by
`milo.app`, so contract mode loads no HTTP client or JWT library.
"""
