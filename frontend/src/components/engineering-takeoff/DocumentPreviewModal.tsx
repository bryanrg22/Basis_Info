'use client';

/**
 * Document Preview Modal
 * 
 * A polished, production-ready modal for viewing document content.
 * Features smooth animations, keyboard support, and clean typography.
 */

import { useEffect, useCallback } from 'react';
import { DocumentReferenceDemo } from '@/types/asset-takeoff.types';

interface DocumentPreviewModalProps {
  document: DocumentReferenceDemo | null;
  onClose: () => void;
}

const DOC_TYPE_COLORS: Record<DocumentReferenceDemo['type'], { bg: string; text: string; border: string }> = {
  PLAN: { bg: 'bg-blue-50', text: 'text-blue-700', border: 'border-blue-200' },
  SPEC: { bg: 'bg-purple-50', text: 'text-purple-700', border: 'border-purple-200' },
  SCHEDULE: { bg: 'bg-green-50', text: 'text-green-700', border: 'border-green-200' },
  COST: { bg: 'bg-amber-50', text: 'text-amber-700', border: 'border-amber-200' },
};

const DOC_TYPE_LABELS: Record<DocumentReferenceDemo['type'], string> = {
  PLAN: 'Drawing',
  SPEC: 'Specification',
  SCHEDULE: 'Schedule',
  COST: 'Cost Document',
};

export function DocumentPreviewModal({ document: doc, onClose }: DocumentPreviewModalProps) {
  // Handle escape key
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      onClose();
    }
  }, [onClose]);

  useEffect(() => {
    if (doc) {
      // Prevent body scroll when modal is open
      document.body.classList.add('overflow-hidden');
      window.addEventListener('keydown', handleKeyDown);
    }
    
    return () => {
      document.body.classList.remove('overflow-hidden');
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [doc, handleKeyDown]);

  if (!doc) return null;

  const colors = DOC_TYPE_COLORS[doc.type];
  const typeLabel = DOC_TYPE_LABELS[doc.type];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-6 lg:p-8">
      {/* Backdrop with fade animation */}
      <div 
        className="absolute inset-0 bg-black/60 backdrop-blur-sm animate-fadeIn"
        onClick={onClose}
      />
      
      {/* Modal container with slide-up animation */}
      <div className="relative w-full max-w-2xl max-h-[85vh] bg-white rounded-xl shadow-2xl overflow-hidden animate-slideUp flex flex-col">
        {/* Header */}
        <div className={`flex-shrink-0 px-4 sm:px-6 py-3 border-b ${colors.border} ${colors.bg}`}>
          <div className="flex items-center justify-between gap-3">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide ${colors.text} bg-white/60`}>
                  {typeLabel}
                </span>
                <h2 className="text-base font-semibold text-gray-900 truncate">
                  {doc.title}
                </h2>
              </div>
              {doc.description && (
                <p className="text-xs text-gray-600 mt-0.5 truncate">
                  {doc.description}
                </p>
              )}
            </div>
            
            {/* Close button */}
            <button
              onClick={onClose}
              className="flex-shrink-0 p-1.5 rounded-md text-gray-500 hover:text-gray-700 hover:bg-white/50 transition-colors"
              aria-label="Close document preview"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>
        
        {/* Content area */}
        <div className="flex-1 overflow-y-auto">
          {/* Thumbnail preview if available */}
          {doc.thumbnailUrl && (
            <div className="px-4 sm:px-6 pt-3">
              <div className="relative rounded-md overflow-hidden bg-gray-100 border border-gray-200">
                <img
                  src={doc.thumbnailUrl}
                  alt={`Preview of ${doc.title}`}
                  className="w-full h-32 object-cover"
                />
              </div>
            </div>
          )}
          
          {/* Document content */}
          <div className="px-4 sm:px-6 py-4">
            {doc.content ? (
              <div className="bg-gray-50 rounded-lg border border-gray-200 overflow-hidden">
                <div className="overflow-x-auto p-4">
                  <pre 
                    className="text-gray-700 whitespace-pre leading-snug"
                    style={{ 
                      fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
                      fontSize: '11px',
                      lineHeight: '1.5',
                    }}
                  >
                    {doc.content}
                  </pre>
                </div>
              </div>
            ) : (
              <div className="text-center py-12">
                <div className={`inline-flex items-center justify-center w-12 h-12 rounded-full ${colors.bg} mb-3`}>
                  <svg className={`w-6 h-6 ${colors.text}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                </div>
                <p className="text-gray-500 text-sm">
                  Document content preview not available.
                </p>
              </div>
            )}
          </div>
        </div>
        
        {/* Footer */}
        <div className="flex-shrink-0 px-4 sm:px-6 py-3 border-t border-gray-100 bg-gray-50">
          <div className="flex items-center justify-between">
            <p className="text-[10px] text-gray-400">
              Press <kbd className="px-1 py-0.5 bg-gray-200 rounded text-gray-500 font-mono text-[10px]">Esc</kbd> to close
            </p>
            <button
              onClick={onClose}
              className="px-3 py-1.5 text-xs font-medium text-gray-600 bg-white border border-gray-200 rounded-md hover:bg-gray-50 hover:border-gray-300 transition-colors"
            >
              Close
            </button>
          </div>
        </div>
      </div>
      
      {/* CSS for animations */}
      <style jsx global>{`
        @keyframes fadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }
        
        @keyframes slideUp {
          from {
            opacity: 0;
            transform: translateY(20px) scale(0.98);
          }
          to {
            opacity: 1;
            transform: translateY(0) scale(1);
          }
        }
        
        .animate-fadeIn {
          animation: fadeIn 0.2s ease-out forwards;
        }
        
        .animate-slideUp {
          animation: slideUp 0.3s ease-out forwards;
        }
      `}</style>
    </div>
  );
}

