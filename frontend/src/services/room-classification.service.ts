/**
 * Room Classification Service
 *
 * Calls the real backend API to analyze images and classify rooms.
 */

import { Room } from '@/types';
import { studyApi } from '@/api/firestore/study.api';
import { workflowApi } from '@/api/backend/workflow.api';
import { logger } from '@/lib/logger';

export interface ClassifyStudyResponse {
  success: boolean;
  study_id: string;
  rooms_created: number;
  rooms: Room[];
  unassigned_count: number;
  total_images: number;
  message?: string;
}

const POLL_INTERVAL = 2000; // 2 seconds
const MAX_POLL_ATTEMPTS = 60; // 2 minutes max

/**
 * Helper to wait for a specified time
 */
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

/**
 * Room Classification Service
 */
export const roomClassificationService = {
  /**
   * Classify all images in a study and create rooms
   *
   * Calls the backend workflow API to analyze images with AI.
   */
  async classifyStudy(studyId: string): Promise<ClassifyStudyResponse> {
    logger.debug('Starting room classification', { studyId });

    // Get the study to find uploaded files
    const study = await studyApi.getById(studyId);
    if (!study) {
      throw new Error(`Study ${studyId} not found`);
    }

    const imageFiles = study.uploadedFiles.filter(f => f.type.startsWith('image/'));
    const totalImages = imageFiles.length;

    if (totalImages === 0) {
      return {
        success: true,
        study_id: studyId,
        rooms_created: 0,
        rooms: [],
        unassigned_count: 0,
        total_images: 0,
        message: 'No images to classify',
      };
    }

    try {
      // Call backend to start the workflow
      logger.debug('Calling backend workflow/start', { studyId });
      const workflowResponse = await workflowApi.startWorkflow({
        study_id: studyId,
        study_doc_ids: study.uploadedFiles.map(f => f.id),
      });

      logger.debug('Workflow started', {
        studyId,
        status: workflowResponse.status,
        currentStage: workflowResponse.current_stage
      });

      // Poll for completion
      let attempts = 0;
      while (attempts < MAX_POLL_ATTEMPTS) {
        await delay(POLL_INTERVAL);
        attempts++;

        try {
          const status = await workflowApi.getStatus(studyId);
          logger.debug('Polling workflow status', {
            studyId,
            roomsCount: status.rooms_count,
            currentStage: status.current_stage,
            attempt: attempts
          });

          // Check if rooms have been created
          if (status.rooms_count > 0) {
            // Rooms are ready - fetch them from Firestore
            const updatedStudy = await studyApi.getById(studyId);
            const rooms = updatedStudy?.rooms || [];

            return {
              success: true,
              study_id: studyId,
              rooms_created: rooms.length,
              rooms,
              unassigned_count: totalImages - rooms.reduce((sum, r) => sum + r.photoIds.length, 0),
              total_images: totalImages,
              message: `Room classification completed - ${rooms.length} rooms created`,
            };
          }

          // Check for error status
          if (status.current_stage === 'error' || workflowResponse.status === 'error') {
            throw new Error('Workflow failed during processing');
          }

        } catch (pollError) {
          logger.warn('Error polling workflow status', { error: pollError, attempt: attempts });
          // Continue polling - backend might be temporarily unavailable
        }
      }

      // Timeout - check if any rooms were created anyway
      const finalStudy = await studyApi.getById(studyId);
      const rooms = finalStudy?.rooms || [];

      if (rooms.length > 0) {
        return {
          success: true,
          study_id: studyId,
          rooms_created: rooms.length,
          rooms,
          unassigned_count: totalImages - rooms.reduce((sum, r) => sum + r.photoIds.length, 0),
          total_images: totalImages,
          message: 'Room classification completed (polling timeout)',
        };
      }

      throw new Error('Room classification timed out - no rooms created');

    } catch (error) {
      logger.error('Error during room classification', { error, studyId });
      throw error;
    }
  },

  /**
   * Health check for the room classification service
   */
  async healthCheck(): Promise<boolean> {
    try {
      const response = await workflowApi.healthCheck();
      return response.status === 'healthy';
    } catch {
      return false;
    }
  },
};
