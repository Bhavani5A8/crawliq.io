'use client'

import { SplineScene } from '@/components/ui/spline-scene'
import { Card } from '@/components/ui/card'
import { Spotlight } from '@/components/ui/spotlight'
import { TrendingUp, FileSearch, BrainCircuit, Globe } from 'lucide-react'

const stats = [
  { icon: Globe,         value: '5,000',  unit: 'pages',   label: 'per crawl run',        color: 'text-indigo-400', bg: 'bg-indigo-500/10' },
  { icon: FileSearch,    value: '50+',    unit: 'signals', label: 'checked per page',     color: 'text-cyan-400',   bg: 'bg-cyan-500/10' },
  { icon: BrainCircuit,  value: '5',      unit: 'AI',      label: 'providers supported',  color: 'text-purple-400', bg: 'bg-purple-500/10' },
  { icon: TrendingUp,    value: '< 3',    unit: 'min',     label: 'full site audit',      color: 'text-emerald-400',bg: 'bg-emerald-500/10' },
]

export function StatsSection() {
  return (
    <section className="relative py-16 bg-black overflow-hidden">
      {/* Spline ambient card */}
      <Card className="max-w-7xl mx-auto mx-6 h-[420px] bg-black/[0.96] relative overflow-hidden border-white/[0.06]">
        <Spotlight className="-top-40 left-0 md:left-60 md:-top-20" fill="white" />

        <div className="flex h-full">
          {/* Left: stats */}
          <div className="flex-1 p-8 relative z-10 flex flex-col justify-center gap-6">
            <div>
              <p className="text-xs text-indigo-400 font-semibold tracking-widest uppercase mb-2">
                By the numbers
              </p>
              <h2 className="text-3xl md:text-4xl font-extrabold">
                <span className="bg-clip-text text-transparent bg-gradient-to-b from-neutral-50 to-neutral-400">
                  Fast, deep, and accurate
                </span>
              </h2>
              <p className="mt-2 text-sm text-neutral-400">
                CrawlIQ delivers enterprise-grade SEO intelligence at any scale.
              </p>
            </div>

            <div className="grid grid-cols-2 gap-3">
              {stats.map((s) => {
                const Icon = s.icon
                return (
                  <div
                    key={s.label}
                    className="flex items-center gap-3 bg-white/[0.03] border border-white/[0.06] rounded-lg p-3"
                  >
                    <div className={`p-2 rounded-lg ${s.bg}`}>
                      <Icon className={`w-4 h-4 ${s.color}`} />
                    </div>
                    <div>
                      <div className="flex items-baseline gap-1">
                        <span className={`text-xl font-extrabold ${s.color}`}>{s.value}</span>
                        <span className="text-xs text-neutral-500">{s.unit}</span>
                      </div>
                      <p className="text-[10px] text-neutral-600">{s.label}</p>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>

          {/* Right: 3D Spline */}
          <div className="flex-1 relative">
            <SplineScene
              scene="https://prod.spline.design/kZDDjO5HuC9GJUM2/scene.splinecode"
              className="w-full h-full"
            />
          </div>
        </div>
      </Card>
    </section>
  )
}
