/**
 * AssetVerificationCard Component
 * 
 * Displays the current asset being verified with all details.
 */

'use client';

import { Asset } from '@/types';
import { formatCurrency, formatPercentage } from '@/utils/formatting';

interface AssetVerificationCardProps {
  asset: Asset;
  originalValue?: number; // Original AI estimate for reference
  onValueChange: (value: number) => void;
  onSkip?: () => void;
  onConfirm?: () => void;
  onComplete?: () => void;
  allVerified?: boolean;
}

const categoryColors = {
  '5-year': 'bg-blue-100 text-blue-800',
  '15-year': 'bg-green-100 text-green-800',
  '27.5-year': 'bg-yellow-100 text-yellow-800',
};

/**
 * Asset Verification Card component
 */
export default function AssetVerificationCard({
  asset,
  originalValue,
  onValueChange,
  onSkip,
  onConfirm,
  onComplete,
  allVerified = false,
}: AssetVerificationCardProps) {
  const categoryColor = categoryColors[asset.category] || 'bg-gray-100 text-gray-800';

  return (
    <div className="sticky top-6 bg-white rounded-lg shadow-sm border border-gray-200 p-6 flex flex-col h-fit">
      {/* Asset Name and Description */}
      <div className="mb-6">
        <h2 className="text-2xl font-bold text-gray-900 mb-2">{asset.name}</h2>
        <p className="text-gray-600">{asset.description}</p>
      </div>

      {/* Asset Details Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
        {/* Depreciation Category */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Depreciation Category
          </label>
          <span className={`inline-flex px-3 py-1 text-sm font-semibold rounded-full ${categoryColor}`}>
            {asset.depreciationPeriod} years
          </span>
        </div>

        {/* Depreciation Period */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Depreciation Period
          </label>
          <p className="text-sm text-gray-900">{asset.depreciationPeriod} years</p>
        </div>

        {/* Percentage of Total */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Percentage of Total
          </label>
          <p className="text-sm text-gray-900">{formatPercentage(asset.percentageOfTotal, 2)}</p>
        </div>
      </div>

      {/* Estimated Value */}
      <div className="mb-6">
        <label className="block text-sm font-medium text-gray-700 mb-2">
          Estimated Value <span className="text-red-500">*</span>
        </label>
        <div className="relative">
          <div className="absolute left-4 top-1/2 -translate-y-1/2 text-gray-500 font-semibold">
            $
          </div>
          <input
            type="number"
            step="0.01"
            min="0"
            value={asset.estimatedValue || ''}
            onChange={(e) => {
              const value = parseFloat(e.target.value) || 0;
              onValueChange(value);
            }}
            className="w-full pl-8 pr-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 text-lg font-semibold transition-all duration-200 hover:border-gray-400"
            placeholder="0.00"
          />
        </div>
        {originalValue !== undefined && (
          <p className="mt-2 text-sm text-gray-500">
            Original AI estimate: {formatCurrency(originalValue)}
          </p>
        )}
      </div>

      {/* Footer Actions */}
      <div className="mt-auto pt-6 border-t border-gray-200 space-y-3">
        {onSkip && (
          <button
            onClick={onSkip}
            className="w-full px-6 py-3 border-2 border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 hover:border-gray-400 active:bg-gray-100 transition-all duration-200 font-medium text-base"
          >
            Skip
          </button>
        )}
        {allVerified && onComplete ? (
          <button
            onClick={onComplete}
            className="w-full px-6 py-3 bg-primary-600 text-white rounded-lg hover:bg-primary-700 active:bg-primary-800 transition-all duration-200 font-medium text-base shadow-sm hover:shadow-md"
          >
            Complete Verification
          </button>
        ) : onConfirm ? (
          <button
            onClick={onConfirm}
            className="w-full px-6 py-3 bg-primary-600 text-white rounded-lg hover:bg-primary-700 active:bg-primary-800 transition-all duration-200 font-medium text-base shadow-sm hover:shadow-md"
          >
            Confirm
          </button>
        ) : null}
      </div>
    </div>
  );
}

