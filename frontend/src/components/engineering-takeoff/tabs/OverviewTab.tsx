'use client';

/**
 * Overview Tab
 * 
 * Summary view showing asset details and takeoff status at a glance.
 */

import { AssetDemo, AssetTakeoffDemo, TabId, TakeoffStatus } from '@/types/asset-takeoff.types';

interface OverviewTabProps {
  asset: AssetDemo;
  takeoff: AssetTakeoffDemo;
  onUpdateStatus: (status: TakeoffStatus) => void;
  onTabChange: (tab: TabId) => void;
}

const DISCIPLINE_BADGES: Record<string, string> = {
  ARCHITECTURAL: 'bg-amber-100 text-amber-800 border-amber-200',
  ELECTRICAL: 'bg-yellow-100 text-yellow-800 border-yellow-200',
  MECHANICAL: 'bg-blue-100 text-blue-800 border-blue-200',
  PLUMBING: 'bg-green-100 text-green-800 border-green-200',
};

const STATUS_CONFIG: Record<TakeoffStatus, { label: string; buttonClass: string }> = {
  NOT_STARTED: {
    label: 'Not Started',
    buttonClass: 'bg-gray-100 text-gray-700 hover:bg-gray-200',
  },
  IN_PROGRESS: {
    label: 'In Progress',
    buttonClass: 'bg-amber-100 text-amber-700 hover:bg-amber-200',
  },
  COMPLETED: {
    label: 'Completed',
    buttonClass: 'bg-primary-100 text-primary-700 hover:bg-primary-200',
  },
};

