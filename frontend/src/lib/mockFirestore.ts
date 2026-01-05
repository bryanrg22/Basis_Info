/**
 * Mock Firestore Service
 * 
 * Provides type-safe CRUD operations using JSON file-based storage via API routes.
 * All operations make HTTP requests to API routes since client can't access file system.
 */

import { Study, WorkflowStatus, Asset, Room, Takeoff, UploadedFile, TakeoffsDocument } from './types';
import { Timestamp, Unsubscribe } from './compat';

// Helper to convert Date/Timestamp to ISO string for JSON
function toTimestamp(value: Date | Timestamp | undefined): Date | Timestamp | undefined {
  if (!value) return undefined;
  if (value instanceof Date) return value;
  if (value instanceof Timestamp) return value;
  return new Date(value);
}

// Helper to convert ISO string or object back to Date/Timestamp
function fromTimestamp(value: any): Date | Timestamp {
  if (value instanceof Date) return value;
  if (value instanceof Timestamp) return value;
  if (typeof value === 'string') {
    // Try to parse as ISO string
    const date = new Date(value);
    if (!isNaN(date.getTime())) {
      return Timestamp.fromDate(date);
    }
  }
  if (value && typeof value === 'object') {
    if ('seconds' in value) {
      return new Timestamp(value.seconds, value.nanoseconds || 0);
    }
    // Try to parse as date-like object
    if ('toDate' in value && typeof value.toDate === 'function') {
      return Timestamp.fromDate(value.toDate());
    }
  }
  return Timestamp.now();
}

/**
 * Study Service - Mock implementation using API routes
 */
