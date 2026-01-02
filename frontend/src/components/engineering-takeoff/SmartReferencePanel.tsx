'use client';

/**
 * Smart Reference Panel
 * 
 * Right panel showing contextual references, documents, and related assets.
 */

import { useState } from 'react';
import { AssetDemo, AssetTakeoffDemo, DocumentReferenceDemo } from '@/types/asset-takeoff.types';
import { DocumentPreviewModal } from './DocumentPreviewModal';

interface SmartReferencePanelProps {
  asset: AssetDemo;
  takeoff: AssetTakeoffDemo;
  relatedAssets: AssetDemo[];
  isOpen: boolean;
  onClose: () => void;
  onSelectAsset: (assetId: string) => void;
}

const DOC_TYPE_ICONS: Record<DocumentReferenceDemo['type'], React.ReactNode> = {
  PLAN: (
    <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7" />
    </svg>
  ),
  SPEC: (
    <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
    </svg>
  ),
  SCHEDULE: (
    <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 10h18M3 14h18m-9-4v8m-7 0h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
    </svg>
  ),
  COST: (
    <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  ),
};

const DOC_TYPE_COLORS: Record<DocumentReferenceDemo['type'], string> = {
  PLAN: 'bg-blue-100 text-blue-700',
  SPEC: 'bg-purple-100 text-purple-700',
  SCHEDULE: 'bg-green-100 text-green-700',
  COST: 'bg-amber-100 text-amber-700',
};

export function SmartReferencePanel({
  asset,
  takeoff,
  relatedAssets,
  isOpen,
  onClose,
  onSelectAsset,
}: SmartReferencePanelProps) {
  const [expandedSections, setExpandedSections] = useState<Record<string, boolean>>({
    documents: true,
    costs: true,
    irs: true,
    related: false,
  });
  
  // State for document preview modal
  const [selectedDocument, setSelectedDocument] = useState<DocumentReferenceDemo | null>(null);
  
  const toggleSection = (section: string) => {
    setExpandedSections(prev => ({ ...prev, [section]: !prev[section] }));
  };
  
  const handleDocumentClick = (ref: DocumentReferenceDemo) => {
    setSelectedDocument(ref);
  };
  
  const handleCloseDocumentPreview = () => {
    setSelectedDocument(null);
  };
  
  const handleCostRefClick = (label: string) => {
    // Could open a cost detail modal in the future
    console.log(`Cost item clicked: ${label}`);
  };
  
  // Mobile overlay
  if (isOpen) {
    return (
      <>
        <div className="lg:hidden fixed inset-0 z-50">
          <div className="absolute inset-0 bg-black/50" onClick={onClose} />
          <div className="absolute right-0 top-0 bottom-0 w-80 bg-white shadow-xl overflow-y-auto">
            <div className="sticky top-0 bg-white border-b border-gray-200 px-4 py-3 flex items-center justify-between">
              <h3 className="font-semibold text-gray-900">References</h3>
              <button onClick={onClose} className="p-1 text-gray-400 hover:text-gray-600">
                <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <PanelContent
              takeoff={takeoff}
              relatedAssets={relatedAssets}
              expandedSections={expandedSections}
              toggleSection={toggleSection}
              onDocumentClick={handleDocumentClick}
              onCostRefClick={handleCostRefClick}
              onSelectAsset={onSelectAsset}
            />
          </div>
        </div>
        
        {/* Document Preview Modal */}
        <DocumentPreviewModal
          document={selectedDocument}
          onClose={handleCloseDocumentPreview}
        />
      </>
    );
  }
  
  // Desktop sticky panel
  return (
    <>
      <div className="hidden lg:flex flex-col w-80 bg-white border-l border-gray-200">
        <div className="sticky top-0 bg-white border-b border-gray-100 px-4 py-3">
          <h3 className="font-semibold text-gray-900">References</h3>
        </div>
        <div className="flex-1 overflow-y-auto">
          <PanelContent
            takeoff={takeoff}
            relatedAssets={relatedAssets}
            expandedSections={expandedSections}
            toggleSection={toggleSection}
            onDocumentClick={handleDocumentClick}
            onCostRefClick={handleCostRefClick}
            onSelectAsset={onSelectAsset}
          />
        </div>
      </div>
      
      {/* Document Preview Modal */}
      <DocumentPreviewModal
        document={selectedDocument}
        onClose={handleCloseDocumentPreview}
      />
    </>
  );
}

interface PanelContentProps {
  takeoff: AssetTakeoffDemo;
  relatedAssets: AssetDemo[];
  expandedSections: Record<string, boolean>;
  toggleSection: (section: string) => void;
  onDocumentClick: (ref: DocumentReferenceDemo) => void;
  onCostRefClick: (label: string) => void;
  onSelectAsset: (assetId: string) => void;
}

