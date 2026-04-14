'use client'

import { Card } from '@/components/ui/card'
import { Check, Zap } from 'lucide-react'

const plans = [
  {
    name: 'Free',
    price: '$0',
    period: '/month',
    description: 'Ideal for solo bloggers and small sites.',
    cta: 'Get Started Free',
    ctaStyle: 'border border-white/10 hover:border-white/20 text-white',
    highlight: false,
    features: [
      '200 pages / month',
      '3 saved projects',
      'Full SEO audit',
      'AI issue scoring',
      'PDF export',
      'SERP tracker (2 jobs)',
    ],
  },
  {
    name: 'Pro',
    price: '$29',
    period: '/month',
    description: 'For freelancers and growing businesses.',
    cta: 'Upgrade to Pro',
    ctaStyle: 'bg-indigo-600 hover:bg-indigo-500 text-white',
    highlight: true,
    badge: 'Most Popular',
    features: [
      '5,000 pages / month',
      '20 saved projects',
      'Everything in Free',
      'Email rank-drop alerts',
      '15 monitor jobs',
      'White-label PDF',
      'Team workspace',
      'API access',
    ],
  },
  {
    name: 'Agency',
    price: '$99',
    period: '/month',
    description: 'Unlimited scale for SEO agencies.',
    cta: 'Start Agency Trial',
    ctaStyle: 'bg-yellow-500 hover:bg-yellow-400 text-black font-bold',
    highlight: false,
    features: [
      'Unlimited pages',
      'Unlimited projects',
      'Everything in Pro',
      'Unlimited monitor jobs',
      'Priority support',
      'Custom branding',
      'Dedicated onboarding',
    ],
  },
]

export function PricingSection() {
  return (
    <section id="pricing" className="relative py-24 bg-[#030303]">
      <div className="absolute inset-0 bg-grid opacity-20" />

      <div className="relative z-10 max-w-6xl mx-auto px-6">
        <div className="text-center mb-16">
          <p className="text-indigo-400 text-sm font-semibold tracking-widest uppercase mb-3">
            Simple pricing
          </p>
          <h2 className="text-4xl md:text-5xl font-extrabold text-white">
            Invest in rankings, not tools
          </h2>
          <p className="mt-4 text-neutral-400 max-w-xl mx-auto">
            No credit card required to start. Upgrade when you need more crawl credits or
            team features.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 items-center">
          {plans.map((plan) => (
            <Card
              key={plan.name}
              className={`relative p-6 flex flex-col gap-5 transition-all duration-300 ${
                plan.highlight
                  ? 'bg-indigo-950/60 border-indigo-500/50 shadow-xl shadow-indigo-900/30 scale-[1.03]'
                  : 'bg-white/[0.02] border-white/[0.06] hover:border-white/[0.12]'
              }`}
            >
              {plan.badge && (
                <div className="absolute -top-3 left-1/2 -translate-x-1/2">
                  <span className="inline-flex items-center gap-1 bg-indigo-600 text-white text-[10px] font-bold px-3 py-1 rounded-full">
                    <Zap className="w-2.5 h-2.5" />
                    {plan.badge}
                  </span>
                </div>
              )}

              <div>
                <p className="text-sm text-neutral-400 font-medium mb-1">{plan.name}</p>
                <div className="flex items-end gap-1">
                  <span className="text-4xl font-extrabold text-white">{plan.price}</span>
                  <span className="text-neutral-500 text-sm mb-1">{plan.period}</span>
                </div>
                <p className="text-xs text-neutral-500 mt-1">{plan.description}</p>
              </div>

              <ul className="space-y-2.5 flex-1">
                {plan.features.map((f) => (
                  <li key={f} className="flex items-center gap-2.5">
                    <Check className="w-3.5 h-3.5 text-emerald-400 flex-shrink-0" />
                    <span className="text-sm text-neutral-300">{f}</span>
                  </li>
                ))}
              </ul>

              <a
                href="http://localhost:7860"
                target="_blank"
                rel="noopener noreferrer"
                className={`inline-flex justify-center items-center px-4 py-2.5 rounded-lg text-sm font-semibold transition-colors ${plan.ctaStyle}`}
              >
                {plan.cta}
              </a>
            </Card>
          ))}
        </div>

        <p className="text-center text-neutral-600 text-xs mt-8">
          All plans include the full crawler, AI analysis, and technical SEO audit. Cancel anytime.
        </p>
      </div>
    </section>
  )
}
