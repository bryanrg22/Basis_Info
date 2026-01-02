/**
 * Workflow Utilities
 * 
 * Functions for working with workflow statuses, routing, and display.
 */

import { WorkflowStatus } from '@/types';

/**
 * Migration map for old workflow status names to new ones
 */
const STATUS_MIGRATION_MAP: Record<string, WorkflowStatus> = {
  'documents_uploaded': 'uploading_documents',
  'first_analysis_pending': 'analyzing_rooms',
  'first_analysis_complete': 'resource_extraction',
  'first_review_complete': 'engineering_takeoff',
  'takeoffs_pending': 'engineering_takeoff',
  'takeoffs_complete': 'engineering_takeoff',
  'takeoffs_review_complete': 'engineering_takeoff',
  'analyzing_takeoffs': 'engineering_takeoff',
  'reviewing_takeoffs': 'engineering_takeoff',
  'reviewing_assets': 'engineering_takeoff',
  'verification_pending': 'engineering_takeoff',
  'verifying_assets': 'engineering_takeoff',
  'completed': 'completed',
};

/**
 * Migrates old workflow status names to new ones
 */
export function migrateWorkflowStatus(status: string): WorkflowStatus {
  if (STATUS_MIGRATION_MAP[status]) {
    return STATUS_MIGRATION_MAP[status];
  }
  return status as WorkflowStatus;
}

/**
 * Gets a human-readable label for a workflow status
 */
export function getWorkflowStatusLabel(status: WorkflowStatus | string): string {
  const migratedStatus = migrateWorkflowStatus(status);
  
  const labels: Record<WorkflowStatus, string> = {
    uploading_documents: 'Uploading Documents',
    analyzing_rooms: 'Analyzing Rooms',
    resource_extraction: 'Resource Extraction',
    reviewing_rooms: 'Reviewing Rooms',
    engineering_takeoff: 'Engineering Takeoffs',
    completed: 'Completed',
  };
  
  return labels[migratedStatus] || status;
}

/**
 * Gets the Tailwind CSS color classes for a workflow status
 */
export function getWorkflowStatusColor(status: WorkflowStatus | string): string {
  const migratedStatus = migrateWorkflowStatus(status);
  
  if (migratedStatus === 'completed') {
    return 'bg-green-100 text-green-800';
  }
  if (migratedStatus === 'analyzing_rooms') {
    return 'bg-blue-100 text-blue-800';
  }
  if (
    migratedStatus === 'reviewing_rooms' || 
    migratedStatus === 'engineering_takeoff'
  ) {
    return 'bg-yellow-100 text-yellow-800';
  }
  return 'bg-gray-100 text-gray-800';
}

/**
 * Gets the page URL for a workflow status
 */
export function getWorkflowPageUrl(studyId: string, workflowStatus: WorkflowStatus | string): string {
  const migratedStatus = migrateWorkflowStatus(workflowStatus);
  
  switch (migratedStatus) {
    case 'uploading_documents':
    case 'analyzing_rooms':
      return `/study/${studyId}/analyze/first`;
    case 'resource_extraction':
      return `/study/${studyId}/review/resources`;
    case 'reviewing_rooms':
      return `/study/${studyId}/review/first`;
    case 'engineering_takeoff':
      return `/study/${studyId}/engineering-takeoff`;
    case 'completed':
      return `/study/${studyId}/complete`;
    default:
      return `/study/${studyId}/engineering-takeoff`;
  }
}

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
 * Get step order/number (0-indexed)
 */
export function getStepOrder(step: WorkflowStatus | string): number {
  const migratedStatus = migrateWorkflowStatus(step);
  return STEP_ORDER.indexOf(migratedStatus);
}

/**
 * Check if a step is accessible (has been visited or is the next step)
 */
export function isStepAccessible(
  step: WorkflowStatus | string,
  currentStep: WorkflowStatus | string,
  visitedSteps: WorkflowStatus[],
  workflowStatus: WorkflowStatus | string
): boolean {
  const migratedStep = migrateWorkflowStatus(step);
  const migratedCurrentStep = migrateWorkflowStatus(currentStep);
  const migratedWorkflowStatus = migrateWorkflowStatus(workflowStatus);
  
  // Can always navigate to previously visited steps
  if (visitedSteps.includes(migratedStep)) {
    return true;
  }

  // Can navigate forward to the next step
  const currentIndex = STEP_ORDER.indexOf(migratedWorkflowStatus);
  const nextIndex = currentIndex + 1;
  if (nextIndex < STEP_ORDER.length && STEP_ORDER[nextIndex] === migratedStep) {
    return true;
  }

  return false;
}

