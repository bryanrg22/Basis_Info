/**
 * AssetNavigationGrid Component
 * 
 * Quick navigation grid showing all assets with visual states.
 */

'use client';

import { Asset } from '@/types';

interface AssetNavigationGridProps {
  assets: Asset[];
  currentAssetId: string | null;
  onAssetClick: (assetId: string) => void;
}

const categoryColors = {
  '5-year': 'border-blue-500',
  '15-year': 'border-green-500',
  '27.5-year': 'border-yellow-500',
};

/**
 * Asset Navigation Grid component
 */
export default function AssetNavigationGrid({
  assets,
  currentAssetId,
  onAssetClick,
}: AssetNavigationGridProps) {
  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
      <h3 className="text-sm font-semibold text-gray-700 mb-3">Quick Navigation</h3>
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-2">
        {assets.map((asset) => {
          const isCurrent = asset.id === currentAssetId;
          const isVerified = asset.verified;
          const categoryColor = categoryColors[asset.category] || 'border-gray-300';

          return (
            <button
              key={asset.id}
              onClick={() => onAssetClick(asset.id)}
              className={`
                relative p-3 rounded-lg border-2 transition-all
                ${isCurrent 
                  ? `${categoryColor} bg-primary-50 shadow-md` 
                  : isVerified
                  ? 'border-green-300 bg-green-50 hover:bg-green-100'
                  : 'border-gray-200 bg-gray-50 hover:bg-gray-100'
                }
              `}
            >
              {/* Verified Checkmark */}
              {isVerified && (
                <div className="absolute top-1 right-1">
                  <svg className="w-4 h-4 text-green-600" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                  </svg>
                </div>
              )}

              {/* Asset Name */}
              <div className="text-left">
                <p className="text-xs font-medium text-gray-900 truncate">{asset.name}</p>
                <p className="text-xs text-gray-500 mt-1">{asset.depreciationPeriod}yr</p>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

