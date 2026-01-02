/**
 * Takeoff Firestore API
 *
 * Manages takeoffs within studies using real Firestore.
 */

import { studyApi } from './study.api';
import { Takeoff } from '@/types';
import { logger } from '@/lib/logger';

// In-memory takeoff subscriptions
const takeoffSubscribers: Map<string, Set<(takeoffs: Takeoff[]) => void>> = new Map();

/**
 * Notify subscribers about takeoff changes
 */
function notifyTakeoffSubscribers(studyId: string, takeoffs: Takeoff[]) {
  const subscribers = takeoffSubscribers.get(studyId);
  if (subscribers) {
    subscribers.forEach(callback => callback(takeoffs));
  }
}

/**
 * Takeoff API
 */
export const takeoffApi = {
  /**
   * Reads the immutable pipeline output (copy)
   */
  async getCopy(studyId: string): Promise<Takeoff[]> {
    const study = await studyApi.getById(studyId);
    if (!study) {
      return [];
    }
    return study.takeoffs || [];
  },

  /**
   * Reads the editable version (active)
   */
  async getActive(studyId: string): Promise<Takeoff[]> {
    const study = await studyApi.getById(studyId);
    if (!study) {
      return [];
    }
    return study.takeoffs || [];
  },

  /**
   * Save takeoffs to active subcollection
   */
  async saveActive(studyId: string, takeoffs: Takeoff[]): Promise<void> {
    await studyApi.update(studyId, { takeoffs });
    logger.debug('Saved active takeoffs', { studyId, count: takeoffs.length });
    notifyTakeoffSubscribers(studyId, takeoffs);
  },

  /**
   * Subscribe to real-time updates for active takeoffs
   */
  subscribeActive(
    studyId: string,
    callback: (takeoffs: Takeoff[]) => void,
    onError?: (error: Error) => void
  ): () => void {
    if (!takeoffSubscribers.has(studyId)) {
      takeoffSubscribers.set(studyId, new Set());
    }
    takeoffSubscribers.get(studyId)!.add(callback);
    
    // Immediately call with current value
    studyApi.getById(studyId).then(study => {
      callback(study?.takeoffs || []);
    }).catch(error => {
      if (onError) {
        onError(error instanceof Error ? error : new Error(String(error)));
      }
    });
    
    // Return unsubscribe function
    return () => {
      const subscribers = takeoffSubscribers.get(studyId);
      if (subscribers) {
        subscribers.delete(callback);
      }
    };
  },
};

