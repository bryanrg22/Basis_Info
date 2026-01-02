/**
 * Asset Service
 *
 * Provides asset-related operations using real Firestore.
 */

import { assetApi } from '@/api/firestore/asset.api';
import { Asset } from '@/types';
import { ValidationError } from '@/errors/error-types';

/**
 * Asset Service
 */
export const assetService = {
  /**
   * Get all assets for a study
   */
  async getByStudyId(studyId: string): Promise<Asset[]> {
    return assetApi.getByStudyId(studyId);
  },

  /**
   * Update all assets for a study
   */
  async updateAll(studyId: string, assets: Asset[]): Promise<void> {
    this.validateAssets(assets);
    await assetApi.updateAll(studyId, assets);
  },

  /**
   * Update a single asset
   */
  async update(studyId: string, assetId: string, updates: Partial<Asset>): Promise<void> {
    if (updates.estimatedValue !== undefined && updates.estimatedValue < 0) {
      throw new ValidationError('Estimated value must be non-negative', 'estimatedValue');
    }
    if (updates.percentageOfTotal !== undefined && (updates.percentageOfTotal < 0 || updates.percentageOfTotal > 100)) {
      throw new ValidationError('Percentage of total must be between 0 and 100', 'percentageOfTotal');
    }
    
    await assetApi.update(studyId, assetId, updates);
  },

  /**
   * Add an asset to a study
   */
  async add(studyId: string, asset: Asset): Promise<void> {
    this.validateAsset(asset);
    await assetApi.add(studyId, asset);
  },

  /**
   * Delete an asset from a study
   */
  async delete(studyId: string, assetId: string): Promise<void> {
    await assetApi.delete(studyId, assetId);
  },

  /**
   * Calculate total value of assets
   */
  calculateTotalValue(assets: Asset[]): number {
    return assets.reduce((sum, asset) => sum + asset.estimatedValue, 0);
  },

  /**
   * Calculate percentage of total for each asset
   */
  calculatePercentages(assets: Asset[], totalValue: number): Asset[] {
    if (totalValue === 0) {
      return assets.map(asset => ({ ...asset, percentageOfTotal: 0 }));
    }
    
    return assets.map(asset => ({
      ...asset,
      percentageOfTotal: (asset.estimatedValue / totalValue) * 100,
    }));
  },

  /**
   * Validate a single asset
   */
  validateAsset(asset: Asset): void {
    if (!asset.id) {
      throw new ValidationError('Asset ID is required', 'id');
    }
    if (!asset.name || asset.name.trim().length === 0) {
      throw new ValidationError('Asset name is required', 'name');
    }
    if (!asset.description || asset.description.trim().length === 0) {
      throw new ValidationError('Asset description is required', 'description');
    }
    if (!['5-year', '15-year', '27.5-year'].includes(asset.category)) {
      throw new ValidationError('Invalid asset category', 'category');
    }
    if (asset.estimatedValue < 0) {
      throw new ValidationError('Estimated value must be non-negative', 'estimatedValue');
    }
    if (asset.depreciationPeriod <= 0) {
      throw new ValidationError('Depreciation period must be positive', 'depreciationPeriod');
    }
    if (asset.percentageOfTotal < 0 || asset.percentageOfTotal > 100) {
      throw new ValidationError('Percentage of total must be between 0 and 100', 'percentageOfTotal');
    }
  },

  /**
   * Validate multiple assets
   */
  validateAssets(assets: Asset[]): void {
    assets.forEach((asset, index) => {
      try {
        this.validateAsset(asset);
      } catch (error) {
        if (error instanceof ValidationError) {
          throw new ValidationError(
            `Asset at index ${index}: ${error.message}`,
            error.field
          );
        }
        throw error;
      }
    });
  },
};

