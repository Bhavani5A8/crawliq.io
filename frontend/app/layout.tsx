import type { Metadata } from 'next'
import { Geist, Geist_Mono } from 'next/font/google'
import './globals.css'

const geistSans = Geist({
  variable: '--font-geist-sans',
  subsets: ['latin'],
})

const geistMono = Geist_Mono({
  variable: '--font-geist-mono',
  subsets: ['latin'],
})

export const metadata: Metadata = {
  title: 'CrawlIQ — AI-Powered SEO Intelligence',
  description:
    'Crawl your entire website, score every page with AI, and surface the exact fixes that move you to page one — all in minutes. Free tier available.',
  keywords: ['SEO', 'crawler', 'AI', 'technical SEO', 'keyword ranking', 'site audit'],
  openGraph: {
    title: 'CrawlIQ — AI-Powered SEO Intelligence',
    description:
      'Crawl your entire website, score every page with AI, and surface the exact fixes that move you to page one.',
    type: 'website',
  },
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased dark`}
    >
      <body className="min-h-full flex flex-col bg-black text-white">{children}</body>
    </html>
  )
}
