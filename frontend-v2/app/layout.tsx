import type { Metadata, Viewport } from "next";
import Script from "next/script";
import "./globals.css";
import AppShell from "@/components/AppShell";

export const metadata: Metadata = {
  title: "CNTx labs - Trading Operations",
  description: "Software Platform for Trading Operations.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="bg-cyber-bg">
      <head>
        <meta name="theme-color" content="#05070b" />
        <Script src="https://telegram.org/js/telegram-web-app.js" strategy="beforeInteractive" />
        <script
          dangerouslySetInnerHTML={{
            __html: `
(function() {
  function setVh() {
    var vh = window.visualViewport ? window.visualViewport.height * 0.01 : window.innerHeight * 0.01;
    document.documentElement.style.setProperty('--vh', vh + 'px');
  }
  setVh();
  window.visualViewport && window.visualViewport.addEventListener('resize', setVh);
  window.addEventListener('resize', setVh);
})();
            `.trim(),
          }}
        />
      </head>
      <body className="antialiased text-cyber-text bg-cyber-bg min-h-screen min-h-[100dvh] pb-[env(safe-area-inset-bottom)] pt-[env(safe-area-inset-top)] selection:bg-cyber-accentGreen/20">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
