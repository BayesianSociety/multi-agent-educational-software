import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Taffy Code Trail",
  description: "A kid-safe block coding game with deterministic execution."
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
