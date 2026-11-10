// raas-tracker-frontend/src/utils/ingredientSource.js

/** Where a recipe ingredient comes from. */
export const SOURCE_IN_HOUSE = 'in_house';
export const SOURCE_OUTWARD = 'outward';

// An outward need never stood on our shelf, so no balance of any kind may be
// printed for it — a number there would read as stock we could spend.
export const NO_STOCK_LABEL = 'Not in our stock';

/** Rows written before sourcing existed carry no source_type at all. */
export function isOutward(row) {
  return String(row?.source_type || SOURCE_IN_HOUSE) === SOURCE_OUTWARD;
}

/** Plain, operational label: what the material is drawn from. */
export function sourceLabel(row) {
  return isOutward(row) ? 'Outward' : 'In house';
}

/** The evidence a client delivery rests on, in reading order. */
export function outwardEvidence(row) {
  return [
    row?.supplier_name,
    row?.purchase_number,
    row?.purchase_date,
    row?.client_name,
  ].filter(Boolean);
}
