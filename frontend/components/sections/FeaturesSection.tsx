'use client'

import { Card } from '@/components/ui/card'
import {
  Search,
  BarChart3,
  BrainCircuit,
  Shield,
  Globe,
  TrendingUp,
  FileText,
  Users,
} from 'lucide-react'

const features = [
  {
    icon: Search,
    color: 'text-indigo-400',
    bg: 'bg-indigo-500/10',
    title: 'Deep BFS Crawler',
    description:
      'Crawls up to 5,000 pages in one run. Handles Cloudflare protection, SSL cascades, and sitemap-driven discovery automatically.',
  },
  {
    icon: BrainCircuit,
    color: 'text-cyan-400',
    bg: 'bg-cyan-500/10',
    title: 'AI-Powered Analysis',
    description:
      'Gemini, GPT-4, Claude, or Groq score each page for E-E-A-T, keyword density, readability, and structured data quality.',
  },
  {
    icon: BarChart3,
    color: 'text-emerald-400',
    bg: 'bg-emerald-500/10',
    title: 'SERP Position Tracker',
    description:
      'Monitor keyword rankings 24/7. Get instant alerts when positions drop and visualise position history over time.',
  },
  {
    icon: Shield,
    color: 'text-yellow-400',
    bg: 'bg-yellow-500/10',
    title: 'Technical SEO Audit',
    description:
      'Checks Core Web Vitals, schema markup, hreflang, canonical URLs, robots.txt, sitemap health, and 50+ on-page signals.',
  },
  {
    icon: TrendingUp,
    color: 'text-pink-400',
    bg: 'bg-pink-500/10',
    title: 'Competitor Intelligence',
    description:
      'Analyse competitor sites side-by-side. Identify content gaps, keyword opportunities, and backlink patterns.',
  },
  {
    icon: Globe,
    color: 'text-orange-400',
    bg: 'bg-orange-500/10',
    title: 'Link Graph & Dedup',
    description:
      'Visualise your internal link graph, detect duplicate content with TF-IDF similarity, and audit canonical signals.',
  },
  {
    icon: FileText,
    color: 'text-purple-400',
    bg: 'bg-purple-500/10',
    title: 'White-Label PDF Reports',
    description:
      'Export branded PDF audits in one click. Add your logo, company name, and custom recommendations for clients.',
  },
  {
    icon: Users,
    color: 'text-teal-400',
    bg: 'bg-teal-500/10',
    title: 'Team Workspace',
    description:
      'Invite teammates as viewers or editors. Share projects, issue statuses, and audit history across your agency.',
  },
]

export function FeaturesSection() {
  return (
    <section id="features" className="relative py-24 bg-[#030303]">
      <div className="absolute inset-0 bg-grid opacity-30" />

      <div className="relative z-10 max-w-7xl mx-auto px-6">
        {/* Heading */}
        <div className="text-center mb-16">
          <p className="text-indigo-400 text-sm font-semibold tracking-widest uppercase mb-3">
            Everything you need
          </p>
          <h2 className="text-4xl md:text-5xl font-extrabold text-white">
            The complete SEO stack
          </h2>
          <p className="mt-4 text-neutral-400 max-w-2xl mx-auto">
            CrawlIQ replaces a team of SEO specialists with one intelligent platform.
            Crawl, analyse, fix, and monitor — all from a single dashboard.
          </p>
        </div>

        {/* Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5">
          {features.map((f) => {
            const Icon = f.icon
            return (
              <Card
                key={f.title}
                className="bg-white/[0.03] border-white/[0.06] hover:border-indigo-500/30 hover:bg-white/[0.06] transition-all duration-300 p-5 group"
              >
                <div className={`inline-flex p-2.5 rounded-lg ${f.bg} mb-4`}>
                  <Icon className={`w-5 h-5 ${f.color}`} />
                </div>
                <h3 className="font-semibold text-white mb-2 group-hover:text-indigo-300 transition-colors">
                  {f.title}
                </h3>
                <p className="text-sm text-neutral-500 leading-relaxed">{f.description}</p>
              </Card>
            )
          })}
        </div>
      </div>
    </section>
  )
}
