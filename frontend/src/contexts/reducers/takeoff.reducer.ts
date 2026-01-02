/**
 * Takeoff Reducer
 * 
 * Handles takeoff-related state updates.
 */

import { Study, Takeoff } from '@/types';

export type TakeoffAction =
  | { type: 'UPDATE_TAKEOFFS'; payload: { studyId: string; takeoffs: Takeoff[] } }
  | { type: 'UPDATE_TAKEOFF'; payload: { studyId: string; takeoffId: string; updates: Partial<Takeoff> } }
  | { type: 'ADD_TAKEOFF'; payload: { studyId: string; takeoff: Takeoff } }
  | { type: 'DELETE_TAKEOFF'; payload: { studyId: string; takeoffId: string } };

export function takeoffReducer(
  state: { studies: Study[]; currentStudy: Study | null },
  action: TakeoffAction
): { studies: Study[]; currentStudy: Study | null } {
  const updateStudyTakeoffs = (study: Study, takeoffs: Takeoff[]): Study => ({
    ...study,
    takeoffs,
  });

  const updateStudyTakeoff = (study: Study, takeoffId: string, updates: Partial<Takeoff>): Study => ({
    ...study,
    takeoffs: study.takeoffs?.map(takeoff =>
      takeoff.id === takeoffId ? { ...takeoff, ...updates } : takeoff
    ) || [],
  });

  const addTakeoffToStudy = (study: Study, takeoff: Takeoff): Study => ({
    ...study,
    takeoffs: [...(study.takeoffs || []), takeoff],
  });

  const deleteTakeoffFromStudy = (study: Study, takeoffId: string): Study => ({
    ...study,
    takeoffs: study.takeoffs?.filter(takeoff => takeoff.id !== takeoffId) || [],
  });

  switch (action.type) {
    case 'UPDATE_TAKEOFFS': {
      return {
        ...state,
        studies: state.studies.map(study =>
          study.id === action.payload.studyId
            ? updateStudyTakeoffs(study, action.payload.takeoffs)
            : study
        ),
        currentStudy: state.currentStudy?.id === action.payload.studyId
          ? updateStudyTakeoffs(state.currentStudy, action.payload.takeoffs)
          : state.currentStudy,
      };
    }
    
    case 'UPDATE_TAKEOFF': {
      return {
        ...state,
        studies: state.studies.map(study =>
          study.id === action.payload.studyId
            ? updateStudyTakeoff(study, action.payload.takeoffId, action.payload.updates)
            : study
        ),
        currentStudy: state.currentStudy?.id === action.payload.studyId
          ? updateStudyTakeoff(state.currentStudy, action.payload.takeoffId, action.payload.updates)
          : state.currentStudy,
      };
    }
    
    case 'ADD_TAKEOFF': {
      return {
        ...state,
        studies: state.studies.map(study =>
          study.id === action.payload.studyId
            ? addTakeoffToStudy(study, action.payload.takeoff)
            : study
        ),
        currentStudy: state.currentStudy?.id === action.payload.studyId
          ? addTakeoffToStudy(state.currentStudy, action.payload.takeoff)
          : state.currentStudy,
      };
    }
    
    case 'DELETE_TAKEOFF': {
      return {
        ...state,
        studies: state.studies.map(study =>
          study.id === action.payload.studyId
            ? deleteTakeoffFromStudy(study, action.payload.takeoffId)
            : study
        ),
        currentStudy: state.currentStudy?.id === action.payload.studyId
          ? deleteTakeoffFromStudy(state.currentStudy, action.payload.takeoffId)
          : state.currentStudy,
      };
    }
    
    default:
      return state;
  }
}

