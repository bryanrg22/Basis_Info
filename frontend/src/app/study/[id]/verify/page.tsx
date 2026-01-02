'use client';

import { useState, useEffect, useMemo, useRef } from 'react';
import { useApp } from '@/contexts/AppContext';
import { formatCurrency } from '@/utils/formatting';
import { useParams, useRouter } from 'next/navigation';
import { Asset, UploadedFile, Study } from '@/types';
import { studyService } from '@/services/study.service';
import { Unsubscribe } from 'firebase/firestore';
import PageLayout from '@/components/layout/PageLayout';
import AssetVerificationCard from '@/components/asset-verification/AssetVerificationCard';
import ProgressIndicator from '@/components/asset-verification/ProgressIndicator';
import DocumentAssociationPanel from '@/components/asset-verification/DocumentAssociationPanel';
import { useAssetVerification } from '@/hooks/useAssetVerification';
import { useFileAssociation } from '@/hooks/useFileAssociation';
import { useFileFilter } from '@/hooks/useFileFilter';

/**
 * Asset Verification Review Page
 * 
 * One-by-one asset verification interface that guides users through verifying each asset.
 * Features:
 * - Progress tracking
 * - Asset-by-asset review
 * - Value editing
 * - Document association
 * - File preview
 * - Navigation controls
 */
