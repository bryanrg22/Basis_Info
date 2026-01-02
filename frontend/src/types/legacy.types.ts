/**
 * Legacy Data Types
 * 
 * Types for handling legacy/migration data that may not match current structure.
 */

import { UploadedFile } from './file.types';

/**
 * Legacy study structure that may be missing fields or have different types
 */
export interface LegacyStudy {
  id?: string;
  propertyName?: string;
  totalAssets?: number;
  analysisDate?: string;
  status?: string;
  workflowStatus?: string;
  assets?: unknown[];
  uploadedFiles?: LegacyUploadedFile[];
  rooms?: unknown[];
  takeoffs?: unknown[];
}

/**
 * Legacy uploaded file structure
 */
export interface LegacyUploadedFile {
  id?: string;
  name?: string;
  type?: string;
  size?: number;
  uploadedAt?: string;
  storagePath?: string;
  downloadURL?: string;
  url?: string; // Old field name
}

