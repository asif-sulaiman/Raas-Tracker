"""P1-9: concurrent regression coverage for the per-thread actor state.

The rest of the suite is single-threaded, so every ``threading.local`` guarantee
in the app is only ever exercised sequentially. That matters because the actor
label is written to ``audit_logs.user_id`` — the single field that says *who*
performed a security-relevant action — and it is stored per thread precisely
because waitress serves from a thread pool.

WHAT IS EXERCISED, AND WHY NOT HTTP

The layer under test is ``raas_tracker.audit._audit_state`` (a
``threading.local``) reached through ``set_audit_actor`` /
``get_audit_actor`` / ``clear_audit_actor``. The Flask test client is NOT
thread-safe — its cookie jar is shared mutable state, so concurrent posts
through one client would be testing the test client, not the app. These tests
therefore drive the resolver directly, one connection per thread, with a
``threading.Barrier`` forcing all threads to be inside their critical section
at the same moment. That is the real waitress topology: N pooled threads, each
holding its own actor, serving requests concurrently.

Each thread opens its own ``psycopg`` connection (a connection is likewise not
safe to share across threads) and asserts the rows it writes are attributed to
its own actor and to nobody else's.

This is NEW COVERAGE for a bug class, not a fix for an observed bug, so it is
expected to pass against current code. If it ever goes red, that is a genuine
concurrency defect, not a flaky assertion.
"""
import threading

from chem_stock import get_connection
from raas_tracker.audit import (
    clear_audit_actor,
    get_audit_actor,
    log_audit_action,
    set_audit_actor,
)

THREADS = 8
# Generous: the barrier simply forces overlap, and a slow box should not be
# reported as a defect.
BARRIER_TIMEOUT = 30


def _run_threads(worker, count=THREADS):
    """Run `worker(index)` on `count` threads, all released simultaneously.

    Returns the per-thread results in thread order. Any exception raised in a
    worker is re-raised here rather than swallowed on the thread.
    """
    barrier = threading.Barrier(count, timeout=BARRIER_TIMEOUT)
    results = [None] * count
    errors = []

    def _run(index):
        try:
            results[index] = worker(index, barrier)
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)
            # Unblock the barrier so the other threads are not left waiting.
            try:
                barrier.abort()
            except Exception:
                pass

    threads = [threading.Thread(target=_run, args=(i,), daemon=True)
               for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=BARRIER_TIMEOUT * 2)
        assert not t.is_alive(), "worker thread hung past the join timeout"
    assert not errors, f"worker raised: {errors[0]!r}"
    return results


def test_each_thread_reads_back_its_own_actor():
    """N threads set N distinct actors; every read-back is its own.

    A shared (non-thread-local) store would make at least one thread observe
    another thread's identity — the exact defect that made a public
    forgot-password get attributed to the previous request's user.
    """

    def _worker(index, barrier):
        set_audit_actor(f"actor-{index}")
        barrier.wait()          # every actor is now set, concurrently
        observed = get_audit_actor()
        barrier.wait()          # all reads done before any teardown
        clear_audit_actor()
        return observed

    observed = _run_threads(_worker)
    assert observed == [f"actor-{index}" for index in range(THREADS)]


def test_clear_in_one_thread_does_not_disturb_another():
    """Teardown is per thread: clearing here must not erase a peer mid-flight.

    This is the shape of the P0 defect at its worst — a pooled thread dropping
    the identity another thread is still relying on.
    """
    teardown_done = threading.Event()

    def _worker(index, barrier):
        if index != 0:
            set_audit_actor(f"actor-{index}")
            barrier.wait()
            assert teardown_done.wait(timeout=BARRIER_TIMEOUT), \
                "peer never finished clearing its own actor"
            return get_audit_actor()

        # Thread 0 clears only its own state, then releases the others.
        set_audit_actor("actor-0")
        barrier.wait()
        clear_audit_actor()
        assert get_audit_actor() == "system", "cleared actor survived teardown"
        teardown_done.set()
        return None

    observed = _run_threads(_worker)
    for index in range(1, THREADS):
        assert observed[index] == f"actor-{index}"


def test_concurrent_audit_rows_keep_their_own_actor(db, pg_dsn):
    """Concurrent writers must not cross-attribute their audit rows.

    End-to-end over the real database: each thread writes its own probe row
    through ``log_audit_action`` (which resolves the actor from the
    thread-local when none is passed) and the resulting rows must each name
    that thread's actor.
    """

    def _worker(index, barrier):
        conn = get_connection(pg_dsn)
        try:
            set_audit_actor(f"actor-{index}")
            barrier.wait()
            log_audit_action(conn, "CONCURRENT_PROBE", "probe", index)
            barrier.wait()
            return get_audit_actor()
        finally:
            clear_audit_actor()
            conn.close()

    _run_threads(_worker)

    rows = db.execute(
        "SELECT entity_id, user_id FROM audit_logs "
        "WHERE action = 'CONCURRENT_PROBE' ORDER BY entity_id"
    ).fetchall()
    assert rows == [(i, f"actor-{i}") for i in range(THREADS)], \
        f"audit rows cross-attributed: {rows!r}"


def test_unset_actor_defaults_to_system_not_a_peer(pg_dsn):
    """A thread that never set an actor must not inherit one from a peer.

    `system` is the documented fallback for work with no verified credential
    (`audit.get_audit_actor`). Reading `actor-0` here would mean the store is
    shared, so this is the read-side counterpart of the isolation tests above.
    """
    holder_set = threading.Event()
    peers_read = threading.Event()

    def _worker(index, barrier):
        barrier.wait()
        if index == 0:
            set_audit_actor("actor-0")
            holder_set.set()
            # Hold the actor live until every peer has read.
            assert peers_read.wait(timeout=BARRIER_TIMEOUT)
            return get_audit_actor()
        # Peers never set an actor, and read while actor-0 is live.
        assert holder_set.wait(timeout=BARRIER_TIMEOUT)
        observed = get_audit_actor()
        with _peers_lock:
            peers_done.append(index)
            if len(peers_done) == THREADS - 1:
                peers_read.set()
        return observed

    peers_done = []
    _peers_lock = threading.Lock()
    observed = _run_threads(_worker)
    assert observed[0] == "actor-0"
    assert observed[1:] == ["system"] * (THREADS - 1)


def test_falsy_actor_is_attributed_as_anonymous(db):
    """`set_audit_actor(None)` records `anonymous`, not the `system` fallback.

    The branch that distinguishes an unauthenticated write from a trusted
    background job (P0-3). Sequential by design: the point here is the value,
    not the isolation — the isolation is what the threaded tests above cover.
    """
    from raas_tracker.audit import ANONYMOUS_ACTOR

    try:
        set_audit_actor(None)
        assert get_audit_actor() == ANONYMOUS_ACTOR
        log_audit_action(db, "ANON_PROBE", "probe", 1)
        user_id = db.execute(
            "SELECT user_id FROM audit_logs WHERE action = 'ANON_PROBE' "
            "ORDER BY id DESC LIMIT 1").fetchone()[0]
        assert user_id == ANONYMOUS_ACTOR
    finally:
        clear_audit_actor()
