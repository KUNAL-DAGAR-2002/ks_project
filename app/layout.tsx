import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "KiranaSaathi — Roz ka hisaab, sab ek jagah",
  description: "Daily sales, stock and credit—managed in one simple app.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
