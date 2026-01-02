'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { UploadedFile, Photo, Room, PhotoReviewState, PhotoObject, PhotoObjectType } from '@/types';
import { storageService } from '@/lib/storage';

interface CategoryConfig {
  label: string;
  icon: string;
  color: string;
  bgColor: string;
}

interface FilePreviewModalProps {
  files: (File | UploadedFile | Photo)[];
  currentIndex: number;
  onClose: () => void;
  onNavigate?: (index: number) => void;
  // Optional props for move functionality (used in review context)
  categories?: Record<string, CategoryConfig>;
  roomsByCategory?: Record<string, Room[]>;
  onMoveToRoom?: (photo: Photo, roomId: string) => void;
  onMoveToCategory?: (photo: Photo, categoryType: string) => void;
  onAddCategory?: (categoryName: string) => Promise<string | void>; // Returns roomId if room was created
  onAddRoom?: (categoryType: string) => Promise<void>;
  // Optional props for delete functionality
  onDelete?: (index: number) => Promise<void> | void;
  showDelete?: boolean; // If onDelete is provided, defaults to true unless explicitly set to false
  // Optional props for photo annotations/objects review
  photoAnnotations?: Record<string, PhotoReviewState>;
  onUpdatePhotoAnnotations?: (
    photoId: string,
    updater: (prev: PhotoReviewState | undefined) => PhotoReviewState
  ) => void;
  onMarkReviewed?: (photoId: string, reviewed: boolean) => void;
}

// Object type options with display labels
const OBJECT_TYPE_OPTIONS: { value: PhotoObjectType; label: string; color: string }[] = [
  { value: 'object', label: 'Object', color: 'bg-blue-100 text-blue-700' },
  { value: 'material', label: 'Material', color: 'bg-purple-100 text-purple-700' },
  { value: 'asset', label: 'Asset', color: 'bg-green-100 text-green-700' },
  { value: 'other', label: 'Other', color: 'bg-gray-100 text-gray-700' },
];

/**
 * FilePreviewModal Component
 * 
 * Full-screen modal for previewing files with:
 * - Image preview with zoom/pan capabilities
 * - PDF viewer or download option
 * - Video player
 * - Navigation between files (next/previous)
 * - Keyboard support (arrow keys, escape)
 * - File information display
 */
