/**
 * Study Firestore API
 *
 * Real Firestore implementation for Study CRUD operations.
 */

import {
  collection,
  doc,
  getDoc,
  getDocs,
  addDoc,
  updateDoc,
  deleteDoc,
  query,
  where,
  orderBy,
  onSnapshot,
  Timestamp,
  serverTimestamp,
} from 'firebase/firestore';
import type { DocumentData } from 'firebase/firestore';
import { firestore, isConfigured } from '@/lib/firebase';
import { Study, WorkflowStatus } from '@/types';
import { logger } from '@/lib/logger';

// Collection reference
const STUDIES_COLLECTION = 'studies';

/**
 * Recursively remove undefined values from an object
 * Firestore does not allow undefined values
 */
function removeUndefined<T>(obj: T): T {
  if (obj === null || obj === undefined) {
    return obj;
  }

  if (Array.isArray(obj)) {
    return obj.map(item => removeUndefined(item)) as T;
  }

  if (typeof obj === 'object') {
    const cleaned: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
      if (value !== undefined) {
        cleaned[key] = removeUndefined(value);
      }
    }
    return cleaned as T;
  }

  return obj;
}

/**
 * Convert Firestore document to Study object
 */
function docToStudy(docId: string, data: Record<string, unknown>): Study {
  return {
    ...data,
    id: docId,
    createdAt: data.createdAt instanceof Timestamp
      ? data.createdAt.toDate()
      : data.createdAt as Date,
    updatedAt: data.updatedAt instanceof Timestamp
      ? data.updatedAt.toDate()
      : data.updatedAt as Date,
    completedAt: data.completedAt instanceof Timestamp
      ? (data.completedAt as Timestamp).toDate()
      : data.completedAt as Date | undefined,
  } as Study;
}

/**
 * Convert Study object to Firestore document
 */
function studyToDoc(study: Partial<Study>): Record<string, unknown> {
  const doc: Record<string, unknown> = { ...study };

  // Remove id as it's stored in the document path
  delete doc.id;

  // Convert dates to Timestamps
  if (study.createdAt instanceof Date) {
    doc.createdAt = Timestamp.fromDate(study.createdAt);
  }
  if (study.updatedAt instanceof Date) {
    doc.updatedAt = Timestamp.fromDate(study.updatedAt);
  }
  if (study.completedAt instanceof Date) {
    doc.completedAt = Timestamp.fromDate(study.completedAt);
  }

  // Remove undefined values - Firestore doesn't allow them
  return removeUndefined(doc);
}

/**
 * Study API - Real Firestore Implementation
 */
