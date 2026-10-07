import ScheduleList from "@/components/ScheduleList";

export default function SchedulePage() {
  return (
    <div>
      <h1 className="text-2xl font-semibold">Schedule</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Open a session to draft, review, and sign its progress note.
      </p>
      <ScheduleList />
    </div>
  );
}
