"""End-to-end smoke tests for v0.0.1 (T1.20).

Two layers:

* :mod:`test_e2e_in_process` runs every component in-process — fastest
  feedback, catches contract bugs.
* :mod:`test_subprocess` exercises the actual install path: the bash
  shims under ``hooks/cc/`` and ``python -m itsme.hooks`` with stdin
  piped in by the OS. Slower; catches packaging / shim bugs the
  in-process tests can't.

What's NOT here: real CC integration. That's a manual runbook —
see ``docs/SMOKE.md``.
"""
