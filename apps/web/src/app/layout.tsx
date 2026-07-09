import type { Metadata } from "next";
import { Literata, Public_Sans } from "next/font/google";
import "./globals.css";

// Literata carries the record itself (transcript, wordmark); Public Sans — a
// government-grade grotesk — carries the UI chrome. Both variable.
const literata = Literata({
  subsets: ["latin"],
  style: ["normal", "italic"],
  variable: "--font-literata",
});
const publicSans = Public_Sans({ subsets: ["latin"], variable: "--font-public-sans" });

export const metadata: Metadata = {
  title: "Senedd Record Explorer",
  description:
    "Search Senedd plenary meetings, watch the video, follow the transcript, and ask questions of the record.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body
        className={`${literata.variable} ${publicSans.variable} h-screen overflow-hidden font-sans antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
