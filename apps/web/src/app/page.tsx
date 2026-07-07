import { redirect } from "next/navigation";
import { latestMeetingId } from "@/lib/queries";

export const dynamic = "force-dynamic";

export default async function Home() {
  const id = await latestMeetingId();
  if (id !== null) redirect(`/meetings/${id}`);
  return (
    <main className="flex h-full items-center justify-center p-8">
      <div className="max-w-md text-center">
        <h1 className="text-2xl font-semibold">Senedd Record Explorer</h1>
        <p className="mt-3 text-sm text-gray-600">
          No meetings in the database yet. Run the pipeline (
          <code className="rounded bg-gray-100 px-1">uv run python main.py</code>) or seed the
          dev fixture (
          <code className="rounded bg-gray-100 px-1">uv run python scripts/seed_fixture.py</code>
          ), then reload.
        </p>
      </div>
    </main>
  );
}
