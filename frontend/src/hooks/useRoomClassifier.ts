/**
 * useRoomClassifier Hook
 * 
 * Manages the room classification process including API calls and progress tracking.
 */

import { useState, useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { roomClassificationService } from '@/services/room-classification.service';
import { studyService } from '@/services/study.service';
import { workflowService } from '@/services/workflow.service';
import { useProgressAnimation } from './useProgressAnimation';
import { usePolling } from './usePolling';
import { getWorkflowPageUrl } from '@/utils/workflow';
import { logger } from '@/lib/logger';
import { Study } from '@/types';

const MESSAGES = [
  'Scanning uploaded documents...',
  'Identifying room types...',
  'Categorizing photos by room...',
  'Detecting objects and features...',
  'Finalizing room categorization...',
];

interface UseRoomClassifierOptions {
  studyId: string;
  study: Study | undefined;
}

/**
 * Hook for managing room classification
 */
export function useRoomClassifier({ studyId, study }: UseRoomClassifierOptions) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [isClassifying, setIsClassifying] = useState(false);
  const hasStartedRef = useRef<string>('');
  const classificationStartedRef = useRef(false);

  const { progress, currentMessage, complete } = useProgressAnimation({
    messages: MESSAGES,
  });

  // Poll for rooms after classification is triggered
  const { start: startPolling, stop: stopPolling } = usePolling(
    async () => {
      const fetchedStudy = await studyService.getById(studyId);
      return fetchedStudy;
    },
    (fetchedStudy) => {
      // Stop when rooms exist and have data
      const hasRooms = !!(fetchedStudy?.rooms && fetchedStudy.rooms.length > 0);
      if (hasRooms) {
        // Complete progress and update workflow
        complete();
        workflowService.updateStatus(studyId, 'reviewing_rooms')
          .then(() => {
            logger.debug('Workflow status updated, navigating to review page');
            setTimeout(() => {
              router.push(`/study/${studyId}/review/first`);
            }, 500);
          })
          .catch((err) => {
            logger.error('Error updating workflow status', { error: err });
            setError('Failed to update workflow status. Please try again.');
          });
      }
      return hasRooms;
    },
    {
      onError: (err) => {
        logger.error('Error polling for rooms', { error: err, studyId });
        // Don't set error state - continue polling
      },
    }
  );

  useEffect(() => {
    if (!study) {
      logger.debug('Study not found, redirecting to dashboard', { studyId });
      router.push('/dashboard');
      return;
    }

    // Navigation guard: redirect to appropriate page based on workflow status
    const status = study.workflowStatus;
    if (status !== 'uploading_documents' && status !== 'analyzing_rooms') {
      logger.debug('Wrong workflow status, redirecting', {
        currentStatus: status,
        expectedStatuses: ['uploading_documents', 'analyzing_rooms'],
      });
      router.push(getWorkflowPageUrl(studyId, status));
      return;
    }

    // Prevent multiple starts
    const workflowKey = `${studyId}-${study.workflowStatus}`;
    if (hasStartedRef.current === workflowKey) {
      logger.debug('Already started for this workflow status, skipping', { workflowKey });
      return;
    }
    hasStartedRef.current = workflowKey;

    // Update workflow status to analyzing_rooms if needed
    if (status === 'uploading_documents') {
      workflowService.updateStatus(studyId, 'analyzing_rooms')
        .catch((err) => {
          logger.error('Error updating workflow status', { error: err });
        });
    }

    // Start classification if not already started
    if (!classificationStartedRef.current) {
      classificationStartedRef.current = true;
      setIsClassifying(true);
      
      logger.debug('Starting room classification', { studyId });
      
      // Call the room classification API
      roomClassificationService.classifyStudy(studyId)
        .then((response) => {
          logger.debug('Room classification API call successful', {
            studyId,
            roomsCreated: response.rooms_created,
            totalImages: response.total_images,
          });
          
          setIsClassifying(false);
          
          // Start polling for rooms (they should be in Firestore now)
          // The API updates Firestore directly, so we poll to detect the update
          startPolling();
        })
        .catch((err) => {
          logger.error('Error calling room classification API', { error: err, studyId });
          setIsClassifying(false);
          setError(
            err instanceof Error 
              ? err.message 
              : 'Failed to classify rooms. Please try again.'
          );
          // Still try to poll in case rooms were created despite the error
          startPolling();
        });
    }

    return () => {
      stopPolling();
      hasStartedRef.current = '';
    };
  }, [studyId, study, router, startPolling, stopPolling]);

  return {
    progress,
    currentMessage,
    error,
    isClassifying,
  };
}

