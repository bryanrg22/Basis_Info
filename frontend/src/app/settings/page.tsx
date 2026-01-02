'use client';

import { useState, useRef, ChangeEvent } from 'react';
import ProtectedRoute from '@/components/ProtectedRoute';
import Header from '@/components/Header';
import Sidebar from '@/components/Sidebar';
import Button from '@/components/ui/Button';

type UploadStatus = 'idle' | 'selected' | 'uploading';

interface SelectedFile {
  name: string;
  size: number;
  type: string;
}

/**
 * Format file size in human readable format
 */
function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function SettingsPage() {
  const [selectedFile, setSelectedFile] = useState<SelectedFile | null>(null);
  const [uploadStatus, setUploadStatus] = useState<UploadStatus>('idle');
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const acceptedFormats = '.pdf,.csv,.xlsx,.xls';
  const acceptedFormatsList = ['PDF', 'CSV', 'Excel (.xlsx, .xls)'];

  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    setError(null);

    if (!file) {
      setSelectedFile(null);
      setUploadStatus('idle');
      return;
    }

    // Validate file type
    const validExtensions = ['.pdf', '.csv', '.xlsx', '.xls'];
    const fileExtension = file.name.toLowerCase().slice(file.name.lastIndexOf('.'));
    
    if (!validExtensions.includes(fileExtension)) {
      setError(`Invalid file type. Please select a ${acceptedFormatsList.join(', ')} file.`);
      setSelectedFile(null);
      setUploadStatus('idle');
      return;
    }

    // Validate file size (max 1GB)
    const maxSize = 1024 * 1024 * 1024;
    if (file.size > maxSize) {
      setError('File size exceeds 1GB limit.');
      setSelectedFile(null);
      setUploadStatus('idle');
      return;
    }

    setSelectedFile({
      name: file.name,
      size: file.size,
      type: file.type,
    });
    setUploadStatus('selected');
  };

  const handleClearFile = () => {
    setSelectedFile(null);
    setUploadStatus('idle');
    setError(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const handleBrowseClick = () => {
    fileInputRef.current?.click();
  };

  return (
    <ProtectedRoute>
      <div className="flex h-screen">
        <Sidebar />
        <div className="flex-1 flex flex-col overflow-hidden">
          <Header />
          <div className="flex-1 overflow-y-auto bg-gray-50">
            <div className="max-w-4xl mx-auto p-6 space-y-8">
              {/* Page Header */}
              <div>
                <h1 className="text-3xl font-bold text-gray-900">Settings</h1>
                <p className="text-gray-600 mt-2">
                  Configure your workspace preferences and integrations.
                </p>
              </div>

              {/* Cost Estimation Database Section */}
              <section className="bg-white rounded-xl border border-gray-200 shadow-sm">
                <div className="p-6 border-b border-gray-100">
                  <div className="flex items-start justify-between">
                    <div>
                      <h2 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
                        Cost Estimation Database
                        <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-800">
                          Early Access
                        </span>
                      </h2>
                      <p className="text-gray-600 mt-1">
                        Upload your cost estimation database to enable accurate pricing for takeoffs.
                      </p>
                    </div>
                  </div>
                </div>

                <div className="p-6 space-y-6">
                  {/* Supported Databases Info */}
                  <div className="bg-blue-50 border border-blue-100 rounded-lg p-4">
                    <h3 className="text-sm font-medium text-blue-900 mb-2">
                      Supported Databases
                    </h3>
                    <ul className="text-sm text-blue-800 space-y-1">
                      <li className="flex items-center gap-2">
                        <svg className="w-4 h-4 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                        </svg>
                        RSMeans Cost Data
                      </li>
                      <li className="flex items-center gap-2">
                        <svg className="w-4 h-4 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                        </svg>
                        Marshall &amp; Swift / Boeckh
                      </li>
                      <li className="flex items-center gap-2">
                        <svg className="w-4 h-4 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                        </svg>
                        Custom cost databases (CSV/Excel format)
                      </li>
                    </ul>
                  </div>

                  {/* File Upload Area */}
                  <div className="space-y-4">
                    <label className="block text-sm font-medium text-gray-700">
                      Upload Database File
                    </label>

                    {/* Hidden file input */}
                    <input
                      ref={fileInputRef}
                      type="file"
                      accept={acceptedFormats}
                      onChange={handleFileChange}
                      className="hidden"
                      aria-label="Upload cost database file"
                    />

                    {/* Drop zone / Upload area */}
                    <div
                      className={`
                        relative border-2 border-dashed rounded-lg p-8
                        transition-colors cursor-pointer
                        ${error 
                          ? 'border-red-300 bg-red-50' 
                          : selectedFile 
                            ? 'border-primary-300 bg-primary-50' 
                            : 'border-gray-300 hover:border-gray-400 bg-gray-50 hover:bg-gray-100'
                        }
                      `}
                      onClick={handleBrowseClick}
                      role="button"
                      tabIndex={0}
                      onKeyDown={(e) => e.key === 'Enter' && handleBrowseClick()}
                    >
                      {selectedFile ? (
                        <div className="text-center">
                          <div className="mx-auto w-12 h-12 bg-primary-100 rounded-full flex items-center justify-center mb-4">
                            <svg className="w-6 h-6 text-primary-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                            </svg>
                          </div>
                          <p className="text-sm font-medium text-gray-900">{selectedFile.name}</p>
                          <p className="text-sm text-gray-500 mt-1">{formatFileSize(selectedFile.size)}</p>
                        </div>
                      ) : (
                        <div className="text-center">
                          <div className="mx-auto w-12 h-12 bg-gray-100 rounded-full flex items-center justify-center mb-4">
                            <svg className="w-6 h-6 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                            </svg>
                          </div>
                          <p className="text-sm text-gray-600">
                            <span className="font-medium text-primary-600">Click to upload</span> or drag and drop
                          </p>
                          <p className="text-xs text-gray-500 mt-1">
                            {acceptedFormatsList.join(', ')} up to 1GB
                          </p>
                        </div>
                      )}
                    </div>

                    {/* Error message */}
                    {error && (
                      <p className="text-sm text-red-600 flex items-center gap-1">
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                        </svg>
                        {error}
                      </p>
                    )}

                    {/* Selected file actions */}
                    {selectedFile && uploadStatus === 'selected' && (
                      <div className="space-y-4">
                        <div className="flex items-center gap-3">
                          <Button onClick={handleClearFile} variant="secondary" size="sm">
                            Remove File
                          </Button>
                          <Button 
                            variant="primary" 
                            size="sm"
                            disabled
                            className="cursor-not-allowed"
                          >
                            Upload Database
                          </Button>
                        </div>
                        <p className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 flex items-start gap-2">
                          <svg className="w-5 h-5 text-amber-500 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                          </svg>
                          <span>
                            Database integration is coming soon. Your file will be processed once this feature is fully available.
                          </span>
                        </p>
                      </div>
                    )}
                  </div>

                  {/* Format Requirements */}
                  <div className="border-t border-gray-100 pt-6">
                    <h3 className="text-sm font-medium text-gray-900 mb-3">Format Requirements</h3>
                    <div className="bg-gray-50 rounded-lg p-4 text-sm text-gray-600">
                      <p className="mb-2">Your database file should include the following columns:</p>
                      <ul className="list-disc list-inside space-y-1 ml-2">
                        <li>Item Code or ID</li>
                        <li>Description</li>
                        <li>Unit of Measure</li>
                        <li>Unit Cost</li>
                        <li>Category (optional)</li>
                      </ul>
                    </div>
                  </div>
                </div>
              </section>

              {/* Placeholder for future settings sections */}
              <section className="bg-white rounded-xl border border-gray-200 shadow-sm opacity-50">
            <div className="p-6">
                  <h2 className="text-xl font-semibold text-gray-400">
                    More Settings Coming Soon
                  </h2>
                  <p className="text-gray-400 mt-1">
                    Additional configuration options will be available in future updates.
                  </p>
                </div>
              </section>
            </div>
          </div>
        </div>
      </div>
    </ProtectedRoute>
  );
}
