import clsx from 'clsx';
import { Loader2 } from 'lucide-react';

const BUTTON_VARIANTS = {
  primary: 'bg-blue-600 hover:bg-blue-700 text-white shadow-xs focus:ring-blue-500/20',
  secondary: 'bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-200 border border-slate-300 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700/60 focus:ring-slate-400/20',
  success: 'bg-emerald-600 hover:bg-emerald-700 text-white shadow-xs focus:ring-emerald-500/20',
  danger: 'bg-rose-600 hover:bg-rose-700 text-white shadow-xs focus:ring-rose-500/20',
  ghost: 'bg-transparent text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 focus:ring-slate-400/20',
};

const BUTTON_SIZES = {
  sm: 'px-2.5 py-1.5 text-xs',
  md: 'px-4 py-2 text-sm',
  lg: 'px-5 py-2.5 text-base',
};

// Icon-only controls keep their visual box but get a centred 44x44 pointer
// target. The pseudo-element belongs to the control for hit testing, so a tap
// slightly outside a 28px button still activates it without shifting layout.
export const ICON_HIT_AREA = "relative after:absolute after:left-1/2 after:top-1/2 after:size-11 after:-translate-x-1/2 after:-translate-y-1/2 after:content-['']";

export default function Button({
  children,
  variant = 'primary',
  size = 'md',
  className = '',
  loading = false,
  icon: Icon,
  disabled,
  ...props
}) {
  // A child that renders nothing (null, undefined, false, an empty string)
  // leaves the button icon-only; a child that renders text does not - "0" is
  // text, and a button showing "0" is not an icon-only control.
  const iconOnly = !children && children !== 0 && Boolean(Icon || loading);

  return (
    <button
      disabled={disabled || loading}
      className={clsx(
        'inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-all duration-150',
        'focus:outline-hidden focus:ring-3 disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer',
        BUTTON_VARIANTS[variant] || BUTTON_VARIANTS.primary,
        BUTTON_SIZES[size] || BUTTON_SIZES.md,
        iconOnly && ICON_HIT_AREA,
        className
      )}
      {...props}
    >
      {loading ? (
        <Loader2 className="w-4 h-4 animate-spin" />
      ) : Icon ? (
        <Icon className="w-4 h-4 shrink-0" />
      ) : null}
      {children}
    </button>
  );
}
