'use client'

import { Card } from '@/components/ui/card'
import { Star } from 'lucide-react'

const testimonials = [
  {
    quote:
      "CrawlIQ found 47 critical issues our previous tool completely missed — including broken hreflang tags that were cannibalising our international traffic. Fixed in a week, rankings up 30%.",
    author: 'Priya Sharma',
    role: 'Head of SEO, FinTech Startup',
    avatar: 'https://images.unsplash.com/photo-1494790108755-2616b612b786?w=60&h=60&fit=crop',
    stars: 5,
  },
  {
    quote:
      "The AI analysis is genuinely insightful — not just a list of 'add keywords'. It tells me exactly which pages are losing authority and why. My agency uses it on every client audit.",
    author: 'Marcus T.',
    role: 'Founder, GrowthStack Agency',
    avatar: 'https://images.unsplash.com/photo-1472099645785-5658abf4ff4e?w=60&h=60&fit=crop',
    stars: 5,
  },
  {
    quote:
      "Competitor intelligence is a game-changer. I can see exactly what keywords my rivals rank for that I don't — and the keyword gap export feeds straight into my content calendar.",
    author: 'Chen Wei',
    role: 'Content Strategist, SaaS Company',
    avatar: 'https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=60&h=60&fit=crop',
    stars: 5,
  },
]

export function SocialProofSection() {
  return (
    <section className="relative py-24 bg-[#030303]">
      <div className="max-w-6xl mx-auto px-6">
        <div className="text-center mb-14">
          <p className="text-yellow-400 text-sm font-semibold tracking-widest uppercase mb-3">
            Trusted by SEO professionals
          </p>
          <h2 className="text-4xl font-extrabold text-white">
            Results that speak for themselves
          </h2>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {testimonials.map((t) => (
            <Card
              key={t.author}
              className="bg-white/[0.02] border-white/[0.06] hover:border-white/[0.10] p-6 flex flex-col gap-4 transition-all duration-300"
            >
              <div className="flex gap-0.5">
                {Array.from({ length: t.stars }).map((_, i) => (
                  <Star key={i} className="w-3.5 h-3.5 fill-yellow-400 text-yellow-400" />
                ))}
              </div>
              <p className="text-sm text-neutral-300 leading-relaxed flex-1">
                &ldquo;{t.quote}&rdquo;
              </p>
              <div className="flex items-center gap-3 pt-2 border-t border-white/[0.06]">
                <img
                  src={t.avatar}
                  alt={t.author}
                  className="w-9 h-9 rounded-full object-cover"
                />
                <div>
                  <p className="text-sm font-semibold text-white">{t.author}</p>
                  <p className="text-xs text-neutral-500">{t.role}</p>
                </div>
              </div>
            </Card>
          ))}
        </div>

        {/* Brand logos row */}
        <div className="mt-16 text-center">
          <p className="text-xs text-neutral-600 uppercase tracking-widest mb-8">
            Trusted by teams at
          </p>
          <div className="flex flex-wrap justify-center items-center gap-8 opacity-30 grayscale">
            {['Shopify', 'Notion', 'Linear', 'Vercel', 'Stripe', 'Figma'].map((brand) => (
              <span key={brand} className="text-white font-bold text-lg tracking-tight">
                {brand}
              </span>
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}
