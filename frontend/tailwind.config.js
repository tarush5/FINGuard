/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Deep charcoal / near-black base with a subtle navy cast.
        void: '#05080D',
        base: '#080C13',
        surface: '#0C121B',
        panel: '#111925',
        raised: '#16202E',
        line: '#1D2836',
        'line-strong': '#2A3849',
        ink: '#E8EEF6',
        muted: '#8A99AD',
        faint: '#5C6980',
        // Semantic accents. Purple is reserved for AI surfaces only.
        info: '#38BDF8',
        positive: '#34D399',
        warning: '#FBBF24',
        critical: '#F87171',
        high: '#FB923C',
        ai: '#A78BFA',
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem', letterSpacing: '0.02em' }],
      },
      boxShadow: {
        panel: '0 1px 0 0 rgba(255,255,255,0.03) inset, 0 12px 32px -16px rgba(0,0,0,0.9)',
        glow: '0 0 0 1px rgba(56,189,248,0.25), 0 0 28px -6px rgba(56,189,248,0.35)',
        'glow-critical': '0 0 0 1px rgba(248,113,113,0.3), 0 0 28px -6px rgba(248,113,113,0.4)',
      },
      backgroundImage: {
        grid: 'linear-gradient(rgba(255,255,255,0.028) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.028) 1px, transparent 1px)',
        'radial-fade': 'radial-gradient(60% 60% at 50% 0%, rgba(56,189,248,0.10) 0%, transparent 70%)',
      },
      backgroundSize: { grid: '44px 44px' },
      keyframes: {
        'pulse-ring': {
          '0%': { transform: 'scale(0.9)', opacity: '0.7' },
          '70%': { transform: 'scale(1.35)', opacity: '0' },
          '100%': { transform: 'scale(1.35)', opacity: '0' },
        },
        'slide-up': {
          from: { opacity: '0', transform: 'translateY(6px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        shimmer: {
          '100%': { transform: 'translateX(100%)' },
        },
        'flow-dash': { to: { strokeDashoffset: '-24' } },
      },
      animation: {
        'pulse-ring': 'pulse-ring 2.4s cubic-bezier(0.4,0,0.6,1) infinite',
        'slide-up': 'slide-up 0.28s ease-out',
        shimmer: 'shimmer 1.6s infinite',
        'flow-dash': 'flow-dash 1s linear infinite',
      },
      transitionTimingFunction: {
        swift: 'cubic-bezier(0.22, 1, 0.36, 1)',
      },
    },
  },
  plugins: [],
};
