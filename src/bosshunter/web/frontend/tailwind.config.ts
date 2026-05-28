/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        background: '#09090b',
        foreground: '#fafafa',
        card: '#18181b',
        'card-border': '#27272a',
        primary: '#3b82f6',
        success: '#22c55e',
        warning: '#f59e0b',
        danger: '#ef4444',
        muted: '#a1a1aa',
      },
    },
  },
  plugins: [],
}
