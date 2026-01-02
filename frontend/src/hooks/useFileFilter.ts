/**
 * useFileFilter Hook
 * 
 * Handles file search and filtering with debounce.
 */

import { useMemo } from 'react';
import { useDebounce } from './useDebounce';
import { UploadedFile } from '@/types';

interface UseFileFilterOptions {
  files: UploadedFile[];
  searchQuery: string;
}

/**
 * Hook for filtering files by search query
 */
export function useFileFilter({ files, searchQuery }: UseFileFilterOptions) {
  const debouncedSearchQuery = useDebounce(searchQuery, 300);

  const filteredFiles = useMemo(() => {
    if (!debouncedSearchQuery.trim()) {
      return files;
    }

    const query = debouncedSearchQuery.toLowerCase();
    return files.filter(file => {
      const fileName = file.name.toLowerCase();
      const fileType = file.type.toLowerCase();
      return fileName.includes(query) || fileType.includes(query);
    });
  }, [files, debouncedSearchQuery]);

  return {
    filteredFiles,
    searchQuery: debouncedSearchQuery,
  };
}

