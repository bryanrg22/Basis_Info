import { WorkflowStatus } from './workflow.types';
import { Asset } from './asset.types';
import { UploadedFile, PhotoReviewState } from './file.types';
import { Room } from './room.types';
import { Takeoff } from './takeoff.types';
import { PersistedStudyState } from '@/utils/takeoff-local-storage';
import { AppraisalResources } from './appraisal-resources.types';

/**
 * Study Types
 * 
 * Note: In demo mode, we use Date instead of Firebase Timestamp
 */

export interface Study {
  id: string;
  userId: string;
  propertyName: string;
  totalAssets: number;
  analysisDate: string; // ISO date string
  status: 'completed' | 'in_progress' | 'pending';
  workflowStatus: WorkflowStatus;
  /** Current step the user is viewing (for navigation) */
  currentStep?: WorkflowStatus;
  /** Steps that have been visited (allows backward navigation) */
  visitedSteps?: WorkflowStatus[];
  createdAt: Date;
  updatedAt: Date;
  completedAt?: Date;
  assets: Asset[];
  uploadedFiles: UploadedFile[];
  rooms?: Room[];
  takeoffs?: Takeoff[];
  /** Per-photo annotations keyed by photo/file ID */
  photoAnnotations?: Record<string, PhotoReviewState>;
  /** Resource extraction checklist state */
  resourceChecklist?: Record<string, boolean>;
  /** Extracted appraisal resources (populated by backend) */
  appraisalResources?: AppraisalResources;
  /** Engineering takeoff state (persisted from localStorage) */
  engineeringTakeoffState?: PersistedStudyState;
}

export interface Statistics {
  studiesCompleted: number;
  revenueGenerated: number;
  taxSavingsProvided: number;
}

export interface User {
  id: string;
  name: string;
  email: string;
  company: string;
}
