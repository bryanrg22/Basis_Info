'use client';

import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useApp } from '@/contexts/AppContext';
import { useParams, useRouter } from 'next/navigation';
import { Room, Photo, UploadedFile, PhotoReviewState, PhotoObject } from '@/types';
import { storageService } from '@/lib/storage';
import { studyService } from '@/services/study.service';
import ProtectedRoute from '@/components/ProtectedRoute';
import Header from '@/components/Header';
import Sidebar from '@/components/Sidebar';
import FilePreviewModal from '@/components/FilePreviewModal';
import StudyBackButton from '@/components/StudyBackButton';
import { exportRoomsToZip, downloadBlob } from '@/utils/room-export';

// Color palette for dynamically generated categories
const categoryColors = [
  { color: 'text-orange-700', bgColor: 'bg-orange-50' },
  { color: 'text-blue-700', bgColor: 'bg-blue-50' },
  { color: 'text-cyan-700', bgColor: 'bg-cyan-50' },
  { color: 'text-purple-700', bgColor: 'bg-purple-50' },
  { color: 'text-green-700', bgColor: 'bg-green-50' },
  { color: 'text-gray-700', bgColor: 'bg-gray-50' },
  { color: 'text-amber-700', bgColor: 'bg-amber-50' },
  { color: 'text-indigo-700', bgColor: 'bg-indigo-50' },
  { color: 'text-pink-700', bgColor: 'bg-pink-50' },
  { color: 'text-teal-700', bgColor: 'bg-teal-50' },
  { color: 'text-rose-700', bgColor: 'bg-rose-50' },
  { color: 'text-violet-700', bgColor: 'bg-violet-50' },
];

