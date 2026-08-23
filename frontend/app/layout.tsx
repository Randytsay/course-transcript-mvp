import type { Metadata } from "next";
import "./globals.css";
import "./accessibility.css";

export const metadata: Metadata = {
  title: "Course Transcript",
  description: "AI-assisted course transcription workspace",
  icons: {
    icon: [{ url: "/images/cisheng-lotus-seal.png", type: "image/png", sizes: "512x512" }],
    apple: [{ url: "/images/cisheng-lotus-seal.png", type: "image/png", sizes: "512x512" }]
  }
};

const fontPreferenceScript = `
(() => {
  try {
    const saved = localStorage.getItem("course-transcript-font-size");
    const size = saved === "standard" || saved === "large" || saved === "xlarge" ? saved : "large";
    document.documentElement.setAttribute("data-font-size", size);
  } catch (_) {
    document.documentElement.setAttribute("data-font-size", "large");
  }
})();
`;

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-Hant" data-font-size="large" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: fontPreferenceScript }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