function PanelContent({
  takeoff,
  relatedAssets,
  expandedSections,
  toggleSection,
  onDocumentClick,
  onCostRefClick,
  onSelectAsset,
}: PanelContentProps) {
  return (
    <div className="divide-y divide-gray-100">
      {/* Related Documents */}
      <CollapsibleSection
        title="Related Documents"
        count={takeoff.references.length}
        isExpanded={expandedSections.documents}
        onToggle={() => toggleSection('documents')}
      >
        <div className="space-y-2">
          {takeoff.references.map((ref) => (
            <button
              key={ref.id}
              onClick={() => onDocumentClick(ref)}
              className="w-full text-left p-3 rounded-lg border border-gray-200 hover:border-gray-300 hover:bg-gray-50 transition-colors"
            >
              <div className="flex items-start gap-3">
                {ref.thumbnailUrl ? (
                  <img
                    src={ref.thumbnailUrl}
                    alt=""
                    className="w-12 h-12 rounded object-cover bg-gray-100"
                  />
                ) : (
                  <div className={`w-10 h-10 rounded flex items-center justify-center ${DOC_TYPE_COLORS[ref.type]}`}>
                    {DOC_TYPE_ICONS[ref.type]}
                  </div>
                )}
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-gray-900 truncate">{ref.title}</p>
                  {ref.description && (
                    <p className="text-xs text-gray-500 truncate mt-0.5">{ref.description}</p>
                  )}
                  <span className={`inline-block mt-1 px-1.5 py-0.5 rounded text-xs font-medium ${DOC_TYPE_COLORS[ref.type]}`}>
                    {ref.type}
                  </span>
                </div>
              </div>
            </button>
          ))}
        </div>
      </CollapsibleSection>
      
      {/* Cost References */}
      <CollapsibleSection
        title="Cost References"
        count={takeoff.costs.breakdown.length}
        isExpanded={expandedSections.costs}
        onToggle={() => toggleSection('costs')}
      >
        <div className="space-y-1">
          {takeoff.costs.breakdown.map((item) => (
            <button
              key={item.id}
              onClick={() => onCostRefClick(item.label)}
              className="w-full flex items-center justify-between px-3 py-2 text-sm rounded-lg hover:bg-gray-50 transition-colors"
            >
              <span className="text-gray-700">{item.label}</span>
              <span className="text-gray-900 font-medium">
                ${item.amount.toLocaleString()}
              </span>
            </button>
          ))}
          <div className="flex items-center justify-between px-3 py-2 text-sm font-semibold border-t border-gray-200 mt-2 pt-2">
            <span className="text-gray-900">Total</span>
            <span className="text-gray-900">
              ${takeoff.costs.actualTotal.toLocaleString()}
            </span>
          </div>
        </div>
      </CollapsibleSection>
      
      {/* IRS Rules */}
      <CollapsibleSection
        title="IRS Classification Rules"
        count={takeoff.classification.irsRuleRefs.length}
        isExpanded={expandedSections.irs}
        onToggle={() => toggleSection('irs')}
      >
        <div className="space-y-2">
          {takeoff.classification.irsRuleRefs.map((rule) => (
            <div
              key={rule.id}
              className="p-3 rounded-lg bg-gray-50 border border-gray-200"
            >
              <p className="text-xs font-medium text-primary-700 mb-1">{rule.codeSection}</p>
              <p className="text-sm font-medium text-gray-900">{rule.title}</p>
              <p className="text-xs text-gray-600 mt-1 line-clamp-2">{rule.shortExcerpt}</p>
            </div>
          ))}
        </div>
      </CollapsibleSection>
      
      {/* Related Assets */}
      <CollapsibleSection
        title="Related Assets"
        count={relatedAssets.length}
        isExpanded={expandedSections.related}
        onToggle={() => toggleSection('related')}
      >
        <div className="space-y-1">
          {relatedAssets.map((relAsset) => (
            <button
              key={relAsset.id}
              onClick={() => onSelectAsset(relAsset.id)}
              className="w-full text-left px-3 py-2 rounded-lg hover:bg-gray-50 transition-colors"
            >
              <p className="text-sm font-medium text-gray-900 truncate">{relAsset.name}</p>
              <p className="text-xs text-gray-500">{relAsset.discipline}</p>
            </button>
          ))}
        </div>
      </CollapsibleSection>
    </div>
  );
}

interface CollapsibleSectionProps {
  title: string;
  count: number;
  isExpanded: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}

function CollapsibleSection({ title, count, isExpanded, onToggle, children }: CollapsibleSectionProps) {
  return (
    <div className="py-3 px-4">
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between text-left"
      >
        <div className="flex items-center gap-2">
          <h4 className="text-sm font-semibold text-gray-900">{title}</h4>
          <span className="px-1.5 py-0.5 text-xs font-medium bg-gray-100 text-gray-600 rounded">
            {count}
          </span>
        </div>
        <svg
          className={`h-4 w-4 text-gray-400 transition-transform ${isExpanded ? 'rotate-180' : ''}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {isExpanded && (
        <div className="mt-3">
          {children}
        </div>
      )}
    </div>
  );
}

