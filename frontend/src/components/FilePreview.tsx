'use client';

import { useState, useEffect, useRef } from 'react';
import { UploadedFile } from '@/types';

interface FilePreviewProps {
  file: File | UploadedFile;
  onRemove?: () => void;
  onClick?: () => void;
  showRemove?: boolean;
  className?: string;
}

/**
 * FilePreview Component
 * 
 * Displays a preview thumbnail for files with support for:
 * - Images: Shows thumbnail preview
 * - PDFs, Videos, Documents: Shows type-specific icons
 * - Remove button on hover (if onRemove provided)
 * - Click handler for opening preview modal
 */
export default function FilePreview({
  file,
  onRemove,
  onClick,
  showRemove = true,
  className = '',
}: FilePreviewProps) {
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const objectUrlRef = useRef<string | null>(null);

  // Determine if file is an image
  const isImage = file.type.startsWith('image/');
  const isPdf = file.type === 'application/pdf';
  const isVideo = file.type.startsWith('video/');

  // Generate preview URL for images
  useEffect(() => {
    if (!isImage) {
      setLoading(false);
      return;
    }

    // If it's a File object, create object URL
    if (file instanceof File) {
      const url = URL.createObjectURL(file);
      objectUrlRef.current = url;
      setPreviewUrl(url);
      setLoading(false);
      return;
    }

    // If it's an UploadedFile with downloadURL, use it
    const uploadedFile = file as UploadedFile;
    if (uploadedFile.downloadURL) {
      setPreviewUrl(uploadedFile.downloadURL);
      setLoading(false);
      return;
    }

    // Otherwise, we'd need to fetch from storage
    // For now, set loading to false and show error
    setLoading(false);
    setError(true);
  }, [file, isImage]);

  // Cleanup object URL on unmount
  useEffect(() => {
    return () => {
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
      }
    };
  }, []);

  const formatFileSize = (bytes: number) => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  const getFileIcon = () => {
    if (isPdf) {
      return (
        <svg className="w-12 h-12 text-red-500" fill="currentColor" viewBox="0 0 20 20">
          <path fillRule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4z" clipRule="evenodd" />
        </svg>
      );
    }
    if (isVideo) {
      return (
        <svg className="w-12 h-12 text-purple-500" fill="currentColor" viewBox="0 0 20 20">
          <path d="M2 6a2 2 0 012-2h6a2 2 0 012 2v8a2 2 0 01-2 2H4a2 2 0 01-2-2V6zM14.553 7.106A1 1 0 0014 8v4a1 1 0 00.553.894l2 1A1 1 0 0018 13V7a1 1 0 00-1.447-.894l-2 1z" />
        </svg>
      );
    }
    // Generic document icon
    return (
      <svg className="w-12 h-12 text-gray-400" fill="currentColor" viewBox="0 0 20 20">
        <path fillRule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4z" clipRule="evenodd" />
      </svg>
    );
  };

  const handleRemove = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (onRemove) {
      onRemove();
    }
  };

  const handleClick = () => {
    if (onClick) {
      onClick();
    }
  };

  return (
    <div
      className={`relative group aspect-square bg-gray-100 rounded-lg overflow-hidden border border-gray-200 hover:border-primary-400 hover:shadow-lg transition-all duration-200 cursor-pointer transform hover:scale-[1.02] ${className}`}
      onClick={handleClick}
    >
      {/* Image Preview */}
      {isImage && previewUrl && !error && (
        <img
          src={previewUrl}
          alt={file.name}
          className="w-full h-full object-cover"
          onLoad={() => setLoading(false)}
          onError={() => {
            setError(true);
            setLoading(false);
          }}
        />
      )}

      {/* Loading State */}
      {loading && isImage && (
        <div className="absolute inset-0 flex items-center justify-center bg-gray-100">
          <svg className="animate-spin h-6 w-6 text-gray-400" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
        </div>
      )}

      {/* Error State or Non-Image File */}
      {(error || !isImage) && (
        <div className="absolute inset-0 flex flex-col items-center justify-center bg-gray-50 p-2">
          {getFileIcon()}
          {error && isImage && (
            <p className="text-xs text-gray-500 mt-2 text-center">Failed to load</p>
          )}
        </div>
      )}

      {/* Hover Overlay */}
      <div className="absolute inset-0 bg-black bg-opacity-0 group-hover:bg-opacity-20 transition-opacity flex items-center justify-center">
        {onClick && (
          <div className="opacity-0 group-hover:opacity-100 transition-opacity">
            <div className="bg-white/90 backdrop-blur-sm px-3 py-1.5 rounded-lg shadow-lg">
              <p className="text-xs font-medium text-gray-700">Click to preview</p>
            </div>
          </div>
        )}
      </div>

      {/* Remove Button */}
      {showRemove && onRemove && (
        <button
          onClick={handleRemove}
          className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-all duration-200 bg-red-500 hover:bg-red-600 text-white rounded-full p-1.5 shadow-lg z-10 transform hover:scale-110"
          aria-label="Remove file"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      )}

      {/* File Info Overlay */}
      <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/60 to-transparent p-2">
        <p className="text-xs text-white font-medium truncate">{file.name}</p>
        <p className="text-xs text-white/80">{formatFileSize(file.size)}</p>
      </div>
    </div>
  );
}

