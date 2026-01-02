/**
 * Takeoff Service
 *
 * Provides takeoff-related operations using real Firestore.
 */

import { takeoffApi } from '@/api/firestore/takeoff.api';
import { Takeoff } from '@/types';
import { ValidationError } from '@/errors/error-types';

/**
 * Takeoff Service
 */
export const takeoffService = {
  /**
   * Get takeoffs from copy subcollection (initial pipeline output)
   */
  async getCopy(studyId: string): Promise<Takeoff[]> {
    return takeoffApi.getCopy(studyId);
  },

  /**
   * Get takeoffs from active subcollection (editable version)
   */
  async getActive(studyId: string): Promise<Takeoff[]> {
    return takeoffApi.getActive(studyId);
  },

  /**
   * Save takeoffs to active subcollection (auto-save)
   */
  async saveActive(studyId: string, takeoffs: Takeoff[]): Promise<void> {
    this.validateTakeoffs(takeoffs);
    await takeoffApi.saveActive(studyId, takeoffs);
  },

  /**
   * Subscribe to real-time updates for active takeoffs
   */
  subscribeActive(
    studyId: string,
    callback: (takeoffs: Takeoff[]) => void,
    onError?: (error: Error) => void
  ) {
    return takeoffApi.subscribeActive(studyId, callback, onError);
  },

  /**
   * Calculate total cost of takeoffs
   */
  calculateTotalCost(takeoffs: Takeoff[]): number {
    return takeoffs.reduce((sum, takeoff) => {
      const cost = takeoff.takeoffCost ?? takeoff.unitCost ?? 0;
      const quantity = takeoff.quantity ?? 0;
      return sum + (cost * quantity);
    }, 0);
  },

  /**
   * Group takeoffs by depreciation class
   */
  groupByDepreciationClass(takeoffs: Takeoff[]): Record<string, Takeoff[]> {
    return takeoffs.reduce((groups, takeoff) => {
      const key = takeoff.depreciationClass || 'unknown';
      if (!groups[key]) {
        groups[key] = [];
      }
      groups[key].push(takeoff);
      return groups;
    }, {} as Record<string, Takeoff[]>);
  },

  /**
   * Validate a single takeoff
   */
  validateTakeoff(takeoff: Takeoff): void {
    if (!takeoff.id) {
      throw new ValidationError('Takeoff ID is required', 'id');
    }
    if (!takeoff.description || takeoff.description.trim().length === 0) {
      throw new ValidationError('Takeoff description is required', 'description');
    }
    if (takeoff.quantity === undefined || takeoff.quantity < 0) {
      throw new ValidationError('Quantity must be non-negative', 'quantity');
    }
  },

  /**
   * Validate multiple takeoffs
   */
  validateTakeoffs(takeoffs: Takeoff[]): void {
    takeoffs.forEach((takeoff, index) => {
      try {
        this.validateTakeoff(takeoff);
      } catch (error) {
        if (error instanceof ValidationError) {
          throw new ValidationError(
            `Takeoff at index ${index}: ${error.message}`,
            error.field
          );
        }
        throw error;
      }
    });
  },
};

