"""Stdio MemPalace adapter — T1.13.5.

Speaks JSON-RPC 2.0 over a subprocess's stdin/stdout to a real MemPalace
MCP server. This is the v0.0.1 GA-blocking missing piece: with the
in-memory adapter, drawers vanish on every MCP server restart, but
``memory.stored`` events live in the persistent ring → the router's
dedup logic skips re-routing → ``ask`` silently misses everything from
prior CC sessions.

Wire model
----------

::

    +-------------------+         stdin/stdout (NDJSON)         +---------------------+
    | itsme MCP server  |  <--- jsonrpc 2.0 line-delimited --->  | mempalace.mcp_server|
    | (this process)    |                                         | (subprocess)        |
    |   StdioMemPalace  |                                         | python3 -m ...      |
    |   Adapter         |                                         | (ChromaDB-backed)   |
    +-------------------+                                         +---------------------+

Each ``write`` / ``search`` is one ``tools/call`` request awaiting one
response. Calls are serialised under an internal lock so a single
in-flight request keeps stdin/stdout framing trivial — premature
optimisation territory to push for parallel calls when the upstream
ChromaDB write is the bottleneck anyway.

Failure model
-------------

* Subprocess spawn failure → :class:`MemPalaceConnectError` at
  ``__init__``. Caller decides whether to fall back to the in-memory
  adapter (the v0.0.1 ``build_default_memory`` does, with a warning).
* Subprocess crash mid-call → :class:`MemPalaceConnectError`. Future
  calls also raise; v0.0.1 does not auto-respawn (would mask deeper
  bugs). Tracked: v0.0.2 may add a supervised-restart wrapper.
* Tool returns ``{"error": "No palace found ..."}`` →
  :meth:`StdioMemPalaceAdapter.search` returns ``[]`` (an empty palace
  isn't an error from the caller's POV). Any *other* ``{"error": ...}``
  payload from search raises :class:`MemPalaceConnectError` so callers
  see the failure instead of an empty result list. :meth:`write`
  raises :class:`MemPalaceWriteError` whenever the underlying call
  doesn't acknowledge the write.
* Duplicate detection (MemPalace's built-in ``check_duplicate`` fires
  before each write) → returned as a successful write that re-uses the
  existing drawer's id. Rationale: from the agent's perspective
  "remember this thing I already remembered" is idempotent, not a
  failure.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
import threading
from collections.abc import Mapping
from typing import Any, Final

from itsme.core.adapters.mempalace import (
    MemPalaceHit,
    MemPalaceWriteResult,
)

_logger = logging.getLogger(__name__)


# --------------------------------------------------------------------- errors


class MemPalaceConnectError(RuntimeError):
    """The MemPalace subprocess could not be reached.

    Raised at ``__init__`` (spawn failed, handshake failed) or on a
    subsequent call if the subprocess has died. Distinct from
    :class:`MemPalaceWriteError` so callers can decide to fall back to
    a different adapter on connection issues without papering over
    real write failures.
    """


class MemPalaceWriteError(RuntimeError):
    """A ``mempalace_add_drawer`` tool call returned a failure payload."""


# --------------------------------------------------------------------- defaults

#: Default command line. Mirrors the ``mcpServers.mempalace.command``
#: shape from MemPalace's own plugin manifest.
DEFAULT_COMMAND: Final[tuple[str, ...]] = ("python3", "-m", "mempalace.mcp_server")

#: ``initialize`` round-trip budget. Cold-starts on slow disks have been
#: observed at ~3s; 10s leaves room for ChromaDB lazy-loading without
#: hanging the MCP boot indefinitely.
DEFAULT_HANDSHAKE_TIMEOUT_S: Final[float] = 10.0

#: Per-call (write / search) timeout. ChromaDB queries on a multi-MB
#: palace land well under 1s; a 30s ceiling guards against runaway
#: writes without breaking realistic workloads.
DEFAULT_CALL_TIMEOUT_S: Final[float] = 30.0


# --------------------------------------------------------------------- adapter


class StdioMemPalaceAdapter:
    """Persistent MemPalace backend — speaks JSON-RPC over a child process.

    This is the production adapter: the in-memory one (in
    :mod:`itsme.core.adapters.mempalace`) is for tests and dev. Use
    :func:`build_default_memory` with ``ITSME_MEMPALACE_BACKEND=stdio``
    to wire it into the MCP server.

    Args:
        command: Argv list to spawn. Defaults to
            :data:`DEFAULT_COMMAND` (the upstream MemPalace plugin
            shape). Override via ``$ITSME_MEMPALACE_COMMAND`` (space-
            separated) when calling :func:`from_env`.
        handshake_timeout_s: How long to wait for ``initialize`` to
            return before giving up.
        call_timeout_s: How long to wait for any ``tools/call``.
        env: Environment overrides for the subprocess. Useful in tests
            for ``MEMPALACE_PALACE_PATH``. ``None`` inherits the parent
            env unchanged.
    """

    def __init__(
        self,
        *,
        command: tuple[str, ...] | list[str] | None = None,
        handshake_timeout_s: float = DEFAULT_HANDSHAKE_TIMEOUT_S,
        call_timeout_s: float = DEFAULT_CALL_TIMEOUT_S,
        env: Mapping[str, str] | None = None,
    ) -> None:
        cmd = list(command) if command is not None else list(DEFAULT_COMMAND)
        if not cmd:
            raise ValueError("command must be a non-empty argv list")

        # Build the merged child env *first* so the PATH lookup honours
        # any caller-supplied PATH override (e.g. tests pointing at a
        # private venv). ``None`` still means "inherit unchanged".
        child_env: dict[str, str] | None = {**os.environ, **dict(env)} if env is not None else None
        lookup_path = (child_env or os.environ).get("PATH")

        # Resolve the executable up-front so a missing interpreter fails
        # loud at __init__ rather than on the first ``write``. Also
        # gives us a clearer error message than Popen's FileNotFoundError.
        if shutil.which(cmd[0], path=lookup_path) is None:
            raise MemPalaceConnectError(
                f"executable not found on PATH: {cmd[0]!r} "
                f"(is MemPalace installed and on the same Python as this process?)"
            )

        self._command: list[str] = cmd
        self._call_timeout_s = call_timeout_s
        self._lock = threading.Lock()
        self._req_id = 0
        # Buffer for stderr drained off-thread so the child doesn't
        # block on a full pipe during long sessions.
        self._stderr_lines: list[str] = []
        self._stderr_lock = threading.Lock()
        self._stderr_thread: threading.Thread | None = None

        try:
            # ``child_env`` was merged once above so the PATH lookup
            # used the same env we hand the subprocess.
            self._proc = subprocess.Popen(  # noqa: S603 — argv is operator-controlled
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,  # line-buffered: matches NDJSON framing
                text=True,
                env=child_env,
            )
        except OSError as exc:
            raise MemPalaceConnectError(f"failed to spawn {cmd!r}: {exc}") from exc

        # Pipes are guaranteed non-None when stdin/stdout/stderr=PIPE.
        assert self._proc.stdin is not None
        assert self._proc.stdout is not None
        assert self._proc.stderr is not None

        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            name="mempalace-stderr-drain",
            daemon=True,
        )
        self._stderr_thread.start()

        try:
            self._initialize(timeout_s=handshake_timeout_s)
        except Exception:
            # If the handshake fails we own the subprocess — clean up
            # before propagating so we don't leak a zombie + pipes.
            self._terminate_quietly()
            raise

    # ----------------------------------------------------------- factories

    @classmethod
    def from_env(cls) -> StdioMemPalaceAdapter:
        """Construct from ``$ITSME_MEMPALACE_*`` environment variables.

        Recognised vars:

        * ``ITSME_MEMPALACE_COMMAND`` — space-separated argv. Defaults
          to ``"python3 -m mempalace.mcp_server"``.
        * ``ITSME_MEMPALACE_HANDSHAKE_TIMEOUT`` — float seconds.
        * ``ITSME_MEMPALACE_CALL_TIMEOUT`` — float seconds.

        Useful when wiring the MCP server: keeps env-handling out of
        :func:`build_default_memory`'s critical path.
        """
        raw_cmd = os.environ.get("ITSME_MEMPALACE_COMMAND", "").strip()
        # ``shlex.split`` so paths with spaces survive proper quoting,
        # e.g. ``"/Users/name with space/.venv/bin/python" -m mempalace``.
        # Naïve ``str.split`` would shred that into 4 tokens.
        cmd = tuple(shlex.split(raw_cmd)) if raw_cmd else None
        return cls(
            command=cmd,
            handshake_timeout_s=_env_float(
                "ITSME_MEMPALACE_HANDSHAKE_TIMEOUT", DEFAULT_HANDSHAKE_TIMEOUT_S
            ),
            call_timeout_s=_env_float("ITSME_MEMPALACE_CALL_TIMEOUT", DEFAULT_CALL_TIMEOUT_S),
        )

    # ----------------------------------------------------------- public API

    def write(
        self,
        *,
        content: str,
        wing: str,
        room: str,
        source_file: str | None = None,
    ) -> MemPalaceWriteResult:
        """Persist *content* via ``mempalace_add_drawer``.

        Duplicate handling: MemPalace runs its own similarity check
        before each write and refuses near-dupes. We treat that as
        success and return the existing drawer's id — see the module
        docstring's "Failure model" section.
        """
        if not content.strip():
            raise ValueError("content must be non-empty")
        wing_clean = wing.strip()
        room_clean = room.strip()
        if not wing_clean or not room_clean:
            raise ValueError("wing and room are required")

        result = self._call_tool(
            "mempalace_add_drawer",
            {
                "wing": wing_clean,
                "room": room_clean,
                "content": content,
                "added_by": "itsme",
                **({"source_file": source_file} if source_file else {}),
            },
        )

        if result.get("success"):
            return MemPalaceWriteResult(
                drawer_id=str(result["drawer_id"]),
                wing=str(result.get("wing", wing_clean)),
                room=str(result.get("room", room_clean)),
            )

        if result.get("reason") == "duplicate":
            matches = result.get("matches") or []
            first = matches[0] if matches else {}
            existing_id = first.get("id")
            if existing_id:
                _logger.info(
                    "itsme: mempalace deduped write into wing=%s room=%s; " "reusing drawer_id=%s",
                    wing_clean,
                    room_clean,
                    existing_id,
                )
                return MemPalaceWriteResult(
                    drawer_id=str(existing_id),
                    wing=str(first.get("wing", wing_clean)),
                    room=str(first.get("room", room_clean)),
                )

        # Anything else: surface the real error so the caller's event
        # ring carries the failure.
        msg = result.get("error") or result.get("reason") or "unknown failure"
        raise MemPalaceWriteError(f"mempalace_add_drawer failed: {msg}")

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        wing: str | None = None,
        room: str | None = None,
    ) -> list[MemPalaceHit]:
        """Query via ``mempalace_search``. Returns at most *limit* hits.

        An "empty palace" response (``{"error": "No palace found ..."}``)
        is treated as zero hits, not as a failure — the agent shouldn't
        crash because the user hasn't filed anything yet.
        """
        if limit <= 0:
            return []

        args: dict[str, Any] = {"query": query, "limit": limit}
        if wing:
            args["wing"] = wing
        if room:
            args["room"] = room

        result = self._call_tool("mempalace_search", args)

        # MemPalace returns ``{"error": "No palace found ..."}`` when the
        # user's palace hasn't been initialised yet — that's the only
        # error we want to silently treat as "no hits". Anything else
        # (mis-typed wing/room arg, ChromaDB blew up, etc.) should bubble
        # up so ``ask`` can surface it instead of pretending to be empty.
        error = result.get("error")
        if error is not None:
            if isinstance(error, str) and error.startswith("No palace found"):
                _logger.debug("itsme: mempalace_search empty-palace: %r", error)
                return []
            raise MemPalaceConnectError(f"mempalace_search failed: {error!r}")

        hits: list[MemPalaceHit] = []
        for raw in result.get("results", [])[:limit]:
            content = raw.get("text", "")
            if not isinstance(content, str) or not content:
                continue
            wing_v = str(raw.get("wing", "unknown"))
            room_v = str(raw.get("room", "unknown"))
            score = float(raw.get("similarity", 0.0))
            score = max(0.0, min(1.0, score))  # MemPalaceHit constraint
            # MemPalace's ``mempalace_search`` does not return chromadb
            # ids today (only the document text + metadata). We need a
            # stable opaque identifier for the ``MemPalaceHit`` contract,
            # so synthesise one keyed on (wing, room, content) — same
            # content always gets the same pseudo-id, so hooks /
            # observability layers can deduplicate naturally. When
            # MemPalace's API exposes the real id we'll switch over.
            digest = hashlib.sha256(f"{wing_v}|{room_v}|{content}".encode()).hexdigest()[:16]
            hits.append(
                MemPalaceHit(
                    drawer_id=f"mp-search:{wing_v}:{room_v}:{digest}",
                    wing=wing_v,
                    room=room_v,
                    content=content,
                    score=score,
                )
            )
        return hits

    def close(self) -> None:
        """Shut down the MemPalace subprocess. Idempotent."""
        self._terminate_quietly()

    # ----------------------------------------------------------- internals

    def _initialize(self, *, timeout_s: float) -> None:
        """Send ``initialize`` + the ``notifications/initialized`` ack.

        Runs once at construction. A successful return guarantees the
        subprocess accepted our protocol version and is ready for
        ``tools/call``.
        """
        resp = self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "itsme", "version": "0.0.1"},
            },
            timeout_s=timeout_s,
        )
        if "error" in resp:
            raise MemPalaceConnectError(f"initialize handshake failed: {resp['error']}")
        # The notifications/initialized notify is fire-and-forget per
        # the MCP spec — no id, no response expected. Send it on its
        # own line so the server's loop sees it.
        self._send_notification("notifications/initialized")

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke ``tools/call`` and unwrap MemPalace's text-block reply.

        MemPalace serialises each tool's return dict as JSON inside a
        single ``text`` content block. We pull that block out and
        re-parse it so the rest of the adapter speaks plain dicts.
        """
        resp = self._request(
            "tools/call",
            {"name": name, "arguments": arguments},
            timeout_s=self._call_timeout_s,
        )
        if "error" in resp:
            err = resp["error"]
            raise MemPalaceConnectError(
                f"tools/call {name!r} failed: code={err.get('code')} "
                f"message={err.get('message')!r}"
            )
        result = resp.get("result", {})
        content = result.get("content") or []
        if not content:
            return {}
        first = content[0]
        text = first.get("text", "")
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MemPalaceConnectError(
                f"tools/call {name!r} returned non-JSON text: {text[:200]!r}"
            ) from exc
        if not isinstance(parsed, dict):
            raise MemPalaceConnectError(f"tools/call {name!r} returned non-object JSON: {parsed!r}")
        return parsed

    def _request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout_s: float,
    ) -> dict[str, Any]:
        """Send a JSON-RPC request and read exactly one response line.

        Single in-flight: the lock guarantees we never interleave two
        write→read pairs, which is what keeps the framing trivial. If
        you need parallelism later, switch to id-keyed multiplexing.
        """
        rid = self._next_id()
        message = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        line = json.dumps(message) + "\n"
        with self._lock:
            self._check_alive()
            assert self._proc.stdin is not None
            try:
                self._proc.stdin.write(line)
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise MemPalaceConnectError(f"write to mempalace stdin failed: {exc}") from exc
            response_line = self._readline_with_timeout(timeout_s=timeout_s)

        try:
            parsed = json.loads(response_line)
        except json.JSONDecodeError as exc:
            raise MemPalaceConnectError(
                f"non-JSON line from mempalace: {response_line[:200]!r}"
            ) from exc
        if not isinstance(parsed, dict):
            raise MemPalaceConnectError(f"non-object response from mempalace: {parsed!r}")
        if parsed.get("id") != rid:
            # Out-of-order ids in single-in-flight mode = protocol
            # corruption. Bail rather than try to recover.
            raise MemPalaceConnectError(f"id mismatch: sent {rid}, got {parsed.get('id')}")
        return parsed

    def _send_notification(self, method: str) -> None:
        """Fire-and-forget JSON-RPC notify (no id, no response)."""
        message = {"jsonrpc": "2.0", "method": method}
        line = json.dumps(message) + "\n"
        with self._lock:
            self._check_alive()
            assert self._proc.stdin is not None
            try:
                self._proc.stdin.write(line)
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise MemPalaceConnectError(f"write to mempalace stdin failed: {exc}") from exc

    def _readline_with_timeout(self, *, timeout_s: float) -> str:
        """``readline`` with a wall-clock budget.

        Python's stdlib ``readline`` is unconditionally blocking; we run
        it in a worker thread and join with a timeout so the caller
        actually gets the timeout it asked for.
        """
        assert self._proc.stdout is not None
        result: list[str] = []
        err: list[BaseException] = []

        def _read() -> None:
            try:
                assert self._proc.stdout is not None
                result.append(self._proc.stdout.readline())
            except BaseException as exc:  # noqa: BLE001 — bubble through .join
                err.append(exc)

        thread = threading.Thread(target=_read, daemon=True)
        thread.start()
        thread.join(timeout=timeout_s)
        if thread.is_alive():
            # The reader is stuck in ``stdout.readline()``. Python can't
            # cancel a thread, so kill the subprocess (which closes
            # stdout) — that unblocks the readline, the thread observes
            # EOF, returns, and we join it. Without this teardown the
            # leaked thread would race with the next request's reader for
            # the next response line, causing wedged-looking failures
            # that are murder to debug.
            self._terminate_quietly()
            thread.join(timeout=1.0)
            raise MemPalaceConnectError(f"mempalace did not respond within {timeout_s}s")
        if err:
            raise MemPalaceConnectError(f"reading mempalace stdout raised: {err[0]}") from err[0]
        line = result[0] if result else ""
        if not line:
            # EOF → child died.
            self._raise_for_dead_child("eof on stdout")
        return line

    def _drain_stderr(self) -> None:
        """Reader loop for the child's stderr; populates ``_stderr_lines``.

        MemPalace logs to stderr on every tool invocation; if we don't
        drain we'll eventually block the child on a full pipe. We keep
        the last 100 lines so they can be surfaced on connect failures.
        """
        assert self._proc.stderr is not None
        for raw in self._proc.stderr:
            line = raw.rstrip("\n")
            with self._stderr_lock:
                self._stderr_lines.append(line)
                if len(self._stderr_lines) > 100:
                    del self._stderr_lines[: len(self._stderr_lines) - 100]

    def _next_id(self) -> int:
        """Monotonic JSON-RPC request id. Lock-free — only the writer
        thread ever touches it (calls are serialised by ``_lock``)."""
        self._req_id += 1
        return self._req_id

    def _check_alive(self) -> None:
        if self._proc.poll() is not None:
            self._raise_for_dead_child(f"exit code {self._proc.returncode}")

    def _raise_for_dead_child(self, why: str) -> str:
        with self._stderr_lock:
            stderr_tail = "\n".join(self._stderr_lines[-10:])
        raise MemPalaceConnectError(
            f"mempalace subprocess gone ({why})\nstderr tail:\n{stderr_tail}"
        )

    def _terminate_quietly(self) -> None:
        """Best-effort shutdown. Used by ``close`` and the __init__ rollback."""
        if self._proc.poll() is not None:
            return
        try:
            if self._proc.stdin is not None:
                with contextlib.suppress(OSError):
                    self._proc.stdin.close()
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    self._proc.wait(timeout=2)
        except Exception as exc:  # noqa: BLE001 — last-ditch shutdown
            print(
                f"itsme: mempalace shutdown raised: {exc}",
                file=sys.stderr,
            )

    # Mirror the other adapter's diagnostic surface so callers can probe
    # without sniffing for class names.
    def __repr__(self) -> str:  # pragma: no cover — debug aid
        alive = "alive" if self._proc.poll() is None else f"dead({self._proc.returncode})"
        return f"<StdioMemPalaceAdapter cmd={self._command!r} {alive}>"


# --------------------------------------------------------------------- helpers


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        _logger.warning("itsme: ignoring bad %s=%r, using %s", name, raw, default)
        return default
