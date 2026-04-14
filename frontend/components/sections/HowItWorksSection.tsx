'use client'

import { Card } from '@/components/ui/card'
import { Globe, BrainCircuit, BarChart3, ArrowRight } from 'lucide-react'

const steps = [
  {
    step: '01',
    icon: Globe,
    color: 'text-indigo-400',
    border: 'border-indigo-500/30',
    glow: 'bg-indigo-500/10',
    title: 'Enter any URL',
    description:
      'Paste your domain and hit crawl. CrawlIQ immediately starts a deep BFS crawl — discovering every page, image, script, and link across your site.',
    detail: 'Up to 5,000 pages in one run · Cloudflare bypass · Auto-sitemap discovery',
  },
  {
    step: '02',
    icon: BrainCircuit,
    color: 'text-cyan-400',
    border: 'border-cyan-500/30',
    glow: 'bg-cyan-500/10',
    title: 'AI scores every page',
    description:
      'Our multi-provider AI engine (Gemini / GPT-4 / Claude / Groq) analyses title tags, meta descriptions, headings, content quality, schema, Core Web Vitals, and 50+ signals.',
    detail: 'Prioritised by impact · Fix suggestions per page · E-E-A-T scoring',
  },
  {
    step: '03',
    icon: BarChart3,
    color: 'text-emerald-400',
    border: 'border-emerald-500/30',
    glow: 'bg-emerald-500/10',
    title: 'Fix & monitor rankings',
    description:
      'Get a prioritised action list. Track fixes over time with snapshot diffs. Monitor keyword positions 24/7 and receive email alerts on rank drops.',
    detail: 'White-label PDF export · Team workspace · SERP position history',
  },
]

export function HowItWorksSection() {
  return (
    <section className="relative py-24 bg-black">
      <div className="max-w-7xl mx-auto px-6">
        <div className="text-center mb-16">
          <p className="text-emerald-400 text-sm font-semibold tracking-widest uppercase mb-3">
            Simple workflow
          </p>
          <h2 className="text-4xl md:text-5xl font-extrabold text-white">
            From crawl to page-one in 3 steps
          </h2>
        </div>

        <div className="relative flex flex-col lg:flex-row items-stretch gap-6">
          {/* Connector line (desktop) */}
          <div className="hidden lg:block absolute top-1/2 left-[calc(33.33%-20px)] right-[calc(33.33%-20px)] h-px bg-gradient-to-r from-indigo-500/40 via-cyan-500/40 to-emerald-500/40 z-0" />

          {steps.map((s, i) => {
            const Icon = s.icon
            return (
              <div key={s.step} className="flex-1 relative z-10">
                <Card
                  className={`h-full bg-[#0a0a0a] border ${s.border} hover:shadow-lg transition-all duration-300 p-6`}
                >
                  {/* Step number */}
                  <div className="flex items-center justify-between mb-4">
                    <span className="text-4xl font-black text-white/[0.06]">{s.step}</span>
                    <div className={`p-2.5 rounded-lg ${s.glow}`}>
                      <Icon className={`w-5 h-5 ${s.color}`} />
                    </div>
                  </div>

                  <h3 className="text-lg font-bold text-white mb-2">{s.title}</h3>
                  <p className="text-sm text-neutral-400 leading-relaxed mb-4">{s.description}</p>

                  <div className={`text-xs ${s.color} bg-white/[0.03] border border-white/[0.06] rounded-lg p-3 font-mono`}>
                    {s.detail}
                  </div>

                  {i < steps.length - 1 && (
                    <div className="flex justify-center mt-4 lg:hidden">
                      <ArrowRight className="w-4 h-4 text-neutral-600 rotate-90" />
                    </div>
                  )}
                </Card>
              </div>
            )
          })}
        </div>

        {/* Demo screenshot placeholder */}
        <div className="mt-16 rounded-2xl overflow-hidden border border-white/[0.06] relative">
          <img
            src="https://images.unsplash.com/photo-1551288049-bebda4e38f71?w=1400&q=80"
            alt="CrawlIQ SEO dashboard showing crawl results and keyword rankings"
            className="w-full h-64 md:h-96 object-cover opacity-60"
          />
          <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-black/30 to-transparent flex items-end p-8">
            <div>
              <p className="text-white font-semibold text-lg">Live SEO Dashboard</p>
              <p className="text-neutral-400 text-sm mt-1">
                Real-time crawl progress · AI issue scoring · SERP rank tracking
              </p>
            </div>
            <a
              href="http://localhost:7860"
              target="_blank"
              rel="noopener noreferrer"
              className="ml-auto inline-flex items-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-semibold px-4 py-2 rounded-lg transition-colors whitespace-nowrap"
            >
              Open Dashboard
              <ArrowRight className="w-3.5 h-3.5" />
            </a>
          </div>
        </div>
      </div>
    </section>
  )
}
