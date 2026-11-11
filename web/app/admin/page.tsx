import AdminDashboard from "@/components/AdminDashboard";

export default function AdminPage() {
  return (
    <div>
      <h1 className="text-2xl font-semibold">Admin</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        How the review, the reviewers, and the model calls are behaving. Counts and labels
        only. No note text appears here.
      </p>
      <AdminDashboard />
    </div>
  );
}
