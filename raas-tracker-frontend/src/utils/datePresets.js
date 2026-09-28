export const DATE_PRESETS = {
  'This Month': () => {
    const now = new Date();
    const year = now.getUTCFullYear();
    const month = now.getUTCMonth();
    return {
      from: `${year}-${String(month + 1).padStart(2, '0')}-01`,
      to: now.toISOString().split('T')[0],
    };
  },
  'Last Month': () => {
    const now = new Date();
    const year = now.getUTCFullYear();
    const month = now.getUTCMonth();
    const first = new Date(Date.UTC(year, month - 1, 1));
    const last = new Date(Date.UTC(year, month, 0));
    return {
      from: first.toISOString().split('T')[0],
      to: last.toISOString().split('T')[0],
    };
  },
  'This Quarter': () => {
    const now = new Date();
    const year = now.getUTCFullYear();
    const month = now.getUTCMonth();
    const qMonth = Math.floor(month / 3) * 3;
    return {
      from: `${year}-${String(qMonth + 1).padStart(2, '0')}-01`,
      to: now.toISOString().split('T')[0],
    };
  },
  'Last Quarter': () => {
    const now = new Date();
    const year = now.getUTCFullYear();
    const month = now.getUTCMonth();
    const qMonth = Math.floor(month / 3) * 3;
    const first = new Date(Date.UTC(year, qMonth - 3, 1));
    const last = new Date(Date.UTC(year, qMonth, 0));
    return {
      from: first.toISOString().split('T')[0],
      to: last.toISOString().split('T')[0],
    };
  },
  'This Year': () => {
    const now = new Date();
    const year = now.getUTCFullYear();
    return {
      from: `${year}-01-01`,
      to: now.toISOString().split('T')[0],
    };
  },
  'Last Year': () => {
    const now = new Date();
    const year = now.getUTCFullYear();
    return {
      from: `${year - 1}-01-01`,
      to: `${year - 1}-12-31`,
    };
  },
};

export const dateAnchorOptions = [
  { value: 'pi_date', label: 'PI Date' },
  { value: 'lc_date', label: 'LC Date' },
  { value: 'shipment_date', label: 'Shipment Date' },
  { value: 'receive_date', label: 'Receive Date' },
  { value: 'maturity_date', label: 'Maturity Date' },
];