import { Navbar } from '@/components/layout/Navbar'
import { Footer } from '@/components/layout/Footer'
import { HeroSection } from '@/components/sections/HeroSection'
import { FeaturesSection } from '@/components/sections/FeaturesSection'
import { HowItWorksSection } from '@/components/sections/HowItWorksSection'
import { StatsSection } from '@/components/sections/StatsSection'
import { SocialProofSection } from '@/components/sections/SocialProofSection'
import { PricingSection } from '@/components/sections/PricingSection'
import { CTASection } from '@/components/sections/CTASection'

export default function Home() {
  return (
    <>
      <Navbar />
      <main className="pt-16">
        <HeroSection />
        <FeaturesSection />
        <div id="how-it-works">
          <HowItWorksSection />
        </div>
        <StatsSection />
        <SocialProofSection />
        <PricingSection />
        <CTASection />
      </main>
      <Footer />
    </>
  )
}
