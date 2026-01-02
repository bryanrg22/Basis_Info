'use client';

/**
 * Takeoff Workflow Panel
 * 
 * Center panel containing tabs for the takeoff workflow.
 */

import { AssetDemo, AssetTakeoffDemo, TabId } from '@/types/asset-takeoff.types';
import { OverviewTab } from './tabs/OverviewTab';
import { QuantityTakeoffTab } from './tabs/QuantityTakeoffTab';
import { ClassificationTab } from './tabs/ClassificationTab';
import { CostsTab } from './tabs/CostsTab';
import { DocumentationTab } from './tabs/DocumentationTab';

interface TakeoffWorkflowPanelProps {
  asset: AssetDemo;
  takeoff: AssetTakeoffDemo;
  currentTab: TabId;
  onTabChange: (tab: TabId) => void;
  onUpdateTakeoff: (updates: Partial<AssetTakeoffDemo>) => void;
  onUpdateStatus: (status: AssetDemo['status']) => void;
}

const TABS: { id: TabId; label: string; icon: React.ReactNode }[] = [
  {
    id: 'overview',
    label: 'Overview',
    icon: (
      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
      </svg>
    ),
  },
  {
    id: 'quantity',
    label: 'Quantity',
    icon: (
      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M7 20l4-16m2 16l4-16M6 9h14M4 15h14" />
      </svg>
    ),
  },
  {
    id: 'classification',
    label: 'Classification',
    icon: (
      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
      </svg>
    ),
  },
  {
    id: 'costs',
    label: 'Costs',
    icon: (
      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
  },
  {
    id: 'documentation',
    label: 'Documentation',
    icon: (
      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
      </svg>
    ),
  },
];

export function TakeoffWorkflowPanel({
  asset,
  takeoff,
  currentTab,
  onTabChange,
  onUpdateTakeoff,
  onUpdateStatus,
}: TakeoffWorkflowPanelProps) {
  return (
    <div className="flex-1 flex flex-col min-w-0 bg-gray-50">
      {/* Tab Navigation */}
      <div className="bg-white border-b border-gray-200 px-4">
        <nav className="flex space-x-1" aria-label="Tabs">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => onTabChange(tab.id)}
              className={`
                flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors
                ${currentTab === tab.id
                  ? 'border-primary-600 text-primary-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                }
              `}
              aria-current={currentTab === tab.id ? 'page' : undefined}
            >
              {tab.icon}
              <span>{tab.label}</span>
            </button>
          ))}
        </nav>
      </div>
      
      {/* Tab Content */}
      <div className="flex-1 overflow-y-auto">
        {currentTab === 'overview' && (
          <OverviewTab
            asset={asset}
            takeoff={takeoff}
            onUpdateStatus={onUpdateStatus}
            onTabChange={onTabChange}
          />
        )}
        {currentTab === 'quantity' && (
          <QuantityTakeoffTab
            asset={asset}
            takeoff={takeoff}
            onUpdateTakeoff={onUpdateTakeoff}
          />
        )}
        {currentTab === 'classification' && (
          <ClassificationTab
            asset={asset}
            takeoff={takeoff}
            onUpdateTakeoff={onUpdateTakeoff}
          />
        )}
        {currentTab === 'costs' && (
          <CostsTab
            asset={asset}
            takeoff={takeoff}
            onUpdateTakeoff={onUpdateTakeoff}
          />
        )}
        {currentTab === 'documentation' && (
          <DocumentationTab
            asset={asset}
            takeoff={takeoff}
            onUpdateTakeoff={onUpdateTakeoff}
          />
        )}
      </div>
    </div>
  );
}

