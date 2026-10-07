import type { Metadata } from "next";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import "./globals.css";

export const metadata: Metadata = {
  title: "NoteLoop",
  description: "AI assisted progress notes for therapists",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <header className="border-b bg-card">
          <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
            <Link href="/" className="text-lg font-semibold">
              NoteLoop
            </Link>
            <Badge variant="warning">Synthetic data only</Badge>
          </div>
        </header>
        <main className="mx-auto max-w-5xl px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
