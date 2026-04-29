import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "~/outreach",
  description: "Outreach pipeline dashboard",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="min-h-screen flex flex-col bg-bg text-ink font-sans antialiased">
        {children}
      </body>
    </html>
  );
}
