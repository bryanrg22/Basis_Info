'use client';

import { useState, useRef } from 'react';
import { useApp } from '@/contexts/AppContext';
import { WorkflowStatus } from '@/types';
import { useRouter } from 'next/navigation';
import ProtectedRoute from '@/components/ProtectedRoute';
import Header from '@/components/Header';
import Sidebar from '@/components/Sidebar';
import FilePreview from '@/components/FilePreview';
import FilePreviewModal from '@/components/FilePreviewModal';
import { logger } from '@/lib/logger';

export default function NewStudy() {
  const { createStudy, uploadFiles, state } = useApp();
  const router = useRouter();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [propertyName, setPropertyName] = useState('');
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [previewIndex, setPreviewIndex] = useState<number | null>(null);

  /**
   * Handles file selection from the file input.
   * Adds newly selected files to the existing selectedFiles array.
   * This allows users to select files in multiple batches.
   */
  const handleFileUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files;
    if (!files) return;

    const newFiles = Array.from(files);
    setSelectedFiles(prev => [...prev, ...newFiles]);
  };

  /**
   * Removes a file from the selected files list by index.
   * This allows users to remove files before uploading.
   */
  const removeFile = (index: number) => {
    setSelectedFiles(prev => prev.filter((_, i) => i !== index));
  };

  /**
   * Handles the "Start Analysis" button click.
   * 
   * Flow:
   * 1. Validates that property name and files are provided
   * 2. Creates a new study document in Firestore with initial state
   * 3. Uploads all selected files to Firebase Storage
   * 4. Updates the study document with uploaded file metadata
   * 5. Navigates to the first analysis page
   * 
   * The study is created with workflowStatus 'documents_uploaded' to indicate
   * that files have been uploaded and are ready for the first analysis step.
   */
  const handleAnalyze = async () => {
    if (!propertyName.trim() || selectedFiles.length === 0) {
      alert('Please provide a property name and upload at least one file');
      return;
    }

    try {
      setUploading(true);
      setUploadProgress(0);

      // Create new study first (without files)
      // Files will be uploaded separately and then linked to the study
      const newStudy = await createStudy({
        propertyName: propertyName.trim(),
        totalAssets: 0, // Will be calculated after analysis
        analysisDate: new Date().toISOString().split('T')[0],
        status: 'pending',
        workflowStatus: 'uploading_documents' as WorkflowStatus,
        assets: [],
        uploadedFiles: [],
        rooms: [],
        takeoffs: [],
      });

      // Upload files to storage and update study
      // Pass the newly created study to preserve assets (avoid race condition)
      await uploadFiles(selectedFiles, newStudy.id, newStudy, {
        onProgress: (percent) => setUploadProgress(percent),
      });

      // Navigate to loading page - it will trigger room classification
      router.push(`/study/${newStudy.id}/analyze/first`);
    } catch (error) {
      console.error('Error creating study:', error);
      alert(error instanceof Error ? error.message : 'Failed to create study. Please try again.');
    } finally {
      setUploading(false);
      setUploadProgress(0);
    }
  };

  const handlePreview = (index: number) => {
    setPreviewIndex(index);
  };

  const handleClosePreview = () => {
    setPreviewIndex(null);
  };

  const handleNavigatePreview = (index: number) => {
    setPreviewIndex(index);
  };

  const handleDeleteFromPreview = async (index: number) => {
    // Remove file from selectedFiles array
    const newFiles = selectedFiles.filter((_, i) => i !== index);
    setSelectedFiles(newFiles);
    
    // Handle preview navigation after deletion
    if (previewIndex !== null) {
      if (newFiles.length === 0) {
        // No files left, close modal
        setPreviewIndex(null);
      } else if (index === previewIndex) {
        // We deleted the currently previewed file
        if (index >= newFiles.length) {
          // Deleted the last file, show the new last file
          setPreviewIndex(newFiles.length - 1);
        }
        // Otherwise, stay on the same index (which now points to the next file)
        // The modal will update via the currentIndex prop
      } else if (index < previewIndex) {
        // We deleted a file before the current one, adjust index
        setPreviewIndex(previewIndex - 1);
      }
    }
  };

  return (
    <ProtectedRoute>
      <div className="flex h-screen">
        <Sidebar />
        <div className="flex-1 flex flex-col overflow-hidden">
          <Header />
          <div className="flex-1 overflow-y-auto">
            <div className="p-6 max-w-4xl mx-auto">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-gray-900">Create New Study</h1>
        <p className="text-gray-600 mt-2">Upload your property documents and let our AI analyze your assets</p>
      </div>

      {/* Property Name */}
      <div className="bg-white p-6 rounded-lg shadow-sm border border-gray-200 mb-6">
        <label htmlFor="propertyName" className="block text-sm font-medium text-gray-700 mb-2">
          Property Name
        </label>
        <input
          type="text"
          id="propertyName"
          value={propertyName}
          onChange={(e) => setPropertyName(e.target.value)}
          placeholder="Enter property name (e.g., Downtown Office Complex)"
          className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
        />
      </div>

      {/* File Upload */}
      <div className="bg-white p-6 rounded-lg shadow-sm border border-gray-200 mb-6">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Upload Property Documents</h2>
        
        {/* Drop Zone */}
        <div
          className="border-2 border-dashed border-gray-300 rounded-lg p-8 text-center hover:border-primary-400 transition-colors cursor-pointer"
          onClick={() => fileInputRef.current?.click()}
        >
          <div className="space-y-2">
            <svg className="mx-auto h-12 w-12 text-gray-400" stroke="currentColor" fill="none" viewBox="0 0 48 48">
              <path d="M28 8H12a4 4 0 00-4 4v20m32-12v8m0 0v8a4 4 0 01-4 4H12a4 4 0 01-4-4v-4m32-4l-3.172-3.172a4 4 0 00-5.656 0L28 28M8 32l9.172-9.172a4 4 0 015.656 0L28 28m0 0l4 4m4-24h8m-4-4v8m-12 4h.02" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <p className="text-gray-600">
              <span className="font-medium text-primary-600">Click to upload</span> or drag and drop
            </p>
            <p className="text-sm text-gray-500">Images (JPG, PNG, GIF, WEBP, SVG, HEIC, etc.), PDFs, videos, and other property documents</p>
          </div>
        </div>

        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".pdf,.jpg,.jpeg,.png,.gif,.webp,.svg,.bmp,.tiff,.tif,.heic,.heif,.avif,.mp4,.mov,.avi"
          onChange={handleFileUpload}
          className="hidden"
        />

        {/* Selected Files Preview Grid */}
        {selectedFiles.length > 0 && (
          <div className="mt-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-medium text-gray-700">
                Selected Files ({selectedFiles.length})
              </h3>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-4">
              {selectedFiles.map((file, index) => (
                <FilePreview
                  key={index}
                  file={file}
                  onRemove={() => removeFile(index)}
                  onClick={() => handlePreview(index)}
                  showRemove={!uploading}
                />
              ))}
            </div>
          </div>
        )}

        {/* Upload Progress */}
        {uploading && (
          <div className="mt-6">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium text-gray-700">Uploading files...</span>
              <span className="text-sm text-gray-500">{uploadProgress}%</span>
            </div>
            <div className="w-full bg-gray-200 rounded-full h-2">
              <div
                className="bg-primary-600 h-2 rounded-full transition-all duration-300"
                style={{ width: `${uploadProgress}%` }}
              />
            </div>
          </div>
        )}
      </div>

      {/* Analyze Button */}
      <div className="flex justify-end">
        <button
          onClick={handleAnalyze}
          disabled={
            // Button is disabled when ANY of these conditions are true:
            // 1. No property name entered (user must provide a name for the study)
            !propertyName.trim() || 
            // 2. No files selected (at least one file is required to start analysis)
            selectedFiles.length === 0 || 
            // 3. Currently uploading files (prevents duplicate submissions)
            uploading || 
            // 4. App is loading studies from Firestore (ensures data is ready before proceeding)
            state.loading || 
            // 5. There's an error in the app state (prevents proceeding with invalid state)
            //    This could be a Firestore connection error, missing index, or other critical errors
            !!state.error
          }
          className="bg-primary-700 text-white px-8 py-3 rounded-lg font-medium hover:bg-primary-800 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors"
        >
          {uploading ? 'Creating Study...' : 'Start Analysis'}
        </button>
      </div>

      {/* Error Display */}
      {state.error && (
        <div className="mt-4 p-4 bg-red-50 border border-red-200 rounded-lg">
          <p className="text-sm text-red-800">{state.error}</p>
        </div>
      )}

      {/* File Preview Modal */}
      {previewIndex !== null && (
        <FilePreviewModal
          files={selectedFiles}
          currentIndex={previewIndex}
          onClose={handleClosePreview}
          onNavigate={handleNavigatePreview}
          onDelete={handleDeleteFromPreview}
          showDelete={!uploading}
        />
      )}
            </div>
          </div>
        </div>
      </div>
    </ProtectedRoute>
  );
}
