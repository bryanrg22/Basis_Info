'use client';

import React, { createContext, useContext, useReducer, useEffect, ReactNode, useRef } from 'react';
import { Study, Statistics, UploadedFile, WorkflowStatus } from '@/types';
import { studyService } from '@/services/study.service';
import { workflowService } from '@/services/workflow.service';
import { statisticsService } from '@/services/statistics.service';
import { fileStorageApi } from '@/api/storage/file.api';
import { useAuth } from './AuthContext';
type Unsubscribe = () => void;
import { logger } from '@/lib/logger';
import { STUDIES_LOAD_TIMEOUT } from '@/config/constants';
import { rootReducer, AppState, AppAction } from './reducers/root.reducer';
import { roomClassificationApi } from '@/api/room-classification.api';

// Helper function to get initial state, loading user photoURL from localStorage if available
// We'll validate the UID matches when the user is synced
const getInitialState = (): AppState => {
  let photoURL: string | null = null;
  if (typeof window !== 'undefined') {
    try {
      const stored = localStorage.getItem('user_photoURL');
      if (stored) {
        photoURL = stored;
      }
    } catch (e) {
      // Ignore localStorage errors
    }
  }
  
  return {
    user: {
      name: '',
      email: '',
      company: '',
      photoURL,
    },
    statistics: {
      studiesCompleted: 0,
      revenueGenerated: 0,
      taxSavingsProvided: 0,
    },
    studies: [],
    currentStudy: null,
    sidebarOpen: true,
    loading: false,
    error: null,
  };
};

const initialState: AppState = getInitialState();

/** Options for uploadFiles including progress tracking */
interface UploadFilesOptions {
  /** Optional callback invoked with aggregate progress (0-100) */
  onProgress?: (progress: number) => void;
  /** Max concurrent uploads (default 3) */
  concurrency?: number;
}

interface AppContextType {
  state: AppState;
  dispatch: React.Dispatch<AppAction>;
  // Helper methods that sync with Firestore
  createStudy: (study: Omit<Study, 'id' | 'userId' | 'createdAt' | 'updatedAt'>) => Promise<Study>;
  updateStudyInFirestore: (studyId: string, updates: Partial<Study>) => Promise<void>;
  updateWorkflowStatus: (studyId: string, status: WorkflowStatus) => Promise<void>;
  navigateToStep: (studyId: string, step: WorkflowStatus) => Promise<void>;
  uploadFiles: (
    files: File[],
    studyId: string,
    currentStudy?: Study,
    options?: UploadFilesOptions
  ) => Promise<UploadedFile[]>;
  deleteFile: (studyId: string, fileId: string) => Promise<void>;
  calculateStatistics: () => Statistics;
  startRoomClassification: (studyId: string) => Promise<void>;
}

const AppContext = createContext<AppContextType | null>(null);

/**
 * Runs async tasks with a concurrency limit.
 * @param tasks - Array of functions that return Promises
 * @param limit - Max concurrent tasks
 * @returns Array of results in the same order as tasks
 */
