/**
 * Room Classification API
 *
 * Triggers the real backend workflow to analyze images and classify rooms.
 */

import { studyApi } from './firestore/study.api';
import { workflowApi } from './backend/workflow.api';
import { logger } from '@/lib/logger';

const POLL_INTERVAL = 2000; // 2 seconds
const MAX_POLL_ATTEMPTS = 90; // 3 minutes max

/**
 * Helper to wait for a specified time
 */
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

export const roomClassificationApi = {
  /**
   * Triggers the classification workflow for the given study.
   * Calls the real backend API to analyze images with AI.
   */
  async startStudyClassification(studyId: string): Promise<void> {
    logger.info('Room classification starting', { studyId });

    // Get the study to find uploaded files
    const study = await studyApi.getById(studyId);
    if (!study) {
      throw new Error(`Study ${studyId} not found`);
    }

    const uploadedFiles = study.uploadedFiles || [];
    if (uploadedFiles.length === 0) {
      logger.warn('No files uploaded for classification', { studyId });
      return;
    }

    try {
      // Call backend to start the workflow
      logger.debug('Calling backend workflow/start', {
        studyId,
        fileCount: uploadedFiles.length
      });

      const workflowResponse = await workflowApi.startWorkflow({
        study_id: studyId,
        study_doc_ids: uploadedFiles.map(f => f.id),
      });

      logger.info('Backend workflow started', {
        studyId,
        status: workflowResponse.status,
        currentStage: workflowResponse.current_stage,
      });

      // Poll for rooms to be created
      let attempts = 0;
      while (attempts < MAX_POLL_ATTEMPTS) {
        await delay(POLL_INTERVAL);
        attempts++;

        try {
          // Check if rooms have been created in Firestore
          const updatedStudy = await studyApi.getById(studyId);
          const rooms = updatedStudy?.rooms || [];

          logger.debug('Polling for rooms', {
            studyId,
            roomsFound: rooms.length,
            attempt: attempts,
          });

          if (rooms.length > 0) {
            logger.info('Room classification completed', {
              studyId,
              roomsCreated: rooms.length,
              totalImages: uploadedFiles.filter(f => f.type.startsWith('image/')).length,
            });
            return;
          }

          // Also check workflow status for completion or error
          try {
            const status = await workflowApi.getStatus(studyId);
            if (status.current_stage === 'error') {
              throw new Error('Workflow failed during processing');
            }
            // If workflow has advanced past room classification, we're done
            if (status.rooms_count > 0) {
              logger.info('Room classification completed via workflow status', {
                studyId,
                roomsCount: status.rooms_count,
              });
              return;
            }
          } catch (statusError) {
            // Status endpoint might not be ready yet - continue polling Firestore
            logger.debug('Error checking workflow status, continuing', { statusError });
          }

        } catch (pollError) {
          logger.warn('Error polling for rooms', { error: pollError, attempt: attempts });
          // Continue polling - might be temporary issue
        }
      }

      // Timeout - log warning but don't throw
      // The polling in the page component will continue checking
      logger.warn('Room classification polling timeout', { studyId, attempts });

    } catch (error) {
      logger.error('Error starting room classification', { error, studyId });
      throw error;
    }
  },
};
