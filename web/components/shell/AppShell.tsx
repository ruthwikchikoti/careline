"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  ClipboardList,
  LayoutDashboard,
  MessageSquareText,
  ShieldAlert,
  ShieldCheck,
  Users,
} from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

// Nav reflects the full product + which member owns each screen (UI-BUILD-PLAN §5).
// Only items with `ready` are built in this foundation slice.
const NAV = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard, ready: true, owner: "Ruthwik" },
  { href: "/console", label: "Live Console", icon: MessageSquareText, ready: true, owner: "Ruthwik" },
  { href: "/patients", label: "Patients", icon: Users, ready: false, owner: "Naga" },
  { href: "/consultations", label: "Consultations", icon: ClipboardList, ready: false, owner: "Naresh" },
  { href: "/escalations", label: "Escalations", icon: ShieldAlert, ready: false, owner: "Vinay" },
  { href: "/audit", label: "Audit", icon: Activity, ready: false, owner: "Vinay" },
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-border bg-surface px-3 py-5 md:flex">
        <Link href="/" className="mb-6 flex items-center gap-2 px-3">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-fg">
            <ShieldCheck className="h-5 w-5" />
          </span>
          <span className="text-lg font-semibold text-ink">CareLine</span>
        </Link>
        <nav className="flex flex-col gap-1">
          {NAV.map(({ href, label, icon: Icon, ready, owner }) => {
            const active = pathname === href;
            const content = (
              <span
                className={cn(
                  "flex items-center gap-3 rounded-lg px-3 py-2 text-sm",
                  active ? "bg-primary-muted font-medium text-primary" : "text-muted",
                  ready ? "hover:bg-canvas hover:text-ink" : "cursor-default opacity-50",
                )}
              >
                <Icon className="h-4 w-4" />
                {label}
                {!ready && <span className="ml-auto text-[10px] uppercase">{owner}</span>}
              </span>
            );
            return ready ? (
              <Link key={href} href={href}>
                {content}
              </Link>
            ) : (
              <div key={href} title={`Owned by ${owner} — coming soon`}>
                {content}
              </div>
            );
          })}
        </nav>
        <p className="mt-auto px-3 text-[11px] leading-relaxed text-muted">
          Post-consultation AI voice agent. Uncertainty always escalates to the doctor.
        </p>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center justify-between border-b border-border bg-surface px-6">
          <span className="text-sm text-muted">Doctor workspace</span>
          <span className="flex items-center gap-2 text-sm font-medium text-ink">
            <span className="flex h-7 w-7 items-center justify-center rounded-full bg-primary-muted text-xs text-primary">
              AR
            </span>
            Dr. Asha Rao
          </span>
        </header>
        <main className="flex-1 p-6">{children}</main>
      </div>
    </div>
  );
}
