import type { Metadata } from "next";
import { IBM_Plex_Sans } from "next/font/google";
import Link from "next/link";
import type { ReactNode } from "react";

import { ReviewerField, ReviewerProvider } from "@/components/Reviewer";

import "./globals.css";

const plex = IBM_Plex_Sans({ subsets: ["latin"], weight: ["400", "500", "600"], display: "swap" });

export const metadata: Metadata = {
  title: "Invoice review",
  description: "Check and approve incoming invoices",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className={plex.className}>
        <ReviewerProvider>
          <header className="masthead">
            <Link href="/" className="masthead-title">
              Invoice review
            </Link>
            <ReviewerField />
          </header>
          <main>{children}</main>
        </ReviewerProvider>
      </body>
    </html>
  );
}
