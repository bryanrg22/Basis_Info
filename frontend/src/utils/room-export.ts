/**
 * Room Export Utility
 * 
 * Exports room-categorized images to a zip file with folder structure:
 * {category}/{roomName}/{fileName}
 * unassigned/{fileName}
 */

import JSZip from 'jszip';
import { Room, UploadedFile, Photo } from '@/types';
import { storageService } from '@/lib/storage';

/**
 * Sanitize folder/file names to be filesystem-safe
 */
function sanitizeFileName(name: string): string {
  return name.replace(/[^a-zA-Z0-9.-]/g, '_').replace(/\s+/g, '_');
}

/**
 * Format category name for folder (convert to Title Case)
 */
function formatCategoryName(categoryType: string): string {
  return categoryType
    .replace(/_/g, ' ')
    .replace(/([A-Z])/g, ' $1')
    .split(' ')
    .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(' ');
}

/**
 * Download an image from a URL and return as blob
 */
async function downloadImageAsBlob(url: string, fileName?: string): Promise<Blob> {
  try {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    const blob = await response.blob();
    
    // Validate that we got an image blob
    if (!blob.type.startsWith('image/') && blob.size === 0) {
      throw new Error('Invalid image data received');
    }
    
    return blob;
  } catch (error) {
    const fileNameStr = fileName ? ` (${fileName})` : '';
    if (error instanceof TypeError && error.message.includes('fetch')) {
      throw new Error(`Network error while downloading image${fileNameStr}. Please check your internet connection.`);
    }
    throw new Error(`Failed to download image${fileNameStr}: ${error instanceof Error ? error.message : 'Unknown error'}`);
  }
}

/**
 * Export result with statistics
 */
export interface ExportResult {
  blob: Blob;
  successCount: number;
  errorCount: number;
  errors: Array<{ fileName: string; error: string }>;
}

/**
 * Export rooms to a zip file
 * 
 * @param rooms - Array of rooms with categorized photos
 * @param uploadedFiles - Array of all uploaded files
 * @param unassignedPhotoIds - Array of photo IDs that are not assigned to any room
 * @param studyName - Name of the study (used for zip filename)
 * @param onProgress - Optional progress callback (0-100)
 * @returns Promise that resolves with the export result
 * @throws Error if export fails completely (e.g., no images, zip generation fails)
 */
