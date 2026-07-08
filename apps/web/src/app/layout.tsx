import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Senedd Record Explorer",
  description:
    "Search Senedd plenary meetings, watch the video, follow the transcript, and ask questions of the record.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="h-screen overflow-hidden antialiased">{children}</body>
    </html>
  );
}
