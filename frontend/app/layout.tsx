import type { Metadata } from "next";
import { Schibsted_Grotesk } from "next/font/google";
import "./globals.css";

const font = Schibsted_Grotesk({ subsets: ["latin"], variable: "--font" });
export const metadata: Metadata = { title: "Invoice desk", description: "Upload, check and correct business documents" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (<html lang="en"><body className={font.variable}>{children}</body></html>);
}
