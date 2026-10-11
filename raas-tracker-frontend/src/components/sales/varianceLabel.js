import { formatNumber } from '../../utils/format';

/**
 * Signed proposal-vs-actual variance for one PI line.
 *
 * INV-REVISION: a PI is a commercial quote, not an accounts-receivable ledger.
 * A "remaining" figure is therefore meaningless once invoice quantity is
 * allowed to exceed the PI — it clamps at zero and stops carrying information.
 * What is meaningful is the variance between what the PI proposed and what has
 * actually been invoiced, and that stays meaningful in both directions:
 * positive = shipping more than proposed, negative = still under.
 */
export default function varianceLabel(piQty, invoiced, unit) {
  const u = unit || 'KG';
  const delta = invoiced - piQty;
  if (delta > 0) return `+${formatNumber(delta)} ${u} over PI`;
  if (delta < 0) return `${formatNumber(Math.abs(delta))} ${u} under PI`;
  return `matches PI (${formatNumber(piQty)} ${u})`;
}