export default function FirstReviewPage() {
  const { state, dispatch, updateWorkflowStatus } = useApp();
  const params = useParams();
  const router = useRouter();
  const studyId = params.id as string;

  const study = state.studies.find(s => s.id === studyId);
  const [rooms, setRooms] = useState<Room[]>([]);
  const [editingRoomId, setEditingRoomId] = useState<string | null>(null);
  const [editingRoomName, setEditingRoomName] = useState('');
  const [selectedPhoto, setSelectedPhoto] = useState<Photo | null>(null);
  const [draggedPhoto, setDraggedPhoto] = useState<Photo | null>(null);
  const [dragOverCategory, setDragOverCategory] = useState<string | null>(null);
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set());
  const [showMoveMenu, setShowMoveMenu] = useState<{ photo: Photo; x: number; y: number } | null>(null);
  const [imageErrors, setImageErrors] = useState<Set<string>>(new Set());
  const [imageUrls, setImageUrls] = useState<Map<string, string>>(new Map());
  const [loadingImages, setLoadingImages] = useState<Set<string>>(new Set());
  const [showAddCategoryModal, setShowAddCategoryModal] = useState(false);
  const [newCategoryName, setNewCategoryName] = useState('');
  const [dynamicCategories, setDynamicCategories] = useState<Record<string, { label: string; icon: string; color: string; bgColor: string }>>({});
  const unassignedPhotosRef = useRef<Photo[]>([]);
  const [previewPhotoIndex, setPreviewPhotoIndex] = useState<number | null>(null);
  const [previewPhotos, setPreviewPhotos] = useState<Photo[]>([]);
  const [moveMenuExpandedCategories, setMoveMenuExpandedCategories] = useState<Set<string>>(new Set());
  const [showAddCategoryInMoveMenu, setShowAddCategoryInMoveMenu] = useState(false);
  const [newCategoryNameInMoveMenu, setNewCategoryNameInMoveMenu] = useState('');
  const [addingCategoryInMoveMenu, setAddingCategoryInMoveMenu] = useState(false);
  const [addingRoomInMoveMenu, setAddingRoomInMoveMenu] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportProgress, setExportProgress] = useState(0);
  const [exportError, setExportError] = useState<string | null>(null);
  
  // Photo annotations state
  const [photoAnnotations, setPhotoAnnotations] = useState<Record<string, PhotoReviewState>>({});
  const [annotationsSaving, setAnnotationsSaving] = useState(false);
  const [annotationsError, setAnnotationsError] = useState<string | null>(null);
  const pendingAnnotationsRef = useRef<Record<string, PhotoReviewState> | null>(null);
  const saveTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // Helper function to get image URL from photo (fetch from storage if needed)
  const getImageUrl = useCallback(async (photo: Photo): Promise<void> => {
    // Check if we already have the URL cached
    setImageUrls(prev => {
      if (prev.has(photo.id)) {
        return prev;
      }
      return prev;
    });

    // If downloadURL is available, use it
    if (photo.downloadURL) {
      setImageUrls(prev => {
        if (prev.has(photo.id)) return prev;
        return new Map(prev).set(photo.id, photo.downloadURL!);
      });
      return;
    }

    // Otherwise, fetch from storagePath
    if (photo.storagePath) {
      // Check if already loading
      setLoadingImages(prev => {
        if (prev.has(photo.id)) return prev;
        const next = new Set(prev);
        next.add(photo.id);
        return next;
      });

      try {
        const url = await storageService.getDownloadURL(photo.storagePath);
        setImageUrls(prev => new Map(prev).set(photo.id, url));
      } catch (error) {
        console.error(`Error fetching image URL for ${photo.id}:`, error);
        setImageErrors(prev => new Set(prev).add(photo.id));
      } finally {
        setLoadingImages(prev => {
          const next = new Set(prev);
          next.delete(photo.id);
          return next;
        });
      }
    }
  }, []);

  // Helper function to convert UploadedFile to Photo (for display purposes)
  const uploadedFileToPhoto = useCallback((file: UploadedFile): Photo => {
    return {
      id: file.id,
      name: file.name,
      storagePath: file.storagePath,
      downloadURL: file.downloadURL,
      uploadedAt: file.uploadedAt,
    };
  }, []);

  // Helper function to get photos for a room by looking up photoIds in uploadedFiles
  const getRoomPhotos = useCallback((room: Room): Photo[] => {
    if (!study) return [];
    return room.photoIds
      .map(id => study.uploadedFiles.find(file => file.id === id))
      .filter((file): file is UploadedFile => file !== undefined && file.type.startsWith('image/'))
      .map(uploadedFileToPhoto);
  }, [study, uploadedFileToPhoto]);

  // ============ Photo Annotations Helpers ============
  
  // Get photo review state with defaults
  const getPhotoReviewState = useCallback((photoId: string): PhotoReviewState => {
    return photoAnnotations[photoId] || {
      objects: [],
      reviewed: false,
      updatedAt: new Date().toISOString(),
    };
  }, [photoAnnotations]);

  // Check if a photo has been reviewed
  const isPhotoReviewed = useCallback((photoId: string): boolean => {
    return photoAnnotations[photoId]?.reviewed || false;
  }, [photoAnnotations]);

  // Get objects for a photo
  const getPhotoObjects = useCallback((photoId: string): PhotoObject[] => {
    return photoAnnotations[photoId]?.objects || [];
  }, [photoAnnotations]);

  // Debounced persistence of annotations
  const saveAnnotations = useCallback(async (annotations: Record<string, PhotoReviewState>) => {
    if (!studyId) return;
    setAnnotationsSaving(true);
    setAnnotationsError(null);
    try {
      await studyService.updatePhotoAnnotations(studyId, annotations);
      pendingAnnotationsRef.current = null;
    } catch (error) {
      console.error('Error saving photo annotations:', error);
      setAnnotationsError('Failed to save changes. Your edits are kept locally.');
    } finally {
      setAnnotationsSaving(false);
    }
  }, [studyId]);

  const debouncedSaveAnnotations = useCallback((annotations: Record<string, PhotoReviewState>) => {
    // Clear any pending save
    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current);
    }
    // Schedule new save after debounce delay
    saveTimeoutRef.current = setTimeout(() => {
      saveAnnotations(annotations);
    }, 800);
  }, [saveAnnotations]);

  // Cleanup save timeout on unmount
  useEffect(() => {
    return () => {
      if (saveTimeoutRef.current) {
        clearTimeout(saveTimeoutRef.current);
      }
    };
  }, []);

  // Update photo annotations with automatic persistence
  const updatePhotoAnnotations = useCallback((
    photoId: string,
    updater: (prev: PhotoReviewState | undefined) => PhotoReviewState
  ) => {
    setPhotoAnnotations(prev => {
      const current = prev[photoId];
      const updated = updater(current);
      const newAnnotations = { ...prev, [photoId]: updated };
      pendingAnnotationsRef.current = newAnnotations;
      debouncedSaveAnnotations(newAnnotations);
      return newAnnotations;
    });
  }, [debouncedSaveAnnotations]);

  // Add or update a photo object
  const upsertPhotoObject = useCallback((photoId: string, object: PhotoObject) => {
    updatePhotoAnnotations(photoId, (prev) => {
      const currentObjects = prev?.objects || [];
      const existingIndex = currentObjects.findIndex(o => o.id === object.id);
      const now = new Date().toISOString();
      
      let newObjects: PhotoObject[];
      if (existingIndex >= 0) {
        newObjects = [...currentObjects];
        newObjects[existingIndex] = { ...object, updatedAt: now };
      } else {
        newObjects = [...currentObjects, { ...object, createdAt: now, updatedAt: now }];
      }
      
      return {
        objects: newObjects,
        reviewed: prev?.reviewed || false,
        reviewedAt: prev?.reviewedAt,
        updatedAt: now,
      };
    });
  }, [updatePhotoAnnotations]);

  // Delete a photo object
  const deletePhotoObject = useCallback((photoId: string, objectId: string) => {
    updatePhotoAnnotations(photoId, (prev) => {
      const currentObjects = prev?.objects || [];
      return {
        objects: currentObjects.filter(o => o.id !== objectId),
        reviewed: prev?.reviewed || false,
        reviewedAt: prev?.reviewedAt,
        updatedAt: new Date().toISOString(),
      };
    });
  }, [updatePhotoAnnotations]);

  // Mark a photo as reviewed or not reviewed
  const markPhotoReviewed = useCallback((photoId: string, reviewed: boolean) => {
    updatePhotoAnnotations(photoId, (prev) => {
      const now = new Date().toISOString();
      return {
        objects: prev?.objects || [],
        reviewed,
        reviewedAt: reviewed ? now : undefined,
        updatedAt: now,
      };
    });
  }, [updatePhotoAnnotations]);

  useEffect(() => {
    if (!study) {
      router.push('/dashboard');
      return;
    }

    // Navigation guard: allow access based on currentStep and visitedSteps
    const status = study.workflowStatus;
    const currentStep = study.currentStep || status;
    const visitedSteps = study.visitedSteps || [status];
    
    // Allow access if current step is reviewing_rooms, or if it's been visited
    const canAccess = 
      currentStep === 'reviewing_rooms' || 
      visitedSteps.includes('reviewing_rooms') ||
      status === 'reviewing_rooms';

    if (!canAccess) {
      // Redirect to appropriate page based on current step
      if (currentStep === 'uploading_documents' || currentStep === 'analyzing_rooms') {
        router.push(`/study/${studyId}/analyze/first`);
      } else if (currentStep === 'resource_extraction') {
        router.push(`/study/${studyId}/review/resources`);
      } else if (currentStep === 'engineering_takeoff') {
        router.push(`/study/${studyId}/engineering-takeoff`);
      } else if (currentStep === 'completed') {
        router.push(`/study/${studyId}/complete`);
      } else {
        router.push('/dashboard');
      }
      return;
    }

    // Update currentStep if needed
    if (currentStep !== 'reviewing_rooms') {
      const updatedVisitedSteps = visitedSteps.includes('reviewing_rooms') 
        ? visitedSteps 
        : [...visitedSteps, 'reviewing_rooms'];
      
      updateWorkflowStatus(studyId, 'reviewing_rooms').catch(() => {
        // Silently handle error
      });
    }

    // Initialize photo annotations from study (populated by backend workflow)
    if (study.photoAnnotations && Object.keys(study.photoAnnotations).length > 0) {
      setPhotoAnnotations(study.photoAnnotations);
    }
    // If no annotations exist, leave empty - backend should have populated them

    if (study.rooms) {
      setRooms(study.rooms);
      
      // Find unassigned photos by comparing uploadedFiles with photoIds in rooms
      const assignedPhotoIds = new Set(
        study.rooms.flatMap(room => room.photoIds)
      );
      
      // Find uploadedFiles that aren't assigned to any room
      const unassignedFiles = study.uploadedFiles.filter(file => {
        if (!file.type.startsWith('image/')) return false;
        // Check if this file is already in a room by ID
        return !assignedPhotoIds.has(file.id);
      });
      
      // Convert unassigned UploadedFiles to Photo format
      unassignedPhotosRef.current = unassignedFiles.map(uploadedFileToPhoto);
      
      // Expand all categories by default
      const allCategories = new Set(study.rooms.map(r => r.type));
      setExpandedCategories(allCategories);
      
      // Initialize categories from rooms in database
      const categoriesFromRooms: Record<string, { label: string; icon: string; color: string; bgColor: string }> = {};
      study.rooms.forEach((room, index) => {
        if (!dynamicCategories[room.type]) {
          // Generate config for category based on room type
          const colorIndex = index % categoryColors.length;
          // Format label: convert snake_case or camelCase to Title Case
          const label = room.type
            .replace(/_/g, ' ')
            .replace(/([A-Z])/g, ' $1')
            .split(' ')
            .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
            .join(' ');
          
          categoriesFromRooms[room.type] = {
            label: label,
            icon: '📁',
            ...categoryColors[colorIndex],
          };
        }
      });
      if (Object.keys(categoriesFromRooms).length > 0) {
        setDynamicCategories(prev => ({ ...prev, ...categoriesFromRooms }));
      }
    } else {
      // No rooms yet - all image files are unassigned
      const imageFiles = study.uploadedFiles.filter(file => file.type.startsWith('image/'));
      unassignedPhotosRef.current = imageFiles.map(uploadedFileToPhoto);
    }

    // Pre-load image URLs for all photos (in rooms and unassigned)
    const allPhotos: Photo[] = [
      ...(study.rooms || []).flatMap(room => getRoomPhotos(room)),
      ...unassignedPhotosRef.current,
    ];

    // Load URLs for all photos that don't have URLs yet
    allPhotos.forEach(photo => {
      // Check if we need to load the URL
      const needsLoading = !imageUrls.has(photo.id) && 
                          !loadingImages.has(photo.id) && 
                          !photo.downloadURL;
      
      if (needsLoading && photo.storagePath) {
        getImageUrl(photo);
      } else if (photo.downloadURL && !imageUrls.has(photo.id)) {
        // Cache the downloadURL if we have it
        setImageUrls(prev => new Map(prev).set(photo.id, photo.downloadURL!));
      }
    });
  }, [study, router, uploadedFileToPhoto, getImageUrl, getRoomPhotos]);

  if (!study) {
    return <div>Loading...</div>;
  }

  // Get category config from dynamic categories (only categories that exist in database)
  const getCategoryConfig = (categoryType: string) => {
    if (dynamicCategories[categoryType]) {
      return dynamicCategories[categoryType];
    }
    // Fallback for categories not yet in dynamicCategories
    // Format label: convert snake_case or camelCase to Title Case
    const label = categoryType
      .replace(/_/g, ' ')
      .replace(/([A-Z])/g, ' $1')
      .split(' ')
      .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
      .join(' ');
    return {
      label: label,
      icon: '📁',
      color: 'text-slate-700',
      bgColor: 'bg-slate-50',
    };
  };

  // Group rooms by category - only include categories that have rooms
  const roomsByCategory = rooms.reduce((acc, room) => {
    if (!acc[room.type]) {
      acc[room.type] = [];
    }
    acc[room.type].push(room);
    return acc;
  }, {} as Record<string, Room[]>);
  
  // Only show categories that actually have rooms assigned
  const hasRooms = rooms.length > 0;

  // Helper to get photo count for a room
  const getRoomPhotoCount = (room: Room): number => {
    return room.photoIds.length;
  };

  // Handle adding new category
  const handleAddCategory = async () => {
    if (!newCategoryName.trim()) {
      alert('Please enter a category name');
      return;
    }

    const categoryKey = newCategoryName.trim().toLowerCase().replace(/\s+/g, '_');
    
    // Check if category already exists
    if (dynamicCategories[categoryKey]) {
      alert('This category already exists. You can add rooms to existing categories using the "Add Room" button in each category.');
      return;
    }

    // Generate color for new category
    const colorIndex = Object.keys(dynamicCategories).length % categoryColors.length;

    // Add to dynamic categories
    const newCategoryConfig = {
      label: newCategoryName.trim(),
      icon: '📁',
      ...categoryColors[colorIndex],
    };
    setDynamicCategories(prev => ({ ...prev, [categoryKey]: newCategoryConfig }));

    // Create a new room in this category
    await handleAddRoom(categoryKey);

    // Close modal
    setShowAddCategoryModal(false);
    setNewCategoryName('');
  };

  const toggleCategory = (category: string) => {
    setExpandedCategories(prev => {
      const next = new Set(prev);
      if (next.has(category)) {
        next.delete(category);
      } else {
        next.add(category);
      }
      return next;
    });
  };

  const handleRoomNameEdit = (roomId: string, currentName: string) => {
    setEditingRoomId(roomId);
    setEditingRoomName(currentName);
  };

  const handleRoomNameSave = async (roomId: string) => {
    if (editingRoomName.trim()) {
      const updatedRooms = rooms.map(room =>
        room.id === roomId ? { ...room, name: editingRoomName.trim() } : room
      );
      
      // Update local state
      setRooms(updatedRooms);
      
      // Dispatch to context
      dispatch({
        type: 'UPDATE_ROOM',
        payload: {
          studyId,
          roomId,
          updates: { name: editingRoomName.trim() },
        },
      });
      
      // Persist to Firestore
      try {
        await studyService.updateRooms(studyId, updatedRooms);
      } catch (error) {
        console.error('Error updating room name in Firestore:', error);
      }
    }
    setEditingRoomId(null);
    setEditingRoomName('');
  };

  const handleAddRoom = async (categoryType: string) => {
    const categoryRooms = roomsByCategory[categoryType] || [];
    const roomNumber = categoryRooms.length + 1;
    const categoryConfig = getCategoryConfig(categoryType);
    const categoryLabel = categoryConfig.label;
    
    const newRoom: Room = {
      id: `room-${categoryType}-${Date.now()}`,
      name: `${categoryLabel} ${roomNumber}`,
      type: categoryType as Room['type'],
      photoIds: [],
    };
    
    const updatedRooms = [...rooms, newRoom];
    
    // Update local state
    setRooms(updatedRooms);
    
    // Dispatch to context
    dispatch({
      type: 'ADD_ROOM',
      payload: { studyId, room: newRoom },
    });
    
    // Persist to Firestore
    try {
      await studyService.updateRooms(studyId, updatedRooms);
    } catch (error) {
      console.error('Error adding room to Firestore:', error);
    }
    
    setEditingRoomId(newRoom.id);
    setEditingRoomName(newRoom.name);
  };

  const handleDeleteRoom = async (roomId: string) => {
    if (confirm('Are you sure you want to delete this room? Photos will be moved to unassigned.')) {
      const roomToDelete = rooms.find(r => r.id === roomId);
      if (roomToDelete && study) {
        // Get the photos that were in this room and add them back to unassigned
        const roomPhotos = getRoomPhotos(roomToDelete);
        unassignedPhotosRef.current = [...unassignedPhotosRef.current, ...roomPhotos];
      }
      
      const updatedRooms = rooms.filter(room => room.id !== roomId);
      
      // Update local state
      setRooms(updatedRooms);
      
      // Dispatch to context
      dispatch({
        type: 'DELETE_ROOM',
        payload: { studyId, roomId },
      });
      
      // Persist to Firestore
      try {
        await studyService.updateRooms(studyId, updatedRooms);
      } catch (error) {
        console.error('Error deleting room from Firestore:', error);
      }
    }
  };

  const movePhotoToRoom = async (photo: Photo, targetRoomId: string) => {
    // Remove photo from current room (if it's in a room) by removing its ID
    const updatedRooms = rooms.map(room => ({
      ...room,
      photoIds: room.photoIds.filter(id => id !== photo.id),
    }));

    // Add photo to target room by adding its ID
    const finalRooms = updatedRooms.map(room => {
      if (room.id === targetRoomId) {
        // Check if photo is already in this room
        if (!room.photoIds.includes(photo.id)) {
          return { ...room, photoIds: [...room.photoIds, photo.id] };
        }
      }
      return room;
    });

    // Remove from unassigned if it was there
    unassignedPhotosRef.current = unassignedPhotosRef.current.filter(p => p.id !== photo.id);

    // Update local state
    setRooms(finalRooms);

    // Dispatch to context
    dispatch({
      type: 'UPDATE_ROOMS',
      payload: { studyId, rooms: finalRooms },
    });

    // Persist to Firestore
    try {
      await studyService.updateRooms(studyId, finalRooms);
    } catch (error) {
      console.error('Error moving photo to room in Firestore:', error);
    }
  };

  const handleDragStart = (e: React.DragEvent, photo: Photo) => {
    setDraggedPhoto(photo);
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', photo.id);
  };

  const handleDragOver = (e: React.DragEvent, category: string) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    setDragOverCategory(category);
  };

  const handleDragLeave = () => {
    setDragOverCategory(null);
  };

  const handleDrop = (e: React.DragEvent, targetRoomId: string) => {
    e.preventDefault();
    if (draggedPhoto) {
      movePhotoToRoom(draggedPhoto, targetRoomId);
    }
    setDraggedPhoto(null);
    setDragOverCategory(null);
  };

  // Handle photo click for preview
  const handlePhotoPreview = (e: React.MouseEvent, photo: Photo, allPhotos: Photo[]) => {
    e.stopPropagation();
    const index = allPhotos.findIndex(p => p.id === photo.id);
    if (index !== -1) {
      setPreviewPhotos(allPhotos);
      setPreviewPhotoIndex(index);
    }
  };

  // Get all photos for preview navigation (all photos in all rooms + unassigned)
  const getAllPhotosForPreview = useCallback((): Photo[] => {
    const roomPhotos = rooms.flatMap(room => getRoomPhotos(room));
    return [...roomPhotos, ...unassignedPhotosRef.current];
  }, [rooms, getRoomPhotos]);

  // Handle preview navigation
  const handlePreviewNavigate = (index: number) => {
    setPreviewPhotoIndex(index);
  };

  // Handle closing preview
  const handleClosePreview = () => {
    setPreviewPhotoIndex(null);
    setPreviewPhotos([]);
  };

  // Handle right-click or move button for move menu
  const handlePhotoMoveMenu = (e: React.MouseEvent, photo: Photo) => {
    e.stopPropagation();
    e.preventDefault();
    setShowMoveMenu({
      photo,
      x: e.clientX,
      y: e.clientY,
    });
  };

  const handleMoveToCategory = (photo: Photo, categoryType: string) => {
    const categoryRooms = roomsByCategory[categoryType] || [];
    if (categoryRooms.length > 0) {
      // Move to first room in category, or create new room
      movePhotoToRoom(photo, categoryRooms[0].id);
    } else {
      // Create new room in category
      handleAddRoom(categoryType);
      // Wait a bit then move photo (simplified - in real app would use callback)
      setTimeout(() => {
        const newRooms = state.studies.find(s => s.id === studyId)?.rooms || [];
        const newRoom = newRooms.find(r => r.type === categoryType && r.photos.length === 0);
        if (newRoom) {
          movePhotoToRoom(photo, newRoom.id);
        }
      }, 100);
    }
    setShowMoveMenu(null);
  };

  const handleExport = async () => {
    if (!study) {
      setExportError('Study not found');
      return;
    }

    setExporting(true);
    setExportError(null);
    setExportProgress(0);

    try {
      // Get unassigned photo IDs
      const assignedPhotoIds = new Set(rooms.flatMap(room => room.photoIds));
      const unassignedPhotoIds = study.uploadedFiles
        .filter(file => file.type.startsWith('image/') && !assignedPhotoIds.has(file.id))
        .map(file => file.id);

      // Get study name for filename (sanitize it)
      const studyName = study.name || 'study';
      const sanitizedStudyName = studyName.replace(/[^a-zA-Z0-9.-]/g, '_').replace(/\s+/g, '_');
      const zipFilename = `${sanitizedStudyName}-rooms-export.zip`;

      // Export to zip
      const zipBlob = await exportRoomsToZip(
        rooms,
        study.uploadedFiles,
        unassignedPhotoIds,
        studyName,
        (progress) => {
          setExportProgress(progress);
        }
      );

      // Download the zip file
      downloadBlob(zipBlob, zipFilename);

      // Reset progress after a short delay
      setTimeout(() => {
        setExportProgress(0);
      }, 1000);
    } catch (error) {
      console.error('Error exporting rooms:', error);
      setExportError(error instanceof Error ? error.message : 'Failed to export rooms');
    } finally {
      setExporting(false);
    }
  };

  const handleContinue = async () => {
    // Update rooms and annotations in study (ensure latest state is persisted)
    try {
      // Persist both rooms and photo annotations
      await studyService.update(studyId, { 
        rooms, 
        photoAnnotations 
      });
      
      // Also dispatch rooms update to context
      dispatch({
        type: 'UPDATE_ROOMS',
        payload: { studyId, rooms },
      });
      
      // Update workflow status and persist to Firestore
      await updateWorkflowStatus(studyId, 'engineering_takeoff');
      
      router.push(`/study/${studyId}/engineering-takeoff`);
    } catch (error) {
      console.error('Error saving rooms before continuing:', error);
      alert('Failed to save changes. Please try again.');
    }
  };

  const totalPhotos = rooms.reduce((sum, room) => sum + getRoomPhotoCount(room), 0) + unassignedPhotosRef.current.length;
  const categorizedPhotos = rooms.reduce((sum, room) => sum + getRoomPhotoCount(room), 0);
  const progressPercentage = totalPhotos > 0 ? Math.round((categorizedPhotos / totalPhotos) * 100) : 100;

  // Compute review statistics
  const allPhotoIds = useMemo(() => {
    const roomPhotoIds = rooms.flatMap(room => room.photoIds);
    const unassignedPhotoIds = unassignedPhotosRef.current.map(p => p.id);
    return [...roomPhotoIds, ...unassignedPhotoIds];
  }, [rooms]);

  const reviewedCount = useMemo(() => {
    return allPhotoIds.filter(id => photoAnnotations[id]?.reviewed).length;
  }, [allPhotoIds, photoAnnotations]);

  const allPhotosReviewed = totalPhotos > 0 && reviewedCount === totalPhotos;
  const reviewProgressPercentage = totalPhotos > 0 ? Math.round((reviewedCount / totalPhotos) * 100) : 100;

  return (
    <ProtectedRoute>
      <div className="flex h-screen">
        <Sidebar />
        <div className="flex-1 flex flex-col overflow-hidden">
          <Header />
          <div className="flex-1 overflow-y-auto">
            <div className="p-4 max-w-[1600px] mx-auto">
      {/* Header */}
      <div className="mb-4">
        <div className="flex items-center gap-4 mb-2">
          <StudyBackButton />
        </div>
        <h1 className="text-xl font-semibold text-gray-900">Review Room Categorization</h1>
        <p className="text-gray-500 text-sm mt-1">Organize photos into room categories and review objects/materials in each image.</p>
        
        {/* Progress Bars */}
        <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-4 max-w-2xl">
          {/* Categorization Progress */}
          <div>
            <div className="flex items-center justify-between text-xs text-gray-600 mb-1">
              <span className="font-medium">Categorization</span>
              <span>{categorizedPhotos}/{totalPhotos}</span>
            </div>
            <div className="bg-gray-200 rounded-full h-1.5">
              <div
                className="bg-primary-600 h-1.5 rounded-full transition-all duration-300"
                style={{ width: `${progressPercentage}%` }}
              />
            </div>
          </div>
          
          {/* Review Progress */}
          <div>
            <div className="flex items-center justify-between text-xs text-gray-600 mb-1">
              <span className="font-medium">Objects Review</span>
              <span className={reviewedCount === totalPhotos ? 'text-emerald-600' : ''}>
                {reviewedCount}/{totalPhotos}
                {reviewedCount === totalPhotos && (
                  <svg className="w-3.5 h-3.5 inline-block ml-1 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                  </svg>
                )}
              </span>
            </div>
            <div className="bg-gray-200 rounded-full h-1.5">
              <div
                className={`h-1.5 rounded-full transition-all duration-300 ${
                  reviewedCount === totalPhotos ? 'bg-emerald-500' : 'bg-amber-500'
                }`}
                style={{ width: `${reviewProgressPercentage}%` }}
              />
            </div>
          </div>
        </div>
      </div>

      {/* Add New Category Button - Only show if there are unassigned photos or existing rooms */}
      {(unassignedPhotosRef.current.length > 0 || hasRooms) && (
        <div className="mb-4 flex items-center justify-between flex-wrap gap-3">
          <button
            onClick={() => setShowAddCategoryModal(true)}
            className="px-3 py-1.5 bg-gray-100 text-gray-700 rounded-md hover:bg-gray-200 transition-colors flex items-center gap-1.5 text-xs font-medium"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            Add New Category
          </button>
          
          {/* Review Status Legend */}
          <div className="flex items-center gap-4 text-xs text-gray-600">
            <div className="flex items-center gap-1.5">
              <div className="w-4 h-4 rounded border-2 border-amber-400 bg-amber-50 flex items-center justify-center">
                <svg className="w-2.5 h-2.5 text-amber-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M12 9v2m0 4h.01" />
                </svg>
              </div>
              <span>Needs review</span>
            </div>
            <div className="flex items-center gap-1.5">
              <div className="w-4 h-4 rounded border-2 border-emerald-500 bg-emerald-50 flex items-center justify-center">
                <svg className="w-2.5 h-2.5 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <span>Reviewed</span>
            </div>
            {annotationsSaving && (
              <div className="flex items-center gap-1 text-gray-400">
                <svg className="animate-spin h-3 w-3" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                <span>Saving...</span>
              </div>
            )}
            {annotationsError && (
              <div className="flex items-center gap-1 text-red-500">
                <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
                <span>Save failed</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Unassigned Photos Section */}
      {unassignedPhotosRef.current.length > 0 && (
        <div className="bg-yellow-50 border border-yellow-300 rounded-lg p-3 mb-4 shadow-sm">
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm font-semibold text-gray-900">
              Unassigned Photos ({unassignedPhotosRef.current.length})
            </h2>
          </div>
          <div className="grid grid-cols-4 sm:grid-cols-6 md:grid-cols-8 lg:grid-cols-10 xl:grid-cols-12 2xl:grid-cols-14 gap-2">
            {unassignedPhotosRef.current.map(photo => {
              const reviewed = isPhotoReviewed(photo.id);
              const objectCount = getPhotoObjects(photo.id).length;
              return (
              <div
                key={photo.id}
                draggable
                onDragStart={(e) => handleDragStart(e, photo)}
                onClick={(e) => handlePhotoPreview(e, photo, unassignedPhotosRef.current)}
                onContextMenu={(e) => handlePhotoMoveMenu(e, photo)}
                className={`relative group cursor-pointer aspect-square bg-gray-200 rounded overflow-hidden transition-all ${
                  reviewed 
                    ? 'border-2 border-emerald-500 ring-1 ring-emerald-300/50' 
                    : 'border-2 border-amber-400 ring-1 ring-amber-300/50'
                }`}
              >
                {imageErrors.has(photo.id) ? (
                  <div className="w-full h-full flex items-center justify-center text-yellow-600">
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
                    </svg>
                  </div>
                ) : loadingImages.has(photo.id) ? (
                  <div className="w-full h-full flex items-center justify-center bg-gray-100">
                    <svg className="animate-spin h-4 w-4 text-gray-400" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                    </svg>
                  </div>
                ) : (
                  <img
                    src={imageUrls.get(photo.id) || photo.downloadURL || ''}
                    alt={photo.name}
                    className="w-full h-full object-cover"
                    onError={() => setImageErrors(prev => new Set(prev).add(photo.id))}
                    onLoad={() => {
                      // Ensure URL is cached if it loaded successfully
                      if (!imageUrls.has(photo.id) && photo.downloadURL) {
                        setImageUrls(prev => new Map(prev).set(photo.id, photo.downloadURL!));
                      }
                    }}
                  />
                )}
                {/* Review status badge */}
                <div className={`absolute top-1 right-1 w-4 h-4 rounded-full flex items-center justify-center shadow-sm ${
                  reviewed ? 'bg-emerald-500' : 'bg-amber-400'
                }`}>
                  {reviewed ? (
                    <svg className="w-2.5 h-2.5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                    </svg>
                  ) : (
                    <svg className="w-2.5 h-2.5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M12 9v2m0 4h.01" />
                    </svg>
                  )}
                </div>
                {/* Object count badge */}
                {objectCount > 0 && (
                  <div className="absolute bottom-1 left-1 px-1.5 py-0.5 bg-black/60 backdrop-blur-sm rounded text-[9px] font-medium text-white">
                    {objectCount} item{objectCount !== 1 ? 's' : ''}
                  </div>
                )}
                <div className="absolute inset-0 bg-black bg-opacity-0 group-hover:bg-opacity-30 transition-opacity flex items-center justify-center">
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handlePhotoMoveMenu(e, photo);
                    }}
                    className="opacity-0 group-hover:opacity-100 transition-opacity bg-white/90 backdrop-blur-sm px-1.5 py-0.5 rounded text-[10px] font-medium text-gray-700 hover:bg-white"
                    title="Move to category"
                  >
                    Move
                  </button>
                </div>
              </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Category Cards - Only show if there are rooms in the database */}
      {hasRooms && Object.entries(roomsByCategory).map(([categoryType, categoryRooms]) => {
        const config = getCategoryConfig(categoryType);
        const isExpanded = expandedCategories.has(categoryType);
        const totalPhotosInCategory = categoryRooms.reduce((sum, room) => sum + getRoomPhotoCount(room), 0);
        const isDragOver = dragOverCategory === categoryType;

        return (
          <div
            key={categoryType}
            className={`bg-white rounded-lg shadow-sm border mb-3 transition-all ${
              isDragOver ? 'border-primary-400 bg-primary-50' : 'border-gray-200'
            }`}
            onDragOver={(e) => handleDragOver(e, categoryType)}
            onDragLeave={handleDragLeave}
          >
            {/* Category Header */}
            <div
              className={`${config.bgColor} px-3 py-2 rounded-t-lg cursor-pointer flex items-center justify-between`}
              onClick={() => toggleCategory(categoryType)}
            >
              <div className="flex items-center gap-2">
                <div>
                  <h2 className={`text-sm font-semibold ${config.color}`}>{config.label}</h2>
                  <p className="text-xs text-gray-500">{totalPhotosInCategory} photos in {categoryRooms.length} {categoryRooms.length === 1 ? 'room' : 'rooms'}</p>
                </div>
              </div>
              <div className="flex items-center gap-1.5">
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleAddRoom(categoryType);
                  }}
                  className="px-2 py-0.5 bg-white text-gray-600 rounded text-xs font-medium hover:bg-gray-50 transition-colors"
                >
                  + Add Room
                </button>
                <svg
                  className={`w-4 h-4 text-gray-500 transition-transform ${isExpanded ? 'rotate-180' : ''}`}
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              </div>
            </div>

            {/* Category Content */}
            {isExpanded && (
              <div className="p-3">
                {categoryRooms.map(room => (
                  <div key={room.id} className="mb-3 last:mb-0">
                    {/* Room Header */}
                    <div className="flex items-center justify-between mb-2 pb-1.5 border-b border-gray-100">
                      <div className="flex items-center gap-2">
                        {editingRoomId === room.id ? (
                          <input
                            type="text"
                            value={editingRoomName}
                            onChange={(e) => setEditingRoomName(e.target.value)}
                            onBlur={() => handleRoomNameSave(room.id)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') {
                                handleRoomNameSave(room.id);
                              } else if (e.key === 'Escape') {
                                setEditingRoomId(null);
                                setEditingRoomName('');
                              }
                            }}
                            className="px-2 py-0.5 border border-primary-500 rounded text-sm focus:ring-1 focus:ring-primary-500 focus:outline-none font-medium"
                            autoFocus
                          />
                        ) : (
                          <h3 className="text-sm font-medium text-gray-900">{room.name}</h3>
                        )}
                        {editingRoomId !== room.id && (
                          <button
                            onClick={() => handleRoomNameEdit(room.id, room.name)}
                            className="text-primary-600 hover:text-primary-700 text-xs"
                          >
                            Edit
                          </button>
                        )}
                        <span className="text-xs text-gray-400">({getRoomPhotoCount(room)} photos)</span>
                      </div>
                      <button
                        onClick={() => handleDeleteRoom(room.id)}
                        className="text-red-500 hover:text-red-600 text-xs font-medium"
                      >
                        Delete
                      </button>
                    </div>

                    {/* Photo Grid */}
                    <div
                      className="grid grid-cols-4 sm:grid-cols-6 md:grid-cols-8 lg:grid-cols-10 xl:grid-cols-12 2xl:grid-cols-14 gap-2"
                      onDrop={(e) => handleDrop(e, room.id)}
                      onDragOver={(e) => {
                        e.preventDefault();
                        e.dataTransfer.dropEffect = 'move';
                      }}
                    >
                      {getRoomPhotos(room).map(photo => {
                        const allRoomPhotos = getRoomPhotos(room);
                        const reviewed = isPhotoReviewed(photo.id);
                        const objectCount = getPhotoObjects(photo.id).length;
                        return (
                        <div
                          key={photo.id}
                          draggable
                          onDragStart={(e) => handleDragStart(e, photo)}
                          onClick={(e) => handlePhotoPreview(e, photo, allRoomPhotos)}
                          onContextMenu={(e) => handlePhotoMoveMenu(e, photo)}
                          className={`relative group cursor-pointer aspect-square bg-gray-200 rounded overflow-hidden transition-all hover:shadow-sm ${
                            reviewed 
                              ? 'border-2 border-emerald-500 ring-1 ring-emerald-300/50 hover:ring-emerald-400/70' 
                              : 'border-2 border-amber-400 ring-1 ring-amber-300/50 hover:ring-amber-400/70'
                          }`}
                        >
                          {imageErrors.has(photo.id) ? (
                            <div className="w-full h-full flex items-center justify-center text-gray-400">
                              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
                              </svg>
                            </div>
                          ) : loadingImages.has(photo.id) ? (
                            <div className="w-full h-full flex items-center justify-center bg-gray-100">
                              <svg className="animate-spin h-4 w-4 text-gray-400" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                              </svg>
                            </div>
                          ) : (
                            <img
                              src={imageUrls.get(photo.id) || photo.downloadURL || ''}
                              alt={photo.name}
                              className="w-full h-full object-cover"
                              onError={() => setImageErrors(prev => new Set(prev).add(photo.id))}
                              onLoad={() => {
                                // Ensure URL is cached if it loaded successfully
                                if (!imageUrls.has(photo.id) && photo.downloadURL) {
                                  setImageUrls(prev => new Map(prev).set(photo.id, photo.downloadURL!));
                                }
                              }}
                            />
                          )}
                          {/* Review status badge */}
                          <div className={`absolute top-1 right-1 w-4 h-4 rounded-full flex items-center justify-center shadow-sm ${
                            reviewed ? 'bg-emerald-500' : 'bg-amber-400'
                          }`}>
                            {reviewed ? (
                              <svg className="w-2.5 h-2.5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                              </svg>
                            ) : (
                              <svg className="w-2.5 h-2.5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M12 9v2m0 4h.01" />
                              </svg>
                            )}
                          </div>
                          {/* Object count badge */}
                          {objectCount > 0 && (
                            <div className="absolute bottom-1 left-1 px-1.5 py-0.5 bg-black/60 backdrop-blur-sm rounded text-[9px] font-medium text-white">
                              {objectCount} item{objectCount !== 1 ? 's' : ''}
                            </div>
                          )}
                          <div className="absolute inset-0 bg-black bg-opacity-0 group-hover:bg-opacity-30 transition-opacity flex items-center justify-center">
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                handlePhotoMoveMenu(e, photo);
                              }}
                              className="opacity-0 group-hover:opacity-100 transition-opacity bg-white/90 backdrop-blur-sm px-1.5 py-0.5 rounded text-[10px] font-medium text-gray-700 hover:bg-white"
                              title="Move to category"
                            >
                              Move
                            </button>
                          </div>
                        </div>
                        );
                      })}
                      
                      {/* Drop Zone Indicator */}
                      {isDragOver && draggedPhoto && (
                        <div className="aspect-square border border-dashed border-primary-400 bg-primary-50 rounded flex items-center justify-center">
                          <span className="text-primary-600 text-[10px] font-medium">Drop here</span>
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}

      {/* Export and Continue Buttons */}
      <div className="flex justify-between items-center mt-5 pt-4 border-t border-gray-100">
        {/* Export Button */}
        <div className="flex flex-col gap-1.5">
          <button
            onClick={handleExport}
            disabled={exporting || rooms.length === 0}
            className="px-4 py-2 bg-gray-100 text-gray-700 rounded-md text-sm font-medium hover:bg-gray-200 transition-colors flex items-center disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {exporting ? (
              <>
                <svg className="animate-spin h-3.5 w-3.5 mr-1.5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                Exporting... {exportProgress > 0 && `${exportProgress}%`}
              </>
            ) : (
              <>
                <svg className="w-3.5 h-3.5 mr-1.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                </svg>
                Export Rooms
              </>
            )}
          </button>
          {exportError && (
            <p className="text-xs text-red-600">{exportError}</p>
          )}
          {exportProgress > 0 && exportProgress < 100 && !exportError && (
            <div className="w-36 bg-gray-200 rounded-full h-1">
              <div
                className="bg-primary-600 h-1 rounded-full transition-all duration-300"
                style={{ width: `${exportProgress}%` }}
              />
            </div>
          )}
        </div>

      {/* Continue Button */}
        <div className="flex flex-col items-end gap-1.5">
          <button
            onClick={handleContinue}
            className={`px-5 py-2 rounded-md text-sm font-medium transition-colors flex items-center ${
              allPhotosReviewed
                ? 'bg-primary-600 text-white hover:bg-primary-700'
                : 'bg-amber-100 text-amber-900 hover:bg-amber-200 border border-amber-300'
            }`}
          >
            Continue to Engineering Takeoffs
            <svg className="w-3.5 h-3.5 ml-1.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
          </button>
          {!allPhotosReviewed && (
            <div className="flex items-start gap-2 text-xs text-amber-700 max-w-md text-right">
              <svg className="w-3.5 h-3.5 mt-[2px]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
              <p>
                You&apos;ve reviewed {reviewedCount}/{totalPhotos} photos. You can continue now, but unreviewed photos may be
                missed in downstream engineering takeoffs.
              </p>
            </div>
          )}
        </div>
      </div>

      {/* Move Menu */}
      {showMoveMenu && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={() => {
              setShowMoveMenu(null);
              setShowAddCategoryInMoveMenu(false);
              setNewCategoryNameInMoveMenu('');
              setMoveMenuExpandedCategories(new Set());
            }}
          />
          <div
            className="fixed z-50 bg-white rounded-md shadow-xl border border-gray-200 py-1.5 min-w-[220px] max-w-[260px] max-h-[60vh] flex flex-col"
            style={{
              left: `${Math.min(showMoveMenu.x, window.innerWidth - 260)}px`,
              top: `${Math.min(showMoveMenu.y, window.innerHeight - 350)}px`,
            }}
          >
            <div className="px-3 py-2 border-b border-gray-100">
              <p className="text-xs font-semibold text-gray-900">Move to category</p>
              <p className="text-[10px] text-gray-500 truncate mt-0.5">{showMoveMenu.photo.name}</p>
            </div>
            <div className="overflow-y-auto flex-1">
              {Object.entries(dynamicCategories).map(([categoryType, config]) => {
                const categoryRooms = roomsByCategory[categoryType] || [];
                const isExpanded = moveMenuExpandedCategories.has(categoryType);
                return (
                  <div key={categoryType}>
                    <button
                      onClick={() => {
                        if (categoryRooms.length > 0) {
                          setMoveMenuExpandedCategories(prev => {
                            const next = new Set(prev);
                            if (next.has(categoryType)) {
                              next.delete(categoryType);
                            } else {
                              next.add(categoryType);
                            }
                            return next;
                          });
                        } else {
                          handleMoveToCategory(showMoveMenu.photo, categoryType);
                        }
                      }}
                      className="w-full text-left px-3 py-1.5 hover:bg-gray-50 flex items-center justify-between transition-colors group"
                    >
                      <div className="flex items-center gap-1.5 flex-1 min-w-0">
                        <span className="text-xs font-medium text-gray-700 truncate">{config.label}</span>
                        {categoryRooms.length > 0 && (
                          <span className="text-[10px] text-gray-500">({categoryRooms.length})</span>
                        )}
                      </div>
                      {categoryRooms.length > 0 && (
                        <svg
                          className={`w-3 h-3 text-gray-400 transition-transform ${isExpanded ? 'rotate-180' : ''}`}
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
                            onClick={() => {
                              movePhotoToRoom(showMoveMenu.photo, room.id);
                              setShowMoveMenu(null);
                            }}
                            className="w-full text-left px-6 py-1.5 hover:bg-gray-100 text-xs text-gray-600 transition-colors"
                          >
                            {room.name}
                          </button>
                        ))}
                        <button
                          onClick={async () => {
                            setAddingRoomInMoveMenu(categoryType);
                            try {
                              await handleAddRoom(categoryType);
                              // After room is created, move photo to it
                              setTimeout(() => {
                                const newRooms = roomsByCategory[categoryType] || [];
                                if (newRooms.length > 0) {
                                  const newRoom = newRooms[newRooms.length - 1];
                                  movePhotoToRoom(showMoveMenu.photo, newRoom.id);
                                }
                                setShowMoveMenu(null);
                              }, 200);
                            } catch (error) {
                              console.error('Error adding room:', error);
                            } finally {
                              setAddingRoomInMoveMenu(null);
                            }
                          }}
                          disabled={addingRoomInMoveMenu === categoryType}
                          className="w-full text-left px-6 py-1.5 hover:bg-gray-100 text-xs text-primary-600 font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5"
                        >
                          {addingRoomInMoveMenu === categoryType ? (
                            <>
                              <svg className="animate-spin h-3 w-3" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                              </svg>
                              Adding...
                            </>
                          ) : (
                            <>
                              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
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
              {Object.keys(dynamicCategories).length === 0 && !showAddCategoryInMoveMenu && (
                <div className="px-3 py-2 text-xs text-gray-500 text-center">
                  No categories yet
                </div>
              )}
              {/* Add Category Form in Move Menu */}
              {showAddCategoryInMoveMenu ? (
                <div className="border-t border-gray-100 p-2.5 bg-gray-50">
                  <form
                    onSubmit={async (e) => {
                      e.preventDefault();
                      if (!newCategoryNameInMoveMenu.trim()) return;
                      
                      setAddingCategoryInMoveMenu(true);
                      try {
                        const categoryKey = newCategoryNameInMoveMenu.trim().toLowerCase().replace(/\s+/g, '_');
                        if (dynamicCategories[categoryKey]) {
                          alert('This category already exists');
                          return;
                        }
                        const colorIndex = Object.keys(dynamicCategories).length % categoryColors.length;
                        const newCategoryConfig = {
                          label: newCategoryNameInMoveMenu.trim(),
                          icon: '📁',
                          ...categoryColors[colorIndex],
                        };
                        setDynamicCategories(prev => ({ ...prev, [categoryKey]: newCategoryConfig }));
                        await handleAddRoom(categoryKey);
                        // Move photo to newly created category
                        setTimeout(() => {
                          const newRooms = roomsByCategory[categoryKey] || [];
                          if (newRooms.length > 0) {
                            const newRoom = newRooms[newRooms.length - 1];
                            movePhotoToRoom(showMoveMenu.photo, newRoom.id);
                          }
                          setShowMoveMenu(null);
                        }, 200);
                        setNewCategoryNameInMoveMenu('');
                        setShowAddCategoryInMoveMenu(false);
                      } catch (error) {
                        console.error('Error adding category:', error);
                      } finally {
                        setAddingCategoryInMoveMenu(false);
                      }
                    }}
                    className="space-y-2"
                  >
                    <input
                      type="text"
                      value={newCategoryNameInMoveMenu}
                      onChange={(e) => setNewCategoryNameInMoveMenu(e.target.value)}
                      placeholder="Category name"
                      className="w-full px-2 py-1.5 border border-gray-300 rounded text-xs focus:ring-1 focus:ring-primary-500 focus:border-primary-500"
                      autoFocus
                      onKeyDown={(e) => {
                        if (e.key === 'Escape') {
                          setShowAddCategoryInMoveMenu(false);
                          setNewCategoryNameInMoveMenu('');
                        }
                      }}
                    />
                    <div className="flex gap-1.5">
                      <button
                        type="submit"
                        disabled={!newCategoryNameInMoveMenu.trim() || addingCategoryInMoveMenu}
                        className="flex-1 px-2 py-1 bg-primary-600 text-white rounded text-xs font-medium hover:bg-primary-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                      >
                        {addingCategoryInMoveMenu ? 'Adding...' : 'Add'}
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setShowAddCategoryInMoveMenu(false);
                          setNewCategoryNameInMoveMenu('');
                        }}
                        className="px-2 py-1 border border-gray-300 text-gray-700 rounded text-xs font-medium hover:bg-gray-50 transition-colors"
                      >
                        Cancel
                      </button>
                    </div>
                  </form>
                </div>
              ) : (
                <button
                  onClick={() => setShowAddCategoryInMoveMenu(true)}
                  className="w-full text-left px-3 py-1.5 border-t border-gray-100 hover:bg-gray-50 text-xs text-primary-600 font-medium transition-colors flex items-center gap-1.5"
                >
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                  </svg>
                  Add New Category
                </button>
              )}
            </div>
          </div>
        </>
      )}

      {/* Add Category Modal */}
      {showAddCategoryModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg p-4 max-w-sm w-full mx-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-gray-900">Add New Category</h3>
              <button
                onClick={() => {
                  setShowAddCategoryModal(false);
                  setNewCategoryName('');
                }}
                className="text-gray-400 hover:text-gray-600"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">
                  Category Name
                </label>
                <input
                  type="text"
                  value={newCategoryName}
                  onChange={(e) => setNewCategoryName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      handleAddCategory();
                    } else if (e.key === 'Escape') {
                      setShowAddCategoryModal(false);
                      setNewCategoryName('');
                    }
                  }}
                  className="w-full px-2.5 py-1.5 border border-gray-300 rounded text-sm focus:ring-1 focus:ring-primary-500 focus:border-primary-500"
                  placeholder="e.g., Office, Dining Room, etc."
                  autoFocus
                />
              </div>
              <div className="flex justify-end gap-2">
                <button
                  onClick={() => {
                    setShowAddCategoryModal(false);
                    setNewCategoryName('');
                  }}
                  className="px-3 py-1.5 border border-gray-300 rounded text-xs text-gray-700 font-medium hover:bg-gray-50 transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={handleAddCategory}
                  className="px-3 py-1.5 bg-primary-600 text-white rounded text-xs font-medium hover:bg-primary-700 transition-colors"
                >
                  Add Category
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* File Preview Modal */}
      {previewPhotoIndex !== null && previewPhotos.length > 0 && (
        <FilePreviewModal
          files={previewPhotos}
          currentIndex={previewPhotoIndex}
          onClose={handleClosePreview}
          onNavigate={handlePreviewNavigate}
          categories={dynamicCategories}
          roomsByCategory={roomsByCategory}
          onMoveToRoom={movePhotoToRoom}
          onMoveToCategory={handleMoveToCategory}
          onAddCategory={async (categoryName: string) => {
            const categoryKey = categoryName.toLowerCase().replace(/\s+/g, '_');
            if (dynamicCategories[categoryKey]) {
              throw new Error('Category already exists');
            }
            const colorIndex = Object.keys(dynamicCategories).length % categoryColors.length;
            const newCategoryConfig = {
              label: categoryName,
              icon: '📁',
              ...categoryColors[colorIndex],
            };
            setDynamicCategories(prev => ({ ...prev, [categoryKey]: newCategoryConfig }));
            
            // Create a new room in this category and return its ID
            const categoryRooms = roomsByCategory[categoryKey] || [];
            const roomNumber = categoryRooms.length + 1;
            const newRoom: Room = {
              id: `room-${categoryKey}-${Date.now()}`,
              name: `${newCategoryConfig.label} ${roomNumber}`,
              type: categoryKey as Room['type'],
              photoIds: [],
            };
            
            const updatedRooms = [...rooms, newRoom];
            setRooms(updatedRooms);
            
            dispatch({
              type: 'ADD_ROOM',
              payload: { studyId, room: newRoom },
            });
            
            try {
              await studyService.updateRooms(studyId, updatedRooms);
            } catch (error) {
              console.error('Error adding room to Firestore:', error);
            }
            
            // Return the room ID so the modal can move the photo to it
            return newRoom.id;
          }}
          onAddRoom={handleAddRoom}
          // Photo annotations props
          photoAnnotations={photoAnnotations}
          onUpdatePhotoAnnotations={updatePhotoAnnotations}
          onMarkReviewed={markPhotoReviewed}
        />
      )}
            </div>
          </div>
        </div>
      </div>
    </ProtectedRoute>
  );
}
