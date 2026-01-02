/**
 * useTakeoffSorting Hook
 * 
 * Manages sorting logic for takeoffs.
 */

import { useMemo } from 'react';
import { Takeoff } from '@/types';
import { getFieldValue } from '@/utils/validation';

interface SortConfig {
  field: string;
  direction: 'asc' | 'desc';
}

interface UseTakeoffSortingOptions {
  takeoffs: Takeoff[];
  sortConfig: SortConfig | null;
}

/**
 * Hook for sorting takeoffs
 */
export function useTakeoffSorting({
  takeoffs,
  sortConfig,
}: UseTakeoffSortingOptions) {
  const sortedTakeoffs = useMemo(() => {
    if (!sortConfig) {
      return takeoffs;
    }

    const sorted = [...takeoffs].sort((a, b) => {
      const aVal = getFieldValue(a as unknown as Record<string, unknown>, sortConfig.field);
      const bVal = getFieldValue(b as unknown as Record<string, unknown>, sortConfig.field);

      // Handle null/undefined
      if (aVal == null && bVal == null) return 0;
      if (aVal == null) return 1;
      if (bVal == null) return -1;

      // Handle numbers
      if (typeof aVal === 'number' && typeof bVal === 'number') {
        return sortConfig.direction === 'asc' ? aVal - bVal : bVal - aVal;
      }

      // Handle strings
      const aStr = String(aVal).toLowerCase();
      const bStr = String(bVal).toLowerCase();

      if (sortConfig.direction === 'asc') {
        return aStr.localeCompare(bStr);
      } else {
        return bStr.localeCompare(aStr);
      }
    });

    return sorted;
  }, [takeoffs, sortConfig]);

  return {
    sortedTakeoffs,
  };
}