export default function AssetVerificationReviewPage() {
  const { state, dispatch, updateWorkflowStatus } = useApp();
  const params = useParams();
  const router = useRouter();
  const studyId = params && params.id ? String(params.id) : '';

  const study = state.studies.find(s => s.id === studyId);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [lastSaveError, setLastSaveError] = useState<string | null>(null);
  const [lastSaveTime, setLastSaveTime] = useState<Date | null>(null);
  
  // UI state
  const [fileViewMode, setFileViewMode] = useState<'grid' | 'list'>('grid');
  const [fileSearchQuery, setFileSearchQuery] = useState('');
  const [showOtherFiles, setShowOtherFiles] = useState(false);
  
  // Refs for real-time sync
  const unsubscribeRef = useRef<Unsubscribe | null>(null);
  const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const fileSaveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isLocalUpdateRef = useRef<boolean>(false);
  const lastLocalUpdateRef = useRef<number>(0);
  const originalAssetValuesRef = useRef<Map<string, number>>(new Map());

  // Load study data and set up real-time listener
  useEffect(() => {
    if (!studyId) {
      router.push('/dashboard');
      return;
    }

    if (!study) {
      router.push('/dashboard');
      return;
    }

    // Navigation guard: redirect to appropriate page based on workflow status
    const status = study.workflowStatus;
    if (
      status === 'uploading_documents' ||
      status === 'analyzing_rooms' ||
      status === 'resource_extraction' ||
      status === 'reviewing_rooms' ||
      status === 'engineering_takeoff'
    ) {
      if (status === 'uploading_documents' || status === 'analyzing_rooms') {
        router.push(`/study/${studyId}/analyze/first`);
      } else if (status === 'resource_extraction') {
        router.push(`/study/${studyId}/review/resources`);
      } else if (status === 'reviewing_rooms') {
        router.push(`/study/${studyId}/review/first`);
      } else if (status === 'engineering_takeoff') {
        router.push(`/study/${studyId}/engineering-takeoff`);
      }
      return;
    }
    
    if (status === 'completed') {
      router.push(`/study/${studyId}/complete`);
      return;
    }

    setLoading(true);

    // Load initial data
    if (study.assets && Array.isArray(study.assets)) {
      const validAssets = study.assets
        .map(asset => {
          if (typeof asset === 'string') {
            try {
              return JSON.parse(asset);
            } catch (e) {
              console.warn('Failed to parse asset string:', asset);
              return null;
            }
          }
          if (asset && typeof asset === 'object' && 'id' in asset && 'estimatedValue' in asset) {
            return asset;
          }
          return null;
        })
        .filter((a): a is Asset => a !== null && typeof a === 'object' && 'id' in a && 'estimatedValue' in a);
      
      setAssets(validAssets);
      
      // Store original values for reference
      validAssets.forEach(asset => {
        originalAssetValuesRef.current.set(asset.id, asset.estimatedValue);
      });
    } else {
      setAssets([]);
    }

    // Load files
    if (study.uploadedFiles && Array.isArray(study.uploadedFiles)) {
      setFiles(study.uploadedFiles);
    } else {
      setFiles([]);
    }

    setLoading(false);

    // Set up real-time listener
    unsubscribeRef.current = studyService.subscribe(
      studyId,
      (updatedStudy) => {
        if (!updatedStudy) {
          return;
        }
        
        const rawAssets = updatedStudy.assets || [];
        const firestoreAssets = rawAssets
          .map(asset => {
            if (typeof asset === 'string') {
              try {
                return JSON.parse(asset);
              } catch (e) {
                return null;
              }
            }
            if (asset && typeof asset === 'object' && 'id' in asset) {
              return asset;
            }
            return null;
          })
          .filter((a): a is Asset => a !== null && typeof a === 'object' && 'id' in a && 'estimatedValue' in a);
        
        // Don't overwrite local changes
        const timeSinceLastLocalUpdate = Date.now() - lastLocalUpdateRef.current;
        if (isLocalUpdateRef.current && timeSinceLastLocalUpdate < 2000) {
          return;
        }
        
        setAssets(currentAssets => {
          const currentAssetsJson = JSON.stringify(currentAssets);
          const firestoreAssetsJson = JSON.stringify(firestoreAssets);
          
          if (currentAssetsJson !== firestoreAssetsJson) {
            if (!isLocalUpdateRef.current) {
              setLoading(false);
              return firestoreAssets;
            }
          }
          
          return currentAssets;
        });

        // Update files
        if (updatedStudy.uploadedFiles && Array.isArray(updatedStudy.uploadedFiles)) {
          setFiles(updatedStudy.uploadedFiles);
        }
      }
    );

    return () => {
      if (unsubscribeRef.current) {
        unsubscribeRef.current();
        unsubscribeRef.current = null;
      }
    };
  }, [studyId, study, router]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (saveTimeoutRef.current) {
        clearTimeout(saveTimeoutRef.current);
        saveTimeoutRef.current = null;
      }
      if (fileSaveTimeoutRef.current) {
        clearTimeout(fileSaveTimeoutRef.current);
        fileSaveTimeoutRef.current = null;
      }
    };
  }, []);

  // Auto-save assets with debouncing
  const saveAssetsToFirestore = async (assetsToSave: Asset[]) => {
    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current);
      saveTimeoutRef.current = null;
    }
    
    const assetsToSaveCopy = [...assetsToSave];
    const currentStudyId = studyId;
    
    const performSave = async () => {
      saveTimeoutRef.current = null;
      setSaveStatus('saving');
      setLastSaveError(null);
      
      try {
        if (!Array.isArray(assetsToSaveCopy) || !currentStudyId) {
          throw new Error('Invalid data');
        }
        
        await studyService.updateAssets(currentStudyId, assetsToSaveCopy);
        
        setSaveStatus('saved');
        setLastSaveTime(new Date());
        
        setTimeout(() => {
          isLocalUpdateRef.current = false;
        }, 1000);
        
        setTimeout(() => {
          setSaveStatus(prev => prev === 'saved' ? 'idle' : prev);
        }, 3000);
        
      } catch (error) {
        const errorMessage = error instanceof Error ? error.message : 'Unknown error';
        setSaveStatus('error');
        setLastSaveError(errorMessage);
        isLocalUpdateRef.current = false;
      }
    };
    
    const timeoutId = setTimeout(() => {
      performSave().catch(error => {
        setSaveStatus('error');
        setLastSaveError(error instanceof Error ? error.message : 'Unknown error');
        isLocalUpdateRef.current = false;
      });
    }, 500);
    
    saveTimeoutRef.current = timeoutId;
  };

  // Auto-save files with debouncing
  const saveFilesToFirestore = async (filesToSave: UploadedFile[]) => {
    if (!study) return;
    
    if (fileSaveTimeoutRef.current) {
      clearTimeout(fileSaveTimeoutRef.current);
      fileSaveTimeoutRef.current = null;
    }
    
    const filesToSaveCopy = [...filesToSave];
    const currentStudyId = studyId;
    
    const performSave = async () => {
      fileSaveTimeoutRef.current = null;
      
      try {
        await studyService.update(currentStudyId, { uploadedFiles: filesToSaveCopy });
      } catch (error) {
        console.error('Error saving files:', error);
      }
    };
    
    const timeoutId = setTimeout(() => {
      performSave().catch(error => {
        console.error('Error in file save:', error);
      });
    }, 500);
    
    fileSaveTimeoutRef.current = timeoutId;
  };

  // Handle asset update
  const handleAssetUpdate = (assetId: string, updates: Partial<Asset>) => {
    isLocalUpdateRef.current = true;
    lastLocalUpdateRef.current = Date.now();
    
    const updatedAssets = assets.map(asset => {
      if (asset.id === assetId) {
        return { ...asset, ...updates };
      }
      return asset;
    });

    // Recalculate percentages
    const total = updatedAssets.reduce((sum, a) => sum + (a.estimatedValue || 0), 0);
    if (total > 0) {
      updatedAssets.forEach(asset => {
        asset.percentageOfTotal = Math.round((asset.estimatedValue / total) * 100 * 100) / 100;
      });
    }
    
    setAssets(updatedAssets);
    saveAssetsToFirestore(updatedAssets);
    
    dispatch({
      type: 'UPDATE_ASSET',
      payload: {
        studyId,
        assetId,
        updates: updatedAssets.find(a => a.id === assetId)!,
      },
    });
  };

  // Handle asset verification
  const handleAssetVerify = (assetId: string) => {
    handleAssetUpdate(assetId, { verified: true });
  };

  // Use verification hook (must be before handlers that use it)
  const verification = useAssetVerification({
    assets,
    onAssetUpdate: handleAssetUpdate,
    onAssetVerify: handleAssetVerify,
  });

  // Handle file association
  const handleAssociateFile = (fileId: string) => {
    if (!verification.currentAsset) return;
    
    const updatedFiles = files.map(file => {
      if (file.id === fileId) {
        const assetIds = file.assetIds || [];
        if (!assetIds.includes(verification.currentAsset.id)) {
          return { ...file, assetIds: [...assetIds, verification.currentAsset.id] };
        }
      }
      return file;
    });
    setFiles(updatedFiles);
    saveFilesToFirestore(updatedFiles);
  };

  // Handle file disassociation
  const handleDisassociateFile = (fileId: string) => {
    if (!verification.currentAsset) return;
    
    const updatedFiles = files.map(file => {
      if (file.id === fileId) {
        const assetIds = (file.assetIds || []).filter(id => id !== verification.currentAsset.id);
        return { ...file, assetIds: assetIds.length > 0 ? assetIds : undefined };
      }
      return file;
    });
    setFiles(updatedFiles);
    saveFilesToFirestore(updatedFiles);
  };

  // Use file association hook
  const fileAssociation = useFileAssociation({
    files,
    currentAssetId: verification.currentAsset?.id || '',
  });

  // Filter files
  const { filteredFiles: filteredAssociatedFiles } = useFileFilter({
    files: fileAssociation.associatedFiles,
    searchQuery: fileSearchQuery,
  });

  const { filteredFiles: filteredOtherFiles } = useFileFilter({
    files: fileAssociation.otherFiles,
    searchQuery: fileSearchQuery,
  });

  // Handle completion
  const handleComplete = async () => {
    // Safety check: ensure all assets are verified
    const unverifiedAssets = assets.filter(asset => !asset.verified);
    if (unverifiedAssets.length > 0) {
      alert(`Please verify all assets before completing. ${unverifiedAssets.length} asset(s) remaining.`);
      return;
    }

    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current);
      saveTimeoutRef.current = null;
    }
    
    try {
      // Ensure all assets are saved
      await studyService.updateAssets(studyId, assets);
      
      const totalValue = assets.reduce((sum, asset) => sum + (asset.estimatedValue || 0), 0);
      
      dispatch({
        type: 'UPDATE_STUDY',
        payload: {
          ...study!,
          assets,
          totalAssets: totalValue,
        },
      });

      // Update workflow status to completed
      await updateWorkflowStatus(studyId, 'completed');
      
      // Navigate to completion page
      router.push(`/study/${studyId}/complete`);
    } catch (error) {
      console.error('Error completing verification:', error);
      alert('Failed to complete verification. Please try again.');
    }
  };

  // Early returns
  if (!study) {
    return (
      <PageLayout>
        <div className="p-6">Loading...</div>
      </PageLayout>
    );
  }

  if (loading && assets.length === 0) {
    return (
      <PageLayout>
        <div className="flex items-center justify-center min-h-[400px]">
          <div className="text-center">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary-600 mx-auto mb-4"></div>
            <p className="text-gray-600">Loading assets...</p>
          </div>
        </div>
      </PageLayout>
    );
  }

  if (assets.length === 0) {
    return (
      <PageLayout>
        <div className="p-6">
          <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6 text-center">
            <p className="text-gray-600">No assets found. Please complete asset analysis first.</p>
          </div>
        </div>
      </PageLayout>
    );
  }

  if (!verification.currentAsset) {
    return (
      <PageLayout>
        <div className="p-6">
          <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6 text-center">
            <p className="text-gray-600">No assets to verify.</p>
          </div>
        </div>
      </PageLayout>
    );
  }

  const originalValue = originalAssetValuesRef.current.get(verification.currentAsset.id);

  return (
    <PageLayout>
      {/* Full-height light-gray canvas */}
      <div className="min-h-screen bg-gray-100">
        {/* Centered content column */}
        <div className="max-w-7xl mx-auto px-6 py-8">
          {/* Header Zone */}
          <div className="mb-6">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-4">
                {/* Back Button */}
                <button
                  onClick={() => router.back()}
                  className="flex items-center justify-center w-10 h-10 rounded-lg bg-white hover:bg-gray-50 active:bg-gray-100 transition-all duration-200"
                  aria-label="Go back"
                >
                  <svg className="w-5 h-5 text-gray-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                  </svg>
                </button>
                <div>
                  <h1 className="text-2xl font-bold text-gray-900">Asset Verification</h1>
                  <p className="text-sm text-gray-600 mt-1">
                    Review and confirm each asset's value. Ensure every asset is verified before completing.
                  </p>
                </div>
              </div>
              {/* Save Status */}
              {saveStatus !== 'idle' && (
                <div className="flex items-center gap-2">
                  {saveStatus === 'saving' && (
                    <div className="flex items-center gap-2 px-3 py-1.5 bg-blue-50 border border-blue-200 rounded-lg">
                      <div className="animate-spin rounded-full h-3 w-3 border-b-2 border-blue-600"></div>
                      <span className="text-sm text-blue-700 font-medium">Saving...</span>
                    </div>
                  )}
                  {saveStatus === 'saved' && (
                    <div className="flex items-center gap-2 px-3 py-1.5 bg-green-50 border border-green-200 rounded-lg">
                      <svg className="w-4 h-4 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                      <span className="text-sm text-green-700 font-medium">
                        Saved{lastSaveTime && ` at ${lastSaveTime.toLocaleTimeString()}`}
                      </span>
                    </div>
                  )}
                  {saveStatus === 'error' && (
                    <div className="flex items-center gap-2 px-3 py-1.5 bg-red-50 border border-red-200 rounded-lg">
                      <svg className="w-4 h-4 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                      <span className="text-sm text-red-700 font-medium">
                        Error: {lastSaveError || 'Failed to save'}
                      </span>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Progress Indicator with Chip Selector */}
            <ProgressIndicator
              assets={assets}
              currentAssetId={verification.currentAsset.id}
              verifiedCount={verification.verifiedCount}
              totalCount={verification.totalCount}
              progressPercentage={verification.progressPercentage}
              onAssetClick={verification.goToAsset}
            />
          </div>

          {/* Main Content - Two Column Layout */}
          <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
            {/* Left Column - Sticky Asset Detail Panel (Narrow) */}
            <div className="lg:col-span-2">
              <AssetVerificationCard
                asset={verification.currentAsset}
                originalValue={originalValue}
                onValueChange={verification.updateAssetValue}
                onSkip={verification.skipAsset}
                onConfirm={verification.confirmAndContinue}
                onComplete={handleComplete}
                allVerified={verification.allVerified}
              />
            </div>

            {/* Right Column - Supporting Documents Panel (Wider) */}
            <div className="lg:col-span-3">
              <DocumentAssociationPanel
                associatedFiles={filteredAssociatedFiles}
                otherFiles={filteredOtherFiles}
                isFileMultiAssociated={fileAssociation.isFileMultiAssociated}
                viewMode={fileViewMode}
                searchQuery={fileSearchQuery}
                onSearchChange={setFileSearchQuery}
                onViewModeChange={setFileViewMode}
                onAssociateFile={handleAssociateFile}
                onDisassociateFile={handleDisassociateFile}
                onToggleOtherFiles={() => setShowOtherFiles(!showOtherFiles)}
                showOtherFiles={showOtherFiles}
                totalFiles={files.length}
              />
            </div>
          </div>
        </div>
      </div>
    </PageLayout>
  );
}