export function OverviewTab({ asset, takeoff, onUpdateStatus, onTabChange }: OverviewTabProps) {
  // Calculate completion stats
  const hasQuantity = takeoff.quantity.manualQuantity !== null || takeoff.quantity.autoDetectedQuantity !== undefined;
  const hasClassification = !!takeoff.classification.appliedCode;
  const hasCosts = takeoff.costs.actualTotal > 0;
  const hasDocs = takeoff.docs.notes.length > 0 || takeoff.docs.attachments.length > 0;
  
  const completedSteps = [hasQuantity, hasClassification, hasCosts, hasDocs].filter(Boolean).length;
  const progressPercent = Math.round((completedSteps / 4) * 100);
  
  return (
    <div className="p-6 space-y-6 max-w-4xl">
      {/* Asset Header Card */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
        <div className="p-6">
          <div className="flex items-start justify-between gap-4">
            <div className="flex-1">
              <div className="flex items-center gap-3 mb-2">
                <span className={`px-2.5 py-1 rounded-full text-xs font-medium border ${DISCIPLINE_BADGES[asset.discipline]}`}>
                  {asset.discipline}
                </span>
                <span className="text-sm text-gray-500">{asset.specSection}</span>
              </div>
              <h2 className="text-xl font-semibold text-gray-900 mb-2">{asset.name}</h2>
              <p className="text-gray-600">{asset.description}</p>
              
              <div className="mt-4 flex flex-wrap gap-4 text-sm">
                <div>
                  <span className="text-gray-500">Location:</span>{' '}
                  <span className="font-medium text-gray-900">{asset.location}</span>
                </div>
                <div>
                  <span className="text-gray-500">Property:</span>{' '}
                  <span className="font-medium text-gray-900">{asset.propertyName}</span>
                </div>
              </div>
            </div>
            
            {/* Status Toggle */}
            <div className="flex flex-col items-end gap-2">
              <div className="flex items-center gap-2">
                {asset.status !== 'COMPLETED' ? (
                  <button
                    onClick={() => onUpdateStatus('COMPLETED')}
                    className="px-4 py-2 bg-primary-600 text-white text-sm font-medium rounded-lg hover:bg-primary-700 transition-colors flex items-center gap-2"
                  >
                    <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7" />
                    </svg>
                    Mark Complete
                  </button>
                ) : (
                  <button
                    onClick={() => onUpdateStatus('IN_PROGRESS')}
                    className="px-4 py-2 bg-amber-100 text-amber-700 text-sm font-medium rounded-lg hover:bg-amber-200 transition-colors"
                  >
                    Reopen
                  </button>
                )}
              </div>
              <span className={`px-3 py-1 rounded-full text-xs font-medium ${STATUS_CONFIG[asset.status].buttonClass}`}>
                {STATUS_CONFIG[asset.status].label}
              </span>
            </div>
          </div>
        </div>
        
        {/* Progress Bar */}
        <div className="px-6 py-4 bg-gray-50 border-t border-gray-100">
          <div className="flex items-center justify-between text-sm mb-2">
            <span className="font-medium text-gray-700">Takeoff Progress</span>
            <span className="text-gray-500">{completedSteps} of 4 sections completed</span>
          </div>
          <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
            <div
              className="h-full bg-primary-600 rounded-full transition-all duration-300"
              style={{ width: `${progressPercent}%` }}
            />
          </div>
        </div>
      </div>
      
      {/* Summary Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {/* Quantity Card */}
        <SummaryCard
          title="Quantity"
          icon={
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M7 20l4-16m2 16l4-16M6 9h14M4 15h14" />
            </svg>
          }
          isComplete={hasQuantity}
          onClick={() => onTabChange('quantity')}
        >
          <div className="mt-2">
            <p className="text-2xl font-bold text-gray-900">
              {takeoff.quantity.manualQuantity ?? takeoff.quantity.autoDetectedQuantity ?? '—'}
              <span className="text-base font-normal text-gray-500 ml-1">
                {takeoff.quantity.manualUnit ?? takeoff.quantity.autoDetectedUnit ?? ''}
              </span>
            </p>
            {takeoff.quantity.manualQuantity !== null && (
              <p className="text-xs text-amber-600 mt-1">Manual override applied</p>
            )}
          </div>
        </SummaryCard>
        
        {/* Classification Card */}
        <SummaryCard
          title="Classification"
          icon={
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
            </svg>
          }
          isComplete={hasClassification}
          onClick={() => onTabChange('classification')}
        >
          <div className="mt-2">
            {takeoff.classification.appliedCode ? (
              <>
                <p className="text-lg font-bold text-gray-900">{takeoff.classification.appliedCode}</p>
                <p className="text-sm text-gray-600 line-clamp-1">{takeoff.classification.appliedDescription}</p>
              </>
            ) : (
              <>
                <p className="text-sm text-gray-500">Suggested: {takeoff.classification.suggestedCode}</p>
                <div className="flex items-center gap-2 mt-1">
                  <div className="flex-1 h-1.5 bg-gray-200 rounded-full">
                    <div
                      className="h-full bg-primary-500 rounded-full"
                      style={{ width: `${takeoff.classification.confidence * 100}%` }}
                    />
                  </div>
                  <span className="text-xs text-gray-500">{Math.round(takeoff.classification.confidence * 100)}%</span>
                </div>
              </>
            )}
          </div>
        </SummaryCard>
        
        {/* Costs Card */}
        <SummaryCard
          title="Costs"
          icon={
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          }
          isComplete={hasCosts}
          onClick={() => onTabChange('costs')}
        >
          <div className="mt-2">
            <p className="text-2xl font-bold text-gray-900">
              ${takeoff.costs.actualTotal.toLocaleString()}
            </p>
            <p className="text-xs text-gray-500 mt-1">
              Est: ${takeoff.costs.estimatedTotal.toLocaleString()} ({takeoff.costs.currency})
            </p>
          </div>
        </SummaryCard>
      </div>
      
      {/* Checklist */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
        <h3 className="text-lg font-semibold text-gray-900 mb-4">Takeoff Checklist</h3>
        <div className="space-y-3">
          <ChecklistItem
            label="Verify quantity from drawings and field photos"
            isComplete={hasQuantity}
            onClick={() => onTabChange('quantity')}
          />
          <ChecklistItem
            label="Complete IRS classification wizard"
            isComplete={hasClassification}
            onClick={() => onTabChange('classification')}
          />
          <ChecklistItem
            label="Review and confirm cost breakdown"
            isComplete={hasCosts}
            onClick={() => onTabChange('costs')}
          />
          <ChecklistItem
            label="Add notes and attach supporting documents"
            isComplete={hasDocs}
            onClick={() => onTabChange('documentation')}
          />
        </div>
      </div>
      
      {/* Quick Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <QuickStat label="Drawing Snippets" value={takeoff.quantity.drawingSnippets.length} />
        <QuickStat label="Field Photos" value={takeoff.quantity.photos.length} />
        <QuickStat label="IRS Rules Referenced" value={takeoff.classification.irsRuleRefs.length} />
        <QuickStat label="Attachments" value={takeoff.docs.attachments.length} />
      </div>
    </div>
  );
}

interface SummaryCardProps {
  title: string;
  icon: React.ReactNode;
  isComplete: boolean;
  onClick: () => void;
  children: React.ReactNode;
}

function SummaryCard({ title, icon, isComplete, onClick, children }: SummaryCardProps) {
  return (
    <button
      onClick={onClick}
      className="bg-white rounded-xl border border-gray-200 shadow-sm p-4 text-left hover:border-primary-300 hover:shadow-md transition-all group"
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className={`p-1.5 rounded-lg ${isComplete ? 'bg-primary-100 text-primary-600' : 'bg-gray-100 text-gray-500'}`}>
            {icon}
          </div>
          <h4 className="font-medium text-gray-900">{title}</h4>
        </div>
        {isComplete ? (
          <svg className="h-5 w-5 text-primary-600" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
          </svg>
        ) : (
          <svg className="h-4 w-4 text-gray-400 opacity-0 group-hover:opacity-100 transition-opacity" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 5l7 7-7 7" />
          </svg>
        )}
      </div>
      {children}
    </button>
  );
}

interface ChecklistItemProps {
  label: string;
  isComplete: boolean;
  onClick: () => void;
}

function ChecklistItem({ label, isComplete, onClick }: ChecklistItemProps) {
  return (
    <button
      onClick={onClick}
      className="w-full flex items-center gap-3 p-3 rounded-lg hover:bg-gray-50 transition-colors text-left"
    >
      <div className={`w-5 h-5 rounded-full border-2 flex items-center justify-center flex-shrink-0 ${
        isComplete 
          ? 'bg-primary-600 border-primary-600' 
          : 'border-gray-300'
      }`}>
        {isComplete && (
          <svg className="h-3 w-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="3" d="M5 13l4 4L19 7" />
          </svg>
        )}
      </div>
      <span className={`flex-1 text-sm ${isComplete ? 'text-gray-500 line-through' : 'text-gray-700'}`}>
        {label}
      </span>
      <svg className="h-4 w-4 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 5l7 7-7 7" />
      </svg>
    </button>
  );
}

interface QuickStatProps {
  label: string;
  value: number;
}

function QuickStat({ label, value }: QuickStatProps) {
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4 text-center">
      <p className="text-2xl font-bold text-gray-900">{value}</p>
      <p className="text-xs text-gray-500 mt-1">{label}</p>
    </div>
  );
}

