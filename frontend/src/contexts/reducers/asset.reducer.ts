/**
 * Asset Reducer
 * 
 * Handles asset-related state updates.
 */

import { Study, Asset } from '@/types';

export type AssetAction =
  | { type: 'UPDATE_ASSET'; payload: { studyId: string; assetId: string; updates: Partial<Asset> } }
  | { type: 'ADD_ASSET'; payload: { studyId: string; asset: Asset } }
  | { type: 'DELETE_ASSET'; payload: { studyId: string; assetId: string } };

export function assetReducer(
  state: { studies: Study[]; currentStudy: Study | null },
  action: AssetAction
): { studies: Study[]; currentStudy: Study | null } {
  const updateStudyAssets = (study: Study, assetId: string, updates: Partial<Asset>): Study => ({
    ...study,
    assets: study.assets.map(asset =>
      asset.id === assetId ? { ...asset, ...updates } : asset
    ),
  });

  const addAssetToStudy = (study: Study, asset: Asset): Study => ({
    ...study,
    assets: [...study.assets, asset],
  });

  const deleteAssetFromStudy = (study: Study, assetId: string): Study => ({
    ...study,
    assets: study.assets.filter(asset => asset.id !== assetId),
  });

  switch (action.type) {
    case 'UPDATE_ASSET': {
      return {
        ...state,
        studies: state.studies.map(study =>
          study.id === action.payload.studyId
            ? updateStudyAssets(study, action.payload.assetId, action.payload.updates)
            : study
        ),
        currentStudy: state.currentStudy?.id === action.payload.studyId
          ? updateStudyAssets(state.currentStudy, action.payload.assetId, action.payload.updates)
          : state.currentStudy,
      };
    }
    
    case 'ADD_ASSET': {
      return {
        ...state,
        studies: state.studies.map(study =>
          study.id === action.payload.studyId
            ? addAssetToStudy(study, action.payload.asset)
            : study
        ),
        currentStudy: state.currentStudy?.id === action.payload.studyId
          ? addAssetToStudy(state.currentStudy, action.payload.asset)
          : state.currentStudy,
      };
    }
    
    case 'DELETE_ASSET': {
      return {
        ...state,
        studies: state.studies.map(study =>
          study.id === action.payload.studyId
            ? deleteAssetFromStudy(study, action.payload.assetId)
            : study
        ),
        currentStudy: state.currentStudy?.id === action.payload.studyId
          ? deleteAssetFromStudy(state.currentStudy, action.payload.assetId)
          : state.currentStudy,
      };
    }
    
    default:
      return state;
  }
}

