'use client';

import { useEffect, useRef, useCallback } from 'react';
import { useApp } from '@/contexts/AppContext';
import { useParams, useRouter } from 'next/navigation';
import ProtectedRoute from '@/components/ProtectedRoute';
import Header from '@/components/Header';
import Sidebar from '@/components/Sidebar';
import { migrateWorkflowStatus } from '@/utils/workflow';
import { useProgressAnimation } from '@/hooks/useProgressAnimation';
import { usePolling } from '@/hooks/usePolling';
import { studyService } from '@/services/study.service';

const messages = [
  'Scanning uploaded documents...',
  'Identifying room types...',
  'Categorizing photos by room...',
  'Detecting objects and features...',
  'Finalizing room categorization...',
];
import ProgressIndicator from '@/components/ui/ProgressIndicator';
import { useRoomClassifier } from '@/hooks/useRoomClassifier';
import StudyBackButton from '@/components/StudyBackButton';

export default function FirstAnalyzerPage() {
  const { state, startRoomClassification, updateWorkflowStatus } = useApp();
  const params = useParams();
  const router = useRouter();
  const studyId = params.id as string;

  const study = state.studies.find(s => s.id === studyId);
  const hasAdvancedRef = useRef(false);
  const classificationStartedRef = useRef(false);
  const { progress, currentMessage, complete } = useProgressAnimation({ messages });

  const navigateToWorkflowStep = useCallback(
    (status: string) => {
      if (status === 'resource_extraction') {
        router.push(`/study/${studyId}/review/resources`);
      } else if (status === 'reviewing_rooms') {
        router.push(`/study/${studyId}/review/first`);
      } else if (status === 'engineering_takeoff') {
        router.push(`/study/${studyId}/engineering-takeoff`);
      } else if (status === 'completed') {
        router.push(`/study/${studyId}/complete`);
      } else {
        router.push('/dashboard');
      }
    },
    [router, studyId]
  );

  const handleRoomsReady = useCallback(() => {
    if (hasAdvancedRef.current) {
      return;
    }
    hasAdvancedRef.current = true;
    complete();

    updateWorkflowStatus(studyId, 'resource_extraction')
      .then(() => {
        router.push(`/study/${studyId}/review/resources`);
      })
      .catch(() => {
        // Silently handle error
      });
  }, [complete, router, studyId, updateWorkflowStatus]);

  useEffect(() => {
    if (!study) {
      router.push('/dashboard');
      return;
    }

    const status = migrateWorkflowStatus(study.workflowStatus as string);
    const currentStep = study.currentStep || status;
    const visitedSteps = study.visitedSteps || [status];

    // Allow access if current step is analyzing_rooms or uploading_documents, or if it's been visited
    const canAccess = 
      currentStep === 'analyzing_rooms' || 
      currentStep === 'uploading_documents' ||
      visitedSteps.includes('analyzing_rooms') ||
      visitedSteps.includes('uploading_documents') ||
      status === 'uploading_documents' ||
      status === 'analyzing_rooms';

    if (!canAccess) {
      navigateToWorkflowStep(currentStep);
      return;
    }

    // Update currentStep if needed
    if (currentStep !== 'analyzing_rooms' && currentStep !== 'uploading_documents') {
      const targetStep = status === 'uploading_documents' ? 'uploading_documents' : 'analyzing_rooms';
      const updatedVisitedSteps = visitedSteps.includes(targetStep) 
        ? visitedSteps 
        : [...visitedSteps, targetStep];
      
      updateWorkflowStatus(studyId, targetStep).catch(() => {
        // Silently handle error
      });
    }

    if (status === 'uploading_documents') {
      // Update workflow status first
      updateWorkflowStatus(studyId, 'analyzing_rooms').catch(() => {
        // Silently handle error
      });
      
      // Trigger room classification if not already started
      if (!classificationStartedRef.current) {
        classificationStartedRef.current = true;
        startRoomClassification(studyId).catch(error => {
          // Show error but allow user to continue (they'll see empty rooms)
          if (typeof window !== 'undefined') {
            alert(
              `Room classification failed to start: ${
                error instanceof Error ? error.message : 'Unknown error'
              }. You can continue, but rooms may need manual review.`
            );
          }
        });
      }
    }
  }, [study, studyId, router, updateWorkflowStatus, navigateToWorkflowStep, startRoomClassification]);

  // Check if appraisal resources have been ingested (PAUSE #1 trigger)
  // This happens BEFORE rooms are ready in the parallel workflow
  const appraisalReady = !!(study?.appraisalResources?.ingested === true);
  const roomsReady = !!(study?.rooms && study.rooms.length > 0);

  useEffect(() => {
    // Navigate to PAUSE #1 (appraisal review) when appraisal is ready
    // Rooms may still be processing in background
    if (appraisalReady) {
      handleRoomsReady();
    }
  }, [appraisalReady, handleRoomsReady]);

  const { start: startPolling, stop: stopPolling } = usePolling(
    async () => {
      return studyService.getById(studyId);
    },
    fetchedStudy => {
      // Check for appraisal ready (PAUSE #1 trigger) - this comes first in parallel workflow
      const hasAppraisal = !!(fetchedStudy?.appraisalResources?.ingested === true);
      if (hasAppraisal) {
        handleRoomsReady();
        return true;
      }
      // Fallback: also check for rooms (backwards compatibility)
      const hasRooms = !!(fetchedStudy?.rooms && fetchedStudy.rooms.length > 0);
      if (hasRooms) {
        handleRoomsReady();
      }
      return hasRooms || hasAppraisal;
    },
    {
      onError: () => {
        if (!hasAdvancedRef.current && typeof window !== 'undefined') {
          alert(
            'Room classification is taking longer than expected. You can continue, but rooms may need manual review.'
          );
          handleRoomsReady();
        }
      },
    }
  );

  useEffect(() => {
    // Stop polling if appraisal is ready (navigating to PAUSE #1)
    if (!study || appraisalReady) {
      stopPolling();
      return;
    }

    startPolling();

    return () => {
      stopPolling();
    };
  }, [study, appraisalReady, startPolling, stopPolling]);

  if (!study) {
    return <div>Loading...</div>;
  }

  return (
    <ProtectedRoute>
      <div className="flex h-screen">
        <Sidebar />
        <div className="flex-1 flex flex-col overflow-hidden">
          <Header />
          <div className="flex-1 overflow-y-auto">
            <div className="p-6 max-w-2xl mx-auto">
      <div className="mb-4">
        <StudyBackButton />
      </div>
      <div className="bg-white p-8 rounded-lg shadow-sm border border-gray-200 text-center">
        <div className="mb-6">
          <div className="w-16 h-16 bg-primary-100 rounded-full flex items-center justify-center mx-auto mb-4">
            <svg className="w-8 h-8 text-primary-600 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
          </div>
          <h2 className="text-xl font-semibold text-gray-900 mb-2">Categorizing Rooms and Objects</h2>
          <p className="text-sm text-gray-600">Our AI is analyzing your photos and identifying room types</p>
        </div>

        {/* Progress Bar */}
        <div className="w-full bg-gray-200 rounded-full h-2 mb-4">
          <div
            className="bg-primary-600 h-2 rounded-full transition-all duration-300"
            style={{ width: `${progress}%` }}
          />
        </div>
        
        {/* Status Messages */}
        <div className="mb-2">
          <p className="text-sm text-primary-600 font-medium">{currentMessage}</p>
        </div>
        
        <p className="text-sm text-gray-500">{Math.round(progress)}% Complete</p>
      </div>
            </div>
          </div>
        </div>
      </div>
    </ProtectedRoute>
  );
}

