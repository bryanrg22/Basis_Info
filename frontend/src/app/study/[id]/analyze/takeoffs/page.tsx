'use client';

/**
 * Legacy redirect page - redirects to the new engineering-takeoff page
 */

import { useEffect } from 'react';
import { useParams, useRouter } from 'next/navigation';

export default function LegacyAnalyzeTakeoffsRedirect() {
  const params = useParams();
  const router = useRouter();
  const studyId = params.id as string;

  useEffect(() => {
    // Redirect to new engineering takeoff page
    router.replace(`/study/${studyId}/engineering-takeoff`);
  }, [router, studyId]);

  return (
    <div className="flex items-center justify-center min-h-screen">
      <div className="text-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600 mx-auto mb-4"></div>
        <p className="text-gray-600">Redirecting...</p>
      </div>
    </div>
  );
}

