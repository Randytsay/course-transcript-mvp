import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Course Transcript",
  description: "AI-assisted course transcription workspace"
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-Hant">
      <body>{children}</body>
    </html>
  );
}
