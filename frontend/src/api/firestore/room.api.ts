/**
 * Room Firestore API
 *
 * Manages rooms within studies using real Firestore.
 */

import { studyApi } from './study.api';
import { Room } from '@/types';
import { NotFoundError } from '@/errors/error-types';

/**
 * Room API
 */
export const roomApi = {
  /**
   * Get all rooms for a study
   */
  async getByStudyId(studyId: string): Promise<Room[]> {
    const study = await studyApi.getById(studyId);
    if (!study) {
      throw new NotFoundError(`Study ${studyId} not found`);
    }
    return study.rooms || [];
  },

  /**
   * Update all rooms for a study
   */
  async updateAll(studyId: string, rooms: Room[]): Promise<void> {
    await studyApi.update(studyId, { rooms });
  },

  /**
   * Update a single room
   */
  async update(studyId: string, roomId: string, updates: Partial<Room>): Promise<void> {
    const study = await studyApi.getById(studyId);
    if (!study) {
      throw new NotFoundError(`Study ${studyId} not found`);
    }

    const currentRooms = study.rooms || [];
    const updatedRooms = currentRooms.map(room =>
      room.id === roomId ? { ...room, ...updates } : room
    );

    await this.updateAll(studyId, updatedRooms);
  },

  /**
   * Add a room to a study
   */
  async add(studyId: string, room: Room): Promise<void> {
    const study = await studyApi.getById(studyId);
    if (!study) {
      throw new NotFoundError(`Study ${studyId} not found`);
    }

    const updatedRooms = [...(study.rooms || []), room];
    await this.updateAll(studyId, updatedRooms);
  },

  /**
   * Delete a room from a study
   */
  async delete(studyId: string, roomId: string): Promise<void> {
    const study = await studyApi.getById(studyId);
    if (!study) {
      throw new NotFoundError(`Study ${studyId} not found`);
    }

    const currentRooms = study.rooms || [];
    const updatedRooms = currentRooms.filter(room => room.id !== roomId);
    await this.updateAll(studyId, updatedRooms);
  },
};

