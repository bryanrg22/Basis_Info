'use client';

/**
 * Asset Checklist Navigator
 * 
 * Left panel sidebar showing all assets grouped by discipline with search/filter.
 */

import { useState, useMemo } from 'react';
import { AssetDemo, Discipline, TakeoffStatus } from '@/types/asset-takeoff.types';

interface AssetChecklistNavigatorProps {
  assets: AssetDemo[];
  currentAssetId: string;
  onSelectAsset: (assetId: string) => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
}

const DISCIPLINE_LABELS: Record<Discipline, string> = {
  ARCHITECTURAL: 'Architectural',
  ELECTRICAL: 'Electrical',
  MECHANICAL: 'Mechanical',
  PLUMBING: 'Plumbing',
};

const DISCIPLINE_COLORS: Record<Discipline, string> = {
  ARCHITECTURAL: 'bg-amber-100 text-amber-800',
  ELECTRICAL: 'bg-yellow-100 text-yellow-800',
  MECHANICAL: 'bg-blue-100 text-blue-800',
  PLUMBING: 'bg-green-100 text-green-800',
};

const STATUS_CONFIG: Record<TakeoffStatus, { label: string; color: string; icon: React.ReactNode }> = {
  NOT_STARTED: {
    label: 'Not Started',
    color: 'bg-gray-100 text-gray-600',
    icon: (
      <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <circle cx="12" cy="12" r="10" strokeWidth="2" />
      </svg>
    ),
  },
  IN_PROGRESS: {
    label: 'In Progress',
    color: 'bg-amber-100 text-amber-700',
    icon: (
      <svg className="h-3 w-3 animate-spin" fill="none" viewBox="0 0 24 24">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
      </svg>
    ),
  },
  COMPLETED: {
    label: 'Completed',
    color: 'bg-green-100 text-green-700',
    icon: (
      <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7" />
      </svg>
    ),
  },
};

