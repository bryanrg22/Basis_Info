/**
 * ProgressIndicator Component
 * 
 * Displays verification progress with progress bar and chip-based asset selector.
 */

'use client';

import { Asset } from '@/types';

interface ProgressIndicatorProps {
  assets: Asset[];
  currentAssetId: string | null;
  verifiedCount: number;
  totalCount: number;
  progressPercentage: number;
  onAssetClick: (assetId: string) => void;
}

/**
 * Progress Indicator component with chip-based asset selector
 */
export default function ProgressIndicator({
  assets,
  currentAssetId,
  verifiedCount,
  totalCount,
  progressPercentage,
  onAssetClick,
}: ProgressIndicatorProps) {
  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
      {/* Header */}
      <h3 className="text-lg font-semibold text-gray-900 mb-4">Verification Progress</h3>
      
      {/* Counts and Progress Bar */}
      <div className="mb-4">
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm font-medium text-gray-700">
            {verifiedCount} of {totalCount} assets verified
          </span>
          <span className="text-sm font-semibold text-gray-900">
            {progressPercentage}%
          </span>
        </div>
        <div className="w-full bg-gray-200 rounded-full h-3">
          <div
            className="bg-primary-600 h-3 rounded-full transition-all duration-300"
            style={{ width: `${progressPercentage}%` }}
          />
        </div>
        <p className="text-xs text-gray-500 mt-1 text-right">
          {progressPercentage}% complete
        </p>
      </div>

      {/* Chip-based Asset Selector */}
      <div className="flex flex-wrap gap-2">
        {assets.map((asset) => {
          const isCurrent = asset.id === currentAssetId;
          const isVerified = asset.verified;

          return (
            <button
              key={asset.id}
              onClick={() => onAssetClick(asset.id)}
              className={`
                inline-flex items-center gap-2 px-4 py-2 rounded-full text-sm font-medium
                transition-all duration-200 hover:scale-105 active:scale-95
                ${
                  isCurrent
                    ? 'border-2 border-primary-600 bg-primary-50 text-primary-700 shadow-sm ring-2 ring-primary-200'
                    : isVerified
                    ? 'border-2 border-green-300 bg-green-100 text-green-800 hover:bg-green-200 hover:border-green-400'
                    : 'border-2 border-gray-200 bg-gray-50 text-gray-700 hover:bg-gray-100 hover:border-gray-300'
                }
              `}
            >
              {isVerified && (
                <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                  <path
                    fillRule="evenodd"
                    d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z"
                    clipRule="evenodd"
                  />
                </svg>
              )}
              <span className="truncate max-w-[120px]">{asset.name}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

