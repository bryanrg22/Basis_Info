/**
 * useWorkflow Hook
 * 
 * Manages workflow status and transitions.
 */

import { useCallback } from 'react';
import { workflowService } from '@/services/workflow.service';
import { WorkflowStatus } from '@/types';
import { logger } from '@/lib/logger';

interface UseWorkflowOptions {
  studyId: string;
  currentStatus: WorkflowStatus;
}

/**
 * Hook for managing workflow operations
 */
export function useWorkflow({ studyId, currentStatus }: UseWorkflowOptions) {
  const updateStatus = useCallback(async (newStatus: WorkflowStatus) => {
    try {
      // Validate transition
      if (!workflowService.isValidTransition(currentStatus, newStatus)) {
        throw new Error(`Invalid workflow transition from ${currentStatus} to ${newStatus}`);
      }

      await workflowService.updateStatus(studyId, newStatus);
      logger.debug('Workflow status updated', { studyId, from: currentStatus, to: newStatus });
    } catch (err) {
      logger.error('Error updating workflow status', { error: err, studyId });
      throw err;
    }
  }, [studyId, currentStatus]);

  const getNextStatus = useCallback((): WorkflowStatus | null => {
    return workflowService.getNextStatus(currentStatus);
  }, [currentStatus]);

  const canTransitionTo = useCallback((status: WorkflowStatus): boolean => {
    return workflowService.isValidTransition(currentStatus, status);
  }, [currentStatus]);

  return {
    currentStatus,
    updateStatus,
    getNextStatus,
    canTransitionTo,
  };
}