export function AssetChecklistNavigator({
  assets,
  currentAssetId,
  onSelectAsset,
  collapsed,
  onToggleCollapse,
}: AssetChecklistNavigatorProps) {
  const [searchText, setSearchText] = useState('');
  const [showUncompletedOnly, setShowUncompletedOnly] = useState(false);
  
  // Filter and group assets
  const filteredAssets = useMemo(() => {
    return assets.filter(asset => {
      const matchesSearch = !searchText || 
        asset.name.toLowerCase().includes(searchText.toLowerCase()) ||
        asset.location?.toLowerCase().includes(searchText.toLowerCase());
      const matchesFilter = !showUncompletedOnly || asset.status !== 'COMPLETED';
      return matchesSearch && matchesFilter;
    });
  }, [assets, searchText, showUncompletedOnly]);
  
  const assetsByDiscipline = useMemo(() => {
    return filteredAssets.reduce((acc, asset) => {
      if (!acc[asset.discipline]) {
        acc[asset.discipline] = [];
      }
      acc[asset.discipline].push(asset);
      return acc;
    }, {} as Record<Discipline, AssetDemo[]>);
  }, [filteredAssets]);
  
  // Progress stats
  const completedCount = assets.filter(a => a.status === 'COMPLETED').length;
  const progressPercent = Math.round((completedCount / assets.length) * 100);
  
  if (collapsed) {
    return (
      <div className="hidden md:flex flex-col w-16 bg-white border-r border-gray-200 py-4">
        <button
          onClick={onToggleCollapse}
          className="mx-auto p-2 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg"
          title="Expand sidebar"
        >
          <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 5l7 7-7 7M5 5l7 7-7 7" />
          </svg>
        </button>
        
        {/* Collapsed asset indicators */}
        <div className="mt-4 space-y-1 px-2">
          {assets.map((asset) => (
            <button
              key={asset.id}
              onClick={() => onSelectAsset(asset.id)}
              className={`w-full p-2 rounded-lg transition-colors ${
                asset.id === currentAssetId 
                  ? 'bg-primary-100 text-primary-700' 
                  : 'hover:bg-gray-100'
              }`}
              title={asset.name}
            >
              <div className={`mx-auto w-3 h-3 rounded-full ${
                asset.status === 'COMPLETED' ? 'bg-primary-600' :
                asset.status === 'IN_PROGRESS' ? 'bg-amber-500' : 'bg-gray-300'
              }`} />
            </button>
          ))}
        </div>
      </div>
    );
  }
  
  return (
    <div className="hidden md:flex flex-col w-72 bg-white border-r border-gray-200">
      {/* Header */}
      <div className="p-4 border-b border-gray-100">
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-semibold text-gray-900">Assets</h2>
          <button
            onClick={onToggleCollapse}
            className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded"
            title="Collapse sidebar"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
            </svg>
          </button>
        </div>
        
        {/* Progress */}
        <div className="mb-3">
          <div className="flex items-center justify-between text-xs text-gray-500 mb-1">
            <span>Progress</span>
            <span>{completedCount} of {assets.length} completed</span>
          </div>
          <div className="h-1.5 bg-gray-200 rounded-full overflow-hidden">
            <div 
              className="h-full bg-primary-600 rounded-full transition-all duration-300"
              style={{ width: `${progressPercent}%` }}
            />
          </div>
        </div>
        
        {/* Search */}
        <div className="relative mb-2">
          <svg className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            type="text"
            placeholder="Search assets..."
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            className="w-full pl-9 pr-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
          />
        </div>
        
        {/* Filter */}
        <label className="flex items-center gap-2 text-xs text-gray-600 cursor-pointer">
          <input
            type="checkbox"
            checked={showUncompletedOnly}
            onChange={(e) => setShowUncompletedOnly(e.target.checked)}
            className="rounded border-gray-300 text-primary-600 focus:ring-primary-500"
          />
          <span>Show uncompleted only</span>
        </label>
      </div>
      
      {/* Asset List */}
      <div className="flex-1 overflow-y-auto py-2">
        {Object.entries(assetsByDiscipline).map(([discipline, disciplineAssets]) => (
          <div key={discipline} className="mb-2">
            {/* Discipline Header */}
            <div className="px-4 py-1.5">
              <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${DISCIPLINE_COLORS[discipline as Discipline]}`}>
                {DISCIPLINE_LABELS[discipline as Discipline]}
              </span>
            </div>
            
            {/* Assets */}
            <div className="space-y-0.5">
              {disciplineAssets.map((asset) => (
                <AssetListItem
                  key={asset.id}
                  asset={asset}
                  isActive={asset.id === currentAssetId}
                  onClick={() => onSelectAsset(asset.id)}
                />
              ))}
            </div>
          </div>
        ))}
        
        {filteredAssets.length === 0 && (
          <div className="px-4 py-8 text-center text-sm text-gray-500">
            <p>No assets found</p>
            <p className="text-xs mt-1">Try adjusting your search or filters</p>
          </div>
        )}
      </div>
    </div>
  );
}

interface AssetListItemProps {
  asset: AssetDemo;
  isActive: boolean;
  onClick: () => void;
}

function AssetListItem({ asset, isActive, onClick }: AssetListItemProps) {
  const statusConfig = STATUS_CONFIG[asset.status];
  
  return (
    <button
      onClick={onClick}
      className={`w-full px-4 py-2.5 text-left transition-colors ${
        isActive 
          ? 'bg-primary-50 border-l-2 border-primary-600' 
          : 'hover:bg-gray-50 border-l-2 border-transparent'
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <p className={`text-sm font-medium truncate ${isActive ? 'text-primary-900' : 'text-gray-900'}`}>
            {asset.name}
          </p>
          {asset.location && (
            <p className="text-xs text-gray-500 truncate mt-0.5">{asset.location}</p>
          )}
        </div>
        <div className={`flex-shrink-0 flex items-center gap-1 px-1.5 py-0.5 rounded text-xs ${statusConfig.color}`}>
          {statusConfig.icon}
        </div>
      </div>
    </button>
  );
}

