import PlaygroundRunner from "@/components/PlaygroundRunner";

export default function HomePage() {
  return (
    <section className="px-6 py-12">
      <div className="max-w-6xl mx-auto">
        <div className="label mb-2">Live inference</div>
        <h1 className="font-display text-[34px] text-[var(--ink)]">Playground</h1>
        <p className="mt-2 text-[15px] text-[var(--ink-soft)] max-w-2xl">
          Edit the system and user prompts, then run the agent against your FHIR server.
          Every turn streams live — reasoning, sandbox code, and tool results.
        </p>
        <div className="mt-8">
          <PlaygroundRunner />
        </div>
      </div>
    </section>
  );
}
