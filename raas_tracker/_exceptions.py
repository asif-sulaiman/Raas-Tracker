"""Typed service refusals — the **status** boundary.

Phase 4-A. This module exists because an HTTP status used to be derived from
the *wording* of a service error message. ``flask_app._state_error_status``
classified refusals like this::

    "invoice already"                  -> 409
    "invoice not produced"             -> 409
    "Invoice not found"                -> 404
    "linked sale " / "linked invoice " -> 404
    everything else                    -> 400

and ``_invoice_item_error`` did the same with ``"remain"``/``"exceed"`` and an
exact-match list. Message wording is not a contract: re-typing "invoice not
produced yet" as "this invoice has not been produced" silently downgraded a
409 to a 400, and because no test asserted the status the drift was invisible.
A status is now carried by the exception's TYPE instead, so rewording a
message can never move it.

Two rules keep this cheap to adopt:

1. **Everything here is a ``ValueError``.** Service functions already raise
   ``ValueError`` and every route already catches it, so a typed refusal keeps
   working in both worlds: an un-migrated ``except ValueError`` handler still
   catches it (and returns 400, the conservative answer), and ``status_for``
   upgrades it to the real status. Nothing turns into a 500.
2. **Only this hierarchy carries a status.** ``status_for`` checks
   ``isinstance(exc, ServiceError)`` rather than duck-typing a ``status_code``
   attribute off an arbitrary exception, so an unrelated error can never
   inherit a status by accident.

Plain validation ``ValueError``s stay plain. Typing them would be churn with no
security benefit and real regression risk: a 400 is already the honest answer
for malformed input. Only refusals whose status was previously decided by
reading the message are typed here.

Like ``_numeric``, this module depends on nothing inside the package so any
service can import it without adding an edge to the dependency graph.
"""


class ServiceError(ValueError):
    """Base for a refusal that already knows which HTTP status it is.

    The base default is 400: an unclassified service refusal is a client fault.
    """

    status_code = 400


class LifecycleConflict(ServiceError):
    """409 — the request is well-formed but conflicts with the current state.

    Distinct from a 400 in the only way that matters to a client: retrying the
    identical request unchanged will never succeed. The state has to move
    first (finish production, free the row, pick a different target).
    """

    status_code = 409


class ResourceMissing(ServiceError):
    """404 — a referenced resource does not exist, or is not this caller's.

    Covers the cross-tenant case as well as the plain missing-row one, which is
    deliberate: "exists but is not yours" must be indistinguishable from "does
    not exist" to the caller.
    """

    status_code = 404


# --------------------------------------------------------------------------- #
# 409 conditions
# --------------------------------------------------------------------------- #


class InvoiceAlreadyAdvanced(LifecycleConflict):
    """The invoice already reached a terminal or later lifecycle state.

    Replaces ``ValueError(f"invoice already {current}")``. The terminal-state
    refusal is its own condition, not a flavour of "not produced": the remedy
    is different and the two must be able to move apart.
    """


class InvoiceNotProduced(LifecycleConflict):
    """A lifecycle step is blocked because the invoice's goods were never built.

    Replaces ``ValueError(_production_blocked_message(...))``. The request is
    valid; the invoice is simply not ready. It shares the 409 of
    :class:`InvoiceAlreadyAdvanced` because both mean "the state has to move
    first", and it is a separate class because the two are told apart by name
    when a route logs or a test asserts them.
    """


class ToleranceExceeded(LifecycleConflict):
    """An invoice line would exceed the PI quantity ceiling.

    Replaces the ``"exceed" in msg`` branch of ``_invoice_item_error``. The
    request conflicts with an agreed commercial limit.
    """


# --------------------------------------------------------------------------- #
# 404 conditions
# --------------------------------------------------------------------------- #


class InvoiceNotFound(ResourceMissing):
    """No invoice with that id (sales.py's own lookups)."""


class LinkedSaleNotFound(ResourceMissing):
    """A sale id referenced by a production run is not this company's."""


class LinkedInvoiceNotFound(ResourceMissing):
    """An invoice id referenced by a production run is not this company's."""


class SaleItemNotFound(ResourceMissing):
    """No PI line with that id on the invoice's sale."""


# --------------------------------------------------------------------------- #
# LC conditions (Phase 4-A, second pass)
# --------------------------------------------------------------------------- #
#
# The LC surface carried three more message classifiers, all of the same
# defect class:
#
#   ``_lc_attach_error``          ``low == "lc not found"`` /
#                                 ``low.startswith("sale ") and
#                                 low.endswith("not found")``  -> 404 else 400
#   ``api_create_lc`` (inline)    ``"already exists" in msg.lower()`` -> 409
#   ``api_move_lc`` (inline)      a five-way substring list -> 409
#
# The third was the worst: three of its five needles ("recipes:",
# "invoices:", "production:") are shared vocabulary taken from the readiness
# payload the message itself is built from, so any future wording that put a
# colon in those words would silently start answering 409 to unrelated
# refusals. The LC readiness text is therefore typed *where it is
# constructed*, in ``lcs.move_lc_stage``.


class LcNumberAlreadyExists(LifecycleConflict):
    """409 — the LC number is already taken for this company.

    Replaces ``ValueError(f"LC number '{n}' already exists for this company")``
    at both the pre-insert scoped lookup and the unique-index race, which must
    not be able to disagree about the status.
    """


class LcNotReadyForShipment(LifecycleConflict):
    """409 — the LC cannot cross into ``shipment_ongoing`` yet.

    Replaces ``ValueError("LC is not ready for shipment: invoices: ...")``.
    """


class LcNotReadyPastShipment(LifecycleConflict):
    """409 — the LC cannot cross into ``payment_due`` yet.

    Replaces ``ValueError("LC is not ready to go past shipment: ...")``. Kept
    separate from :class:`LcNotReadyForShipment` because the two gates carry
    different detail in their messages, and the readiness response the client
    renders is built from that message.
    """


class LcNotFound(ResourceMissing):
    """404 — no letter of credit with that id."""


class LcSaleNotFound(ResourceMissing):
    """404 — the sale an LC attach names is absent **or is not this LC's
    company's**.

    The cross-tenant case is deliberately indistinguishable from the missing
    case: identical class, identical message. The old code raised "sale N
    belongs to a different company" and the route answered 400 while a truly
    absent sale answered 404, which let a caller walk ``sale_id`` values and
    learn which ones exist and belong to someone else. Answering the same 404
    with the same body closes the oracle.
    """


def status_for(exc: BaseException) -> int:
    """The HTTP status for a service refusal: its own, or 400.

    The single place a route should ask. It reads the TYPE, never the message:

    >>> status_for(InvoiceAlreadyAdvanced("invoice already shipped"))
    409
    >>> status_for(InvoiceAlreadyAdvanced("this one has moved on"))
    409
    >>> status_for(ValueError("quantity must be finite and > 0"))
    400
    """
    if isinstance(exc, ServiceError):
        return exc.status_code
    return 400