'use client';

/**
 * Engineering Takeoff Landing Page
 *
 * Loads assets from the study and redirects to the first asset.
 */

import { useEffect } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { useApp } from '@/contexts/AppContext';
import ProtectedRoute from '@/components/ProtectedRoute';
import Header from '@/components/Header';
import Sidebar from '@/components/Sidebar';
import StudyBackButton from '@/components/StudyBackButton';

export default function EngineeringTakeoffPage() {
  const params = useParams();
  const router = useRouter();
  const { state } = useApp();

  const studyId = params.id as string;
  const study = state.studies.find((s) => s.id === studyId);

  useEffect(() => {
    if (!study) {
      router.push('/dashboard');
      return;
    }

    // Check if we have assets to show
    const assets = study.assets || [];
    if (assets.length > 0) {
      // Redirect to the first asset
      router.push(`/study/${studyId}/engineering-takeoff/${assets[0].id}`);
    }
  }, [study, studyId, router]);

  // Show loading or empty state while checking
  const assets = study?.assets || [];

  if (!study) {
    return null; // Will redirect to dashboard
  }

  if (assets.length === 0) {
    return (
      <ProtectedRoute>
        <div className="flex h-screen bg-gray-50">
          <Sidebar currentStudyId={studyId} />
          <div className="flex-1 flex flex-col min-w-0">
            <Header />
            <main className="flex-1 overflow-auto p-6">
              <StudyBackButton studyId={studyId} />
              <div className="flex items-center justify-center min-h-[60vh]">
                <div className="text-center">
                  <h1 className="text-2xl font-semibold text-gray-900 mb-2">
                    No Assets Found
                  </h1>
                  <p className="text-gray-600">
                    There are no assets available for takeoff. Please ensure the
                    analysis workflow has completed.
                  </p>
                </div>
              </div>
            </main>
          </div>
        </div>
      </ProtectedRoute>
    );
  }

  // Show loading while redirecting
  return (
    <ProtectedRoute>
      <div className="flex h-screen bg-gray-50">
        <Sidebar currentStudyId={studyId} />
        <div className="flex-1 flex flex-col min-w-0">
          <Header />
          <main className="flex-1 overflow-auto p-6">
            <div className="flex items-center justify-center min-h-[60vh]">
              <div className="text-center">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600 mx-auto mb-4"></div>
                <p className="text-gray-600">Loading assets...</p>
              </div>
            </div>
          </main>
        </div>
      </div>
    </ProtectedRoute>
  );
}
