export default function Footer() {
  return (
    <footer className="px-6 py-10 mt-16 border-t border-[var(--rule)]">
      <div className="max-w-6xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-3 text-[12px] text-[var(--ink-faint)]">
        <span>
          <span className="font-semibold text-[var(--ink-soft)]">PerceptEye FHIR Harness</span> —
          inference against your Open FHIR or AWS HealthLake server.
        </span>
        <span className="font-mono">pe-harness · config-driven · no bundled tasks</span>
      </div>
    </footer>
  );
}
