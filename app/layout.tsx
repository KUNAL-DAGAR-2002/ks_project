import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "DailyOps — Simple shop management",
  description: "Sales, stock, udhaar and purchase planning for Indian kirana stores.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
