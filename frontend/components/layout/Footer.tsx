import { Zap, ExternalLink } from 'lucide-react'

export function Footer() {
  return (
    <footer className="bg-[#030303] border-t border-white/[0.06] py-12">
      <div className="max-w-7xl mx-auto px-6 flex flex-col md:flex-row items-center justify-between gap-6">
        {/* Brand */}
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded-md bg-indigo-600 flex items-center justify-center">
            <Zap className="w-3.5 h-3.5 text-white" />
          </div>
          <span className="text-white font-bold">
            Crawl<span className="text-indigo-400">IQ</span>
          </span>
          <span className="text-neutral-600 text-sm ml-2">
            AI-Powered SEO Intelligence
          </span>
        </div>

        {/* Links */}
        <div className="flex items-center gap-6 text-sm text-neutral-500">
          <a href="#features" className="hover:text-white transition-colors">Features</a>
          <a href="#pricing" className="hover:text-white transition-colors">Pricing</a>
          <a
            href="https://github.com/Bhavani5A8/seo-project"
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-white transition-colors flex items-center gap-1"
          >
            <ExternalLink className="w-3 h-3" />
            GitHub
          </a>
        </div>

        <p className="text-xs text-neutral-700">
          &copy; {new Date().getFullYear()} CrawlIQ. All rights reserved.
        </p>
      </div>
    </footer>
  )
}
