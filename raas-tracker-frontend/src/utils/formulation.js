// A formulation is balanced when ingredients + water total 100%. Incomplete
// percentages are legal to save and preview; production is what requires the
// total, so this one rule is shared by the card, the production modal and the
// Reports page to keep their wording and gating in step.
export const BALANCE_TOLERANCE = 0.01;

export const isBalanced = (total) => {
  const value = Number(total);
  // +1e-9 because 100 - 99.99 is 0.010000000000005 in binary float.
  // Number(null) is 0, so a missing total is correctly not balanced.
  return Number.isFinite(value)
    && Math.abs(value - 100) <= BALANCE_TOLERANCE + 1e-9;
};
