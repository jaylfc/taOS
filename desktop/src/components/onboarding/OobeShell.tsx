import type { FormEvent, ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { ChevronLeft } from "lucide-react";
import "./oobe.css";

/**
 * Full-bleed setup surface. Each step renders one OobePage; the shell slides
 * between them by `stepKey`, in the direction of travel. Reduced motion keeps
 * only the cross-fade.
 */
export function OobeShell({
  stepKey,
  direction,
  children,
}: {
  stepKey: string;
  /** 1 going forward, -1 going back. */
  direction: 1 | -1;
  children: ReactNode;
}) {
  const reduce = useReducedMotion();
  const offset = reduce ? 0 : 24 * direction;
  return (
    <div className="oobe-root">
      <AnimatePresence mode="wait" initial={false} custom={direction}>
        <motion.div
          key={stepKey}
          className="oobe-stage"
          initial={{ opacity: 0, x: offset, filter: reduce ? "none" : "blur(4px)" }}
          animate={{ opacity: 1, x: 0, filter: "none" }}
          exit={{ opacity: 0, x: -offset, filter: reduce ? "none" : "blur(4px)" }}
          transition={{ duration: reduce ? 0.15 : 0.28, ease: [0.32, 0.72, 0, 1] }}
        >
          {children}
        </motion.div>
      </AnimatePresence>
    </div>
  );
}

/**
 * One screen: optional Back, optional mark, title, subtitle, content, and a
 * footer pinned to the bottom so the primary action stays reachable above an
 * on-screen keyboard. The page is a form, so Return submits it.
 */
export function OobePage({
  label,
  title,
  subtitle,
  mark,
  icon,
  onBack,
  onSubmit,
  footer,
  children,
}: {
  /** Accessible name for the form; defaults to the title. */
  label?: string;
  title: string;
  subtitle?: ReactNode;
  mark?: boolean;
  /** A glyph above the title, for screens with no other content. */
  icon?: ReactNode;
  onBack?: () => void;
  onSubmit: () => void;
  footer: ReactNode;
  children?: ReactNode;
}) {
  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    onSubmit();
  }
  return (
    <form className="oobe-page" onSubmit={handleSubmit} aria-label={label ?? title} noValidate>
      <div className="oobe-topbar">
        {onBack && (
          <button type="button" className="oobe-back" onClick={onBack}>
            <ChevronLeft size={22} aria-hidden="true" />
            Back
          </button>
        )}
      </div>
      <header className="oobe-header">
        {mark && <img className="oobe-mark" src="/static/taos-logo.png" alt="" />}
        {icon}
        <h1 className="oobe-title">{title}</h1>
        {subtitle && <p className="oobe-subtitle">{subtitle}</p>}
      </header>
      <div className="oobe-content">{children}</div>
      <div className="oobe-footer">{footer}</div>
    </form>
  );
}
