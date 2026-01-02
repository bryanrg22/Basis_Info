/**
 * Workflow Status Types
 * 
 * Simplified workflow:
 * 1. uploading_documents - Initial upload
 * 2. analyzing_rooms - AI analyzing rooms (loading state)
<<<<<<< HEAD
 * 3. reviewing_rooms - Room classification review
 * 4. engineering_takeoff - Engineering takeoff & asset classification (includes verification)
 * 5. completed - Study complete
=======
 * 3. resource_extraction - Appraisal resource extraction review (new step)
 * 4. reviewing_rooms - Room classification review
 * 5. engineering_takeoff - Engineering takeoff & asset classification
 * 6. verifying_assets - Asset verification
 * 7. completed - Study complete
>>>>>>> af00e6d (Resource Extration)
 */

export type WorkflowStatus = 
  | 'uploading_documents'
  | 'analyzing_rooms'
  | 'resource_extraction'
  | 'reviewing_rooms'
  | 'engineering_takeoff'
  | 'completed';

