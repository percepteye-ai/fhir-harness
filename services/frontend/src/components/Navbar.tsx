"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [{ href: "/", label: "Playground" }];

export default function Navbar() {
  const pathname = usePathname();
  return (
    <nav className="sticky top-4 z-40 px-4 pt-4">
      <div className="max-w-6xl mx-auto bg-[#141722]/70 backdrop-blur-md border border-[var(--rule)] rounded-[18px] px-4 py-2.5 flex items-center justify-between shadow-[0_1px_2px_rgba(0,0,0,0.4),0_10px_30px_-8px_rgba(0,0,0,0.6)]">
        <Link href="/" className="flex items-center gap-2.5 group">
          <span className="w-7 h-7 rounded-lg bg-[var(--accent)] text-[var(--accent-on)] font-display text-[15px] flex items-center justify-center">
            P
          </span>
          <span className="font-display text-[17px] tracking-tight text-[var(--ink)] flex items-center gap-2">
            PerceptEye
            <span className="text-[var(--ink-ghost)] font-normal">/</span>
            <span className="text-[var(--accent)]">FHIR Harness</span>
          </span>
        </Link>
        <div className="flex items-center gap-1.5 text-[13px] font-medium">
          {LINKS.map((l) => {
            const active = pathname === l.href;
            return (
              <Link
                key={l.href}
                href={l.href}
                className="px-3 py-1.5 rounded-full transition"
                style={
                  active
                    ? { background: "var(--accent)", color: "var(--accent-on)" }
                    : { color: "var(--ink-soft)" }
                }
              >
                {l.label}
              </Link>
            );
          })}
        </div>
      </div>
    </nav>
  );
}
