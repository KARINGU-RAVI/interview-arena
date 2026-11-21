"""Run graph steps in a background thread, one at a time per session (no Streamlit here).

Why: Streamlit stops a running script when the browser refreshes or a rerun is requested,
by raising StopException/RerunException at the next st.* call. If the graph runs inside the
script, that can cut it off BETWEEN two nodes and leave the checkpoint half-way (next node
pending, no pause) — the "session stopped part-way" bug. A worker thread never calls st.*,
so Streamlit can't interrupt it: every step always finishes at a clean checkpointed pause.

Rules:
- One step per session at a time: submit() returns False while a step is running (busy),
  so double clicks / Ctrl+Enter twice can't resume the graph twice.
- The page only polls status(); it never runs the graph itself.
- Each finished node is recorded; a node that runs again for the same question is logged
  as a REPEATED step (e.g. an automatic resume), and counted for the session details.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import config

log = logging.getLogger("interview_arena.worker")

_POOL = ThreadPoolExecutor(max_workers=config.GRAPH_WORKER_THREADS, thread_name_prefix="graph-step")


@dataclass
class StepState:
    running: bool = False
    label: str = ""
    started: float = 0.0
    done_nodes: list[str] = field(default_factory=list)  # nodes finished in the current/last step
    error: BaseException | None = None  # last step's exception (until consumed)
    seen: set = field(default_factory=set)  # (node, question index) finished before
    repeats: int = 0  # nodes that ran again for the same question
    auto_resumed: bool = False  # silent recovery already tried once


_steps: dict[str, StepState] = {}
_lock = threading.Lock()


def _state(sid: str) -> StepState:
    with _lock:
        return _steps.setdefault(sid, StepState())


def is_known(sid: str) -> bool:
    with _lock:
        return sid in _steps


def is_busy(sid: str) -> bool:
    return _state(sid).running


def status(sid: str) -> StepState:
    return _state(sid)


def take_error(sid: str) -> BaseException | None:
    """Return the last step's error once (then clear it)."""
    st_ = _state(sid)
    with _lock:
        err, st_.error = st_.error, None
    return err


def submit(sid: str, graph, payload: Any, config: dict, context, label: str) -> bool:
    """Start one graph step in the background. Returns False if this session is busy."""
    st_ = _state(sid)
    with _lock:
        if st_.running:
            log.debug("ignored %r: a step is already running | sid %s", label, sid[:8])
            return False
        st_.running, st_.label, st_.started = True, label, time.perf_counter()
        st_.done_nodes, st_.error = [], None
    _POOL.submit(_run, sid, graph, payload, config, context)
    return True


def _step_key(changes) -> int | None:
    """Which question a finished node belongs to, read from the node's own update:
    ask_question adds an answer (count = question number); evaluate_answer moves
    current_index forward. Other nodes run once per session (None)."""
    changes = changes or {}
    if "current_index" in changes:
        return changes["current_index"]
    if "answers" in changes:
        return len(changes["answers"])
    return None


def _run(sid: str, graph, payload, config, context) -> None:
    st_ = _state(sid)
    log.debug("graph start | sid %s | %r", sid[:8], st_.label)
    try:
        for update in graph.stream(payload, config, context=context, stream_mode="updates"):
            for node, changes in update.items():
                if node == "__interrupt__":
                    continue
                key = (node, _step_key(changes))
                if key in st_.seen and node not in ("start_gate", "send_gate", "deliver"):
                    st_.repeats += 1
                    log.warning("REPEATED step %s (question %s) | sid %s", node, key[1], sid[:8])
                st_.seen.add(key)
                st_.done_nodes.append(node)
        log.debug(
            "graph end | sid %s | %.1fs | nodes %s | next %s",
            sid[:8],
            time.perf_counter() - st_.started,
            st_.done_nodes,
            graph.get_state(config).next,
        )
    except BaseException as exc:  # recorded for the page to show; the checkpoint stays valid
        log.warning("graph step failed | sid %s | %s", sid[:8], type(exc).__name__)
        st_.error = exc
    finally:
        st_.running = False


def mark_auto_resumed(sid: str) -> bool:
    """True the first time only — silent recovery is attempted once per session."""
    st_ = _state(sid)
    with _lock:
        if st_.auto_resumed:
            return False
        st_.auto_resumed = True
        return True


def forget(sid: str) -> None:
    with _lock:
        _steps.pop(sid, None)
