'use client';

/**
 * Documentation Tab
 * 
 * Tab for notes, attachments, and auto-generated summaries.
 */

import { useState } from 'react';
import { AssetDemo, AssetTakeoffDemo, DocumentationInfoDemo, AttachmentDemo } from '@/types/asset-takeoff.types';

interface DocumentationTabProps {
  asset: AssetDemo;
  takeoff: AssetTakeoffDemo;
  onUpdateTakeoff: (updates: Partial<AssetTakeoffDemo>) => void;
}

const ALTERNATE_SUMMARIES = [
  'Based on the asset classification and cost analysis, this HVAC rooftop unit is eligible for accelerated depreciation under MACRS 15-year property. The total installed cost of $45,000 can be fully depreciated, with 100% bonus depreciation available for qualified property.',
  'Analysis complete. The RTU-1 asset qualifies as Section 1245 property eligible for 15-year MACRS depreciation. Key factors supporting this classification include: (1) equipment serves specific building zones, (2) can be removed without significant structural damage, and (3) has a determinable useful life shorter than the building.',
  'Engineering takeoff summary: RTU-1 rooftop unit representing $45,000 in depreciable basis. Classification as 15-year property supported by Rev. Proc. 87-56 Asset Class 57.0. Quantity verified at 1 EA with no field adjustments required.',
];

