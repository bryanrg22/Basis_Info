/**
 * Page Layout Component
 * 
 * Reusable layout wrapper that includes ProtectedRoute, Sidebar, and Header.
 * Eliminates duplication across all pages.
 */

'use client';

import { ReactNode } from 'react';
import ProtectedRoute from '@/components/ProtectedRoute';
import Header from '@/components/Header';
import Sidebar from '@/components/Sidebar';

interface PageLayoutProps {
  children: ReactNode;
}

/**
 * Standard page layout with sidebar and header
 */
export default function PageLayout({ children }: PageLayoutProps) {
  return (
    <ProtectedRoute>
      <div className="flex h-screen">
        <Sidebar />
        <div className="flex-1 flex flex-col overflow-hidden">
          <Header />
          <div className="flex-1 overflow-y-auto">
            {children}
          </div>
        </div>
      </div>
    </ProtectedRoute>
  );
}

