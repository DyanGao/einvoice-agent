import type { Metadata } from "next";
import { IBM_Plex_Sans } from "next/font/google";
import type { ReactNode } from "react";

import { Masthead } from "@/components/Masthead";
import { ReviewerProvider, SignedIn } from "@/components/Reviewer";
import { LocaleProvider } from "@/lib/i18n";

import "./globals.css";

const plex = IBM_Plex_Sans({ subsets: ["latin"], weight: ["400", "500", "600"], display: "swap" });

export const metadata: Metadata = {
  title: "Invoice review · Rechnungsprüfung",
  description: "Check and approve incoming invoices",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // The language is set on the page once the browser's choice is known (lib/i18n.tsx).
    <html lang="en">
      <body className={plex.className}>
        <LocaleProvider>
          <ReviewerProvider>
            <Masthead />
            <main>
              <SignedIn>{children}</SignedIn>
            </main>
          </ReviewerProvider>
        </LocaleProvider>
      </body>
    </html>
  );
}
