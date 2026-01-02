/**
 * Root Reducer
 * 
 * Combines all domain-specific reducers.
 */

import { Study, UploadedFile, WorkflowStatus } from '@/types';
import { studyReducer, StudyAction } from './study.reducer';
import { assetReducer, AssetAction } from './asset.reducer';
import { roomReducer, RoomAction } from './room.reducer';
import { takeoffReducer, TakeoffAction } from './takeoff.reducer';

export interface AppState {
  user: {
    name: string;
    email: string;
    company: string;
    photoURL: string | null;
    uid?: string;
  };
  statistics: {
    studiesCompleted: number;
    revenueGenerated: number;
    taxSavingsProvided: number;
  };
  studies: Study[];
  currentStudy: Study | null;
  sidebarOpen: boolean;
  loading: boolean;
  error: string | null;
}

export type AppAction =
  | { type: 'SET_SIDEBAR_OPEN'; payload: boolean }
  | { type: 'SET_USER'; payload: { name: string; email: string; company: string; photoURL: string | null; uid?: string } }
  | { type: 'SET_LOADING'; payload: boolean }
  | { type: 'SET_ERROR'; payload: string | null }
  | { type: 'SET_UPLOADED_FILES'; payload: UploadedFile[] }
  | { type: 'UPDATE_WORKFLOW_STATUS'; payload: { studyId: string; status: WorkflowStatus } }
  | StudyAction
  | AssetAction
  | RoomAction
  | TakeoffAction;

/**
 * Root reducer that combines all domain reducers
 */
export function rootReducer(state: AppState, action: AppAction): AppState {
  // Handle app-level actions
  switch (action.type) {
    case 'SET_SIDEBAR_OPEN':
      return { ...state, sidebarOpen: action.payload };
    
    case 'SET_USER': {
      const { payload: newUser } = action;
      // Only update if user data has actually changed
      if (
        state.user.uid === newUser.uid &&
        state.user.name === newUser.name &&
        state.user.email === newUser.email &&
        state.user.company === newUser.company &&
        state.user.photoURL === newUser.photoURL
      ) {
        return state;
      }
      
      // Persist photoURL to localStorage
      if (typeof window !== 'undefined') {
        try {
          if (newUser.photoURL && newUser.uid) {
            localStorage.setItem('user_photoURL', newUser.photoURL);
            localStorage.setItem('user_photoURL_uid', newUser.uid);
          } else if (!newUser.photoURL) {
            localStorage.removeItem('user_photoURL');
            localStorage.removeItem('user_photoURL_uid');
          }
        } catch (e) {
          // Ignore localStorage errors
        }
      }
      
      return { ...state, user: newUser };
    }
    
    case 'SET_LOADING':
      return { ...state, loading: action.payload };
    
    case 'SET_ERROR':
      return { ...state, error: action.payload };
    
    case 'SET_UPLOADED_FILES': {
      if (!state.currentStudy) return state;
      const updatedStudy = { ...state.currentStudy, uploadedFiles: action.payload };
      return {
        ...state,
        currentStudy: updatedStudy,
        studies: state.studies.map(study =>
          study.id === updatedStudy.id ? updatedStudy : study
        ),
      };
    }
    
    case 'UPDATE_WORKFLOW_STATUS': {
      const updateStudyStatus = (study: Study): Study => ({
        ...study,
        workflowStatus: action.payload.status,
      });
      return {
        ...state,
        studies: state.studies.map(study =>
          study.id === action.payload.studyId ? updateStudyStatus(study) : study
        ),
        currentStudy: state.currentStudy?.id === action.payload.studyId
          ? updateStudyStatus(state.currentStudy)
          : state.currentStudy,
      };
    }
  }

  // Delegate to domain-specific reducers
  const studyState = { studies: state.studies, currentStudy: state.currentStudy };
  
  // Try study reducer
  if (
    action.type === 'SET_STUDIES' ||
    action.type === 'ADD_STUDY' ||
    action.type === 'UPDATE_STUDY' ||
    action.type === 'SET_CURRENT_STUDY'
  ) {
    const updated = studyReducer(studyState, action as StudyAction);
    return { ...state, studies: updated.studies, currentStudy: updated.currentStudy };
  }
  
  // Try asset reducer
  if (
    action.type === 'UPDATE_ASSET' ||
    action.type === 'ADD_ASSET' ||
    action.type === 'DELETE_ASSET'
  ) {
    const updated = assetReducer(studyState, action as AssetAction);
    return { ...state, studies: updated.studies, currentStudy: updated.currentStudy };
  }
  
  // Try room reducer
  if (
    action.type === 'UPDATE_ROOMS' ||
    action.type === 'UPDATE_ROOM' ||
    action.type === 'ADD_ROOM' ||
    action.type === 'DELETE_ROOM'
  ) {
    const updated = roomReducer(studyState, action as RoomAction);
    return { ...state, studies: updated.studies, currentStudy: updated.currentStudy };
  }
  
  // Try takeoff reducer
  if (
    action.type === 'UPDATE_TAKEOFFS' ||
    action.type === 'UPDATE_TAKEOFF' ||
    action.type === 'ADD_TAKEOFF' ||
    action.type === 'DELETE_TAKEOFF'
  ) {
    const updated = takeoffReducer(studyState, action as TakeoffAction);
    return { ...state, studies: updated.studies, currentStudy: updated.currentStudy };
  }

  return state;
}