export const studyApi = {
  /**
   * Create a new study
   */
  async create(study: Omit<Study, 'id' | 'createdAt' | 'updatedAt'>): Promise<Study> {
    if (!isConfigured || !firestore) {
      throw new Error('Firestore is not configured');
    }

    const now = new Date();
    const studyData: Omit<Study, 'id'> = {
      ...study,
      createdAt: now,
      updatedAt: now,
      assets: study.assets || [],
      currentStep: study.currentStep || study.workflowStatus,
      visitedSteps: study.visitedSteps || [study.workflowStatus],
    };

    const docData = studyToDoc(studyData);
    docData.createdAt = serverTimestamp();
    docData.updatedAt = serverTimestamp();

    const docRef = await addDoc(collection(firestore, STUDIES_COLLECTION), docData);
    logger.debug('Created study', { studyId: docRef.id, propertyName: study.propertyName });

    return {
      ...studyData,
      id: docRef.id,
    } as Study;
  },

  /**
   * Get a study by ID
   */
  async getById(studyId: string): Promise<Study | null> {
    if (!isConfigured || !firestore) {
      throw new Error('Firestore is not configured');
    }

    const docRef = doc(firestore, STUDIES_COLLECTION, studyId);
    const docSnap = await getDoc(docRef);

    if (!docSnap.exists()) {
      return null;
    }

    return docToStudy(docSnap.id, docSnap.data());
  },

  /**
   * Update a study
   */
  async update(studyId: string, updates: Partial<Study>): Promise<void> {
    if (!isConfigured || !firestore) {
      throw new Error('Firestore is not configured');
    }

    const docRef = doc(firestore, STUDIES_COLLECTION, studyId);
    const updateData = studyToDoc(updates);
    updateData.updatedAt = serverTimestamp();

    await updateDoc(docRef, updateData as DocumentData);
    logger.debug('Updated study', { studyId, updateKeys: Object.keys(updates) });
  },

  /**
   * Delete a study
   */
  async delete(studyId: string): Promise<void> {
    if (!isConfigured || !firestore) {
      throw new Error('Firestore is not configured');
    }

    const docRef = doc(firestore, STUDIES_COLLECTION, studyId);
    await deleteDoc(docRef);
    logger.debug('Deleted study', { studyId });
  },

  /**
   * Get all studies for a user
   */
  async getByUserId(userId: string): Promise<Study[]> {
    if (!isConfigured || !firestore) {
      throw new Error('Firestore is not configured');
    }

    const q = query(
      collection(firestore, STUDIES_COLLECTION),
      where('userId', '==', userId),
      orderBy('createdAt', 'desc')
    );

    const snapshot = await getDocs(q);
    return snapshot.docs.map(doc => docToStudy(doc.id, doc.data()));
  },

  /**
   * Get studies filtered by status
   */
  async getByStatus(userId: string, status: Study['status']): Promise<Study[]> {
    if (!isConfigured || !firestore) {
      throw new Error('Firestore is not configured');
    }

    const q = query(
      collection(firestore, STUDIES_COLLECTION),
      where('userId', '==', userId),
      where('status', '==', status),
      orderBy('createdAt', 'desc')
    );

    const snapshot = await getDocs(q);
    return snapshot.docs.map(doc => docToStudy(doc.id, doc.data()));
  },

  /**
   * Get studies filtered by workflow status
   */
  async getByWorkflowStatus(userId: string, workflowStatus: WorkflowStatus): Promise<Study[]> {
    if (!isConfigured || !firestore) {
      throw new Error('Firestore is not configured');
    }

    const q = query(
      collection(firestore, STUDIES_COLLECTION),
      where('userId', '==', userId),
      where('workflowStatus', '==', workflowStatus),
      orderBy('createdAt', 'desc')
    );

    const snapshot = await getDocs(q);
    return snapshot.docs.map(doc => docToStudy(doc.id, doc.data()));
  },

  /**
   * Subscribe to real-time updates for a study
   */
  subscribe(studyId: string, callback: (study: Study | null) => void): () => void {
    if (!isConfigured || !firestore) {
      console.warn('Firestore not configured - subscription not available');
      callback(null);
      return () => {};
    }

    const docRef = doc(firestore, STUDIES_COLLECTION, studyId);

    return onSnapshot(
      docRef,
      (docSnap) => {
        if (docSnap.exists()) {
          callback(docToStudy(docSnap.id, docSnap.data()));
        } else {
          callback(null);
        }
      },
      (error) => {
        console.error('Study subscription error:', error);
        callback(null);
      }
    );
  },

  /**
   * Subscribe to real-time updates for user's studies
   */
  subscribeByUserId(
    userId: string,
    callback: (studies: Study[]) => void,
    onError?: (error: Error) => void
  ): () => void {
    if (!isConfigured || !firestore) {
      console.warn('Firestore not configured - subscription not available');
      callback([]);
      return () => {};
    }

    const q = query(
      collection(firestore, STUDIES_COLLECTION),
      where('userId', '==', userId),
      orderBy('createdAt', 'desc')
    );

    return onSnapshot(
      q,
      (snapshot) => {
        const studies = snapshot.docs.map(doc => docToStudy(doc.id, doc.data()));
        callback(studies);
      },
      (error) => {
        console.error('User studies subscription error:', error);
        if (onError) {
          onError(error);
        }
        callback([]);
      }
    );
  },
};