export const studyService = {
  /**
   * Create a new study
   */
  async createStudy(study: Omit<Study, 'id' | 'createdAt' | 'updatedAt'>): Promise<Study> {
    const now = new Date();
    const newStudy: Study = {
      ...study,
      id: `study-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
      createdAt: now,
      updatedAt: now,
      assets: study.assets || [],
    };

    const response = await fetch('/api/db/studies', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(newStudy),
    });

    if (!response.ok) {
      throw new Error(`Failed to create study: ${response.statusText}`);
    }

    return newStudy;
  },

  /**
   * Get a study by ID
   */
  async getStudy(studyId: string): Promise<Study | null> {
    const response = await fetch(`/api/db/studies/${studyId}`);
    
    if (response.status === 404) {
      return null;
    }
    
    if (!response.ok) {
      throw new Error(`Failed to get study: ${response.statusText}`);
    }

    const data = await response.json();
    // Convert timestamps
    if (data.createdAt) data.createdAt = fromTimestamp(data.createdAt);
    if (data.updatedAt) data.updatedAt = fromTimestamp(data.updatedAt);
    if (data.completedAt) data.completedAt = fromTimestamp(data.completedAt);
    
    return data as Study;
  },

  /**
   * Update a study
   */
  async updateStudy(studyId: string, updates: Partial<Study>): Promise<void> {
    const updateData: any = {
      ...updates,
      updatedAt: Timestamp.now(),
    };

    // Convert timestamps to serializable format
    if (updateData.createdAt) {
      updateData.createdAt = updateData.createdAt instanceof Date 
        ? updateData.createdAt.toISOString()
        : updateData.createdAt instanceof Timestamp
        ? { seconds: updateData.createdAt.seconds, nanoseconds: updateData.createdAt.nanoseconds }
        : updateData.createdAt;
    }
    if (updateData.completedAt) {
      updateData.completedAt = updateData.completedAt instanceof Date 
        ? updateData.completedAt.toISOString()
        : updateData.completedAt instanceof Timestamp
        ? { seconds: updateData.completedAt.seconds, nanoseconds: updateData.completedAt.nanoseconds }
        : updateData.completedAt;
    }
    if (updateData.updatedAt) {
      updateData.updatedAt = updateData.updatedAt instanceof Date 
        ? updateData.updatedAt.toISOString()
        : updateData.updatedAt instanceof Timestamp
        ? { seconds: updateData.updatedAt.seconds, nanoseconds: updateData.updatedAt.nanoseconds }
        : updateData.updatedAt;
    }

    const response = await fetch(`/api/db/studies/${studyId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updateData),
    });

    if (!response.ok) {
      throw new Error(`Failed to update study: ${response.statusText}`);
    }
  },

  /**
   * Delete a study
   */
  async deleteStudy(studyId: string): Promise<void> {
    const response = await fetch(`/api/db/studies/${studyId}`, {
      method: 'DELETE',
    });

    if (!response.ok) {
      throw new Error(`Failed to delete study: ${response.statusText}`);
    }
  },

  /**
   * Get all studies for a user
   */
  async getUserStudies(userId: string): Promise<Study[]> {
    const response = await fetch(`/api/db/studies?userId=${userId}`);
    
    if (!response.ok) {
      throw new Error(`Failed to get user studies: ${response.statusText}`);
    }

    const studies = await response.json();
    // Convert timestamps
    return studies.map((study: any) => {
      if (study.createdAt) study.createdAt = fromTimestamp(study.createdAt);
      if (study.updatedAt) study.updatedAt = fromTimestamp(study.updatedAt);
      if (study.completedAt) study.completedAt = fromTimestamp(study.completedAt);
      return study;
    }) as Study[];
  },

  /**
   * Get studies filtered by status
   */
  async getStudiesByStatus(userId: string, status: Study['status']): Promise<Study[]> {
    const response = await fetch(`/api/db/studies?userId=${userId}&status=${status}`);
    
    if (!response.ok) {
      throw new Error(`Failed to get studies by status: ${response.statusText}`);
    }

    const studies = await response.json();
    return studies.map((study: any) => {
      if (study.createdAt) study.createdAt = fromTimestamp(study.createdAt);
      if (study.updatedAt) study.updatedAt = fromTimestamp(study.updatedAt);
      if (study.completedAt) study.completedAt = fromTimestamp(study.completedAt);
      return study;
    }) as Study[];
  },

  /**
   * Get studies filtered by workflow status
   */
  async getStudiesByWorkflowStatus(userId: string, workflowStatus: WorkflowStatus): Promise<Study[]> {
    const response = await fetch(`/api/db/studies?userId=${userId}&workflowStatus=${workflowStatus}`);
    
    if (!response.ok) {
      throw new Error(`Failed to get studies by workflow status: ${response.statusText}`);
    }

    const studies = await response.json();
    return studies.map((study: any) => {
      if (study.createdAt) study.createdAt = fromTimestamp(study.createdAt);
      if (study.updatedAt) study.updatedAt = fromTimestamp(study.updatedAt);
      if (study.completedAt) study.completedAt = fromTimestamp(study.completedAt);
      return study;
    }) as Study[];
  },

  /**
   * Update workflow status
   */
  async updateWorkflowStatus(studyId: string, status: WorkflowStatus): Promise<void> {
    await this.updateStudy(studyId, { workflowStatus: status });
  },

  /**
   * Update study status
   */
  async updateStatus(studyId: string, status: Study['status']): Promise<void> {
    const updates: Partial<Study> = { status };
    if (status === 'completed') {
      updates.completedAt = new Date();
    }
    await this.updateStudy(studyId, updates);
  },

  /**
   * Update assets in a study
   */
  async updateAssets(studyId: string, assets: Asset[]): Promise<void> {
    await this.updateStudy(studyId, { assets });
  },

  /**
   * Update a single asset
   */
  async updateAsset(studyId: string, assetId: string, updates: Partial<Asset>): Promise<void> {
    const study = await this.getStudy(studyId);
    if (!study) {
      throw new Error('Study not found');
    }

    const updatedAssets = study.assets.map(asset =>
      asset.id === assetId ? { ...asset, ...updates } : asset
    );

    await this.updateAssets(studyId, updatedAssets);
  },

  /**
   * Update rooms in a study
   */
  async updateRooms(studyId: string, rooms: Room[]): Promise<void> {
    await this.updateStudy(studyId, { rooms });
  },

  /**
   * Update a single room
   */
  async updateRoom(studyId: string, roomId: string, updates: Partial<Room>): Promise<void> {
    const study = await this.getStudy(studyId);
    if (!study) {
      throw new Error('Study not found');
    }

    const updatedRooms = (study.rooms || []).map(room =>
      room.id === roomId ? { ...room, ...updates } : room
    );

    await this.updateRooms(studyId, updatedRooms);
  },

  /**
   * Add a room to a study
   */
  async addRoom(studyId: string, room: Room): Promise<void> {
    const study = await this.getStudy(studyId);
    if (!study) {
      throw new Error('Study not found');
    }

    const updatedRooms = [...(study.rooms || []), room];
    await this.updateRooms(studyId, updatedRooms);
  },

  /**
   * Delete a room from a study
   */
  async deleteRoom(studyId: string, roomId: string): Promise<void> {
    const study = await this.getStudy(studyId);
    if (!study) {
      throw new Error('Study not found');
    }

    const updatedRooms = (study.rooms || []).filter(room => room.id !== roomId);
    await this.updateRooms(studyId, updatedRooms);
  },

  /**
   * Update takeoffs in a study
   */
  async updateTakeoffs(studyId: string, takeoffs: Takeoff[]): Promise<void> {
    await this.updateStudy(studyId, { takeoffs });
  },

  /**
   * Update a single takeoff
   */
  async updateTakeoff(studyId: string, takeoffId: string, updates: Partial<Takeoff>): Promise<void> {
    const study = await this.getStudy(studyId);
    if (!study) {
      throw new Error('Study not found');
    }

    const updatedTakeoffs = (study.takeoffs || []).map(takeoff =>
      takeoff.id === takeoffId ? { ...takeoff, ...updates } : takeoff
    );

    await this.updateTakeoffs(studyId, updatedTakeoffs);
  },

  /**
   * Add a takeoff to a study
   */
  async addTakeoff(studyId: string, takeoff: Takeoff): Promise<void> {
    const study = await this.getStudy(studyId);
    if (!study) {
      throw new Error('Study not found');
    }

    const updatedTakeoffs = [...(study.takeoffs || []), takeoff];
    await this.updateTakeoffs(studyId, updatedTakeoffs);
  },

  /**
   * Delete a takeoff from a study
   */
  async deleteTakeoff(studyId: string, takeoffId: string): Promise<void> {
    const study = await this.getStudy(studyId);
    if (!study) {
      throw new Error('Study not found');
    }

    const updatedTakeoffs = (study.takeoffs || []).filter(takeoff => takeoff.id !== takeoffId);
    await this.updateTakeoffs(studyId, updatedTakeoffs);
  },

  /**
   * Update uploaded files in a study
   */
  async updateUploadedFiles(studyId: string, files: UploadedFile[]): Promise<void> {
    await this.updateStudy(studyId, { uploadedFiles: files });
  },

  /**
   * Get takeoffs from copy subcollection (initial pipeline output)
   */
  async getTakeoffsCopy(studyId: string): Promise<Takeoff[]> {
    // For mock, we'll get from study document
    const study = await this.getStudy(studyId);
    return study?.takeoffs || [];
  },

  /**
   * Get takeoffs from active subcollection (editable version)
   */
  async getTakeoffsActive(studyId: string): Promise<Takeoff[]> {
    // For mock, we'll get from study document
    const study = await this.getStudy(studyId);
    return study?.takeoffs || [];
  },

  /**
   * Save takeoffs to active subcollection (auto-save)
   */
  async saveTakeoffsActive(studyId: string, takeoffs: Takeoff[]): Promise<void> {
    // For mock, we'll save to study document
    await this.updateTakeoffs(studyId, takeoffs);
  },

  /**
   * Subscribe to real-time updates for active takeoffs
   */
  subscribeToTakeoffsActive(
    studyId: string,
    callback: (takeoffs: Takeoff[]) => void,
    onError?: (error: Error) => void
  ): Unsubscribe {
    // Use polling for now (can be upgraded to SSE later)
    let intervalId: NodeJS.Timeout | null = null;
    let lastTakeoffs: Takeoff[] = [];

    const poll = async () => {
      try {
        const study = await this.getStudy(studyId);
        const currentTakeoffs = study?.takeoffs || [];
        
        // Only call callback if data changed
        if (JSON.stringify(currentTakeoffs) !== JSON.stringify(lastTakeoffs)) {
          lastTakeoffs = currentTakeoffs;
          callback(currentTakeoffs);
        }
      } catch (error) {
        if (onError) {
          onError(error instanceof Error ? error : new Error(String(error)));
        }
      }
    };

    // Poll immediately
    poll();
    
    // Then poll every 1 second
    intervalId = setInterval(poll, 1000);

    return () => {
      if (intervalId) {
        clearInterval(intervalId);
      }
    };
  },

  /**
   * Subscribe to real-time updates for a study
   */
  subscribeToStudy(studyId: string, callback: (study: Study | null) => void): Unsubscribe {
    // Use polling for now (can be upgraded to SSE later)
    let intervalId: NodeJS.Timeout | null = null;
    let lastStudy: Study | null = null;

    const poll = async () => {
      try {
        const study = await this.getStudy(studyId);
        
        // Only call callback if data changed
        if (JSON.stringify(study) !== JSON.stringify(lastStudy)) {
          lastStudy = study;
          callback(study);
        }
      } catch (error) {
        callback(null);
      }
    };

    // Poll immediately
    poll();
    
    // Then poll every 1 second
    intervalId = setInterval(poll, 1000);

    return () => {
      if (intervalId) {
        clearInterval(intervalId);
      }
    };
  },

  /**
   * Subscribe to real-time updates for user's studies
   */
  subscribeToUserStudies(
    userId: string, 
    callback: (studies: Study[]) => void,
    onError?: (error: Error) => void
  ): Unsubscribe {
    // Use polling for now (can be upgraded to SSE later)
    let intervalId: NodeJS.Timeout | null = null;
    let lastStudies: Study[] = [];
    let isUnsubscribed = false;
    let hasCalledInitialCallback = false;

    const poll = async () => {
      if (isUnsubscribed) return;
      
      try {
        const studies = await this.getUserStudies(userId);
        
        if (isUnsubscribed) return;
        
        // Always call callback on first poll, then only if data changed
        const isFirstCall = !hasCalledInitialCallback;
        if (isFirstCall || JSON.stringify(studies) !== JSON.stringify(lastStudies)) {
          lastStudies = studies;
          hasCalledInitialCallback = true;
          callback(studies);
        }
      } catch (error) {
        if (isUnsubscribed) return;
        
        // On first call, always call callback with empty array to prevent timeout
        if (!hasCalledInitialCallback) {
          hasCalledInitialCallback = true;
          callback([]);
        }
        
        if (onError) {
          onError(error instanceof Error ? error : new Error(String(error)));
        } else {
          console.error('Error polling studies:', error);
        }
      }
    };

    // Poll immediately and ensure callback is called
    poll().catch(error => {
      if (isUnsubscribed) return;
      
      // Ensure callback is called even on error to prevent timeout
      if (!hasCalledInitialCallback) {
        hasCalledInitialCallback = true;
        callback([]);
      }
      
      if (onError) {
        onError(error instanceof Error ? error : new Error(String(error)));
      }
    });
    
    // Then poll every 1 second
    intervalId = setInterval(poll, 1000);

    return () => {
      isUnsubscribed = true;
      if (intervalId) {
        clearInterval(intervalId);
        intervalId = null;
      }
    };
  },
};

