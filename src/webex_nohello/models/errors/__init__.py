"""The full failure surface, one exception per module.

Every one derives from `WebexNoHelloError` and carries operator-facing remediation, so
`cli.main` has a single base class to catch and a single way to render it
(Articles IV.6 and XII.2).
"""
