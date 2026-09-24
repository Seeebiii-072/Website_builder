/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{js,ts,jsx,tsx}", "./components/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        base: "#0b0d12",
        panel: "#12151c",
        border: "#1f2430",
      },
    },
  },
  plugins: [],
};
