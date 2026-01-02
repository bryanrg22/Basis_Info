import './globals.css';
import { Inter } from 'next/font/google';
import { AppProvider } from '@/contexts/AppContext';
import { AuthProvider } from '@/contexts/AuthContext';
import { UserSync } from '@/components/UserSync';

const inter = Inter({ subsets: ['latin'] });

export const metadata = {
  title: 'Basis',
  description: 'AI-Native Cost Segregation Platform',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className={inter.className}>
        <AuthProvider>
          <AppProvider>
            <UserSync />
            <main className="min-h-screen bg-gray-50">
              {children}
            </main>
          </AppProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
