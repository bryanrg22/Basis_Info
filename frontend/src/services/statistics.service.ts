/**
 * Statistics Service
 *
 * Calculates statistics from real study data.
 */

import { Study, Statistics } from '@/types';
import { REVENUE_MULTIPLIER, TAX_SAVINGS_MULTIPLIER } from '@/config/constants';

/**
 * Statistics Service
 */
export const statisticsService = {
  /**
   * Calculate statistics from studies
   * Returns real calculated values based on actual studies
   */
  calculate(studies: Study[]): Statistics {
    if (studies.length === 0) {
      // Return zeros for empty state - no mock data
      return {
        studiesCompleted: 0,
        revenueGenerated: 0,
        taxSavingsProvided: 0,
      };
    }

    const completedStudies = studies.filter(s => s.status === 'completed');
    const studiesCompleted = completedStudies.length;

    // Calculate revenue and tax savings from completed studies
    const revenueGenerated = completedStudies.reduce(
      (sum, study) => sum + (study.totalAssets * REVENUE_MULTIPLIER),
      0
    );
    const taxSavingsProvided = completedStudies.reduce(
      (sum, study) => sum + (study.totalAssets * TAX_SAVINGS_MULTIPLIER),
      0
    );

    return {
      studiesCompleted,
      revenueGenerated: Math.round(revenueGenerated),
      taxSavingsProvided: Math.round(taxSavingsProvided),
    };
  },
};
