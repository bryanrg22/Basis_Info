/**
 * DocumentAssociationPanel Component
 * 
 * Manages file associations with the current asset.
 * Redesigned with improved structure, visual design, and user experience.
 */

'use client';

import { useState } from 'react';
import { UploadedFile } from '@/types';
import DocumentThumbnail from './DocumentThumbnail';
import FilePreviewModal from '@/components/FilePreviewModal';

interface DocumentAssociationPanelProps {
  associatedFiles: UploadedFile[];
  otherFiles: UploadedFile[];
  isFileMultiAssociated: (fileId: string) => boolean;
  viewMode: 'grid' | 'list';
  searchQuery: string;
  onSearchChange: (query: string) => void;
  onViewModeChange: (mode: 'grid' | 'list') => void;
  onAssociateFile: (fileId: string) => void;
  onDisassociateFile: (fileId: string) => void;
  onToggleOtherFiles: () => void;
  showOtherFiles: boolean;
  totalFiles?: number;
}

/**
 * Document Association Panel component
 */
export default function DocumentAssociationPanel({
  associatedFiles,
  otherFiles,
  isFileMultiAssociated,
  viewMode,
  searchQuery,
  onSearchChange,
  onViewModeChange,
  onAssociateFile,
  onDisassociateFile,
  onToggleOtherFiles,
  showOtherFiles,
  totalFiles,
}: DocumentAssociationPanelProps) {
  const [previewIndex, setPreviewIndex] = useState<number | null>(null);
  const [previewFiles, setPreviewFiles] = useState<UploadedFile[]>([]);

  const handleFileClick = (file: UploadedFile, fileList: UploadedFile[]) => {
    const index = fileList.findIndex(f => f.id === file.id);
    setPreviewFiles(fileList);
    setPreviewIndex(index);
  };

  const handleClosePreview = () => {
    setPreviewIndex(null);
  };

  const handleNavigatePreview = (index: number) => {
    setPreviewIndex(index);
  };

  const formatFileSize = (bytes?: number) => {
    if (!bytes) return 'Unknown size';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const getMultiAssetCount = (fileId: string): number => {
    const file = [...associatedFiles, ...otherFiles].find(f => f.id === fileId);
    return file?.assetIds?.length ?? 0;
  };

  // Empty state
  const hasFiles = (totalFiles ?? 0) > 0;
  const hasAssociatedFiles = associatedFiles.length > 0;
  const hasOtherFiles = otherFiles.length > 0;
  const hasSearchResults = associatedFiles.length > 0 || (showOtherFiles && otherFiles.length > 0);

  // Render grid view card
  const renderGridCard = (file: UploadedFile, showAssociateButton: boolean) => {
    const isMultiAssociated = isFileMultiAssociated(file.id);
    const multiCount = getMultiAssetCount(file.id);

    return (
      <div
        key={file.id}
        className="relative group bg-white rounded-lg border border-gray-200 overflow-hidden hover:shadow-md transition-all duration-200 cursor-pointer"
        onClick={() => handleFileClick(file, showAssociateButton ? otherFiles : associatedFiles)}
      >
        {/* Thumbnail area */}
        <div className="relative aspect-square bg-gray-100">
          <DocumentThumbnail file={file} size="grid" />
          
          {/* Association badges - top left */}
          <div className="absolute top-1 left-1 flex flex-col gap-1">
            {!showAssociateButton && (
              <div className="inline-flex items-center gap-1 bg-green-500 text-white text-xs px-1.5 py-0.5 rounded font-medium shadow-sm">
                <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                </svg>
                {isMultiAssociated && multiCount > 0 && <span>{multiCount}</span>}
              </div>
            )}
            {showAssociateButton && isMultiAssociated && (
              <div className="inline-flex items-center gap-1 bg-blue-500 text-white text-xs px-1.5 py-0.5 rounded font-medium shadow-sm">
                <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4z" clipRule="evenodd" />
                </svg>
                {multiCount > 0 && <span>{multiCount}</span>}
              </div>
            )}
          </div>

          {/* Action overlay - top right, appears on hover */}
          <div className="absolute top-1 right-1 opacity-0 group-hover:opacity-100 transition-opacity duration-200">
            {showAssociateButton ? (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onAssociateFile(file.id);
                }}
                className="bg-white text-primary-600 px-2 py-1 rounded text-xs font-medium hover:bg-primary-50 transition-colors shadow-sm"
              >
                Associate
              </button>
            ) : (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onDisassociateFile(file.id);
                }}
                className="bg-white text-gray-600 hover:text-red-600 hover:bg-red-50 rounded-full p-1.5 transition-colors shadow-sm"
                aria-label="Remove association"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            )}
          </div>
        </div>

        {/* File info section */}
        <div className="p-3">
          {/* Top row: File name and badges */}
          <div className="flex items-start justify-between gap-2 mb-1">
            <p className="text-xs font-semibold text-gray-900 truncate flex-1" title={file.name}>
              {file.name}
            </p>
          </div>
          {/* Bottom row: File type and size */}
          <div className="flex items-center gap-1 text-xs text-gray-500">
            <span>{file.type.split('/')[1]?.toUpperCase() || 'FILE'}</span>
            <span>•</span>
            <span>{formatFileSize(file.size)}</span>
          </div>
        </div>
      </div>
    );
  };

  // Render list view card
  const renderListCard = (file: UploadedFile, showAssociateButton: boolean) => {
    const isMultiAssociated = isFileMultiAssociated(file.id);
    const multiCount = getMultiAssetCount(file.id);

    return (
      <div
        key={file.id}
        className="flex items-center gap-4 p-3 bg-white rounded-lg border border-gray-200 hover:shadow-md transition-all duration-200 cursor-pointer group"
        onClick={() => handleFileClick(file, showAssociateButton ? otherFiles : associatedFiles)}
      >
        {/* Thumbnail */}
        <DocumentThumbnail file={file} size="list" />

        {/* File info */}
        <div className="flex-1 min-w-0">
          {/* Top row: File name and badges */}
          <div className="flex items-center gap-2 mb-1">
            <p className="text-sm font-medium text-gray-900 truncate">{file.name}</p>
            {!showAssociateButton && (
              <span className="inline-flex items-center gap-1 bg-green-100 text-green-800 text-xs px-2 py-0.5 rounded font-medium flex-shrink-0">
                <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                </svg>
                {isMultiAssociated && multiCount > 0 && <span>{multiCount}</span>}
              </span>
            )}
            {showAssociateButton && isMultiAssociated && (
              <span className="inline-flex items-center gap-1 bg-blue-100 text-blue-800 text-xs px-2 py-0.5 rounded font-medium flex-shrink-0">
                <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4z" clipRule="evenodd" />
                </svg>
                {multiCount > 0 && <span>{multiCount}</span>}
              </span>
            )}
          </div>
          {/* Bottom row: File type and size */}
          <div className="flex items-center gap-2 text-xs text-gray-500">
            <span>{file.type.split('/')[1]?.toUpperCase() || 'FILE'}</span>
            <span>•</span>
            <span>{formatFileSize(file.size)}</span>
          </div>
        </div>

        {/* Actions */}
        <div className="flex-shrink-0" onClick={(e) => e.stopPropagation()}>
          {showAssociateButton ? (
            <button
              onClick={() => onAssociateFile(file.id)}
              className="px-3 py-1.5 text-sm text-primary-600 font-medium rounded hover:bg-primary-50 transition-colors"
            >
              Associate
            </button>
          ) : (
            <button
              onClick={() => onDisassociateFile(file.id)}
              className="text-gray-400 hover:text-red-600 p-1.5 rounded transition-colors"
              aria-label="Remove association"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>
      </div>
    );
  };

  // Render file grid/list
  const renderFiles = (files: UploadedFile[], showAssociateButton: boolean) => {
    if (files.length === 0) {
      return null;
    }

    if (viewMode === 'grid') {
      return (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {files.map((file) => renderGridCard(file, showAssociateButton))}
        </div>
      );
    } else {
      return (
        <div className="space-y-2">
          {files.map((file) => renderListCard(file, showAssociateButton))}
        </div>
      );
    }
  };

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200">
      {/* Header Section */}
      <div className="flex items-center justify-between p-6 border-b border-gray-200">
        <div>
          <h3 className="text-lg font-semibold text-gray-900">Related Documents & Images</h3>
          {hasFiles && (
            <p className="text-sm text-gray-600 mt-1">
              {associatedFiles.length} associated {associatedFiles.length === 1 ? 'file' : 'files'} • {otherFiles.length} other {otherFiles.length === 1 ? 'file' : 'files'}
            </p>
          )}
        </div>
        {hasFiles && totalFiles !== undefined && (
          <div className="px-3 py-1 bg-gray-100 text-gray-700 text-sm font-medium rounded-full">
            {totalFiles} {totalFiles === 1 ? 'file' : 'files'}
          </div>
        )}
      </div>

      {/* Controls Section */}
      {hasFiles && (
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-4 p-6 border-b border-gray-200">
          {/* Search Input */}
          <div className="relative flex-1 sm:max-w-md">
            <div className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400">
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
            </div>
            <input
              type="text"
              placeholder="Search files..."
              value={searchQuery}
              onChange={(e) => onSearchChange(e.target.value)}
              className="w-full pl-10 pr-10 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 transition-all"
            />
            {searchQuery && (
              <button
                onClick={() => onSearchChange('')}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 transition-colors"
                aria-label="Clear search"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            )}
          </div>

          {/* Right Side Controls */}
          <div className="flex items-center gap-4">
            {/* Show all files checkbox */}
            {hasOtherFiles && (
              <label className="flex items-center gap-2 cursor-pointer group">
                <input
                  type="checkbox"
                  checked={showOtherFiles}
                  onChange={onToggleOtherFiles}
                  className="w-4 h-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500 focus:ring-2 cursor-pointer"
                />
                <span className="text-sm font-medium text-gray-700 group-hover:text-gray-900 transition-colors">
                  Show all files
                </span>
              </label>
            )}

            {/* View Mode Toggle */}
            <div className="flex items-center gap-1 border border-gray-300 rounded-lg p-1">
              <button
                onClick={() => onViewModeChange('grid')}
                className={`p-1.5 rounded transition-colors ${
                  viewMode === 'grid'
                    ? 'bg-primary-600 text-white'
                    : 'text-gray-600 hover:text-gray-900 hover:bg-gray-100'
                }`}
                title="Grid view"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
                </svg>
              </button>
              <button
                onClick={() => onViewModeChange('list')}
                className={`p-1.5 rounded transition-colors ${
                  viewMode === 'list'
                    ? 'bg-primary-600 text-white'
                    : 'text-gray-600 hover:text-gray-900 hover:bg-gray-100'
                }`}
                title="List view"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
                </svg>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Content Section */}
      <div className="p-6">
        {/* Empty State */}
        {!hasFiles && (
          <div className="text-center py-12">
            <div className="text-6xl mb-4">📄</div>
            <p className="text-gray-600 mb-2">No documents uploaded yet</p>
            <p className="text-sm text-gray-500">Documents will appear here after upload</p>
          </div>
        )}

        {/* Search Results - No matches overall */}
        {hasFiles && searchQuery && !hasSearchResults && (
          <div className="text-center py-12">
            <div className="text-6xl mb-4">🔍</div>
            <p className="text-gray-600 mb-2">No files match your search</p>
            <button
              onClick={() => onSearchChange('')}
              className="text-primary-600 hover:text-primary-700 font-medium text-sm transition-colors"
            >
              Clear search
            </button>
          </div>
        )}

        {/* Associated Documents Section */}
        {hasFiles && hasAssociatedFiles && (
          <div className="mb-6">
            <h4 className="text-sm font-medium text-gray-700 mb-4">
              Associated Documents ({associatedFiles.length})
            </h4>
            {searchQuery && associatedFiles.length === 0 ? (
              <div className="text-center py-8 text-gray-500 text-sm">
                No associated files match your search
              </div>
            ) : (
              renderFiles(associatedFiles, false)
            )}
          </div>
        )}

        {/* Other Documents Section */}
        {hasFiles && showOtherFiles && hasOtherFiles && (
          <div>
            <h4 className="text-sm font-medium text-gray-700 mb-4">
              Other Documents ({otherFiles.length})
            </h4>
            {searchQuery && otherFiles.length === 0 ? (
              <div className="text-center py-8 text-gray-500 text-sm">
                No other files match your search
              </div>
            ) : (
              renderFiles(otherFiles, true)
            )}
          </div>
        )}
      </div>

      {/* File Preview Modal */}
      {previewIndex !== null && previewFiles.length > 0 && (
        <FilePreviewModal
          files={previewFiles}
          currentIndex={previewIndex}
          onClose={handleClosePreview}
          onNavigate={handleNavigatePreview}
        />
      )}
    </div>
  );
}
