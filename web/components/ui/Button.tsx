import type { ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

type Variant = "primary" | "ghost";

export function Button({
  variant = "primary",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  const base =
    "inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50";
  const variants: Record<Variant, string> = {
    primary: "bg-primary text-primary-fg hover:bg-primary/90",
    ghost: "text-ink hover:bg-canvas",
  };
  return <button className={cn(base, variants[variant], className)} {...props} />;
}
