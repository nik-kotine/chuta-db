import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Chuta DB Console",
  description: "Consola de consultas para el motor Chuta DB",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es">
      <body>{children}</body>
    </html>
  );
}
