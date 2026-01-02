/**
 * File and Photo Types
 */

export interface UploadedFile {
  id: string;
  name: string;
  type: string; // MIME type (e.g., 'application/pdf', 'image/jpeg', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
  size: number; // bytes
  uploadedAt: string; // ISO date string
  storagePath: string; // Required: Firebase Storage path "users/{userId}/studies/{studyId}/files/{fileName}"
  downloadURL?: string; // Firebase Storage download URL (generated on-demand, can expire)
  assetIds?: string[]; // Optional: Array of asset IDs this file is associated with (many-to-many relationship)
}

export interface Photo {
  id: string;
  name: string;
  storagePath: string; // Firebase Storage path (required)
  downloadURL?: string; // Firebase Storage download URL (generated on-demand)
  uploadedAt: string; // ISO date string
}

/**
 * PhotoObject represents an object, material, or asset identified in a photo.
 */
export type PhotoObjectType = 'object' | 'material' | 'asset' | 'other';

export interface PhotoObject {
  id: string;
  label: string;
  type: PhotoObjectType;
  confidence: number; // 0-1 scale
  source?: 'auto' | 'manual'; // How the object was identified
  material?: string; // Material composition (e.g., "copper", "PVC", "wood")
  notes?: string;
  createdAt: string; // ISO date string
  updatedAt: string; // ISO date string
}

/**
 * PhotoReviewState tracks the review status and detected objects for a single photo.
 * Keyed by photo ID in the study's photoAnnotations map.
 */
export interface PhotoReviewState {
  objects: PhotoObject[];
  reviewed: boolean;
  reviewedAt?: string; // ISO date string when marked as reviewed
  updatedAt: string; // ISO date string of last modification
}

