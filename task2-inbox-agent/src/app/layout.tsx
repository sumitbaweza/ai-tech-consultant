import type { Metadata } from "next";
import { Geist } from "next/font/google";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import "./globals.css";

// Downloaded once at build time and served by the app, so pages make no Google Fonts request
const geist = Geist({ subsets: ["latin"], variable: "--font-sans" });

export const metadata: Metadata = {
  title: "Inbox Agent",
  description: "Drafts replies from the Knowledge Brain and learns from your edits",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={cn("font-sans", geist.variable)}>
      <body className="bg-background text-foreground antialiased">{children}</body>
    </html>
  );
}
