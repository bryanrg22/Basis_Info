'use client';

/**
 * Engineering Takeoff Client Component
 *
 * Main client component that manages the 3-panel layout and state.
 * Persists engineer edits to localStorage for quick iteration during development.
 */

import { useState, useCallback, useEffect, useRef, useMemo } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import PageLayout from '@/components/layout/PageLayout';
import { AssetDemo, AssetTakeoffDemo, TabId } from '@/types/asset-takeoff.types';
import { AssetChecklistNavigator } from '@/components/engineering-takeoff/AssetChecklistNavigator';
import { TakeoffWorkflowPanel } from '@/components/engineering-takeoff/TakeoffWorkflowPanel';
import { SmartReferencePanel } from '@/components/engineering-takeoff/SmartReferencePanel';
import { useApp } from '@/contexts/AppContext';
import {
  loadTakeoffState,
  saveAssetState,
  resetTakeoffState,
  PersistedStudyState,
} from '@/utils/takeoff-local-storage';
import StudyBackButton from '@/components/StudyBackButton';

interface EngineeringTakeoffClientProps {
  studyId: string;
  asset: AssetDemo;
  takeoff: AssetTakeoffDemo;
  allAssets: AssetDemo[];
  relatedAssets: AssetDemo[];
  initialTab: TabId;
}

