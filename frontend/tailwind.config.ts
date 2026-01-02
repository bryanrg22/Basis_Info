import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "var(--background)",
        foreground: "var(--foreground)",
        primary: {
          50: '#f0f7f4',
          100: '#d9ebe3',
          200: '#b3d7c7',
          300: '#8dc3ab',
          400: '#8fb8a6',
          500: '#669A80',
          600: '#558a6d',
          700: '#456d5a',
          800: '#355047',
        },
        secondary: {
          50: '#f8fafc',
          100: '#f1f5f9',
          500: '#64748b',
          600: '#475569',
        },
      },
    },
  },
  plugins: [],
};
export default config;
