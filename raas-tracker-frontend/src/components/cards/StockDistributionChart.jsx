import {
  PieChart,
  Pie,
  Cell,
  Tooltip,
  ResponsiveContainer,
  Legend
} from 'recharts';

function CustomPieTooltip({ active, payload, total }) {
  if (active && payload && payload.length) {
    const data = payload[0];
    return (
      <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white/95 dark:bg-slate-900/95 p-2.5 shadow-lg text-xs">
        <p className="font-semibold text-slate-800 dark:text-slate-100 flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full" style={{ backgroundColor: data.payload.color }} />
          {data.name}
        </p>
        <p className="mt-1 font-mono font-bold text-slate-700 dark:text-slate-300">
          {data.value} items ({total > 0 ? ((data.value / total) * 100).toFixed(1) : '0.0'}%)
        </p>
      </div>
    );
  }
  return null;
}

export default function StockDistributionChart({ data = [], title = 'Audit Resolution Share', caption = null, matchPct = null }) {
  if (!Array.isArray(data) || data.length === 0) {
    return (
      <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-5 shadow-xs">
        <h3 className="text-base font-semibold text-slate-900 dark:text-white">{title}</h3>
        <p className="text-xs text-slate-500 dark:text-slate-400 mt-4 py-8 text-center">
          No stock checks yet — the breakdown appears after your first upload.
        </p>
      </div>
    );
  }
  const total = data.reduce((acc, curr) => acc + curr.value, 0);

  return (
    <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-5 shadow-xs flex flex-col justify-between">
      <div>
        <div className="flex items-center justify-between">
          <h3 className="text-base font-semibold text-slate-900 dark:text-white">
            {title}
          </h3>
          <span className="text-xs text-slate-500 dark:text-slate-400">
            Total {total} items
          </span>
        </div>
        <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
          {caption || 'Latest balance sheet analysis'}
        </p>
      </div>

      <div className="h-60 w-full my-2 relative">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Tooltip content={<CustomPieTooltip total={total} />} />
            <Pie
              data={data}
              cx="50%"
              cy="50%"
              innerRadius={55}
              outerRadius={80}
              paddingAngle={4}
              dataKey="value"
            >
              {data.map((entry, index) => (
                <Cell key={`cell-${index}`} fill={entry.color} stroke="transparent" />
              ))}
            </Pie>
            <Legend
              layout="horizontal"
              verticalAlign="bottom"
              align="center"
              wrapperStyle={{ fontSize: '11px', paddingTop: '8px' }}
              iconType="circle"
            />
          </PieChart>
        </ResponsiveContainer>
        {/* Center label */}
        <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none pb-6">
          <span className="text-2xl font-bold text-slate-900 dark:text-white">{matchPct ?? '—'}</span>
          <span className="text-[10px] uppercase tracking-wider text-slate-500 dark:text-slate-400">Match</span>
        </div>
      </div>
    </div>
  );
}
