/**
 * usePolling Hook
 * 
 * Generic polling hook for checking data at intervals.
 */

import { useEffect, useRef, useCallback } from 'react';
import { logger } from '@/lib/logger';
import { POLLING_INTERVAL, MAX_POLLING_TIME } from '@/config/constants';

interface UsePollingOptions {
  enabled?: boolean;
  interval?: number;
  maxTime?: number;
  onError?: (error: Error) => void;
}

/**
 * Polls a function at regular intervals
 * @param pollFn - Function to call on each poll
 * @param shouldStop - Function that returns true when polling should stop
 * @param options - Polling options
 */
export function usePolling<T>(
  pollFn: () => Promise<T>,
  shouldStop: (result: T) => boolean,
  options: UsePollingOptions = {}
): {
  start: () => void;
  stop: () => void;
  isPolling: boolean;
} {
  const {
    enabled = true,
    interval = POLLING_INTERVAL,
    maxTime = MAX_POLLING_TIME,
    onError,
  } = options;

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const startTimeRef = useRef<number>(0);
  const isPollingRef = useRef(false);

  const stop = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
    isPollingRef.current = false;
  }, []);

  const start = useCallback(() => {
    if (!enabled || isPollingRef.current) {
      return;
    }

    isPollingRef.current = true;
    startTimeRef.current = Date.now();

    const poll = async () => {
      const elapsed = Date.now() - startTimeRef.current;

      // Check timeout
      if (elapsed >= maxTime) {
        logger.warn('Polling timeout reached', { elapsed, maxTime });
        stop();
        if (onError) {
          onError(new Error('Polling timeout reached'));
        }
        return;
      }

      try {
        const result = await pollFn();
        
        if (shouldStop(result)) {
          logger.debug('Polling stopped - condition met');
          stop();
        }
      } catch (error) {
        logger.error('Polling error', { error });
        if (onError) {
          onError(error instanceof Error ? error : new Error(String(error)));
        }
        // Continue polling on error (don't stop)
      }
    };

    // Start immediately
    poll();

    // Then poll at intervals
    intervalRef.current = setInterval(poll, interval);

    // Set timeout
    timeoutRef.current = setTimeout(() => {
      stop();
      if (onError) {
        onError(new Error('Polling timeout reached'));
      }
    }, maxTime);
  }, [enabled, interval, maxTime, pollFn, shouldStop, onError, stop]);

  useEffect(() => {
    return () => {
      stop();
    };
  }, [stop]);

  return {
    start,
    stop,
    isPolling: isPollingRef.current,
  };
}

