'use client';

/**
 * Engineering Takeoff - Asset Page
 *
 * Main page for performing detailed takeoffs on individual assets.
 * Loads data from the study in Firestore.
 */

import { useEffect, useState, useMemo } from 'react';
import { useParams, useSearchParams, useRouter } from 'next/navigation';
import { useApp } from '@/contexts/AppContext';
import { EngineeringTakeoffClient } from './client';
import { AssetDemo, AssetTakeoffDemo, TabId } from '@/types/asset-takeoff.types';
import { Asset } from '@/types';
import ProtectedRoute from '@/components/ProtectedRoute';
import Header from '@/components/Header';
import Sidebar from '@/components/Sidebar';
import StudyBackButton from '@/components/StudyBackButton';

/**
 * Convert Study Asset to AssetDemo format
 */
function assetToAssetDemo(asset: Asset, propertyName: string): AssetDemo {
  return {
    id: asset.id,
    name: asset.name,
    propertyName,
    discipline: 'ARCHITECTURAL', // Default discipline
    location: undefined,
    description: asset.description,
    specSection: undefined,
    status: asset.verified ? 'COMPLETED' : 'NOT_STARTED',
  };
}

/**
 * Create default empty takeoff data for an asset
 */
function createDefaultTakeoff(assetId: string): AssetTakeoffDemo {
  return {
    assetId,
    quantity: {
      autoDetectedQuantity: undefined,
      autoDetectedUnit: undefined,
      manualQuantity: null,
      manualUnit: null,
      drawingSnippets: [],
      photos: [],
    },
    classification: {
      suggestedCode: '',
      suggestedDescription: '',
      confidence: 0,
      appliedCode: undefined,
      appliedDescription: undefined,
      decisionSource: undefined,
      wizardAnswers: {},
      irsRuleRefs: [],
    },
    costs: {
      actualTotal: 0,
      estimatedTotal: 0,
      currency: 'USD',
      breakdown: [],
      historicalNotes: undefined,
    },
    docs: {
      notes: '',
      attachments: [],
      autoSummary: '',
    },
    references: [],
  };
}

export default function EngineeringTakeoffAssetPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const router = useRouter();
  const { state } = useApp();

  const studyId = params.id as string;
  const assetId = params.assetId as string;
  const tab = searchParams.get('tab');

  const study = state.studies.find((s) => s.id === studyId);
  const [isLoading, setIsLoading] = useState(true);

  // Convert study assets to AssetDemo format
  const { asset, takeoff, allAssets, relatedAssets } = useMemo(() => {
    if (!study) {
      return { asset: null, takeoff: null, allAssets: [], relatedAssets: [] };
    }

    const studyAssets = study.assets || [];
    const allAssetDemos = studyAssets.map((a) =>
      assetToAssetDemo(a, study.propertyName)
    );

    const currentAsset = allAssetDemos.find((a) => a.id === assetId) || null;
    const relatedAssetDemos = allAssetDemos.filter((a) => a.id !== assetId);

    // For now, use default empty takeoff data
    // In the future, this would come from study.engineeringTakeoffState or backend
    const takeoffData = currentAsset ? createDefaultTakeoff(assetId) : null;

    return {
      asset: currentAsset,
      takeoff: takeoffData,
      allAssets: allAssetDemos,
      relatedAssets: relatedAssetDemos,
    };
  }, [study, assetId]);

  useEffect(() => {
    if (!study) {
      router.push('/dashboard');
      return;
    }
    setIsLoading(false);
  }, [study, router]);

  // Validate tab parameter
  const validTabs = [
    'overview',
    'quantity',
    'classification',
    'costs',
    'documentation',
  ] as const;
  const initialTab = validTabs.includes(tab as TabId) ? (tab as TabId) : 'overview';

  if (isLoading || !study) {
    return (
      <ProtectedRoute>
        <div className="flex h-screen bg-gray-50">
          <Sidebar currentStudyId={studyId} />
          <div className="flex-1 flex flex-col min-w-0">
            <Header />
            <main className="flex-1 overflow-auto p-6">
              <div className="flex items-center justify-center min-h-[60vh]">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
              </div>
            </main>
          </div>
        </div>
      </ProtectedRoute>
    );
  }

  if (!asset || !takeoff) {
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
                    Asset Not Found
                  </h1>
                  <p className="text-gray-600">
                    The requested asset could not be found in this study.
                  </p>
                </div>
              </div>
            </main>
          </div>
        </div>
      </ProtectedRoute>
    );
  }

  return (
    <EngineeringTakeoffClient
      studyId={studyId}
      asset={asset}
      takeoff={takeoff}
      allAssets={allAssets}
      relatedAssets={relatedAssets}
      initialTab={initialTab}
    />
  );
}
