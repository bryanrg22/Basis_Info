/**
 * Room Reducer
 * 
 * Handles room-related state updates.
 */

import { Study, Room } from '@/types';

export type RoomAction =
  | { type: 'UPDATE_ROOMS'; payload: { studyId: string; rooms: Room[] } }
  | { type: 'UPDATE_ROOM'; payload: { studyId: string; roomId: string; updates: Partial<Room> } }
  | { type: 'ADD_ROOM'; payload: { studyId: string; room: Room } }
  | { type: 'DELETE_ROOM'; payload: { studyId: string; roomId: string } };

export function roomReducer(
  state: { studies: Study[]; currentStudy: Study | null },
  action: RoomAction
): { studies: Study[]; currentStudy: Study | null } {
  const updateStudyRooms = (study: Study, rooms: Room[]): Study => ({
    ...study,
    rooms,
  });

  const updateStudyRoom = (study: Study, roomId: string, updates: Partial<Room>): Study => ({
    ...study,
    rooms: study.rooms?.map(room =>
      room.id === roomId ? { ...room, ...updates } : room
    ) || [],
  });

  const addRoomToStudy = (study: Study, room: Room): Study => ({
    ...study,
    rooms: [...(study.rooms || []), room],
  });

  const deleteRoomFromStudy = (study: Study, roomId: string): Study => ({
    ...study,
    rooms: study.rooms?.filter(room => room.id !== roomId) || [],
  });

  switch (action.type) {
    case 'UPDATE_ROOMS': {
      return {
        ...state,
        studies: state.studies.map(study =>
          study.id === action.payload.studyId
            ? updateStudyRooms(study, action.payload.rooms)
            : study
        ),
        currentStudy: state.currentStudy?.id === action.payload.studyId
          ? updateStudyRooms(state.currentStudy, action.payload.rooms)
          : state.currentStudy,
      };
    }
    
    case 'UPDATE_ROOM': {
      return {
        ...state,
        studies: state.studies.map(study =>
          study.id === action.payload.studyId
            ? updateStudyRoom(study, action.payload.roomId, action.payload.updates)
            : study
        ),
        currentStudy: state.currentStudy?.id === action.payload.studyId
          ? updateStudyRoom(state.currentStudy, action.payload.roomId, action.payload.updates)
          : state.currentStudy,
      };
    }
    
    case 'ADD_ROOM': {
      return {
        ...state,
        studies: state.studies.map(study =>
          study.id === action.payload.studyId
            ? addRoomToStudy(study, action.payload.room)
            : study
        ),
        currentStudy: state.currentStudy?.id === action.payload.studyId
          ? addRoomToStudy(state.currentStudy, action.payload.room)
          : state.currentStudy,
      };
    }
    
    case 'DELETE_ROOM': {
      return {
        ...state,
        studies: state.studies.map(study =>
          study.id === action.payload.studyId
            ? deleteRoomFromStudy(study, action.payload.roomId)
            : study
        ),
        currentStudy: state.currentStudy?.id === action.payload.studyId
          ? deleteRoomFromStudy(state.currentStudy, action.payload.roomId)
          : state.currentStudy,
      };
    }
    
    default:
      return state;
  }
}