export function DocumentationTab({ asset, takeoff, onUpdateTakeoff }: DocumentationTabProps) {
  const [notes, setNotes] = useState(takeoff.docs.notes);
  const [isRegenerating, setIsRegenerating] = useState(false);
  const [summaryIndex, setSummaryIndex] = useState(0);
  
  const handleNotesChange = (newNotes: string) => {
    setNotes(newNotes);
  };
  
  const handleNotesBlur = () => {
    const newDocs: DocumentationInfoDemo = {
      ...takeoff.docs,
      notes,
    };
    onUpdateTakeoff({ docs: newDocs });
  };
  
  const handleRegenerateSummary = async () => {
    setIsRegenerating(true);
    // Simulate AI regeneration
    await new Promise(resolve => setTimeout(resolve, 1500));
    
    const nextIndex = (summaryIndex + 1) % ALTERNATE_SUMMARIES.length;
    setSummaryIndex(nextIndex);
    
    const newDocs: DocumentationInfoDemo = {
      ...takeoff.docs,
      autoSummary: ALTERNATE_SUMMARIES[nextIndex],
    };
    onUpdateTakeoff({ docs: newDocs });
    setIsRegenerating(false);
  };
  
  const handleUploadFile = () => {
    // Simulate file upload
    const newAttachment: AttachmentDemo = {
      id: `att-${Date.now()}`,
      fileName: `Document_${Date.now()}.pdf`,
      sizeLabel: '1.2 MB',
    };
    
    const newDocs: DocumentationInfoDemo = {
      ...takeoff.docs,
      attachments: [...takeoff.docs.attachments, newAttachment],
    };
    onUpdateTakeoff({ docs: newDocs });
  };
  
  const handleRemoveAttachment = (attachmentId: string) => {
    const newDocs: DocumentationInfoDemo = {
      ...takeoff.docs,
      attachments: takeoff.docs.attachments.filter(a => a.id !== attachmentId),
    };
    onUpdateTakeoff({ docs: newDocs });
  };
  
  const handleExportPDF = () => {
    alert('Would generate and download asset section PDF');
  };
  
  return (
    <div className="p-6">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left: Notes and Attachments */}
        <div className="lg:col-span-2 space-y-6">
          {/* Notes */}
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-100 flex items-center justify-between">
              <h3 className="text-lg font-semibold text-gray-900">Notes</h3>
              <span className="text-xs text-gray-400">{notes.length} characters</span>
            </div>
            
            <div className="p-4">
              <textarea
                value={notes}
                onChange={(e) => handleNotesChange(e.target.value)}
                onBlur={handleNotesBlur}
                placeholder="Add notes about this asset, special considerations, or observations from field verification..."
                rows={8}
                className="w-full px-4 py-3 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent resize-none text-gray-700"
              />
              <p className="text-xs text-gray-400 mt-2">
                Notes auto-save when you click away
              </p>
            </div>
          </div>
          
          {/* Attachments */}
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-100 flex items-center justify-between">
              <h3 className="text-lg font-semibold text-gray-900">Attachments</h3>
              <button
                onClick={handleUploadFile}
                className="px-3 py-1.5 text-sm font-medium text-primary-600 hover:text-primary-700 hover:bg-primary-50 rounded-lg transition-colors flex items-center gap-1"
              >
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4v16m8-8H4" />
                </svg>
                Upload
              </button>
            </div>
            
            <div className="p-4">
              {takeoff.docs.attachments.length > 0 ? (
                <div className="space-y-2">
                  {takeoff.docs.attachments.map((attachment) => (
                    <div
                      key={attachment.id}
                      className="flex items-center justify-between p-3 bg-gray-50 rounded-lg group"
                    >
                      <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-lg bg-red-100 flex items-center justify-center">
                          <svg className="h-5 w-5 text-red-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
                          </svg>
                        </div>
                        <div>
                          <p className="text-sm font-medium text-gray-900">{attachment.fileName}</p>
                          <p className="text-xs text-gray-500">{attachment.sizeLabel}</p>
                        </div>
                      </div>
                      
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => alert(`Would download ${attachment.fileName}`)}
                          className="p-2 text-gray-400 hover:text-gray-600 transition-colors"
                        >
                          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                          </svg>
                        </button>
                        <button
                          onClick={() => handleRemoveAttachment(attachment.id)}
                          className="p-2 text-gray-400 hover:text-red-600 transition-colors"
                        >
                          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                          </svg>
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center py-8">
                  <div className="w-12 h-12 rounded-full bg-gray-100 flex items-center justify-center mx-auto mb-3">
                    <svg className="h-6 w-6 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" />
                    </svg>
                  </div>
                  <p className="text-sm text-gray-500">No attachments yet</p>
                  <button
                    onClick={handleUploadFile}
                    className="mt-2 text-sm text-primary-600 hover:text-primary-700"
                  >
                    Upload your first file
                  </button>
                </div>
              )}
              
              {/* Drop zone hint */}
              <div className="mt-4 p-4 border-2 border-dashed border-gray-200 rounded-lg text-center">
                <p className="text-sm text-gray-400">
                  Drag and drop files here, or click Upload above
                </p>
              </div>
            </div>
          </div>
        </div>
        
        {/* Right: Auto Summary and Export */}
        <div className="space-y-6">
          {/* Auto Summary */}
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-100 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div className="w-6 h-6 rounded-full bg-primary-100 flex items-center justify-center">
                  <svg className="h-3.5 w-3.5 text-primary-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
                  </svg>
                </div>
                <h3 className="font-semibold text-gray-900">AI Summary</h3>
              </div>
              <button
                onClick={handleRegenerateSummary}
                disabled={isRegenerating}
                className="px-3 py-1.5 text-sm font-medium text-gray-600 hover:text-gray-800 hover:bg-gray-100 rounded-lg transition-colors flex items-center gap-1 disabled:opacity-50"
              >
                <svg className={`h-4 w-4 ${isRegenerating ? 'animate-spin' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
                {isRegenerating ? 'Generating...' : 'Regenerate'}
              </button>
            </div>
            
            <div className="p-4">
              {isRegenerating ? (
                <div className="space-y-2">
                  <div className="h-4 bg-gray-200 rounded animate-pulse" />
                  <div className="h-4 bg-gray-200 rounded animate-pulse w-5/6" />
                  <div className="h-4 bg-gray-200 rounded animate-pulse w-4/6" />
                  <div className="h-4 bg-gray-200 rounded animate-pulse w-5/6" />
                </div>
              ) : (
                <div className="p-4 bg-gray-50 rounded-lg border border-gray-200">
                  <p className="text-sm text-gray-700 leading-relaxed">
                    {takeoff.docs.autoSummary}
                  </p>
                </div>
              )}
              
              <p className="text-xs text-gray-400 mt-3">
                Generated based on asset data, classification, and cost analysis
              </p>
            </div>
          </div>
          
          {/* Export Options */}
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
            <h3 className="font-semibold text-gray-900 mb-4">Export Options</h3>
            
            <div className="space-y-2">
              <button
                onClick={handleExportPDF}
                className="w-full px-4 py-3 text-sm font-medium text-white bg-primary-600 rounded-lg hover:bg-primary-700 transition-colors flex items-center justify-center gap-2"
              >
                <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                Export Asset Section (PDF)
              </button>
              
              <button
                onClick={() => alert('Would export all asset data to Excel')}
                className="w-full px-4 py-3 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors flex items-center justify-center gap-2"
              >
                <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 10h18M3 14h18m-9-4v8m-7 0h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
                </svg>
                Export to Excel
              </button>
            </div>
          </div>
          
          {/* Asset Info Summary */}
          <div className="bg-gray-50 rounded-xl border border-gray-200 p-6">
            <h4 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">Asset Summary</h4>
            
            <dl className="space-y-2 text-sm">
              <div className="flex justify-between">
                <dt className="text-gray-500">Name</dt>
                <dd className="font-medium text-gray-900 text-right">{asset.name}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-gray-500">Discipline</dt>
                <dd className="font-medium text-gray-900">{asset.discipline}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-gray-500">Location</dt>
                <dd className="font-medium text-gray-900 text-right">{asset.location}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-gray-500">Spec Section</dt>
                <dd className="font-medium text-gray-900">{asset.specSection}</dd>
              </div>
              <div className="border-t border-gray-200 pt-2 mt-2">
                <div className="flex justify-between">
                  <dt className="text-gray-500">Total Cost</dt>
                  <dd className="font-bold text-gray-900">${takeoff.costs.actualTotal.toLocaleString()}</dd>
                </div>
              </div>
            </dl>
          </div>
        </div>
      </div>
    </div>
  );
}

