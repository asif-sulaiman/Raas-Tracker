"""Numeric guards for the service layer — the **output** boundary.

Phase 2-B. One module, no dependencies inside the package, imported by
``sales``, ``stock``, ``uploads`` and ``recipes``.

It lives here rather than in any one of those four because those four import
each other: ``recipes`` imports ``stock``, ``uploads`` imports ``stock``,
``sales`` imported ``stock`` for this function. A shared guard parked in a
module that has to be imported for an unrelated reason makes the dependency
graph — and the audit surface — depend on which service happens to be the
oldest. ``_numeric`` depends on nothing, so it can be imported by any of them
without adding an edge.
"""
import math
from typing import Any


def finite_values(*values: Any) -> None:
    """Raise ValueError if any supplied value is NaN, Infinity or -Infinity.

    Phase 2-B, the **output** boundary. Phase 2-A's two walls — the
    ``_FiniteJSONProvider`` and ``allow_inf_nan=False`` — are both properties of
    one ingress: a Flask JSON request body. Every other caller walks past them
    (a service calling another service, a spreadsheet cell, a CLI argument, a
    JSON file on disk, a test). And ``inf <= 0`` and ``nan <= 0`` are both
    False, so the ``<= 0`` guards that sit in front of nearly every write in
    this package wave a non-finite value straight through into a REAL column,
    where it poisons every SUM, every comparison and every serializer that ever
    reads it back.

    One definition, shared by ``sales``, ``stock``, ``uploads`` and ``recipes``:
    one place to audit and one place to extend, the same reasoning that put
    ``_FiniteModel`` behind one base model in Phase 2-A.

    Every call site must sit BEFORE that function's first write, not merely
    before the offending INSERT — a guard that raises after a partial mutation
    leaves the row changed even though the caller was told the call failed.

    ``None`` and non-numeric values pass through untouched. This wall is about
    finiteness only; how ``None`` or a bad type is handled is an existing
    concern of each caller's own type checks, and changing it here would be a
    silent behaviour change on the financial path.

    It is NOT a sign rule. ``0`` and negatives are finite; whether a particular
    column may hold them is a per-field business decision (see the ``> 0``
    checks in ``sales.add_sale_item`` / ``update_sale_item`` / ``add_sale`` /
    ``update_sale_full``). Folding a sign policy into this helper would make
    the one shared guard silently mean two different things at different call
    sites, which is the failure mode a shared guard exists to prevent.
    """
    for value in values:
        try:
            ok = math.isfinite(value)
        except TypeError:
            continue
        if not ok:
            raise ValueError("value must be a finite number")