export default function FilePreviewModal({
  files,
  currentIndex,
  onClose,
  onNavigate,
  categories,
  roomsByCategory,
  onMoveToRoom,
  onMoveToCategory,
  onAddCategory,
  onAddRoom,
  onDelete,
  showDelete = onDelete !== undefined, // Default to true if onDelete is provided
  photoAnnotations,
  onUpdatePhotoAnnotations,
  onMarkReviewed,
}: FilePreviewModalProps) {
  const [currentFileIndex, setCurrentFileIndex] = useState(currentIndex);
  const [fileUrl, setFileUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [imageZoom, setImageZoom] = useState(1);
  const [imagePosition, setImagePosition] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const objectUrlRef = useRef<string | null>(null);
  const imageRef = useRef<HTMLImageElement>(null);
  const [showMoveMenu, setShowMoveMenu] = useState(false);
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set());
  const [showAddCategoryForm, setShowAddCategoryForm] = useState(false);
  const [newCategoryName, setNewCategoryName] = useState('');
  const [addingCategory, setAddingCategory] = useState(false);
  const [addingRoomForCategory, setAddingRoomForCategory] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  
  // Object editor state
  const [newObjectName, setNewObjectName] = useState('');
  const [newObjectType, setNewObjectType] = useState<PhotoObjectType>('object');
  const [editingObjectId, setEditingObjectId] = useState<string | null>(null);
  const [editingObjectName, setEditingObjectName] = useState('');
  const newObjectInputRef = useRef<HTMLInputElement>(null);

  const currentFile = files[currentFileIndex];
  
  // Determine file type - Photo objects are always images, File/UploadedFile have type property
  const getFileType = (file: File | UploadedFile | Photo | undefined): string => {
    if (!file) return '';
    if (file instanceof File) return file.type;
    if ('type' in file) return (file as UploadedFile).type;
    // Photo objects are always images
    return 'image/*';
  };
  
  const fileType = getFileType(currentFile);
  const isImage = fileType.startsWith('image/');
  const isPdf = fileType === 'application/pdf';
  const isVideo = fileType.startsWith('video/');
  
  // Get file size - Photo objects don't have size, use 0 as default
  const getFileSize = (file: File | UploadedFile | Photo | undefined): number => {
    if (!file) return 0;
    if (file instanceof File) return file.size;
    if ('size' in file) return (file as UploadedFile).size;
    return 0; // Photo objects don't have size
  };
  
  const fileSize = getFileSize(currentFile);
  
  // Get file name
  const getFileName = (file: File | UploadedFile | Photo | undefined): string => {
    if (!file) return '';
    return file.name;
  };
  
  const fileName = getFileName(currentFile);

  // Update currentFileIndex when currentIndex prop changes
  useEffect(() => {
    setCurrentFileIndex(currentIndex);
  }, [currentIndex]);

  // Reset deleting state when files array changes (after successful deletion)
  const prevFilesLengthRef = useRef(files.length);
  useEffect(() => {
    // If files array length changed and we were deleting, reset the deleting state
    if (prevFilesLengthRef.current !== files.length && deleting) {
      const timer = setTimeout(() => {
        setDeleting(false);
      }, 150);
      prevFilesLengthRef.current = files.length;
      return () => clearTimeout(timer);
    }
    prevFilesLengthRef.current = files.length;
  }, [files.length, deleting]);

  // Safety: Reset deleting state if it's been true for too long (prevents stuck state)
  useEffect(() => {
    if (deleting) {
      const safetyTimer = setTimeout(() => {
        console.warn('Deleting state was active for too long, resetting');
        setDeleting(false);
      }, 5000); // 5 second safety timeout
      return () => clearTimeout(safetyTimer);
    }
  }, [deleting]);

  // Load file URL when current file changes
  useEffect(() => {
    if (!currentFile) {
      setFileUrl(null);
      setLoading(false);
      // If no file at current index, close modal
      if (files.length === 0) {
        onClose();
      }
      return;
    }

    setLoading(true);
    setError(false);
    setImageZoom(1);
    setImagePosition({ x: 0, y: 0 });

    // Cleanup previous object URL
    if (objectUrlRef.current) {
      URL.revokeObjectURL(objectUrlRef.current);
      objectUrlRef.current = null;
    }

    const loadFile = async () => {
      try {
        // If it's a File object, create object URL
        if (currentFile instanceof File) {
          const url = URL.createObjectURL(currentFile);
          objectUrlRef.current = url;
          setFileUrl(url);
          setLoading(false);
          return;
        }

        // If it's an UploadedFile or Photo with downloadURL, use it
        const uploadedFile = currentFile as UploadedFile | Photo;
        if (uploadedFile.downloadURL) {
          setFileUrl(uploadedFile.downloadURL);
          setLoading(false);
          return;
        }

        // Otherwise, fetch from storage
        if (uploadedFile.storagePath) {
          const url = await storageService.getDownloadURL(uploadedFile.storagePath);
          setFileUrl(url);
          setLoading(false);
          return;
        }

        throw new Error('No file URL available');
      } catch (err) {
        console.error('Error loading file:', err);
        setError(true);
        setLoading(false);
      }
    };

    loadFile();
  }, [currentFile]);

  // Cleanup object URL on unmount
  useEffect(() => {
    return () => {
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
      }
    };
  }, []);

  const handleNext = useCallback(() => {
    if (currentFileIndex < files.length - 1) {
      const newIndex = currentFileIndex + 1;
      setCurrentFileIndex(newIndex);
      if (onNavigate) {
        onNavigate(newIndex);
      }
    }
  }, [currentFileIndex, files.length, onNavigate]);

  const handlePrevious = useCallback(() => {
    if (currentFileIndex > 0) {
      const newIndex = currentFileIndex - 1;
      setCurrentFileIndex(newIndex);
      if (onNavigate) {
        onNavigate(newIndex);
      }
    }
  }, [currentFileIndex, onNavigate]);

  // Keyboard navigation
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Don't handle navigation keys when focused on input/textarea/select
      const activeElement = document.activeElement;
      const isInputFocused = activeElement instanceof HTMLInputElement ||
                             activeElement instanceof HTMLTextAreaElement ||
                             activeElement instanceof HTMLSelectElement;
      
      if (e.key === 'Escape') {
        if (showDeleteConfirm) {
          setShowDeleteConfirm(false);
        } else if (showMoveMenu) {
          setShowMoveMenu(false);
        } else if (isInputFocused) {
          // Blur the input on Escape
          (activeElement as HTMLElement).blur();
        } else {
          onClose();
        }
      } else if (!isInputFocused) {
        // Only navigate when no input is focused
        if (e.key === 'ArrowLeft' && !showDeleteConfirm) {
          handlePrevious();
        } else if (e.key === 'ArrowRight' && !showDeleteConfirm) {
          handleNext();
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [currentFileIndex, files.length, showMoveMenu, showDeleteConfirm, onClose, handleNext, handlePrevious]);

  const handleZoomIn = () => {
    setImageZoom(prev => Math.min(prev + 0.25, 3));
  };

  const handleZoomOut = () => {
    setImageZoom(prev => Math.max(prev - 0.25, 0.5));
  };

  const handleResetZoom = () => {
    setImageZoom(1);
    setImagePosition({ x: 0, y: 0 });
  };

  const handleImageMouseDown = (e: React.MouseEvent) => {
    if (imageZoom > 1) {
      setIsDragging(true);
      setDragStart({ x: e.clientX - imagePosition.x, y: e.clientY - imagePosition.y });
    }
  };

  const handleImageMouseMove = (e: React.MouseEvent) => {
    if (isDragging && imageZoom > 1) {
      setImagePosition({
        x: e.clientX - dragStart.x,
        y: e.clientY - dragStart.y,
      });
    }
  };

  const handleImageMouseUp = () => {
    setIsDragging(false);
  };

  // Handle wheel zoom with Ctrl/Cmd
  const handleWheel = useCallback((e: WheelEvent) => {
    if (isImage && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      const delta = e.deltaY > 0 ? -0.1 : 0.1;
      setImageZoom(prev => {
        const newZoom = Math.max(0.5, Math.min(3, prev + delta));
        return newZoom;
      });
    }
  }, [isImage]);

  useEffect(() => {
    if (isImage) {
      window.addEventListener('wheel', handleWheel, { passive: false });
      return () => window.removeEventListener('wheel', handleWheel);
    }
  }, [isImage, handleWheel]);

  const formatFileSize = (bytes: number) => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  const handleDownload = () => {
    if (fileUrl) {
      const link = document.createElement('a');
      link.href = fileUrl;
      link.download = fileName;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    }
  };

  // Check if move functionality is available (only for Photo objects in review context)
  const isMoveAvailable = categories && roomsByCategory && onMoveToRoom && onMoveToCategory && 
                          currentFile && !(currentFile instanceof File) && 'id' in currentFile;

  const currentPhoto = isMoveAvailable ? (currentFile as Photo) : null;

  // Check if object annotations are available
  const isAnnotationsAvailable = photoAnnotations !== undefined && 
                                  onUpdatePhotoAnnotations !== undefined && 
                                  currentFile && 
                                  !(currentFile instanceof File) && 
                                  'id' in currentFile;

  // Get current photo ID for annotations
  const currentPhotoId = isAnnotationsAvailable && currentFile && 'id' in currentFile 
    ? (currentFile as Photo).id 
    : null;

  // Get current photo's review state
  const currentReviewState: PhotoReviewState | undefined = currentPhotoId && photoAnnotations 
    ? photoAnnotations[currentPhotoId] 
    : undefined;

  const isReviewed = currentReviewState?.reviewed || false;
  const currentObjects = currentReviewState?.objects || [];

  // Handle adding a new object
  const handleAddObject = useCallback(() => {
    if (!newObjectName.trim() || !currentPhotoId || !onUpdatePhotoAnnotations) return;
    
    const newObject: PhotoObject = {
      id: `obj-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
      label: newObjectName.trim(),
      type: newObjectType,
      confidence: 1.0, // Default to 100% confidence for manually added items
      source: 'manual',
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };

    onUpdatePhotoAnnotations(currentPhotoId, (prev) => ({
      objects: [...(prev?.objects || []), newObject],
      reviewed: prev?.reviewed || false,
      reviewedAt: prev?.reviewedAt,
      updatedAt: new Date().toISOString(),
    }));

    // Reset form
    setNewObjectName('');
    setNewObjectType('object');
    newObjectInputRef.current?.focus();
  }, [newObjectName, newObjectType, currentPhotoId, onUpdatePhotoAnnotations]);

  // Handle updating an object
  const handleUpdateObject = useCallback((objectId: string, updates: Partial<PhotoObject>) => {
    if (!currentPhotoId || !onUpdatePhotoAnnotations) return;

    onUpdatePhotoAnnotations(currentPhotoId, (prev) => ({
      objects: (prev?.objects || []).map(obj => 
        obj.id === objectId 
          ? { ...obj, ...updates, updatedAt: new Date().toISOString() }
          : obj
      ),
      reviewed: prev?.reviewed || false,
      reviewedAt: prev?.reviewedAt,
      updatedAt: new Date().toISOString(),
    }));
  }, [currentPhotoId, onUpdatePhotoAnnotations]);

  // Handle deleting an object
  const handleDeleteObject = useCallback((objectId: string) => {
    if (!currentPhotoId || !onUpdatePhotoAnnotations) return;

    onUpdatePhotoAnnotations(currentPhotoId, (prev) => ({
      objects: (prev?.objects || []).filter(obj => obj.id !== objectId),
      reviewed: prev?.reviewed || false,
      reviewedAt: prev?.reviewedAt,
      updatedAt: new Date().toISOString(),
    }));
  }, [currentPhotoId, onUpdatePhotoAnnotations]);

  // Handle marking as reviewed
  const handleToggleReviewed = useCallback(() => {
    if (!currentPhotoId || !onMarkReviewed) return;
    onMarkReviewed(currentPhotoId, !isReviewed);
  }, [currentPhotoId, isReviewed, onMarkReviewed]);

  // Get confidence color
  const getConfidenceColor = (confidence: number) => {
    if (confidence >= 0.8) return 'text-emerald-600 bg-emerald-50';
    if (confidence >= 0.5) return 'text-amber-600 bg-amber-50';
    return 'text-red-600 bg-red-50';
  };

  // Get object type config
  const getObjectTypeConfig = (type: PhotoObjectType) => {
    return OBJECT_TYPE_OPTIONS.find(o => o.value === type) || OBJECT_TYPE_OPTIONS[3];
  };

  const toggleCategory = (categoryType: string) => {
    setExpandedCategories(prev => {
      const next = new Set(prev);
      if (next.has(categoryType)) {
        next.delete(categoryType);
      } else {
        next.add(categoryType);
      }
      return next;
    });
  };

  const handleMoveToRoom = async (roomId: string) => {
    if (currentPhoto && onMoveToRoom) {
      try {
        await onMoveToRoom(currentPhoto, roomId);
        setShowMoveMenu(false);
      } catch (error) {
        console.error('Error moving photo to room:', error);
      }
    }
  };

  const handleMoveToCategory = async (categoryType: string) => {
    if (currentPhoto && onMoveToCategory) {
      try {
        await onMoveToCategory(currentPhoto, categoryType);
        setShowMoveMenu(false);
      } catch (error) {
        console.error('Error moving photo to category:', error);
      }
    }
  };

  // Reset move menu state when modal closes
  useEffect(() => {
    if (!showMoveMenu) {
      setShowAddCategoryForm(false);
      setNewCategoryName('');
      setExpandedCategories(new Set());
      setAddingCategory(false);
      setAddingRoomForCategory(null);
    }
  }, [showMoveMenu]);

  const handleAddCategorySubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newCategoryName.trim() || !onAddCategory || !currentPhoto) return;
    
    setAddingCategory(true);
    const categoryName = newCategoryName.trim();
    const categoryKey = categoryName.toLowerCase().replace(/\s+/g, '_');
    
    try {
      const roomId = await onAddCategory(categoryName);
      setNewCategoryName('');
      setShowAddCategoryForm(false);
      
      // Move photo to the newly created room if roomId was returned
      if (roomId && onMoveToRoom) {
        await onMoveToRoom(currentPhoto, roomId);
      } else {
        // Fallback: wait a bit and try to find the room
        setTimeout(async () => {
          const categoryRooms = roomsByCategory?.[categoryKey] || [];
          if (categoryRooms.length > 0 && onMoveToRoom) {
            const newRoom = categoryRooms[categoryRooms.length - 1];
            await onMoveToRoom(currentPhoto, newRoom.id);
          } else if (onMoveToCategory) {
            await onMoveToCategory(currentPhoto, categoryKey);
          }
        }, 300);
      }
      setShowMoveMenu(false);
      setAddingCategory(false);
    } catch (error) {
      console.error('Error adding category:', error);
      setAddingCategory(false);
    }
  };

  const handleAddRoom = async (categoryType: string) => {
    if (!onAddRoom) return;
    
    setAddingRoomForCategory(categoryType);
    try {
      await onAddRoom(categoryType);
      // After room is created, move photo to it
      setTimeout(async () => {
        const categoryRooms = roomsByCategory?.[categoryType] || [];
        if (categoryRooms.length > 0 && currentPhoto && onMoveToRoom) {
          const newRoom = categoryRooms[categoryRooms.length - 1];
          await onMoveToRoom(currentPhoto, newRoom.id);
        }
        setShowMoveMenu(false);
      }, 200);
    } catch (error) {
      console.error('Error adding room:', error);
    } finally {
      setAddingRoomForCategory(null);
    }
  };

  const handleDeleteClick = () => {
    setShowDeleteConfirm(true);
  };

  const handleDeleteConfirm = async () => {
    if (!onDelete) return;
    
    setDeleting(true);
    setShowDeleteConfirm(false);
    try {
      await onDelete(currentFileIndex);
      // Parent component will handle navigation and closing the modal if needed
      // The deleting state will be reset by useEffect when files array updates
    } catch (error) {
      console.error('Error deleting file:', error);
      alert('Failed to delete file. Please try again.');
      setDeleting(false);
      setShowDeleteConfirm(false);
    }
  };

  const handleDeleteCancel = () => {
    setShowDeleteConfirm(false);
  };

  if (!currentFile) {
    return null;
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 animate-in fade-in duration-300"
      onClick={(e) => {
        if (e.target === e.currentTarget) {
          onClose();
        }
      }}
    >
      {/* Modal Container */}
      <div className="bg-white rounded-lg shadow-2xl max-w-7xl max-h-[90vh] w-full mx-4 flex flex-col overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200 bg-white">
          {/* Left: File name and metadata */}
          <div className="flex-1 min-w-0 mr-4">
            <h3 className="text-lg font-semibold text-gray-900 truncate">{fileName}</h3>
            <div className="flex items-center gap-2 mt-1 text-sm text-gray-500">
              <span>{formatFileSize(fileSize)}</span>
              <span>•</span>
              <span>{fileType.split('/')[1]?.toUpperCase() || 'FILE'}</span>
              <span>•</span>
              <span>File {currentFileIndex + 1} of {files.length}</span>
            </div>
          </div>

          {/* Right: Controls */}
          <div className="flex items-center gap-2">
            {/* Zoom controls (images only) */}
            {isImage && !loading && !error && (
              <div className="flex items-center gap-1 border border-gray-300 rounded-lg p-1">
                <button
                  onClick={handleZoomOut}
                  disabled={imageZoom <= 0.5}
                  className="p-1.5 text-gray-600 hover:text-gray-900 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                  aria-label="Zoom out"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM13 10H7" />
                  </svg>
                </button>
                <span className="text-sm font-medium text-gray-700 min-w-[50px] text-center">
                  {Math.round(imageZoom * 100)}%
                </span>
                <button
                  onClick={handleZoomIn}
                  disabled={imageZoom >= 3}
                  className="p-1.5 text-gray-600 hover:text-gray-900 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                  aria-label="Zoom in"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM10 7v6m3-3H7" />
                  </svg>
                </button>
                {imageZoom !== 1 && (
                  <button
                    onClick={handleResetZoom}
                    className="px-2 py-1 text-xs text-gray-600 hover:text-gray-900 transition-colors"
                    aria-label="Reset zoom"
                  >
                    Reset
                  </button>
                )}
              </div>
            )}

            {/* Download button (PDFs and other files) */}
            {(isPdf || (!isImage && !isVideo)) && !loading && !error && (
              <button
                onClick={handleDownload}
                className="px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors flex items-center gap-2"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                </svg>
                Download
              </button>
            )}

            {/* Move Button (only shown in review context) */}
            {isMoveAvailable && (
              <button
                onClick={() => setShowMoveMenu(!showMoveMenu)}
                className="p-2 text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
                aria-label="Move to category"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4" />
                </svg>
              </button>
            )}

            {/* Delete Button */}
            {onDelete && (showDelete !== false) && !deleting && (
              <button
                onClick={handleDeleteClick}
                className="p-2 text-red-600 hover:bg-red-50 rounded-lg transition-colors"
                aria-label="Delete file"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                </svg>
              </button>
            )}

            {/* Delete Loading State */}
            {deleting && (
              <div className="p-2 text-red-600">
                <svg className="animate-spin w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
              </div>
            )}

            {/* Close Button */}
            <button
              onClick={onClose}
              disabled={deleting}
              className="p-2 text-gray-600 hover:bg-gray-100 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              aria-label="Close preview"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        {/* Content Area - Two-pane layout when annotations are available for images */}
        <div className={`flex-1 overflow-hidden flex ${isAnnotationsAvailable && isImage ? '' : ''}`}>
          {/* Main Image/Content Area */}
          <div className={`flex-1 overflow-auto bg-gray-100 flex items-center justify-center p-4 relative ${isAnnotationsAvailable && isImage ? 'pr-2' : ''}`}>
            {/* Navigation Buttons */}
            {files.length > 1 && (
              <>
                <button
                  onClick={handlePrevious}
                  disabled={currentFileIndex === 0}
                  className="absolute left-4 top-1/2 -translate-y-1/2 z-10 bg-white hover:bg-gray-50 text-gray-700 rounded-full p-3 shadow-lg transition-all duration-200 transform hover:scale-110 disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:scale-100"
                  aria-label="Previous file"
                >
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                  </svg>
                </button>
                <button
                  onClick={handleNext}
                  disabled={currentFileIndex === files.length - 1}
                  className="absolute top-1/2 -translate-y-1/2 z-10 bg-white hover:bg-gray-50 text-gray-700 rounded-full p-3 shadow-lg transition-all duration-200 transform hover:scale-110 disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:scale-100 right-4"
                  aria-label="Next file"
                >
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                  </svg>
                </button>
              </>
            )}

            {loading && (
              <div className="flex flex-col items-center justify-center">
                <svg className="animate-spin h-12 w-12 text-gray-400" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                <p className="text-gray-600 mt-4 text-sm">Loading preview...</p>
              </div>
            )}

            {error && (
              <div className="flex flex-col items-center justify-center text-gray-700">
                <svg className="w-16 h-16 mb-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <p className="text-lg font-medium mb-2">Failed to load file</p>
                <p className="text-sm text-gray-500 mb-4">{fileName}</p>
                <button
                  onClick={handleDownload}
                  className="px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white rounded-lg transition-colors"
                >
                  Download File
                </button>
              </div>
            )}

            {!loading && !error && fileUrl && (
              <>
                {/* Image Preview */}
                {isImage && (
                  <div className="relative w-full h-full flex items-center justify-center overflow-hidden">
                    <img
                      ref={imageRef}
                      src={fileUrl}
                      alt={fileName}
                      className="max-w-full max-h-full object-contain select-none"
                      style={{
                        transform: `scale(${imageZoom}) translate(${imagePosition.x / imageZoom}px, ${imagePosition.y / imageZoom}px)`,
                        cursor: imageZoom > 1 ? (isDragging ? 'grabbing' : 'grab') : 'default',
                        transition: isDragging ? 'none' : 'transform 0.2s ease-out',
                      }}
                      onMouseDown={handleImageMouseDown}
                      onMouseMove={handleImageMouseMove}
                      onMouseUp={handleImageMouseUp}
                      onMouseLeave={handleImageMouseUp}
                      draggable={false}
                    />
                  </div>
                )}

                {/* PDF Preview */}
                {isPdf && (
                  <div className="w-full h-full flex items-center justify-center">
                    <iframe
                      src={fileUrl}
                      className="w-full h-full min-h-[600px] border-0 rounded-lg"
                      title={fileName}
                    />
                  </div>
                )}

                {/* Video Preview */}
                {isVideo && (
                  <div className="w-full h-full flex items-center justify-center">
                    <video
                      src={fileUrl}
                      controls
                      className="max-w-full max-h-[90vh] rounded-lg"
                      autoPlay
                    >
                      Your browser does not support the video tag.
                    </video>
                  </div>
                )}

                {/* Other File Types */}
                {!isImage && !isPdf && !isVideo && (
                  <div className="flex flex-col items-center justify-center text-gray-700">
                    <svg className="w-24 h-24 mb-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                    </svg>
                    <p className="text-lg font-medium mb-2">{fileName}</p>
                    <p className="text-sm text-gray-500 mb-4">{formatFileSize(fileSize)}</p>
                    <button
                      onClick={handleDownload}
                      className="px-6 py-3 bg-primary-600 hover:bg-primary-700 text-white rounded-lg transition-colors flex items-center gap-2"
                    >
                      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                      </svg>
                      Download File
                    </button>
                  </div>
                )}
              </>
            )}
          </div>

          {/* Objects/Materials Panel (only for images when annotations are available) */}
          {isAnnotationsAvailable && isImage && (
            <div className="w-[320px] bg-white border-l border-gray-200 flex flex-col overflow-hidden">
              {/* Panel Header */}
              <div className="p-4 border-b border-gray-200 flex-shrink-0">
                <div className="flex items-center justify-between mb-2">
                  <h4 className="text-sm font-semibold text-gray-900">Objects & Materials</h4>
                  <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                    isReviewed 
                      ? 'bg-emerald-100 text-emerald-700' 
                      : 'bg-amber-100 text-amber-700'
                  }`}>
                    {isReviewed ? 'Reviewed' : 'Not reviewed'}
                  </span>
                </div>
                {currentObjects.length > 0 && (
                  <p className="text-xs text-gray-500">
                    {currentObjects.length} item{currentObjects.length !== 1 ? 's' : ''} identified
                  </p>
                )}
              </div>

              {/* Objects List */}
              <div className="flex-1 overflow-y-auto p-3">
                {currentObjects.length === 0 ? (
                  <div className="flex flex-col items-center justify-center py-8 text-center">
                    <svg className="w-12 h-12 text-gray-300 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
                    </svg>
                    <p className="text-sm text-gray-500 mb-1">No objects added yet</p>
                    <p className="text-xs text-gray-400">Use the form below to add items you see</p>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {currentObjects.map((obj) => {
                      const typeConfig = getObjectTypeConfig(obj.type);
                      const isEditing = editingObjectId === obj.id;
                      
                      return (
                        <div
                          key={obj.id}
                          className="bg-gray-50 rounded-lg p-3 border border-gray-100 hover:border-gray-200 transition-colors group"
                        >
                          <div className="flex items-start gap-2">
                            <div className="flex-1 min-w-0">
                              {isEditing ? (
                                <input
                                  type="text"
                                  value={editingObjectName}
                                  onChange={(e) => setEditingObjectName(e.target.value)}
                                  onBlur={() => {
                                    if (editingObjectName.trim()) {
                                      handleUpdateObject(obj.id, { label: editingObjectName.trim() });
                                    }
                                    setEditingObjectId(null);
                                    setEditingObjectName('');
                                  }}
                                  onKeyDown={(e) => {
                                    if (e.key === 'Enter') {
                                      if (editingObjectName.trim()) {
                                        handleUpdateObject(obj.id, { label: editingObjectName.trim() });
                                      }
                                      setEditingObjectId(null);
                                      setEditingObjectName('');
                                    } else if (e.key === 'Escape') {
                                      setEditingObjectId(null);
                                      setEditingObjectName('');
                                    }
                                  }}
                                  className="w-full px-2 py-1 text-sm border border-primary-500 rounded focus:ring-1 focus:ring-primary-500 focus:outline-none"
                                  autoFocus
                                />
                              ) : (
                                <button
                                  onClick={() => {
                                    setEditingObjectId(obj.id);
                                    setEditingObjectName(obj.label);
                                  }}
                                  className="text-sm font-medium text-gray-900 hover:text-primary-600 text-left truncate block w-full"
                                  title="Click to edit"
                                >
                                  {obj.label}
                                </button>
                              )}
                              <div className="flex items-center gap-2 mt-1.5">
                                <select
                                  value={obj.type}
                                  onChange={(e) => handleUpdateObject(obj.id, { type: e.target.value as PhotoObjectType })}
                                  className={`px-1.5 py-0.5 rounded text-[10px] font-medium border-0 cursor-pointer focus:ring-1 focus:ring-primary-500 ${typeConfig.color}`}
                                >
                                  {OBJECT_TYPE_OPTIONS.map(opt => (
                                    <option key={opt.value} value={opt.value}>{opt.label}</option>
                                  ))}
                                </select>
                                <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${getConfidenceColor(obj.confidence)}`}>
                                  {Math.round(obj.confidence * 100)}%
                                </span>
                              </div>
                            </div>
                            <button
                              onClick={() => handleDeleteObject(obj.id)}
                              className="opacity-0 group-hover:opacity-100 p-1 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded transition-all"
                              title="Delete item"
                            >
                              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                              </svg>
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>

              {/* Add Object Form */}
              <div className="p-3 border-t border-gray-200 bg-gray-50 flex-shrink-0">
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    handleAddObject();
                  }}
                  className="space-y-2"
                >
                  <input
                    ref={newObjectInputRef}
                    type="text"
                    value={newObjectName}
                    onChange={(e) => setNewObjectName(e.target.value)}
                    placeholder="Add an object, material, or asset..."
                    className="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                  />
                  <div className="flex items-center gap-2">
                    <select
                      value={newObjectType}
                      onChange={(e) => setNewObjectType(e.target.value as PhotoObjectType)}
                      className="flex-1 px-2 py-1.5 text-xs border border-gray-300 rounded-lg focus:ring-1 focus:ring-primary-500 focus:border-primary-500"
                    >
                      {OBJECT_TYPE_OPTIONS.map(opt => (
                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                      ))}
                    </select>
                    <button
                      type="submit"
                      disabled={!newObjectName.trim()}
                      className="px-3 py-1.5 bg-primary-600 text-white rounded-lg text-xs font-medium hover:bg-primary-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    >
                      Add
                    </button>
                  </div>
                </form>
              </div>

              {/* Mark as Reviewed Button */}
              <div className="p-3 border-t border-gray-200 flex-shrink-0">
                <button
                  onClick={handleToggleReviewed}
                  className={`w-full py-2.5 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2 ${
                    isReviewed
                      ? 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                      : 'bg-emerald-600 text-white hover:bg-emerald-700'
                  }`}
                >
                  {isReviewed ? (
                    <>
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                      Mark as not reviewed
                    </>
                  ) : (
                    <>
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                      Mark as reviewed
                    </>
                  )}
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Footer Navigation Dots */}
        {files.length > 1 && (
          <div className="flex items-center justify-center gap-2 p-4 border-t border-gray-200 bg-white">
            {files.map((_, index) => (
              <button
                key={index}
                onClick={() => {
                  setCurrentFileIndex(index);
                  if (onNavigate) onNavigate(index);
                }}
                className={`transition-all duration-200 rounded-full ${
                  index === currentFileIndex
                    ? 'bg-primary-600 w-8 h-2'
                    : 'bg-gray-300 w-2 h-2 hover:bg-gray-400'
                }`}
                aria-label={`Go to file ${index + 1}`}
              />
            ))}
          </div>
        )}
      </div>

      {/* Delete Confirmation Dialog - Separate overlay */}
      {showDeleteConfirm && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 backdrop-blur-sm animate-in fade-in duration-200">
          <div className="bg-white rounded-lg shadow-2xl p-6 max-w-md mx-4 animate-in zoom-in-95 duration-200">
            <h3 className="text-lg font-semibold text-gray-900 mb-2">Delete File</h3>
            <p className="text-sm text-gray-600 mb-6">
              Are you sure you want to delete <span className="font-medium">{fileName}</span>? This action cannot be undone.
            </p>
            <div className="flex gap-3 justify-end">
              <button
                onClick={handleDeleteCancel}
                disabled={deleting}
                className="px-4 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors font-medium"
              >
                Cancel
              </button>
              <button
                onClick={handleDeleteConfirm}
                disabled={deleting}
                className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors font-medium flex items-center gap-2"
              >
                {deleting ? (
                  <>
                    <svg className="animate-spin h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                    </svg>
                    Deleting...
                  </>
                ) : (
                  'Delete'
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Move Menu */}
      {showMoveMenu && isMoveAvailable && categories && roomsByCategory && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={() => setShowMoveMenu(false)}
          />
          <div className="absolute top-20 right-4 z-50 bg-white rounded-lg shadow-2xl border border-gray-200 py-2 min-w-[280px] max-w-[320px] max-h-[70vh] flex flex-col animate-in fade-in slide-in-from-top-2 duration-200">
            <div className="px-4 py-3 border-b border-gray-200">
              <p className="text-sm font-semibold text-gray-900">Move to category</p>
              <p className="text-xs text-gray-500 truncate mt-1">{fileName}</p>
            </div>
            <div className="overflow-y-auto flex-1">
              {Object.entries(categories).map(([categoryType, config]) => {
                const categoryRooms = roomsByCategory[categoryType] || [];
                const isExpanded = expandedCategories.has(categoryType);
                return (
                  <div key={categoryType}>
                    <button
                      onClick={() => {
                        if (categoryRooms.length > 0) {
                          toggleCategory(categoryType);
                        } else {
                          handleMoveToCategory(categoryType);
                        }
                      }}
                      className="w-full text-left px-4 py-2.5 hover:bg-gray-50 flex items-center justify-between transition-colors group"
                    >
                      <div className="flex items-center gap-2 flex-1 min-w-0">
                        <span className="text-sm font-medium text-gray-700 truncate">{config.label}</span>
                        {categoryRooms.length > 0 && (
                          <span className="text-xs text-gray-500">({categoryRooms.length})</span>
                        )}
                      </div>
                      {categoryRooms.length > 0 && (
                        <svg
                          className={`w-4 h-4 text-gray-400 transition-transform ${isExpanded ? 'rotate-180' : ''}`}
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                        >
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                        </svg>
                      )}
                    </button>
                    {isExpanded && categoryRooms.length > 0 && (
                      <div className="bg-gray-50 border-t border-gray-100">
                        {categoryRooms.map(room => (
                          <button
                            key={room.id}
                            onClick={() => handleMoveToRoom(room.id)}
                            className="w-full text-left px-8 py-2 hover:bg-gray-100 text-sm text-gray-600 transition-colors"
                          >
                            {room.name}
                          </button>
                        ))}
                        <button
                          onClick={() => handleAddRoom(categoryType)}
                          disabled={addingRoomForCategory === categoryType}
                          className="w-full text-left px-8 py-2 hover:bg-gray-100 text-sm text-primary-600 font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                        >
                          {addingRoomForCategory === categoryType ? (
                            <>
                              <svg className="animate-spin h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                              </svg>
                              Adding...
                            </>
                          ) : (
                            <>
                              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                              </svg>
                              Add Room
                            </>
                          )}
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
              {Object.keys(categories).length === 0 && !showAddCategoryForm && (
                <div className="px-4 py-3 text-sm text-gray-500 text-center">
                  No categories yet
                </div>
              )}
              {/* Add Category Form */}
              {showAddCategoryForm ? (
                <div className="border-t border-gray-200 p-4 bg-gray-50">
                  <form onSubmit={handleAddCategorySubmit} className="space-y-3">
                    <input
                      type="text"
                      value={newCategoryName}
                      onChange={(e) => setNewCategoryName(e.target.value)}
                      placeholder="Category name"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 text-sm"
                      autoFocus
                      onKeyDown={(e) => {
                        if (e.key === 'Escape') {
                          setShowAddCategoryForm(false);
                          setNewCategoryName('');
                        }
                      }}
                    />
                    <div className="flex gap-2">
                      <button
                        type="submit"
                        disabled={!newCategoryName.trim() || addingCategory}
                        className="flex-1 px-3 py-1.5 bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50 disabled:cursor-not-allowed text-sm font-medium transition-colors"
                      >
                        {addingCategory ? 'Adding...' : 'Add'}
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setShowAddCategoryForm(false);
                          setNewCategoryName('');
                        }}
                        className="px-3 py-1.5 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 text-sm font-medium transition-colors"
                      >
                        Cancel
                      </button>
                    </div>
                  </form>
                </div>
              ) : (
                <button
                  onClick={() => setShowAddCategoryForm(true)}
                  className="w-full text-left px-4 py-2.5 border-t border-gray-200 hover:bg-gray-50 text-sm text-primary-600 font-medium transition-colors flex items-center gap-2"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                  </svg>
                  Add New Category
                </button>
              )}
            </div>
          </div>
        </>
      )}

      {/* File Info Bar */}
      <div className="absolute bottom-0 left-0 right-0 bg-black/50 backdrop-blur-sm border-t border-white/10 p-4 animate-in fade-in slide-in-from-bottom-4 duration-300">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div className="flex-1 min-w-0">
            <p className="text-white font-medium truncate">{fileName}</p>
            <p className="text-white/70 text-sm">
              {fileSize > 0 ? formatFileSize(fileSize) : 'Size unknown'} • {fileType || 'Unknown type'}
            </p>
          </div>
          {files.length > 1 && (
            <div className="ml-4 text-white/70 text-sm">
              {currentFileIndex + 1} of {files.length}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

