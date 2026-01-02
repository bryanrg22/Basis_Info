/**
 * Room Types
 */

export interface Room {
  id: string;
  name: string;
  type: 'bedroom' | 'bathroom' | 'kitchen' | 'living' | 'exterior' | 'garage' | 'basement' | 'attic' | 'other' | string; // Allow custom room types
  photoIds: string[]; // References to UploadedFile IDs in study.uploadedFiles
}

