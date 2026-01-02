/**
 * useAssets Hook
 * 
 * Manages asset data and operations for a study.
 */

import { useState, useEffect, useCallback } from 'react';
import { assetService } from '@/services/asset.service';
import { Asset } from '@/types';
import { logger } from '@/lib/logger';

interface UseAssetsOptions {
  studyId: string;
  enabled?: boolean;
}

/**
 * Hook for managing assets
 */
export function useAssets({ studyId, enabled = true }: UseAssetsOptions) {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  const loadAssets = useCallback(async () => {
    if (!enabled || !studyId) {
      setLoading(false);
      return;
    }

    try {
      setLoading(true);
      setError(null);
      const fetchedAssets = await assetService.getByStudyId(studyId);
      setAssets(fetchedAssets);
    } catch (err) {
      logger.error('Error loading assets', { error: err, studyId });
      setError(err instanceof Error ? err : new Error(String(err)));
    } finally {
      setLoading(false);
    }
  }, [studyId, enabled]);

  useEffect(() => {
    loadAssets();
  }, [loadAssets]);

  const updateAsset = useCallback(async (assetId: string, updates: Partial<Asset>) => {
    try {
      await assetService.update(studyId, assetId, updates);
      await loadAssets(); // Reload to get latest data
    } catch (err) {
      logger.error('Error updating asset', { error: err, studyId, assetId });
      throw err;
    }
  }, [studyId, loadAssets]);

  const addAsset = useCallback(async (asset: Asset) => {
    try {
      await assetService.add(studyId, asset);
      await loadAssets(); // Reload to get latest data
    } catch (err) {
      logger.error('Error adding asset', { error: err, studyId });
      throw err;
    }
  }, [studyId, loadAssets]);

  const deleteAsset = useCallback(async (assetId: string) => {
    try {
      await assetService.delete(studyId, assetId);
      await loadAssets(); // Reload to get latest data
    } catch (err) {
      logger.error('Error deleting asset', { error: err, studyId, assetId });
      throw err;
    }
  }, [studyId, loadAssets]);

  return {
    assets,
    loading,
    error,
    updateAsset,
    addAsset,
    deleteAsset,
    refresh: loadAssets,
  };
}

