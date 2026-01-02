/**
 * useFileAssociation Hook
 * 
 * Manages file-to-asset associations for the asset verification page.
 */

import { useMemo } from 'react';
import { UploadedFile } from '@/types';

interface UseFileAssociationOptions {
  files: UploadedFile[];
  currentAssetId: string;
}

/**
 * Hook for managing file-to-asset associations
 */
export function useFileAssociation({ files, currentAssetId }: UseFileAssociationOptions) {
  // Get files associated with the current asset
  const associatedFiles = useMemo(() => {
    return files.filter(file => file.assetIds?.includes(currentAssetId));
  }, [files, currentAssetId]);

  // Get files not associated with the current asset
  const otherFiles = useMemo(() => {
    return files.filter(file => !file.assetIds?.includes(currentAssetId));
  }, [files, currentAssetId]);

  // Check if a file is associated with the current asset
  const isFileAssociated = (fileId: string): boolean => {
    const file = files.find(f => f.id === fileId);
    return file?.assetIds?.includes(currentAssetId) ?? false;
  };

  // Check if a file is associated with multiple assets
  const isFileMultiAssociated = (fileId: string): boolean => {
    const file = files.find(f => f.id === fileId);
    return (file?.assetIds?.length ?? 0) > 1;
  };

  return {
    associatedFiles,
    otherFiles,
    isFileAssociated,
    isFileMultiAssociated,
  };
}

