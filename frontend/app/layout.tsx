import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";

import { KeyboardController } from "@/components/keyboard";
import { Sidebar } from "@/components/sidebar";
import { Topbar } from "@/components/topbar";
import { LiveProvider } from "@/lib/ws";

import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono" });

export const metadata: Metadata = {
  title: "AV1 Pipeline",
  description: "Veilige lokale mediapipeline",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="nl" className="dark">
      <body className={`${inter.variable} ${mono.variable}`}>
        <LiveProvider><div className="shell"><Sidebar /><div className="main"><Topbar /><main className="content">{children}</main></div></div><KeyboardController /></LiveProvider>
      </body>
    </html>
  );
}