async function runWithConcurrency<T>(
  tasks: (() => Promise<T>)[],
  limit: number
): Promise<T[]> {
  const results: T[] = new Array(tasks.length);
  let nextIndex = 0;

  const worker = async (): Promise<void> => {
    while (nextIndex < tasks.length) {
      const idx = nextIndex++;
      results[idx] = await tasks[idx]();
    }
  };

  // Spawn `limit` workers
  const workers = Array.from({ length: Math.min(limit, tasks.length) }, () => worker());
  await Promise.all(workers);

  return results;
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(rootReducer, initialState);
  const { currentUser } = useAuth();
  const unsubscribeRef = useRef<Unsubscribe | null>(null);

  /**
   * Loads studies from Firestore when user is authenticated.
   * 
   * This effect:
   * 1. Sets up a real-time listener that automatically updates when studies change
   * 2. Manages loading state to prevent UI actions while data is being fetched
   * 3. Handles errors gracefully by setting error state (which disables actions like creating new studies)
   * 4. Includes a safety timeout to prevent the loading state from getting stuck indefinitely
   * 
   * The loading and error states are used throughout the app to ensure users can only
   * proceed when the application is in a valid, ready state.
   */
  useEffect(() => {
    if (!currentUser?.uid) {
      // No user logged in - clear studies and ensure loading is false
      dispatch({ type: 'SET_STUDIES', payload: [] });
      dispatch({ type: 'SET_LOADING', payload: false });
      return;
    }

    // Start loading state - this will disable actions like "Start Analysis" button
    dispatch({ type: 'SET_LOADING', payload: true });
    dispatch({ type: 'SET_ERROR', payload: null });

    // Safety timeout: If studies don't load within timeout period, assume something went wrong
    // This prevents the UI from being stuck in a loading state indefinitely
    // The timeout will set an error state, which keeps actions disabled until resolved
    const timeoutId = setTimeout(() => {
      dispatch({ type: 'SET_LOADING', payload: false });
      // Set error if we timeout - this indicates something went wrong
      dispatch({ type: 'SET_ERROR', payload: 'Failed to load studies: Request timed out' });
    }, STUDIES_LOAD_TIMEOUT);

    try {
      // Set up real-time listener for user's studies
      // This listener will fire immediately with current data, and then whenever data changes
      const unsubscribe = studyService.subscribeByUserId(
        currentUser.uid,
        // Success callback: Studies loaded successfully
        (studies) => {
          clearTimeout(timeoutId);
          dispatch({ type: 'SET_STUDIES', payload: studies });
          dispatch({ type: 'SET_LOADING', payload: false });
          dispatch({ type: 'SET_ERROR', payload: null }); // Clear any previous errors on success
        },
        // Error callback: Something went wrong (e.g., missing Firestore index, network error)
        (error) => {
          clearTimeout(timeoutId);
          logger.error('Error in study subscription', { error });
          dispatch({ type: 'SET_ERROR', payload: error.message || 'Failed to load studies' });
          dispatch({ type: 'SET_LOADING', payload: false }); // Clear loading on error
          // Note: Error state remains, which keeps actions disabled until error is resolved
        }
      );

      unsubscribeRef.current = unsubscribe;

      // Cleanup: Remove listener and clear timeout when component unmounts or user changes
      return () => {
        clearTimeout(timeoutId);
        if (unsubscribeRef.current) {
          unsubscribeRef.current();
        }
      };
    } catch (error) {
      // Catch synchronous errors (e.g., Firestore not initialized)
      clearTimeout(timeoutId);
      logger.error('Error loading studies', { error });
      dispatch({ type: 'SET_ERROR', payload: error instanceof Error ? error.message : 'Failed to load studies' });
      dispatch({ type: 'SET_LOADING', payload: false }); // Clear loading on error
    }
  }, [currentUser?.uid]);

  // Cleanup listener on unmount
  useEffect(() => {
    return () => {
      if (unsubscribeRef.current) {
        unsubscribeRef.current();
      }
    };
  }, []);

  // Helper: Create a new study
  const createStudy = async (study: Omit<Study, 'id' | 'userId' | 'createdAt' | 'updatedAt'>): Promise<Study> => {
    if (!currentUser?.uid) {
      throw new Error('User must be authenticated to create a study');
    }

    try {
      dispatch({ type: 'SET_ERROR', payload: null });
      const newStudy = await studyService.create({
        ...study,
        userId: currentUser.uid,
      });
      dispatch({ type: 'ADD_STUDY', payload: newStudy });
      return newStudy;
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Failed to create study';
      dispatch({ type: 'SET_ERROR', payload: errorMessage });
      throw error;
    }
  };

  // Helper: Update study in Firestore
  const updateStudyInFirestore = async (studyId: string, updates: Partial<Study>): Promise<void> => {
    try {
      dispatch({ type: 'SET_ERROR', payload: null });
      await studyService.update(studyId, updates);
      // The real-time listener will update the state automatically
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Failed to update study';
      dispatch({ type: 'SET_ERROR', payload: errorMessage });
      throw error;
    }
  };

  // Helper: Update workflow status and persist to Firestore
  // Also updates the 'status' field (pending/in_progress/completed) appropriately
  const updateWorkflowStatus = async (studyId: string, workflowStatus: WorkflowStatus): Promise<void> => {
    try {
      dispatch({ type: 'SET_ERROR', payload: null });
      
      // Get current study to determine if this is backward navigation
      const study = state.studies.find(s => s.id === studyId);
      const currentStep = study?.currentStep || study?.workflowStatus || 'uploading_documents';
      const visitedSteps = study?.visitedSteps || [];
      
      // Determine if this is backward navigation
      const STEP_ORDER: WorkflowStatus[] = [
        'uploading_documents',
        'analyzing_rooms',
        'resource_extraction',
        'reviewing_rooms',
        'engineering_takeoff',
        'completed',
      ];
      const currentStepIndex = STEP_ORDER.indexOf(currentStep);
      const targetStepIndex = STEP_ORDER.indexOf(workflowStatus);
      const isBackward = targetStepIndex < currentStepIndex;
      
      // Update local state immediately for responsive UI
      dispatch({
        type: 'UPDATE_WORKFLOW_STATUS',
        payload: { studyId, status: workflowStatus },
      });
      
      // Persist to Firestore using workflow service
      await workflowService.updateStatus(studyId, workflowStatus, {
        isBackward,
        visitedSteps,
      });
      
      // The real-time listener will sync the update back to local state
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Failed to update workflow status';
      dispatch({ type: 'SET_ERROR', payload: errorMessage });
      throw error;
    }
  };

  // Helper: Navigate to a specific step (handles both forward and backward)
  const navigateToStep = async (studyId: string, step: WorkflowStatus): Promise<void> => {
    try {
      dispatch({ type: 'SET_ERROR', payload: null });
      
      const study = state.studies.find(s => s.id === studyId);
      if (!study) {
        throw new Error('Study not found');
      }

      const currentStep = study.currentStep || study.workflowStatus;
      const visitedSteps = study.visitedSteps || [study.workflowStatus];
      
      // Check if navigation is allowed
      if (!workflowService.canNavigateTo(step, currentStep, visitedSteps, study.workflowStatus)) {
        throw new Error(`Cannot navigate to step ${step} from current step ${currentStep}`);
      }

      // Determine if this is backward navigation
      const STEP_ORDER: WorkflowStatus[] = [
        'uploading_documents',
        'analyzing_rooms',
        'resource_extraction',
        'reviewing_rooms',
        'engineering_takeoff',
        'completed',
      ];
      const currentStepIndex = STEP_ORDER.indexOf(currentStep);
      const targetStepIndex = STEP_ORDER.indexOf(step);
      const isBackward = targetStepIndex < currentStepIndex;

      // Update visited steps if this is a new step
      const updatedVisitedSteps = [...visitedSteps];
      if (!updatedVisitedSteps.includes(step)) {
        updatedVisitedSteps.push(step);
      }

      // Update workflowStatus only if moving forward to a new step
      let newWorkflowStatus = study.workflowStatus;
      if (!isBackward && targetStepIndex > STEP_ORDER.indexOf(study.workflowStatus)) {
        newWorkflowStatus = step;
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
        currentStep: step,
        visitedSteps: updatedVisitedSteps,
        status,
      };

      if (status === 'completed') {
        updates.completedAt = new Date();
      }

      await studyService.update(studyId, updates);
      
      // The real-time listener will sync the update back to local state
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Failed to navigate to step';
      dispatch({ type: 'SET_ERROR', payload: errorMessage });
      throw error;
    }
  };

  // Helper: Upload files using storage service with concurrency control
  // Optionally accepts currentStudy to avoid race conditions when study was just created
  const uploadFiles = async (
    files: File[],
    studyId: string,
    currentStudy?: Study,
    options?: UploadFilesOptions
  ): Promise<UploadedFile[]> => {
    if (!currentUser?.uid) {
      throw new Error('User must be authenticated to upload files');
    }

    const { onProgress, concurrency = 3 } = options || {};

    try {
      dispatch({ type: 'SET_ERROR', payload: null });

      // Calculate total bytes for aggregate progress tracking
      const totalBytes = files.reduce((sum, f) => sum + f.size, 0);
      // Map fileId -> bytes transferred so far
      const progressMap = new Map<string, number>();

      const updateOverallProgress = () => {
        if (!onProgress) return;
        const transferred = Array.from(progressMap.values()).reduce((sum, b) => sum + b, 0);
        const percent = totalBytes > 0 ? Math.round((transferred / totalBytes) * 100) : 0;
        onProgress(percent);
      };

      // Build upload tasks (one per file)
      const uploadTasks = files.map((file) => {
        const fileId = `file-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
        const storagePath = `users/${currentUser.uid}/studies/${studyId}/files/${fileId}_${file.name.replace(/[^a-zA-Z0-9.-]/g, '_')}`;

        return async (): Promise<UploadedFile> => {
          progressMap.set(fileId, 0);
          const { downloadURL } = await fileStorageApi.upload(file, storagePath, (percent) => {
            progressMap.set(fileId, (percent / 100) * file.size);
            updateOverallProgress();
          });

          return {
            id: fileId,
            name: file.name,
            type: file.type,
            size: file.size,
            uploadedAt: new Date().toISOString(),
            storagePath,
            downloadURL,
          };
        };
      });

      // Execute tasks with concurrency limit
      const uploadedFiles = await runWithConcurrency(uploadTasks, concurrency);

      // Update study with uploaded files
      // Use provided study if available (to avoid race conditions), otherwise fetch from Firestore
      const study = currentStudy || await studyService.getById(studyId);
      if (study) {
        const updatedFiles = [...(study.uploadedFiles || []), ...uploadedFiles];
        // Use updateStudy directly to preserve assets and other fields
        // Only include assets in update if they exist (to avoid overwriting with empty array)
        const updateData: Partial<Study> = { 
          uploadedFiles: updatedFiles,
        };
        // Only include assets if they exist and we want to preserve them
        if (study.assets && study.assets.length > 0) {
          updateData.assets = study.assets;
        }
        await studyService.update(studyId, updateData);
        logger.debug('Updated study with uploaded files, preserved assets', {
          studyId,
          assetCount: (study.assets || []).length,
        });
      }

      return uploadedFiles;
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Failed to upload files';
      dispatch({ type: 'SET_ERROR', payload: errorMessage });
      throw error;
    }
  };

  // Helper: Delete a file from storage and study
  const deleteFile = async (studyId: string, fileId: string): Promise<void> => {
    if (!currentUser?.uid) {
      throw new Error('User must be authenticated to delete files');
    }

    try {
      dispatch({ type: 'SET_ERROR', payload: null });
      
      // Get the study to find the file
      const study = await studyService.getById(studyId);
      if (!study) {
        throw new Error('Study not found');
      }

      // Find the file to delete
      const fileToDelete = study.uploadedFiles?.find(f => f.id === fileId);
      if (!fileToDelete) {
        throw new Error('File not found');
      }

      // Delete from Firebase Storage if storagePath exists
      if (fileToDelete.storagePath) {
        try {
          await fileStorageApi.delete(fileToDelete.storagePath);
        } catch (error) {
          // Log error but continue with removing from study
          // File might already be deleted or not exist
          logger.warn('Error deleting file from storage (continuing with study update)', { error, storagePath: fileToDelete.storagePath });
        }
      }

      // Remove file from study's uploadedFiles array
      const updatedFiles = (study.uploadedFiles || []).filter(f => f.id !== fileId);
      await studyService.update(studyId, { uploadedFiles: updatedFiles });
      
      logger.debug('File deleted successfully', { studyId, fileId });
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Failed to delete file';
      dispatch({ type: 'SET_ERROR', payload: errorMessage });
      throw error;
    }
  };

  // Helper: Calculate statistics from studies
  const calculateStatistics = (): Statistics => {
    return statisticsService.calculate(state.studies);
  };

  const startRoomClassification = async (studyId: string): Promise<void> => {
    try {
      await roomClassificationApi.startStudyClassification(studyId);
    } catch (error) {
      throw (error instanceof Error
        ? error
        : new Error('Room classification service is unavailable right now.'));
    }
  };

  // Update statistics when studies change
  useEffect(() => {
    if (state.studies.length > 0) {
      const stats = calculateStatistics();
      // Update statistics in state (we'll keep this in state for now)
      // In the future, we could store this in the user document
    }
  }, [state.studies]);

  const value: AppContextType = {
    state,
    dispatch,
    createStudy,
    updateStudyInFirestore,
    updateWorkflowStatus,
    navigateToStep,
    uploadFiles,
    deleteFile,
    calculateStatistics,
    startRoomClassification,
  };

  return (
    <AppContext.Provider value={value}>
      {children}
    </AppContext.Provider>
  );
}

export function useApp(): AppContextType {
  const context = useContext(AppContext);
  if (!context) {
    throw new Error('useApp must be used within an AppProvider');
  }
  return context;
}