export function EngineeringTakeoffClient({
  studyId,
  asset,
  takeoff,
  allAssets,
  relatedAssets,
  initialTab,
}: EngineeringTakeoffClientProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { updateWorkflowStatus, state, updateStudyInFirestore } = useApp();
  
  const study = state.studies.find(s => s.id === studyId);
  
  // Track if we've done initial hydration from localStorage
  const hasHydratedRef = useRef(false);
  
  // Local state for takeoff data (allows editing without backend)
  const [localTakeoff, setLocalTakeoff] = useState(takeoff);
  const [localAsset, setLocalAsset] = useState(asset);
  
  // Track persisted state for all assets (for progress calculation)
  const [persistedState, setPersistedState] = useState<PersistedStudyState>({});
  
  // Sidebar collapsed state
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  
  // Reference panel collapsed state (mobile)
  const [referencePanelOpen, setReferencePanelOpen] = useState(false);
  
  // Saving state for autosave
  const [isSaving, setIsSaving] = useState(false);
  const [lastSaved, setLastSaved] = useState<Date | null>(null);
  
  // Debounce timer ref for saving
  const saveTimerRef = useRef<NodeJS.Timeout | null>(null);
  
  // Current tab from URL
  const currentTab = (searchParams.get('tab') as TabId) || initialTab;
  
  // Load persisted state on mount (from localStorage and Study)
  useEffect(() => {
    // First try to load from Study (preferred source)
    let saved: PersistedStudyState | null = null;
    if (study?.engineeringTakeoffState) {
      saved = study.engineeringTakeoffState;
      // Also sync to localStorage for consistency
      if (typeof window !== 'undefined') {
        try {
          const key = `engineering_takeoffs:${studyId}`;
          window.localStorage.setItem(key, JSON.stringify(saved));
        } catch (err) {
          console.warn('Failed to sync takeoff state to localStorage:', err);
        }
      }
    } else {
      // Fallback to localStorage
      saved = loadTakeoffState(studyId);
      // If we have localStorage state but not Study state, sync it
      if (saved && study) {
        updateStudyInFirestore(studyId, {
          engineeringTakeoffState: saved,
        }).catch(err => console.error('Failed to sync takeoff state to study:', err));
      }
    }
    
    if (saved) {
      setPersistedState(saved);
      
      // If there's saved state for the current asset, hydrate it
      const savedAssetState = saved[asset.id];
      if (savedAssetState) {
        setLocalAsset(savedAssetState.asset);
        setLocalTakeoff(savedAssetState.takeoff);
      }
    }
    hasHydratedRef.current = true;
  }, [studyId, asset.id, study, updateStudyInFirestore]);
  
  // Handle asset change - check for persisted state
  useEffect(() => {
    // Skip on initial mount (handled above)
    if (!hasHydratedRef.current) return;
    
    const savedAssetState = persistedState[asset.id];
    if (savedAssetState) {
      setLocalAsset(savedAssetState.asset);
      setLocalTakeoff(savedAssetState.takeoff);
    } else {
      // Use pristine initial state from server
      setLocalAsset(asset);
      setLocalTakeoff(takeoff);
    }
    setLastSaved(null);
  }, [asset.id, takeoff, asset, persistedState]);
  
  // Debounced save to localStorage and Study
  const debouncedSave = useCallback((newAsset: AssetDemo, newTakeoff: AssetTakeoffDemo) => {
    // Clear any pending save
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current);
    }
    
    setIsSaving(true);
    
    saveTimerRef.current = setTimeout(() => {
      const success = saveAssetState(studyId, newAsset.id, newAsset, newTakeoff);
      
      if (success) {
        // Update persisted state cache
        const updatedState = {
          ...persistedState,
          [newAsset.id]: {
            asset: newAsset,
            takeoff: newTakeoff,
            lastUpdated: new Date().toISOString(),
          },
        };
        setPersistedState(updatedState);
        setLastSaved(new Date());
        
        // Also sync to Study
        if (study) {
          updateStudyInFirestore(studyId, {
            engineeringTakeoffState: updatedState,
          }).catch(err => console.error('Failed to sync takeoff state to study:', err));
        }
      }
      
      setIsSaving(false);
    }, 300);
  }, [studyId, persistedState, study, updateStudyInFirestore]);
  
  // Cleanup save timer on unmount
  useEffect(() => {
    return () => {
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current);
      }
    };
  }, []);
  
  // Handle tab change
  const handleTabChange = useCallback((tab: TabId) => {
    router.push(`/study/${studyId}/engineering-takeoff/${asset.id}?tab=${tab}`, { scroll: false });
  }, [router, studyId, asset.id]);
  
  // Handle asset selection
  const handleSelectAsset = useCallback((assetId: string) => {
    router.push(`/study/${studyId}/engineering-takeoff/${assetId}?tab=${currentTab}`);
  }, [router, studyId, currentTab]);
  
  // Handle takeoff data updates
  const handleUpdateTakeoff = useCallback((updates: Partial<AssetTakeoffDemo>) => {
    setLocalTakeoff(prev => {
      const updated = { ...prev, ...updates };
      debouncedSave(localAsset, updated);
      return updated;
    });
  }, [debouncedSave, localAsset]);
  
  // Handle asset status update
  const handleUpdateStatus = useCallback((status: AssetDemo['status']) => {
    setLocalAsset(prev => {
      const updated = { ...prev, status };
      debouncedSave(updated, localTakeoff);
      return updated;
    });
  }, [debouncedSave, localTakeoff]);
  
  // Handle reset all progress
  const handleResetProgress = useCallback(() => {
    if (window.confirm('This will reset all takeoff progress for this study. This action cannot be undone. Continue?')) {
      resetTakeoffState(studyId);
      setPersistedState({});
      // Reset current asset to pristine state
      setLocalAsset(asset);
      setLocalTakeoff(takeoff);
      setLastSaved(null);
    }
  }, [studyId, asset, takeoff]);
  
  // Merge persisted state with pristine allAssets for accurate progress
  // Also ensure the current asset reflects the latest local state
  const mergedAllAssets = useMemo(() => {
    return allAssets.map(pristineAsset => {
      // For the current asset, always use the latest local state
      if (pristineAsset.id === localAsset.id) {
        return localAsset;
      }
      // For other assets, use persisted state if available
      const persisted = persistedState[pristineAsset.id];
      return persisted ? persisted.asset : pristineAsset;
    });
  }, [allAssets, persistedState, localAsset]);
  
  // Use merged assets for current asset display (for consistency)
  const displayAsset = useMemo(() => {
    // For the current asset, use localAsset (latest state)
    if (localAsset.id === asset.id) {
      return localAsset;
    }
    return mergedAllAssets.find(a => a.id === asset.id) ?? localAsset;
  }, [mergedAllAssets, localAsset, asset.id]);
  
  // Merge related assets with persisted state
  const mergedRelatedAssets = useMemo(() => {
    return mergedAllAssets.filter(a => a.id !== asset.id);
  }, [mergedAllAssets, asset.id]);
  
  // Calculate completion stats using merged state
  const completedAssets = mergedAllAssets.filter(a => a.status === 'COMPLETED').length;
  const totalAssets = mergedAllAssets.length;
  const progressPercentage = totalAssets > 0 ? Math.round((completedAssets / totalAssets) * 100) : 0;
  
  // Handle complete study
  const handleCompleteStudy = useCallback(async () => {
    try {
      await updateWorkflowStatus(studyId, 'completed');
      router.push(`/study/${studyId}/complete`);
    } catch (error) {
      console.error('Error updating workflow status:', error);
      alert('Failed to complete study. Please try again.');
    }
  }, [updateWorkflowStatus, studyId, router]);
  
  return (
    <PageLayout>
      <div className="flex flex-col h-full bg-gray-50">
        {/* Page Header */}
        <div className="bg-white border-b border-gray-200 px-6 py-4">
          <div className="flex items-center justify-between">
            <div className="flex-1">
              <div className="mb-2">
                <StudyBackButton />
              </div>
              <div className="flex items-center gap-2 text-sm text-gray-500 mb-1">
                <span>Engineering Takeoff</span>
                <span>/</span>
                <span>{localAsset.propertyName}</span>
              </div>
              <h1 className="text-2xl font-semibold text-gray-900">{localAsset.name}</h1>
            </div>
            <div className="flex items-center gap-4">
              {/* Saving indicator */}
              {isSaving && (
                <div className="flex items-center gap-2 text-sm text-gray-500">
                  <svg className="animate-spin h-4 w-4 text-primary-600" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                  </svg>
                  <span>Saving...</span>
                </div>
              )}
              {!isSaving && lastSaved && (
                <div className="flex items-center gap-2 text-sm text-green-600" title="Changes saved to browser storage">
                  <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                  <span>Saved locally</span>
                </div>
              )}
              
              {/* Reset Progress */}
              <button
                onClick={handleResetProgress}
                className="px-3 py-2 text-sm font-medium text-gray-600 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 hover:text-red-600 transition-colors"
                title="Reset all takeoff progress for this study"
              >
                Reset Progress
              </button>
              
              {/* Mobile reference panel toggle */}
              <button
                onClick={() => setReferencePanelOpen(!referencePanelOpen)}
                className="lg:hidden px-3 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50"
              >
                References
              </button>
              
              {/* Complete Study */}
              <button
                onClick={handleCompleteStudy}
                className="px-4 py-2 text-sm font-medium text-white bg-primary-600 rounded-lg hover:bg-primary-700 transition-colors flex items-center gap-2"
              >
                Complete Study
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
              </button>
            </div>
          </div>
          
          {/* Progress Bar */}
          <div className="mt-4">
            <div className="flex items-center justify-between text-sm text-gray-600 mb-2">
              <span>Takeoff Progress</span>
              <span>{completedAssets} of {totalAssets} assets completed ({progressPercentage}%)</span>
            </div>
            <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
              <div 
                className="h-full bg-primary-600 transition-all duration-300"
                style={{ width: `${progressPercentage}%` }}
              />
            </div>
          </div>
        </div>
        
        {/* Main Content - 3 Panel Layout */}
        <div className="flex-1 flex overflow-hidden">
          {/* Left Panel - Asset Navigator */}
          <AssetChecklistNavigator
            assets={mergedAllAssets}
            currentAssetId={localAsset.id}
            onSelectAsset={handleSelectAsset}
            collapsed={sidebarCollapsed}
            onToggleCollapse={() => setSidebarCollapsed(!sidebarCollapsed)}
          />
          
          {/* Center Panel - Workflow */}
          <TakeoffWorkflowPanel
            asset={localAsset}
            takeoff={localTakeoff}
            currentTab={currentTab}
            onTabChange={handleTabChange}
            onUpdateTakeoff={handleUpdateTakeoff}
            onUpdateStatus={handleUpdateStatus}
          />
          
          {/* Right Panel - References */}
          <SmartReferencePanel
            asset={localAsset}
            takeoff={localTakeoff}
            relatedAssets={mergedRelatedAssets}
            isOpen={referencePanelOpen}
            onClose={() => setReferencePanelOpen(false)}
            onSelectAsset={handleSelectAsset}
          />
        </div>
      </div>
    </PageLayout>
  );
}

