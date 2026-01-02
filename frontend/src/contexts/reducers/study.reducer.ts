/**
 * Study Reducer
 * 
 * Handles study-related state updates.
 */

import { Study } from '@/types';

export type StudyAction =
  | { type: 'SET_STUDIES'; payload: Study[] }
  | { type: 'ADD_STUDY'; payload: Study }
  | { type: 'UPDATE_STUDY'; payload: Study }
  | { type: 'SET_CURRENT_STUDY'; payload: Study | null };

export function studyReducer(
  state: { studies: Study[]; currentStudy: Study | null },
  action: StudyAction
): { studies: Study[]; currentStudy: Study | null } {
  switch (action.type) {
    case 'SET_STUDIES':
      return { ...state, studies: action.payload };
    
    case 'ADD_STUDY':
      return { ...state, studies: [...state.studies, action.payload] };
    
    case 'UPDATE_STUDY':
      return {
        ...state,
        studies: state.studies.map(study =>
          study.id === action.payload.id ? action.payload : study
        ),
        currentStudy: state.currentStudy?.id === action.payload.id
          ? action.payload
          : state.currentStudy,
      };
    
    case 'SET_CURRENT_STUDY':
      return { ...state, currentStudy: action.payload };
    
    default:
      return state;
  }
}

