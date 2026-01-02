/**
 * useAssetVerification Hook
 * 
 * Main hook managing asset verification state, navigation, and updates.
 */

import { useState, useMemo, useCallback } from 'react';
import { Asset } from '@/types';

interface UseAssetVerificationOptions {
  assets: Asset[];
  onAssetUpdate: (assetId: string, updates: Partial<Asset>) => void;
  onAssetVerify: (assetId: string) => void;
}

/**
 * Hook for managing asset verification workflow
 */
export function useAssetVerification({
  assets,
  onAssetUpdate,
  onAssetVerify,
}: UseAssetVerificationOptions) {
  const [currentAssetIndex, setCurrentAssetIndex] = useState(0);

  // Get unverified assets
  const unverifiedAssets = useMemo(() => {
    return assets.filter(asset => !asset.verified);
  }, [assets]);

  // Get verified count
  const verifiedCount = useMemo(() => {
    return assets.filter(asset => asset.verified).length;
  }, [assets]);

  // Get total count
  const totalCount = assets.length;

  // Get progress percentage
  const progressPercentage = useMemo(() => {
    if (totalCount === 0) return 0;
    return Math.round((verifiedCount / totalCount) * 100);
  }, [verifiedCount, totalCount]);

  // Track selected asset ID for direct navigation
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);

  // Get current asset (prioritize unverified, but allow direct selection)
  const currentAsset = useMemo(() => {
    // If a specific asset was selected, show it
    if (selectedAssetId) {
      const selected = assets.find(a => a.id === selectedAssetId);
      if (selected) {
        // If it becomes verified and we have unverified assets, clear selection
        if (selected.verified && unverifiedAssets.length > 0) {
          // Keep showing it if user explicitly selected it
          return selected;
        }
        return selected;
      }
    }
    
    // Otherwise, show unverified assets in order
    if (unverifiedAssets.length > 0) {
      const targetIndex = currentAssetIndex < unverifiedAssets.length 
        ? currentAssetIndex 
        : 0;
      return unverifiedAssets[targetIndex];
    }
    // If all verified, show first asset
    return assets[0] || null;
  }, [assets, unverifiedAssets, currentAssetIndex, selectedAssetId]);

  // Get current asset index in full assets array
  const currentAssetIndexInFull = useMemo(() => {
    if (!currentAsset) return -1;
    return assets.findIndex(a => a.id === currentAsset.id);
  }, [assets, currentAsset]);

  // Check if all assets are verified
  const allVerified = useMemo(() => {
    return assets.length > 0 && verifiedCount === totalCount;
  }, [assets.length, verifiedCount, totalCount]);

  // Navigate to next unverified asset
  const goToNextUnverified = useCallback(() => {
    // If no unverified assets, don't navigate
    if (unverifiedAssets.length === 0) {
      setSelectedAssetId(null);
      return;
    }
    
    setSelectedAssetId(null); // Clear any selected asset
    
    // If current asset is verified or not in unverified list, start from beginning
    const currentInUnverified = unverifiedAssets.findIndex(a => a.id === currentAsset?.id);
    if (currentInUnverified === -1) {
      setCurrentAssetIndex(0);
      return;
    }
    
    // Move to next unverified asset, wrapping around if needed
    const nextIndex = (currentInUnverified + 1) % unverifiedAssets.length;
    setCurrentAssetIndex(nextIndex);
  }, [unverifiedAssets, currentAsset]);

  // Navigate to specific asset
  const goToAsset = useCallback((assetId: string) => {
    // First try to find in unverified assets
    const unverifiedIndex = unverifiedAssets.findIndex(a => a.id === assetId);
    if (unverifiedIndex !== -1) {
      setCurrentAssetIndex(unverifiedIndex);
      setSelectedAssetId(null); // Clear selection when navigating to unverified
      return;
    }
    
    // If not found in unverified, it's a verified asset - select it directly
    const asset = assets.find(a => a.id === assetId);
    if (asset) {
      setSelectedAssetId(assetId);
    }
  }, [assets, unverifiedAssets]);

  // Skip current asset (move to next without verifying)
  const skipAsset = useCallback(() => {
    goToNextUnverified();
  }, [goToNextUnverified]);

  // Confirm and continue (verify and move to next)
  const confirmAndContinue = useCallback(() => {
    if (!currentAsset) return;
    
    // Verify the current asset
    onAssetVerify(currentAsset.id);
    setSelectedAssetId(null); // Clear selection after verifying
    
    // Move to next unverified after a short delay to allow state update
    // Only advance if there are still unverified assets
    setTimeout(() => {
      // Check if there are still unverified assets after verification
      // The assets array will be updated by the parent component
      // We'll advance to the next unverified asset if available
      goToNextUnverified();
    }, 150);
  }, [currentAsset, onAssetVerify, goToNextUnverified]);

  // Update asset value
  const updateAssetValue = useCallback((value: number) => {
    if (!currentAsset) return;
    onAssetUpdate(currentAsset.id, { estimatedValue: value });
  }, [currentAsset, onAssetUpdate]);

  return {
    currentAsset,
    currentAssetIndex: currentAssetIndexInFull,
    verifiedCount,
    totalCount,
    progressPercentage,
    allVerified,
    unverifiedAssets,
    goToNextUnverified,
    goToAsset,
    skipAsset,
    confirmAndContinue,
    updateAssetValue,
  };
}

