/**
 * useTakeoffFilters Hook
 * 
 * Manages filtering logic for takeoffs.
 */

import { useMemo } from 'react';
import { Takeoff } from '@/types';

interface UseTakeoffFiltersOptions {
  takeoffs: Takeoff[];
  filterCategory: string;
  filterRoom: string;
  searchQuery: string;
}

/**
 * Hook for filtering takeoffs
 */
export function useTakeoffFilters({
  takeoffs,
  filterCategory,
  filterRoom,
  searchQuery,
}: UseTakeoffFiltersOptions) {
  const filteredTakeoffs = useMemo(() => {
    return takeoffs.filter((takeoff) => {
      // Category filter
      if (filterCategory !== 'all' && takeoff.category !== filterCategory) {
        return false;
      }

      // Room filter
      if (filterRoom !== 'all' && takeoff.room !== filterRoom) {
        return false;
      }

      // Search query filter
      if (searchQuery.trim()) {
        const query = searchQuery.toLowerCase();
        const searchableText = [
          takeoff.description,
          takeoff.category,
          takeoff.room,
          takeoff.location,
          takeoff.notes,
          takeoff.titles,
          takeoff.takeoffCode,
        ]
          .filter(Boolean)
          .join(' ')
          .toLowerCase();

        if (!searchableText.includes(query)) {
          return false;
        }
      }

      return true;
    });
  }, [takeoffs, filterCategory, filterRoom, searchQuery]);

  // Get unique categories and rooms for filter dropdowns
  const categories = useMemo(() => {
    const unique = new Set(takeoffs.map((t) => t.category).filter(Boolean));
    return Array.from(unique).sort();
  }, [takeoffs]);

  const rooms = useMemo(() => {
    const unique = new Set(takeoffs.map((t) => t.room).filter(Boolean));
    return Array.from(unique).sort();
  }, [takeoffs]);

  return {
    filteredTakeoffs,
    categories,
    rooms,
  };
}

