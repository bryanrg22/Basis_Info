/**
 * Asset Firestore API
 *
 * Manages assets within studies using real Firestore.
 */

import { studyApi } from './study.api';
import { Asset } from '@/types';
import { NotFoundError } from '@/errors/error-types';

/**
 * Asset API
 */
export const assetApi = {
  /**
   * Get all assets for a study
   */
  async getByStudyId(studyId: string): Promise<Asset[]> {
    const study = await studyApi.getById(studyId);
    if (!study) {
      throw new NotFoundError(`Study ${studyId} not found`);
    }
    return study.assets || [];
  },

  /**
   * Update all assets for a study
   */
  async updateAll(studyId: string, assets: Asset[]): Promise<void> {
    await studyApi.update(studyId, { assets });
  },

  /**
   * Update a single asset
   */
  async update(studyId: string, assetId: string, updates: Partial<Asset>): Promise<void> {
    const study = await studyApi.getById(studyId);
    if (!study) {
      throw new NotFoundError(`Study ${studyId} not found`);
    }

    const currentAssets = study.assets || [];
    const updatedAssets = currentAssets.map(asset =>
      asset.id === assetId ? { ...asset, ...updates } : asset
    );

    await this.updateAll(studyId, updatedAssets);
  },

  /**
   * Add an asset to a study
   */
  async add(studyId: string, asset: Asset): Promise<void> {
    const study = await studyApi.getById(studyId);
    if (!study) {
      throw new NotFoundError(`Study ${studyId} not found`);
    }

    const updatedAssets = [...(study.assets || []), asset];
    await this.updateAll(studyId, updatedAssets);
  },

  /**
   * Delete an asset from a study
   */
  async delete(studyId: string, assetId: string): Promise<void> {
    const study = await studyApi.getById(studyId);
    if (!study) {
      throw new NotFoundError(`Study ${studyId} not found`);
    }

    const currentAssets = study.assets || [];
    const updatedAssets = currentAssets.filter(asset => asset.id !== assetId);
    await this.updateAll(studyId, updatedAssets);
  },
};

