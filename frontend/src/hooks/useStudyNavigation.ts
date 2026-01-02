/**
 * Hook for Study Navigation
 * 
 * Provides navigation functions for moving between study workflow steps
 * with automatic state persistence and visited step tracking.
 */

import { useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { useApp } from '@/contexts/AppContext';
import { WorkflowStatus } from '@/types';
import { workflowService } from '@/services/workflow.service';
import { getWorkflowPageUrl } from '@/utils/workflow';

interface UseStudyNavigationOptions {
  studyId: string;
}

/**
 * Hook for navigating between study workflow steps
 */
export function useStudyNavigation({ studyId }: UseStudyNavigationOptions) {
  const { state, navigateToStep, updateWorkflowStatus } = useApp();
  const router = useRouter();

  const study = state.studies.find(s => s.id === studyId);
  const currentStep = study?.currentStep || study?.workflowStatus || 'uploading_documents';
  const visitedSteps = study?.visitedSteps || [];
  const workflowStatus = study?.workflowStatus || 'uploading_documents';

  /**
   * Navigate to a specific step
   */
  const goToStep = useCallback(async (step: WorkflowStatus) => {
    if (!study) {
      throw new Error('Study not found');
    }

    // Check if navigation is allowed
    if (!workflowService.canNavigateTo(step, currentStep, visitedSteps, workflowStatus)) {
      throw new Error(`Cannot navigate to step ${step}`);
    }

    // Navigate to the step
    await navigateToStep(studyId, step);
    
    // Navigate to the page
    const url = getWorkflowPageUrl(studyId, step);
    router.push(url);
  }, [study, studyId, currentStep, visitedSteps, workflowStatus, navigateToStep, router]);

  /**
   * Navigate to the next step
   */
  const goToNextStep = useCallback(async () => {
    const nextStep = workflowService.getNextStatus(workflowStatus);
    if (!nextStep) {
      throw new Error('No next step available');
    }
    await goToStep(nextStep);
  }, [workflowStatus, goToStep]);

  /**
   * Navigate to the previous step
   */
  const goToPreviousStep = useCallback(async () => {
    const prevStep = workflowService.getPreviousStatus(currentStep);
    if (!prevStep) {
      throw new Error('No previous step available');
    }
    
    // Only allow going back to visited steps
    if (!visitedSteps.includes(prevStep)) {
      throw new Error('Previous step has not been visited');
    }
    
    await goToStep(prevStep);
  }, [currentStep, visitedSteps, goToStep]);

  /**
   * Check if a step can be navigated to
   */
  const canNavigateTo = useCallback((step: WorkflowStatus): boolean => {
    return workflowService.canNavigateTo(step, currentStep, visitedSteps, workflowStatus);
  }, [currentStep, visitedSteps, workflowStatus]);

  /**
   * Check if there is a next step
   */
  const hasNextStep = useCallback((): boolean => {
    return workflowService.getNextStatus(workflowStatus) !== null;
  }, [workflowStatus]);

  /**
   * Check if there is a previous step
   */
  const hasPreviousStep = useCallback((): boolean => {
    const prevStep = workflowService.getPreviousStatus(currentStep);
    return prevStep !== null && visitedSteps.includes(prevStep);
  }, [currentStep, visitedSteps]);

  return {
    currentStep,
    visitedSteps,
    workflowStatus,
    goToStep,
    goToNextStep,
    goToPreviousStep,
    canNavigateTo,
    hasNextStep,
    hasPreviousStep,
  };
}

