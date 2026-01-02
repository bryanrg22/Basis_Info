/**
 * Firestore Utilities
 */

import { Study } from '@/types';

/**
 * Study converter for Firestore serialization
 */
export const studyConverter = {
  toFirestore: (study: Study) => study,
  fromFirestore: (data: unknown) => data as Study,
};

