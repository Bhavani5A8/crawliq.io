'use client'

import { SplineScene } from '@/components/ui/spline-scene'
import { Spotlight } from '@/components/ui/spotlight'
import { Badge } from '@/components/ui/badge'
import { ArrowRight, Zap } from 'lucide-react'

export function HeroSection() {
  return (
    <section className="relative min-h-screen w-full overflow-hidden bg-black flex items-center">
      {/* Background grid */}
      <div className="absolute inset-0 bg-grid opacity-40" />

      {/* Spotlights */}
      <Spotlight className="-top-40 left-0 md:left-60 md:-top-20" fill="white" />
      <Spotlight className="top-10 right-0 md:right-40" fill="#6366f1" />

      {/* Radial fade at bottom */}
      <div className="pointer-events-none absolute bottom-0 left-0 right-0 h-40 bg-gradient-to-t from-black to-transparent z-10" />

      <div className="relative z-10 flex flex-col lg:flex-row items-center w-full max-w-7xl mx-auto px-6 py-20 gap-12">

        {/* ── Left: copy ─────────────────────────────────────────── */}
        <div className="flex-1 flex flex-col items-start gap-6 max-w-2xl">
          <Badge
            variant="outline"
            className="border-indigo-500/40 text-indigo-400 bg-indigo-500/10 gap-1.5"
          >
            <Zap className="w-3 h-3" />
            AI-Powered SEO Intelligence
          </Badge>

          <h1 className="text-5xl md:text-6xl lg:text-7xl font-extrabold leading-tight">
            <span className="bg-clip-text text-transparent bg-gradient-to-b from-neutral-50 to-neutral-400">
              Dominate
            </span>
            <br />
            <span className="bg-clip-text text-transparent bg-gradient-to-b from-indigo-400 to-indigo-600">
              Search Rankings
            </span>
            <br />
            <span className="bg-clip-text text-transparent bg-gradient-to-b from-neutral-50 to-neutral-400">
              With Precision
            </span>
          </h1>

          <p className="text-lg text-neutral-400 leading-relaxed max-w-lg">
            CrawlIQ crawls your entire website, scores every page with AI, and surfaces
            the exact fixes that move you to page one — all in minutes.
          </p>

          <div className="flex flex-col sm:flex-row gap-3 mt-2">
            <a
              href="http://localhost:7860"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-white font-semibold px-6 py-3 rounded-lg transition-colors"
            >
              Start Free Crawl
              <ArrowRight className="w-4 h-4" />
            </a>
            <a
              href="#features"
              className="inline-flex items-center gap-2 border border-white/10 hover:border-white/20 text-neutral-300 hover:text-white font-semibold px-6 py-3 rounded-lg transition-colors"
            >
              See How It Works
            </a>
          </div>

          {/* Stat pills */}
          <div className="flex flex-wrap gap-4 mt-4">
            {[
              { value: '5,000+', label: 'Pages / crawl' },
              { value: '50+', label: 'SEO signals' },
              { value: '< 3 min', label: 'Full audit' },
            ].map((s) => (
              <div
                key={s.label}
                className="flex flex-col items-center bg-white/5 border border-white/10 rounded-lg px-4 py-2"
              >
                <span className="text-xl font-bold text-indigo-400">{s.value}</span>
                <span className="text-xs text-neutral-500">{s.label}</span>
              </div>
            ))}
          </div>
        </div>

        {/* ── Right: 3D Spline scene ──────────────────────────────── */}
        <div className="flex-1 relative w-full lg:max-w-2xl h-[420px] lg:h-[560px]">
          {/* Glow behind scene */}
          <div className="absolute inset-0 rounded-2xl bg-indigo-600/10 blur-3xl" />
          <SplineScene
            scene="https://prod.spline.design/kZDDjO5HuC9GJUM2/scene.splinecode"
            className="w-full h-full"
          />
        </div>
      </div>
    </section>
  )
}
