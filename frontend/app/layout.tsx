import './globals.css';

export const metadata = {
  title: 'AI Website Builder',
  description: 'Describe a website and watch AI build it live.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
