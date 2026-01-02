/**
 * DocumentThumbnail Component
 * 
 * Displays a thumbnail for a document with proper styling for grid and list views.
 */

'use client';

import { useState } from 'react';
import { UploadedFile } from '@/types';

interface DocumentThumbnailProps {
  file: UploadedFile;
  className?: string;
  size?: 'grid' | 'list';
  onError?: () => void;
}

export default function DocumentThumbnail({
  file,
  className = '',
  size = 'grid',
  onError,
}: DocumentThumbnailProps) {
  const [imageError, setImageError] = useState(false);
  const [imageLoading, setImageLoading] = useState(true);

  const isImage = file.type.startsWith('image/');
  const isPdf = file.type === 'application/pdf';

  const handleImageError = () => {
    setImageError(true);
    setImageLoading(false);
    if (onError) onError();
  };

  const handleImageLoad = () => {
    setImageLoading(false);
  };

  if (size === 'grid') {
    return (
      <div className={`relative aspect-square bg-gray-100 rounded overflow-hidden ${className}`}>
        {isImage && file.downloadURL && !imageError ? (
          <>
            {imageLoading && (
              <div className="absolute inset-0 flex items-center justify-center bg-gray-100">
                <svg className="animate-spin h-6 w-6 text-gray-400" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
              </div>
            )}
            <img
              src={file.downloadURL}
              alt={file.name}
              className="w-full h-full object-cover"
              onLoad={handleImageLoad}
              onError={handleImageError}
              loading="lazy"
            />
            {!imageLoading && (
              <div className="absolute top-1 left-1 bg-black/50 text-white text-xs px-1.5 py-0.5 rounded font-medium">
                Image
              </div>
            )}
          </>
        ) : isPdf ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center bg-red-50">
            <svg className="w-12 h-12 text-red-500 mb-2" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4z" clipRule="evenodd" />
            </svg>
            <span className="text-xs font-medium text-red-700">PDF</span>
          </div>
        ) : (
          <div className="absolute inset-0 flex flex-col items-center justify-center bg-gray-50">
            <svg className="w-12 h-12 text-gray-400 mb-2" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4z" clipRule="evenodd" />
            </svg>
            <span className="text-xs font-medium text-gray-600">File</span>
          </div>
        )}
        {imageError && isImage && (
          <div className="absolute inset-0 flex flex-col items-center justify-center bg-gray-50">
            <svg className="w-8 h-8 text-gray-400 mb-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
            </svg>
            <span className="text-xs text-gray-500">Image unavailable</span>
          </div>
        )}
      </div>
    );
  }

  // List view
  return (
    <div className={`relative w-16 h-16 bg-gray-100 rounded overflow-hidden flex-shrink-0 ${className}`}>
      {isImage && file.downloadURL && !imageError ? (
        <>
          {imageLoading && (
            <div className="absolute inset-0 flex items-center justify-center bg-gray-100">
              <svg className="animate-spin h-4 w-4 text-gray-400" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
              </svg>
            </div>
          )}
          <img
            src={file.downloadURL}
            alt={file.name}
            className="w-full h-full object-cover"
            onLoad={handleImageLoad}
            onError={handleImageError}
            loading="lazy"
          />
          {!imageLoading && (
            <div className="absolute bottom-0 right-0 bg-primary-600 text-white text-[10px] px-1 py-0.5 rounded-tl font-medium">
              IMG
            </div>
          )}
        </>
      ) : isPdf ? (
        <div className="absolute inset-0 flex items-center justify-center bg-red-50">
          <svg className="w-8 h-8 text-red-500" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4z" clipRule="evenodd" />
          </svg>
        </div>
      ) : (
        <div className="absolute inset-0 flex items-center justify-center bg-gray-50">
          <svg className="w-8 h-8 text-gray-400" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4z" clipRule="evenodd" />
          </svg>
        </div>
      )}
      {imageError && isImage && (
        <div className="absolute inset-0 flex items-center justify-center bg-gray-50">
          <svg className="w-6 h-6 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
          </svg>
        </div>
      )}
    </div>
  );
}

