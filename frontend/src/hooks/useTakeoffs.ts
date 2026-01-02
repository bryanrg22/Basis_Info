/**
 * useTakeoffs Hook
 * 
 * Manages takeoff data and operations for a study.
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { takeoffService } from '@/services/takeoff.service';
import { Takeoff } from '@/types';
import { logger } from '@/lib/logger';
import { useDebounce } from './useDebounce';
import { AUTO_SAVE_DEBOUNCE } from '@/config/constants';

interface UseTakeoffsOptions {
  studyId: string;
  enabled?: boolean;
  autoSave?: boolean;
}

/**
 * Hook for managing takeoffs with real-time sync and auto-save
 */
export function useTakeoffs({ studyId, enabled = true, autoSave = true }: UseTakeoffsOptions) {
  const [takeoffs, setTakeoffs] = useState<Takeoff[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const isLocalUpdateRef = useRef(false);
  const lastLocalUpdateRef = useRef(0);

  // Debounce takeoffs for auto-save
  const debouncedTakeoffs = useDebounce(takeoffs, autoSave ? AUTO_SAVE_DEBOUNCE : 0);

  // Load takeoffs
  const loadTakeoffs = useCallback(async () => {
    if (!enabled || !studyId) {
      setLoading(false);
      return;
    }

    try {
      setLoading(true);
      setError(null);
      const fetchedTakeoffs = await takeoffService.getActive(studyId);
      setTakeoffs(fetchedTakeoffs);
    } catch (err) {
      logger.error('Error loading takeoffs', { error: err, studyId });
      setError(err instanceof Error ? err : new Error(String(err)));
    } finally {
      setLoading(false);
    }
  }, [studyId, enabled]);

  // Subscribe to real-time updates
  useEffect(() => {
    if (!enabled || !studyId) {
      return;
    }

    const unsubscribe = takeoffService.subscribeActive(
      studyId,
      (updatedTakeoffs) => {
        // Ignore updates for a short time after local edits to prevent conflicts
        const timeSinceLocalUpdate = Date.now() - lastLocalUpdateRef.current;
        if (isLocalUpdateRef.current && timeSinceLocalUpdate < 2000) {
          return;
        }
        setTakeoffs(updatedTakeoffs);
      },
      (err) => {
        logger.error('Error in takeoff subscription', { error: err, studyId });
        setError(err);
      }
    );

    // Also load immediately
    loadTakeoffs();

    return () => {
      unsubscribe();
    };
  }, [studyId, enabled, loadTakeoffs]);

  // Auto-save debounced takeoffs
  useEffect(() => {
    if (!autoSave || !enabled || !studyId || loading) {
      return;
    }

    // Don't auto-save if this is the initial load
    if (takeoffs.length === 0 && debouncedTakeoffs.length === 0) {
      return;
    }

    const save = async () => {
      try {
        setSaveStatus('saving');
        isLocalUpdateRef.current = true;
        lastLocalUpdateRef.current = Date.now();
        await takeoffService.saveActive(studyId, debouncedTakeoffs);
        setSaveStatus('saved');
        setTimeout(() => setSaveStatus('idle'), 2000);
        isLocalUpdateRef.current = false;
      } catch (err) {
        logger.error('Error auto-saving takeoffs', { error: err, studyId });
        setSaveStatus('error');
        setError(err instanceof Error ? err : new Error(String(err)));
        isLocalUpdateRef.current = false;
      }
    };

    save();
  }, [debouncedTakeoffs, studyId, enabled, autoSave, loading]);

  const updateTakeoff = useCallback((takeoffId: string, updates: Partial<Takeoff>) => {
    setTakeoffs((prev) =>
      prev.map((takeoff) =>
        takeoff.id === takeoffId ? { ...takeoff, ...updates } : takeoff
      )
    );
  }, []);

  const addTakeoff = useCallback((takeoff: Takeoff) => {
    setTakeoffs((prev) => [...prev, takeoff]);
  }, []);

  const deleteTakeoff = useCallback((takeoffId: string) => {
    setTakeoffs((prev) => prev.filter((takeoff) => takeoff.id !== takeoffId));
  }, []);

  return {
    takeoffs,
    loading,
    error,
    saveStatus,
    updateTakeoff,
    addTakeoff,
    deleteTakeoff,
    refresh: loadTakeoffs,
  };
}