export async function exportRoomsToZip(
  rooms: Room[],
  uploadedFiles: UploadedFile[],
  unassignedPhotoIds: string[],
  studyName: string = 'study',
  onProgress?: (progress: number) => void
): Promise<ExportResult> {
  // Validate inputs
  if (!Array.isArray(rooms)) {
    throw new Error('Invalid rooms data: expected an array');
  }
  if (!Array.isArray(uploadedFiles)) {
    throw new Error('Invalid uploadedFiles data: expected an array');
  }
  if (!Array.isArray(unassignedPhotoIds)) {
    throw new Error('Invalid unassignedPhotoIds data: expected an array');
  }

  const zip = new JSZip();
  
  // Create a map of fileId -> UploadedFile for quick lookup
  const fileMap = new Map<string, UploadedFile>();
  uploadedFiles.forEach(file => {
    if (file.type.startsWith('image/')) {
      fileMap.set(file.id, file);
    }
  });

  // Track statistics
  let successCount = 0;
  let errorCount = 0;
  const errors: Array<{ fileName: string; error: string }> = [];

  // Group rooms by category
  const roomsByCategory = new Map<string, Room[]>();
  rooms.forEach(room => {
    if (!room || !room.type || !Array.isArray(room.photoIds)) {
      console.warn('Invalid room data:', room);
      return;
    }
    if (!roomsByCategory.has(room.type)) {
      roomsByCategory.set(room.type, []);
    }
    roomsByCategory.get(room.type)!.push(room);
  });

  // Track total operations for progress
  let totalOperations = 0;
  let completedOperations = 0;

  // Count total images to process
  rooms.forEach(room => {
    if (room && Array.isArray(room.photoIds)) {
      totalOperations += room.photoIds.length;
    }
  });
  totalOperations += unassignedPhotoIds.length;

  // Check if there are any images to export
  if (totalOperations === 0) {
    throw new Error('No images to export. Please ensure rooms have photos assigned.');
  }

  // Helper to update progress
  const updateProgress = () => {
    if (onProgress && totalOperations > 0) {
      onProgress(Math.round((completedOperations / totalOperations) * 100));
    }
  };

  // Process each category
  for (const [categoryType, categoryRooms] of Array.from(roomsByCategory.entries())) {
    try {
      const categoryFolderName = sanitizeFileName(formatCategoryName(categoryType));
      if (!categoryFolderName) {
        console.warn(`Invalid category name: ${categoryType}`);
        continue;
      }
      
      const categoryFolder = zip.folder(categoryFolderName);
      if (!categoryFolder) {
        throw new Error(`Failed to create category folder: ${categoryFolderName}`);
      }

      // Process each room in the category
      for (const room of categoryRooms) {
        if (!room || !room.name) {
          console.warn('Invalid room data:', room);
          continue;
        }

        try {
          const roomFolderName = sanitizeFileName(room.name);
          if (!roomFolderName) {
            console.warn(`Invalid room name: ${room.name}`);
            continue;
          }
          
          const roomFolder = categoryFolder.folder(roomFolderName);
          if (!roomFolder) {
            throw new Error(`Failed to create room folder: ${roomFolderName}`);
          }

          // Process each photo in the room
          if (!Array.isArray(room.photoIds)) {
            console.warn(`Invalid photoIds for room ${room.name}:`, room.photoIds);
            continue;
          }

          for (const photoId of room.photoIds) {
            if (!photoId || typeof photoId !== 'string') {
              console.warn(`Invalid photo ID: ${photoId}`);
              completedOperations++;
              updateProgress();
              continue;
            }

            const file = fileMap.get(photoId);
            if (!file) {
              const errorMsg = `File not found for photo ID: ${photoId}`;
              console.warn(errorMsg);
              errors.push({ fileName: photoId, error: errorMsg });
              errorCount++;
              completedOperations++;
              updateProgress();
              continue;
            }

            try {
              // Validate file has required properties
              if (!file.storagePath && !file.downloadURL) {
                throw new Error('File missing both storagePath and downloadURL');
              }

              // Get download URL if not already available
              let downloadURL = file.downloadURL;
              if (!downloadURL) {
                if (!file.storagePath) {
                  throw new Error('File missing storagePath');
                }
                try {
                  downloadURL = await storageService.getDownloadURL(file.storagePath);
                } catch (storageError) {
                  throw new Error(`Failed to get download URL: ${storageError instanceof Error ? storageError.message : 'Unknown error'}`);
                }
              }

              // Download image as blob
              const imageBlob = await downloadImageAsBlob(downloadURL, file.name);
              
              // Add to zip with sanitized filename
              const sanitizedFileName = sanitizeFileName(file.name) || `image_${photoId}`;
              roomFolder.file(sanitizedFileName, imageBlob);
              
              successCount++;
              completedOperations++;
              updateProgress();
            } catch (error) {
              const errorMsg = error instanceof Error ? error.message : 'Unknown error';
              console.error(`Error processing image ${file.name}:`, error);
              errors.push({ fileName: file.name, error: errorMsg });
              errorCount++;
              completedOperations++;
              updateProgress();
              // Continue with other images even if one fails
            }
          }
        } catch (error) {
          console.error(`Error processing room ${room.name}:`, error);
          // Continue with other rooms
        }
      }
    } catch (error) {
      console.error(`Error processing category ${categoryType}:`, error);
      // Continue with other categories
    }
  }

  // Process unassigned photos
  if (unassignedPhotoIds.length > 0) {
    try {
      const unassignedFolder = zip.folder('unassigned');
      if (!unassignedFolder) {
        throw new Error('Failed to create unassigned folder');
      }

      for (const photoId of unassignedPhotoIds) {
        if (!photoId || typeof photoId !== 'string') {
          console.warn(`Invalid unassigned photo ID: ${photoId}`);
          completedOperations++;
          updateProgress();
          continue;
        }

        const file = fileMap.get(photoId);
        if (!file) {
          const errorMsg = `File not found for unassigned photo ID: ${photoId}`;
          console.warn(errorMsg);
          errors.push({ fileName: photoId, error: errorMsg });
          errorCount++;
          completedOperations++;
          updateProgress();
          continue;
        }

        try {
          // Validate file has required properties
          if (!file.storagePath && !file.downloadURL) {
            throw new Error('File missing both storagePath and downloadURL');
          }

          // Get download URL if not already available
          let downloadURL = file.downloadURL;
          if (!downloadURL) {
            if (!file.storagePath) {
              throw new Error('File missing storagePath');
            }
            try {
              downloadURL = await storageService.getDownloadURL(file.storagePath);
            } catch (storageError) {
              throw new Error(`Failed to get download URL: ${storageError instanceof Error ? storageError.message : 'Unknown error'}`);
            }
          }

          // Download image as blob
          const imageBlob = await downloadImageAsBlob(downloadURL, file.name);
          
          // Add to zip with sanitized filename
          const sanitizedFileName = sanitizeFileName(file.name) || `image_${photoId}`;
          unassignedFolder.file(sanitizedFileName, imageBlob);
          
          successCount++;
          completedOperations++;
          updateProgress();
        } catch (error) {
          const errorMsg = error instanceof Error ? error.message : 'Unknown error';
          console.error(`Error processing unassigned image ${file.name}:`, error);
          errors.push({ fileName: file.name, error: errorMsg });
          errorCount++;
          completedOperations++;
          updateProgress();
          // Continue with other images even if one fails
        }
      }
    } catch (error) {
      console.error('Error processing unassigned photos:', error);
      // Continue to zip generation even if unassigned folder fails
    }
  }

  // Check if we successfully added any files
  if (successCount === 0) {
    throw new Error('No images were successfully exported. Please check that images are accessible and try again.');
  }

  // Generate zip file
  if (onProgress) {
    onProgress(100);
  }
  
  try {
    const blob = await zip.generateAsync({ 
      type: 'blob',
      compression: 'DEFLATE',
      compressionOptions: { level: 6 }
    });
    
    return {
      blob,
      successCount,
      errorCount,
      errors
    };
  } catch (error) {
    throw new Error(`Failed to generate zip file: ${error instanceof Error ? error.message : 'Unknown error'}`);
  }
}

/**
 * Download a blob as a file
 */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

