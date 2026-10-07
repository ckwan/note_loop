import Link from "next/link";
import { notFound } from "next/navigation";
import NoteWorkspace from "@/components/NoteWorkspace";
import { Button } from "@/components/ui/button";

export default async function SessionPage(props: PageProps<"/sessions/[id]">) {
  const { id } = await props.params;
  const sessionId = Number(id);
  if (!Number.isInteger(sessionId) || sessionId < 1) notFound();

  return (
    <div>
      <Button asChild variant="ghost" size="sm" className="-ml-3">
        <Link href="/">Back to schedule</Link>
      </Button>
      <NoteWorkspace sessionId={sessionId} />
    </div>
  );
}
