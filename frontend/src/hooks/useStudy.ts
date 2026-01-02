/**
 * useStudy Hook
 * 
 * Manages study data and operations.
 */

import { useState, useEffect } from 'react';
import { studyService } from '@/services/study.service';
import { Study } from '@/types';
import { logger } from '@/lib/logger';

interface UseStudyOptions {
  studyId: string;
  enabled?: boolean;
}

/**
 * Hook for managing a single study
 */
export function useStudy({ studyId, enabled = true }: UseStudyOptions) {
  const [study, setStudy] = useState<Study | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (!enabled || !studyId) {
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);

    // Subscribe to real-time updates
    const unsubscribe = studyService.subscribe(studyId, (updatedStudy) => {
      setStudy(updatedStudy);
      setLoading(false);
    });

    // Also fetch immediately
    studyService.getById(studyId)
      .then((fetchedStudy) => {
        setStudy(fetchedStudy);
        setLoading(false);
      })
      .catch((err) => {
        logger.error('Error fetching study', { error: err, studyId });
        setError(err instanceof Error ? err : new Error(String(err)));
        setLoading(false);
      });

    return () => {
      unsubscribe();
    };
  }, [studyId, enabled]);

  return {
    study,
    loading,
    error,
  };
}

