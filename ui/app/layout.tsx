import type { Metadata } from "next";
import { IBM_Plex_Sans } from "next/font/google";
import Link from "next/link";
import type { ReactNode } from "react";

import { ReviewerField, ReviewerProvider, SignedIn } from "@/components/Reviewer";

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
            <nav className="masthead-nav" aria-label="Pages">
              <Link href="/">Invoices</Link>
              <Link href="/mail">Mail</Link>
            </nav>
            <ReviewerField />
          </header>
          <main>
            <SignedIn>{children}</SignedIn>
          </main>
        </ReviewerProvider>
      </body>
    </html>
  );
}
