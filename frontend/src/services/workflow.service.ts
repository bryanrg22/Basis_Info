/**
 * Workflow Service
 *
 * Manages workflow state transitions and navigation.
 */

import { studyApi } from '@/api/firestore/study.api';
import { WorkflowStatus, Study } from '@/types';

/**
 * Workflow step order for navigation
 */
const STEP_ORDER: WorkflowStatus[] = [
  'uploading_documents',
  'analyzing_rooms',
  'resource_extraction',
  'reviewing_rooms',
  'engineering_takeoff',
  'completed',
];

/**
 * Get the next workflow status
 */
function getNextStatus(currentStatus: WorkflowStatus): WorkflowStatus | null {
  const nextStatusMap: Record<WorkflowStatus, WorkflowStatus | null> = {
    uploading_documents: 'analyzing_rooms',
    analyzing_rooms: 'resource_extraction',
    resource_extraction: 'reviewing_rooms',
    reviewing_rooms: 'engineering_takeoff',
    engineering_takeoff: 'completed',
    completed: null,
  };

  return nextStatusMap[currentStatus] ?? null;
}

/**
 * Get the previous workflow status
 */
function getPreviousStatus(currentStatus: WorkflowStatus): WorkflowStatus | null {
  const prevStatusMap: Record<WorkflowStatus, WorkflowStatus | null> = {
    uploading_documents: null,
    analyzing_rooms: 'uploading_documents',
    resource_extraction: 'analyzing_rooms',
    reviewing_rooms: 'resource_extraction',
    engineering_takeoff: 'reviewing_rooms',
    completed: 'engineering_takeoff',
  };

  return prevStatusMap[currentStatus] ?? null;
}

/**
 * Workflow Service
 */
export const workflowService = {
  /**
   * Update workflow status and persist
   * Handles both forward and backward navigation
   */
  async updateStatus(
    studyId: string,
    workflowStatus: WorkflowStatus,
    options?: { isBackward?: boolean; visitedSteps?: WorkflowStatus[] }
  ): Promise<void> {
    const study = await studyApi.getById(studyId);
    if (!study) {
      throw new Error(`Study ${studyId} not found`);
    }

    const isBackward = options?.isBackward ?? false;
    const currentWorkflowStatus = study.workflowStatus;
    const currentStep = study.currentStep || currentWorkflowStatus;
    const visitedSteps = study.visitedSteps || [currentWorkflowStatus];

    // Determine if this is forward or backward navigation
    const currentStepIndex = STEP_ORDER.indexOf(currentStep);
    const targetStepIndex = STEP_ORDER.indexOf(workflowStatus);
    const isMovingBackward = targetStepIndex < currentStepIndex;

    // Update visited steps if this is a new step
    const updatedVisitedSteps = [...visitedSteps];
    if (!updatedVisitedSteps.includes(workflowStatus)) {
      updatedVisitedSteps.push(workflowStatus);
    }

    // Update workflowStatus only if moving forward to a new step
    // Keep workflowStatus as the highest completed step
    let newWorkflowStatus = currentWorkflowStatus;
    if (!isMovingBackward && targetStepIndex > STEP_ORDER.indexOf(currentWorkflowStatus)) {
      newWorkflowStatus = workflowStatus;
    }

    // Update status field based on workflowStatus
    let status: Study['status'] = 'in_progress';
    if (newWorkflowStatus === 'completed') {
      status = 'completed';
    } else if (newWorkflowStatus === 'uploading_documents') {
      status = 'pending';
    }

    const updates: Partial<Study> = {
      workflowStatus: newWorkflowStatus,
      currentStep: workflowStatus,
      visitedSteps: updatedVisitedSteps,
      status,
    };

    if (status === 'completed') {
      updates.completedAt = new Date();
    }

    await studyApi.update(studyId, updates);
  },

  /**
   * Check if a workflow transition is valid (forward only)
   */
  isValidTransition(from: WorkflowStatus, to: WorkflowStatus): boolean {
    const validTransitions: Record<WorkflowStatus, WorkflowStatus[]> = {
      uploading_documents: ['analyzing_rooms'],
      analyzing_rooms: ['resource_extraction'],
      resource_extraction: ['reviewing_rooms'],
      reviewing_rooms: ['engineering_takeoff'],
      engineering_takeoff: ['completed'],
      completed: [],
    };

    return validTransitions[from]?.includes(to) ?? false;
  },

  /**
   * Check if navigation to a step is allowed
   * Allows navigation to any previously visited step or the next step
   */
  canNavigateTo(
    targetStep: WorkflowStatus,
    currentStep: WorkflowStatus,
    visitedSteps: WorkflowStatus[],
    workflowStatus: WorkflowStatus
  ): boolean {
    // Can always navigate to previously visited steps
    if (visitedSteps.includes(targetStep)) {
      return true;
    }

    // Can navigate forward to the next step
    const nextStep = getNextStatus(workflowStatus);
    if (nextStep === targetStep) {
      return true;
    }

    return false;
  },

  /**
   * Get the next workflow status
   */
  getNextStatus,

  /**
   * Get the previous workflow status
   */
  getPreviousStatus,

  /**
   * Get step order/number (0-indexed)
   */
  getStepOrder(step: WorkflowStatus): number {
    return STEP_ORDER.indexOf(step);
  },
};

