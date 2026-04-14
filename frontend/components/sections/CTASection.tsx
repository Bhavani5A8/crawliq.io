'use client'

import { Spotlight } from '@/components/ui/spotlight'
import { ArrowRight, Zap } from 'lucide-react'

export function CTASection() {
  return (
    <section className="relative py-28 bg-black overflow-hidden">
      <Spotlight className="-top-40 left-1/2 -translate-x-1/2" fill="#6366f1" />

      <div className="relative z-10 max-w-3xl mx-auto px-6 text-center">
        <div className="inline-flex items-center gap-2 bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 text-xs font-semibold px-3 py-1.5 rounded-full mb-6">
          <Zap className="w-3 h-3" />
          No credit card required
        </div>

        <h2 className="text-5xl md:text-6xl font-extrabold text-white leading-tight">
          Your competitors are
          <br />
          <span className="bg-clip-text text-transparent bg-gradient-to-r from-indigo-400 to-cyan-400">
            already crawling
          </span>
        </h2>

        <p className="mt-6 text-lg text-neutral-400 max-w-xl mx-auto">
          Start your first crawl in under 60 seconds. No setup, no credit card, no commitment.
          200 pages free every month — forever.
        </p>

        <div className="flex flex-col sm:flex-row justify-center gap-4 mt-10">
          <a
            href="http://localhost:7860"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center justify-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-white font-semibold px-8 py-4 rounded-xl text-base transition-colors shadow-lg shadow-indigo-900/30"
          >
            Start Free Crawl Now
            <ArrowRight className="w-4 h-4" />
          </a>
        </div>

        <div className="mt-8 flex justify-center gap-8 text-xs text-neutral-600">
          {['Free forever plan', 'No setup required', 'Cancel anytime'].map((t) => (
            <span key={t} className="flex items-center gap-1.5">
              <span className="w-1 h-1 rounded-full bg-neutral-600" />
              {t}
            </span>
          ))}
        </div>
      </div>
    </section>
  )
}
