/**
 * Study Service
 *
 * Provides study-related operations using real Firestore.
 */

import { studyApi } from '@/api/firestore/study.api';
import { roomApi } from '@/api/firestore/room.api';
import { assetApi } from '@/api/firestore/asset.api';
import { takeoffApi } from '@/api/firestore/takeoff.api';
import { Study, WorkflowStatus, Room, Asset, Takeoff, PhotoReviewState } from '@/types';
import { NotFoundError } from '@/errors/error-types';

/**
 * Study Service
 */
export const studyService = {
  /**
   * Create a new study
   */
  async create(study: Omit<Study, 'id' | 'createdAt' | 'updatedAt'>): Promise<Study> {
    return studyApi.create(study);
  },

  /**
   * Get a study by ID
   */
  async getById(studyId: string): Promise<Study> {
    const study = await studyApi.getById(studyId);
    if (!study) {
      throw new NotFoundError(`Study ${studyId} not found`);
    }
    return study;
  },

  /**
   * Update a study
   */
  async update(studyId: string, updates: Partial<Study>): Promise<void> {
    await studyApi.update(studyId, updates);
  },

  /**
   * Delete a study
   */
  async delete(studyId: string): Promise<void> {
    await studyApi.delete(studyId);
  },

  /**
   * Get all studies for a user
   */
  async getByUserId(userId: string): Promise<Study[]> {
    return studyApi.getByUserId(userId);
  },

  /**
   * Get studies filtered by status
   */
  async getByStatus(userId: string, status: Study['status']): Promise<Study[]> {
    return studyApi.getByStatus(userId, status);
  },

  /**
   * Get studies filtered by workflow status
   */
  async getByWorkflowStatus(userId: string, workflowStatus: WorkflowStatus): Promise<Study[]> {
    return studyApi.getByWorkflowStatus(userId, workflowStatus);
  },

  /**
   * Subscribe to real-time updates for a study
   */
  subscribe(studyId: string, callback: (study: Study | null) => void) {
    return studyApi.subscribe(studyId, callback);
  },

  /**
   * Subscribe to real-time updates for user's studies
   */
  subscribeByUserId(
    userId: string,
    callback: (studies: Study[]) => void,
    onError?: (error: Error) => void
  ) {
    return studyApi.subscribeByUserId(userId, callback, onError);
  },

  /**
   * Update rooms in a study
   */
  async updateRooms(studyId: string, rooms: Room[]): Promise<void> {
    await roomApi.updateAll(studyId, rooms);
  },

  /**
   * Update assets in a study
   */
  async updateAssets(studyId: string, assets: Asset[]): Promise<void> {
    await assetApi.updateAll(studyId, assets);
  },

  /**
   * Update takeoffs in a study
   */
  async updateTakeoffs(studyId: string, takeoffs: Takeoff[]): Promise<void> {
    await takeoffApi.saveActive(studyId, takeoffs);
  },

  /**
   * Update photo annotations in a study
   */
  async updatePhotoAnnotations(
    studyId: string,
    photoAnnotations: Record<string, PhotoReviewState>
  ): Promise<void> {
    await studyApi.update(studyId, { photoAnnotations });
  },
};

